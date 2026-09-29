"""Reader for Claude Code transcripts: ~/.claude/projects/**/*.jsonl

Observed shape (confirmed against 159 local files, 2026-08):

  {"type": "user"|"assistant"|"system"|"attachment"|"file-history-snapshot"|...,
   "uuid": ..., "parentUuid": ..., "sessionId": ..., "timestamp": ISO8601,
   "cwd": ..., "gitBranch": ..., "version": ..., "isSidechain": bool,
   "isMeta": true?,
   "message": {"role": ..., "content": str | [block, ...],
               "usage": {"input_tokens": n, "output_tokens": n, ...}}}

Content blocks seen in the wild: text, thinking, tool_use, tool_result, image.
Only `user` and `assistant` records carry a message; every other `type` is
bookkeeping (queue operations, titles, file-history snapshots) and is skipped.

Two traps this reader exists to handle:

1. One assistant response is written as SEVERAL records — one per content block
   — and each repeats the same final `message.usage`. Summing usage per record
   inflated the token total by 2.5x on real data. Records sharing a
   `message.id` are merged into a single turn and the usage is counted once.
2. The newest group of records may still be streaming when the file is read.
   Unless the file has been quiet for a while, the trailing group is held back
   and the cursor stops before it, so a half-written response is never stored.

中文说明：这是 Claude Code 转录文件（~/.claude/projects/**/*.jsonl）的读取器。
有两个坑是这个模块专门用来处理的：
1. 一次 assistant 回复会被拆成多条记录写入（每个内容块一条），且每条记录都
   重复带着同一份最终的 `message.usage`。如果对每条记录都累加 usage，会把
   token 总数虚高 2.5 倍（在真实数据上验证过）。解决办法是把 `message.id`
   相同的记录合并成一轮，usage 只计一次。
2. 读取文件时，最新的一组记录可能还在被流式写入（尚未写完）。除非这个文件
   已经有一段时间没有新写入，否则最后一组记录会被暂时保留、不写入游标之前，
   这样就不会把一个"写了一半"的回复当成完整数据存下来。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from api.memory import count_tokens

from .models import ParsedTurn, ParseOutcome
from .redaction import redact

SOURCE = "claude-code"

# Tool payloads are enormous — whole files, full command output — and they are
# the least useful part of a memory record. Off by default: it keeps the store
# small and avoids persisting file contents a second time. --include-tool-io
# turns it on.
# 中文：工具调用的原始负载（tool payload）通常非常大——完整文件内容、完整命令
# 输出——但对记忆系统来说价值最低。默认关闭，这样存储体积更小，也避免把文件
# 内容重复存一份。用 --include-tool-io 可以打开它。
TOOL_IO_MAX_CHARS = 600


def default_root() -> Path:
    """Default location Claude Code writes its per-project transcripts to.

    中文：Claude Code 默认写入各项目转录文件的目录位置。

    Returns:
        Path to `~/.claude/projects`. `~/.claude/projects` 的路径。
    """
    return Path.home() / ".claude" / "projects"


def iter_lines(path: Path, start_offset: int = 0) -> Iterator[tuple[int, str]]:
    """Yield (offset_after_line, line) so a cursor can resume mid-file.

    JSONL here is append-only, so a byte offset is a safe resume point. Opened
    in binary and decoded per line, because a byte offset into a text-mode file
    is not portable.

    中文：逐行产出 (读完这一行之后的字节偏移量, 这一行内容)，这样游标就可以
    从文件中间的某个位置继续读。这里的 JSONL 文件是只追加写入的（append-only），
    所以字节偏移量是一个安全的续读点。之所以用二进制模式打开、再逐行解码，是
    因为文本模式下的字节偏移量在不同平台上不一定一致，不可移植。

    Args:
        path: Transcript file to read. 要读取的转录文件路径。
        start_offset: Byte offset to resume from. 续读的起始字节偏移量。

    Yields:
        Tuples of (byte offset after this line, decoded line text).
        (读完这一行后的字节偏移量, 解码后的这一行文本) 元组。
    """
    with path.open("rb") as handle:
        handle.seek(start_offset)
        for raw in handle:
            offset = handle.tell()
            yield offset, raw.decode("utf-8", errors="replace")


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse an ISO8601 timestamp, tolerating a trailing 'Z' and no timezone.

    中文：解析 ISO8601 格式的时间戳，兼容末尾的 'Z' 写法以及缺省时区的情况
    （缺省时按 UTC 处理）。

    Args:
        value: The raw timestamp field, expected to be a string. 原始时间戳字段（预期为字符串）。

    Returns:
        A timezone-aware datetime, or None if value isn't a parseable timestamp.
        带时区信息的 datetime；如果 value 不是可解析的时间戳则返回 None。
    """
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _flatten_content(
    content: Any, include_tool_io: bool, include_thinking: bool
) -> tuple[str, list[str]]:
    """Turn a content field into plain text plus the tool names it invoked.

    中文：把一个 content 字段展平成纯文本，并同时收集其中调用过的工具名称。

    Args:
        content: The raw `message.content` — a string or a list of content
            blocks. 原始的 `message.content` 字段，可能是字符串或内容块列表。
        include_tool_io: Whether to include tool_result payload text.
            是否包含工具返回结果（tool_result）的文本内容。
        include_thinking: Whether to include assistant "thinking" blocks.
            是否包含 assistant 的"思考"（thinking）内容块。

    Returns:
        A tuple of (joined plain text, tool names invoked in this content).
        一个 (拼接后的纯文本, 本次内容中调用的工具名列表) 元组。
    """
    if isinstance(content, str):
        return content.strip(), []
    if not isinstance(content, list):
        return "", []

    parts: list[str] = []
    tools: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
        elif kind == "thinking":
            if include_thinking:
                text = block.get("thinking")
                if isinstance(text, str) and text.strip():
                    parts.append(f"[thinking] {text.strip()}")
        elif kind == "tool_use":
            name = block.get("name")
            if isinstance(name, str):
                tools.append(name)
                # Record that a tool ran, never its arguments — those carry
                # file paths, diffs and occasionally credentials.
                # 中文：只记录"某个工具被调用过"，绝不记录它的参数——参数里
                # 常常带有文件路径、代码 diff，偶尔还有凭证信息。
                parts.append(f"[tool:{name}]")
        elif kind == "tool_result":
            if not include_tool_io:
                continue
            payload = block.get("content")
            text = _tool_result_text(payload)
            if text:
                parts.append(f"[tool_result] {text[:TOOL_IO_MAX_CHARS]}")
        elif kind == "image":
            parts.append("[image]")
    return "\n".join(parts).strip(), tools


def _tool_result_text(payload: Any) -> str:
    """Extract plain text from a tool_result block's `content` payload.

    中文：从 tool_result 内容块的 `content` 负载中提取纯文本。

    Args:
        payload: The tool_result's raw content — a string or list of blocks.
            tool_result 的原始 content，可能是字符串或内容块列表。

    Returns:
        Joined plain text, or an empty string if none is present.
        拼接后的纯文本；如果没有可提取的文本则返回空字符串。
    """
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        chunks = [
            block.get("text", "")
            for block in payload
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(chunk for chunk in chunks if chunk).strip()
    return ""


def parse_file(
    path: Path,
    start_offset: int = 0,
    *,
    include_tool_io: bool = False,
    include_thinking: bool = False,
    include_sidechains: bool = False,
    assume_complete: bool = True,
) -> ParseOutcome:
    """Read one transcript from `start_offset` to EOF.

    `assume_complete=False` holds back the trailing message group, for a file
    that may still be receiving writes.

    中文：从 `start_offset` 开始读取一个转录文件，直到文件末尾。当
    `assume_complete=False` 时，会保留最后一组消息记录不返回——因为这个文件
    有可能还在被继续写入（比如一次流式响应尚未结束）。这样可以避免把一个还
    没写完的回复当作最终数据摄取进来。

    Args:
        path: Transcript file to read. 要读取的转录文件路径。
        start_offset: Byte offset to resume from. 续读的起始字节偏移量。
        include_tool_io: Whether to include tool_result payload text.
            是否包含工具返回结果的文本内容。
        include_thinking: Whether to include assistant "thinking" blocks.
            是否包含 assistant 的"思考"内容块。
        include_sidechains: Whether to include subagent (sidechain) turns.
            是否包含子代理（sidechain）产生的对话轮。
        assume_complete: If False, the trailing message group is held back
            because the file may still be streaming. 如果为 False，会保留最后
            一组消息（因为文件可能仍在被流式写入）。

    Returns:
        A ParseOutcome describing the turns found and how far reading got.
        一个 ParseOutcome，描述解析出的对话轮以及读取进度。
    """
    size = path.stat().st_size
    restarted = False
    if start_offset > size:
        # The file shrank: rotated or rewritten. Re-read from the top rather
        # than resuming into the middle of a different file.
        # 中文：文件变小了，说明它被轮转（rotate）或重写过；此时从头重新读取，
        # 而不是继续从旧的偏移量续读——否则可能读到的是另一个文件的中间内容。
        start_offset = 0
        restarted = True

    turns: list[ParsedTurn] = []
    offset = start_offset
    lines_read = skipped = malformed = 0

    # Records belonging to one assistant response, plus the byte offset where
    # that group started, so the cursor can stop before an unfinished group.
    # 中文：属于同一次 assistant 回复的记录组，以及这组记录开始的字节偏移量——
    # 这样游标就可以停在一个尚未写完的组之前，而不会把它当成完整数据存下来。
    group: list[dict[str, Any]] = []
    group_id: str | None = None
    group_start = start_offset
    safe_offset = start_offset

    def flush() -> None:
        nonlocal group, group_id, safe_offset
        if group:
            merged = _merge_group(
                group,
                include_tool_io=include_tool_io,
                include_thinking=include_thinking,
                include_sidechains=include_sidechains,
            )
            if merged is not None:
                turns.append(merged)
        group = []
        group_id = None
        safe_offset = offset

    for new_offset, line in iter_lines(path, start_offset):
        line_start = new_offset - len(line.encode("utf-8"))
        offset = new_offset
        lines_read += 1
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            # A partially flushed line is normal while a session is live: stop
            # before it and let the next run pick it up once complete.
            # 中文：会话仍在进行中时，出现写了一半的行是正常现象；这里停在这
            # 一行之前，等它写完之后由下一次运行来处理。
            malformed += 1
            offset = line_start
            break
        if not isinstance(record, dict):
            malformed += 1
            continue

        message = record.get("message")
        message_id = (
            message.get("id")
            if record.get("type") == "assistant" and isinstance(message, dict)
            else None
        )

        if message_id is not None and message_id == group_id:
            group.append(record)
            continue

        flush()

        if message_id is not None:
            group = [record]
            group_id = message_id
            group_start = line_start
            continue

        turn = _record_to_turn(
            record,
            include_tool_io=include_tool_io,
            include_thinking=include_thinking,
            include_sidechains=include_sidechains,
        )
        if turn is None:
            skipped += 1
        else:
            turns.append(turn)
        safe_offset = offset

    if assume_complete:
        flush()
    elif group:
        # Leave the in-flight response for the next run.
        # 中文：把还在进行中（可能未写完）的回复留给下一次运行处理，游标退回
        # 到这组记录开始的位置。
        offset = group_start
    else:
        offset = safe_offset

    return ParseOutcome(
        path=str(path),
        source=SOURCE,
        turns=turns,
        bytes_read=offset,
        lines_read=lines_read,
        lines_skipped=skipped,
        malformed_lines=malformed,
        restarted=restarted,
    )


def _merge_group(
    records: list[dict[str, Any]],
    *,
    include_tool_io: bool,
    include_thinking: bool,
    include_sidechains: bool,
) -> ParsedTurn | None:
    """Collapse the records of one assistant response into a single turn.

    Content is concatenated in file order; usage is taken from the group once,
    since every record repeats the same figure. Identity comes from the first
    record's uuid so the merged turn has a stable source_key.

    中文：把属于同一次 assistant 回复的多条记录合并成一轮。内容按文件中的顺序
    拼接；usage（token 用量）只从这组记录里取一次，因为每条记录都重复带着
    同一个数值——这正是避免 2.5 倍虚高的关键所在。身份标识（source_key）取自
    第一条记录的 uuid，这样合并后的这一轮才有一个稳定的 source_key。

    Args:
        records: All records sharing one `message.id`. 共享同一个 `message.id` 的所有记录。
        include_tool_io: Whether to include tool_result payload text.
            是否包含工具返回结果的文本内容。
        include_thinking: Whether to include assistant "thinking" blocks.
            是否包含 assistant 的"思考"内容块。
        include_sidechains: Whether to include subagent (sidechain) turns.
            是否包含子代理（sidechain）产生的对话轮。

    Returns:
        The merged ParsedTurn, or None if the group produced no usable text.
        合并后的 ParsedTurn；如果这组记录没有产出可用文本则返回 None。
    """
    turn = _record_to_turn(
        records[0],
        include_tool_io=include_tool_io,
        include_thinking=include_thinking,
        include_sidechains=include_sidechains,
        text_override=None,
    )
    if len(records) == 1:
        return turn

    texts: list[str] = []
    tools: list[str] = []
    for record in records:
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        text, block_tools = _flatten_content(
            message.get("content"), include_tool_io, include_thinking
        )
        if text:
            texts.append(text)
        tools.extend(block_tools)

    combined = "\n".join(texts).strip()
    if not combined:
        return None
    if turn is None:
        # The first record was filtered (meta/sidechain); so is the group.
        # 中文：如果第一条记录被过滤掉了（元信息或子代理），那这整组记录也
        # 一并过滤掉。
        return None

    combined, redactions = redact(combined)
    return turn.model_copy(
        update={
            "text": combined,
            "tool_names": tools,
            "redactions": turn.redactions + redactions,
        }
    )


def _record_to_turn(
    record: dict[str, Any],
    *,
    include_tool_io: bool,
    include_thinking: bool,
    include_sidechains: bool,
    text_override: str | None = None,
) -> ParsedTurn | None:
    """Convert one raw JSONL record into a ParsedTurn, or None if it should be skipped.

    中文：把一条原始 JSONL 记录转换成 ParsedTurn；如果这条记录应该被跳过（不是
    真正的用户/模型发言），则返回 None。

    Args:
        record: One decoded JSON line from the transcript. 转录文件中解码后的一行 JSON。
        include_tool_io: Whether to include tool_result payload text.
            是否包含工具返回结果的文本内容。
        include_thinking: Whether to include assistant "thinking" blocks.
            是否包含 assistant 的"思考"内容块。
        include_sidechains: Whether to include subagent (sidechain) turns.
            是否包含子代理（sidechain）产生的对话轮。
        text_override: If set, use this text instead of flattening the record's
            own content. Only used internally by `_merge_group`.
            如果设置了该值，就用它替代从记录本身内容展平出的文本；仅供
            `_merge_group` 内部使用。

    Returns:
        A ParsedTurn, or None if this record carries no storable turn.
        一个 ParsedTurn；如果这条记录不包含可存储的对话轮则返回 None。
    """
    if record.get("type") not in ("user", "assistant"):
        return None
    if record.get("isMeta"):
        # System-injected context, not something the user or model said.
        # 中文：这是系统注入的上下文信息，不是用户或模型真正说的话。
        return None
    if record.get("isSidechain") and not include_sidechains:
        # Subagent traffic: useful for debugging, noise for a memory record.
        # 中文：子代理产生的流量，对调试有用，但对记忆记录来说是噪音。
        return None

    message = record.get("message")
    if not isinstance(message, dict):
        return None
    role = message.get("role")
    if role not in ("user", "assistant"):
        return None

    text, tools = _flatten_content(
        message.get("content"), include_tool_io, include_thinking
    )
    if text_override is not None:
        text = text_override
    if not text:
        return None

    created_at = _parse_timestamp(record.get("timestamp"))
    if created_at is None:
        return None

    text, redactions = redact(text)

    # Prefer the provider's own accounting. output_tokens is what this turn
    # actually produced; input_tokens describes the whole context window, so
    # summing them would count the same history once per turn.
    # 中文：优先使用服务商自己给出的用量统计。output_tokens 才是这一轮真正
    # 产出的部分；input_tokens 描述的是整个上下文窗口，如果把两者加在一起，
    # 就会把同样的历史内容在每一轮里都重复计一次。
    usage = message.get("usage")
    token_count = None
    token_source: str = "local"
    if isinstance(usage, dict) and isinstance(usage.get("output_tokens"), int):
        token_count = usage["output_tokens"]
        token_source = "provider"
    if token_count is None:
        token_count = count_tokens(text)

    cwd = record.get("cwd") if isinstance(record.get("cwd"), str) else None
    session_id = str(record.get("sessionId") or "unknown")
    uuid = record.get("uuid")
    source_key = (
        f"{SOURCE}:{uuid}"
        if isinstance(uuid, str) and uuid
        else f"{SOURCE}:{session_id}:{created_at.isoformat()}"
    )
    return ParsedTurn(
        source=SOURCE,
        session_id=session_id,
        source_key=source_key,
        role=role,
        text=text,
        created_at=created_at,
        token_count=token_count,
        token_source=token_source,  # type: ignore[arg-type]
        tool_names=tools,
        cwd=cwd,
        project=Path(cwd).name if cwd else None,
        git_branch=(
            record.get("gitBranch")
            if isinstance(record.get("gitBranch"), str) and record.get("gitBranch")
            else None
        ),
        redactions=redactions,
    )


def discover(root: Path | None = None) -> list[Path]:
    """Find every Claude Code transcript file under `root`.

    中文：找出 `root` 目录下所有的 Claude Code 转录文件。

    Args:
        root: Directory to search; defaults to `default_root()`.
            要搜索的目录；默认为 `default_root()` 返回的路径。

    Returns:
        Sorted list of transcript file paths, or an empty list if root doesn't
        exist. 排好序的转录文件路径列表；如果目录不存在则返回空列表。
    """
    root = root or default_root()
    if not root.exists():
        return []
    return sorted(root.glob("**/*.jsonl"))
