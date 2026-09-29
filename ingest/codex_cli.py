"""Reader for Codex CLI rollouts in both active and archived session folders.

Observed shape (confirmed against local files, 2026-08) — a flat envelope,
unlike Claude Code's:

  {"timestamp": ISO8601,
   "type": "response_item" | "event_msg" | "turn_context",
   "payload": {"type": "message"|"agent_message"|"user_message"|"reasoning"
                       |"function_call"|"function_call_output"|"token_count"|...,
               "role": "user"|"assistant"|"developer",
               "content": [{"type": "input_text"|"output_text", "text": ...}]}}

Conversation lives in payload.type == "message" (with a role) and in the
convenience events "user_message" / "agent_message". Those overlap, so the
reader takes `message` records and ignores the event duplicates to avoid
double-counting a turn.

Session id comes from the filename: rollout-<ISO timestamp>-<uuid>.jsonl.

中文说明：这是 Codex CLI 会话记录（rollout）的读取器，同时覆盖活跃会话和归档
会话两个目录。Codex 的 JSONL 结构是扁平的信封格式，和 Claude Code 不一样：
真正的对话内容在 payload.type == "message"（带 role）里，另外还有
"user_message" / "agent_message" 这两个便捷事件，但它们和 message 记录内容
重复，所以读取器只取 message 记录、忽略这些重复事件，避免同一轮对话被计两次。
会话 id 从文件名中提取：rollout-<ISO 时间戳>-<uuid>.jsonl。
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
import re
from pathlib import Path
from typing import Any

from api.memory import count_tokens

from .claude_code import iter_lines, _parse_timestamp
from .models import ParsedTurn, ParseOutcome
from .redaction import redact

SOURCE = "codex-cli"

_FILENAME_RE = re.compile(
    r"^rollout-(?P<stamp>\d{4}-\d{2}-\d{2}T[\d-]+)-(?P<uuid>[0-9a-f-]{36})$"
)

# "developer" is the harness injecting instructions, not the human.
# 中文："developer" 角色是运行环境（harness）注入的指令，不是人类用户说的话。
_ROLES = {"user": "user", "assistant": "assistant"}


def default_root() -> Path:
    """Default location Codex CLI writes its rollout session files to.

    中文：Codex CLI 默认写入会话记录（rollout）文件的目录位置。

    Returns:
        Path to `~/.codex`. `~/.codex` 的路径。
    """
    return Path.home() / ".codex"


def session_id_for(path: Path) -> str:
    """Extract the session uuid from a rollout filename, if it matches the pattern.

    中文：从 rollout 文件名中提取会话 uuid（如果文件名符合约定格式）。

    Args:
        path: Path to a rollout JSONL file. rollout JSONL 文件的路径。

    Returns:
        The uuid from the filename, or the bare filename stem as a fallback.
        文件名中的 uuid；如果不匹配约定格式，则回退使用文件名本身（不含扩展名）。
    """
    match = _FILENAME_RE.match(path.stem)
    if match:
        return match.group("uuid")
    return path.stem


def _flatten_content(content: Any) -> str:
    """Turn a Codex `content` field (string or block list) into plain text.

    中文：把 Codex 的 `content` 字段（字符串或内容块列表）展平成纯文本。

    Args:
        content: The raw content field from a message payload.
            消息负载中的原始 content 字段。

    Returns:
        Joined plain text from any input_text/output_text/text blocks.
        从 input_text/output_text/text 类型内容块中拼接出的纯文本。
    """
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") in ("input_text", "output_text", "text"):
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text.strip())
    return "\n".join(parts).strip()


def parse_file(
    path: Path,
    start_offset: int = 0,
    *,
    include_tool_io: bool = False,
    include_thinking: bool = False,
    include_sidechains: bool = False,
    assume_complete: bool = True,
) -> ParseOutcome:
    """Signature mirrors the Claude Code reader so the runner stays source-agnostic.

    中文：这个函数签名刻意和 Claude Code 读取器的 `parse_file` 保持一致，这样
    runner 就不需要区分调用的是哪个来源的读取器。

    Args:
        path: Rollout file to read. 要读取的 rollout 文件路径。
        start_offset: Byte offset to resume from. 续读的起始字节偏移量。
        include_tool_io: Whether to include function_call_output payload text.
            是否包含工具调用输出（function_call_output）的文本内容。
        include_thinking: Whether to include "reasoning" blocks.
            是否包含 "reasoning"（推理）内容块。
        include_sidechains: Unused here — Codex rollouts have no sidechains.
            Accepted only so the signature matches the Claude Code reader.
            此参数在这里不生效——Codex 的 rollout 没有 sidechain 概念，
            接受它只是为了和 Claude Code 读取器的签名保持一致。
        assume_complete: Unused here — one Codex message is one record, so
            there is no multi-record group that could be left half-written.
            此参数在这里不生效——Codex 里一条消息就是一条记录，不存在像
            Claude Code 那样"一组记录可能只写了一半"的情况。

    Returns:
        A ParseOutcome describing the turns found and how far reading got.
        一个 ParseOutcome，描述解析出的对话轮以及读取进度。
    """
    # Codex rollouts have no sidechains, and one message is one record, so
    # there is no multi-record group to hold back.
    # 中文：Codex 的 rollout 没有 sidechain，且一条消息就是一条记录，所以不存在
    # 需要暂时保留、等下次运行处理的"多记录组"。
    del include_sidechains, assume_complete

    size = path.stat().st_size
    restarted = False
    if start_offset > size:
        # The file shrank: rotated or rewritten. Re-read from the top rather
        # than resuming into the middle of a different file.
        # 中文：文件变小了，说明它被轮转或重写过；此时从头重新读取。
        start_offset = 0
        restarted = True

    session_id = session_id_for(path)
    turns: list[ParsedTurn] = []
    offset = start_offset
    lines_read = skipped = malformed = 0
    pending_tools: list[str] = []
    # Codex reports the working directory once per turn in a `turn_context`
    # record rather than on every message, so it has to be carried forward.
    # Without this every Codex turn lands with project=None, and once Codex
    # became the majority source the day cards started reading
    # "led by unknown project" for most of the corpus.
    # 中文：Codex 每一轮只在 `turn_context` 记录里报告一次工作目录（cwd），
    # 而不是每条消息都带，所以必须把它保存下来、延续到后续消息。如果不这样
    # 做，每一条 Codex 记录的 project 都会是 None；一旦 Codex 成为数据的主要
    # 来源，日卡片就会大面积显示 "led by unknown project"。
    current_cwd: str | None = None
    seen_identities: dict[str, int] = defaultdict(int)

    for new_offset, line in iter_lines(path, start_offset):
        offset = new_offset
        lines_read += 1
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            malformed += 1
            offset = new_offset - len(line.encode("utf-8"))
            break
        if not isinstance(record, dict):
            malformed += 1
            continue

        payload = record.get("payload")
        if not isinstance(payload, dict):
            skipped += 1
            continue
        kind = payload.get("type")

        if record.get("type") == "turn_context":
            # See the comment above current_cwd's declaration: this is the
            # once-per-turn cwd report that has to be carried forward.
            # 中文：对应上面 current_cwd 声明处的说明——这是每轮只报告一次的
            # cwd 信息，需要延续保存到后续消息。
            cwd = payload.get("cwd")
            if isinstance(cwd, str) and cwd:
                current_cwd = cwd
            continue

        # Newer rollouts emit `custom_tool_call` where older ones emitted
        # `function_call`; accept both so a format change does not silently
        # zero out the tool tallies.
        # 中文：较新的 rollout 会发出 `custom_tool_call`，旧版本发出的是
        # `function_call`；两种都接受，这样格式变化就不会悄悄地把工具调用
        # 统计清零。
        if kind in ("function_call", "custom_tool_call", "local_shell_call"):
            name = payload.get("name") or payload.get("tool_name")
            if isinstance(name, str):
                # Attach to the next assistant turn, mirroring how Claude Code
                # carries tool_use blocks inside the assistant message.
                # 中文：把工具名暂存起来，挂到下一个 assistant 轮次上，这与
                # Claude Code 把 tool_use 块放进 assistant 消息内部的做法类似。
                pending_tools.append(name)
            continue

        if kind == "reasoning" and not include_thinking:
            continue
        if kind in ("function_call_output", "custom_tool_call_output") and not include_tool_io:
            continue

        if kind != "message":
            # user_message / agent_message duplicate `message`; token_count,
            # task_started and friends are bookkeeping.
            # 中文：user_message / agent_message 和 message 记录内容重复；
            # token_count、task_started 之类的都是纯记账信息，不是对话内容。
            skipped += 1
            continue

        role = _ROLES.get(str(payload.get("role")))
        if role is None:
            skipped += 1
            continue

        text = _flatten_content(payload.get("content"))
        if not text:
            skipped += 1
            continue

        created_at = _parse_timestamp(record.get("timestamp"))
        if created_at is None:
            skipped += 1
            continue

        text, redactions = redact(text)
        tools = pending_tools if role == "assistant" else []
        if role == "assistant":
            pending_tools = []
        if tools:
            text = "\n".join([text, *(f"[tool:{name}]" for name in tools)])

        # Rollouts carry no per-record id, so the key is derived from what
        # identifies the turn in the file. It deliberately excludes the turn
        # TEXT: keying on text meant that teaching the parser to recognise a
        # new tool-call type changed the rendered text, changed the key, and
        # re-inserted every affected turn as a new row. A parser improvement
        # must not look like new data.
        #
        # `seq` disambiguates the rare case of two turns sharing a session,
        # timestamp and role.
        # 中文：rollout 记录本身没有逐条的唯一 id，所以主键要靠"是什么把这一
        # 轮标识出来"来推导，而这里刻意不包含对话的文本内容：如果用文本参与
        # 生成主键，那么当解析器学会识别一种新的工具调用类型、导致渲染出的
        # 文本变化时，主键也会跟着变，进而把所有受影响的轮次当成"新数据"
        # 重新插入一遍。解析器的改进不应该看起来像是产生了新数据。
        # `seq` 用来区分极少数"同一会话、同一时间戳、同一角色"出现两轮的情况。
        identity = f"{session_id}|{created_at.isoformat()}|{role}"
        seq = seen_identities[identity]
        seen_identities[identity] += 1
        digest = hashlib.blake2b(
            f"{identity}|{seq}".encode(), digest_size=12
        ).hexdigest()
        turns.append(
            ParsedTurn(
                source=SOURCE,
                session_id=session_id,
                source_key=f"{SOURCE}:{digest}",
                role=role,  # type: ignore[arg-type]
                text=text,
                created_at=created_at,
                # Codex reports token_count as a separate cumulative event, not
                # per message, so a per-turn provider figure is not available.
                # 中文：Codex 把 token_count 作为一个独立的累计事件上报，而不是
                # 挂在每条消息上，所以拿不到"这一轮"对应的服务商数值，只能本地计数。
                token_count=count_tokens(text),
                token_source="local",
                tool_names=tools,
                cwd=current_cwd,
                project=Path(current_cwd).name if current_cwd else None,
                git_branch=None,
                redactions=redactions,
            )
        )

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


def discover(root: Path | None = None) -> list[Path]:
    """Find every Codex CLI rollout file under `root`, active or archived.

    中文：找出 `root` 目录下所有 Codex CLI 的会话记录（rollout）文件，包括
    活跃会话和已归档的会话。

    Args:
        root: Directory to search; defaults to `default_root()`.
            要搜索的目录；默认为 `default_root()` 返回的路径。

    Returns:
        Sorted list of rollout file paths, or an empty list if root doesn't
        exist. 排好序的 rollout 文件路径列表；如果目录不存在则返回空列表。
    """
    root = root or default_root()
    if not root.exists():
        return []
    # Keep the flat glob for callers that still pass ~/.codex/archived_sessions
    # directly. The default root is ~/.codex so new, still-active sessions and
    # archived sessions enter the same source without exposing any other file.
    # 中文：保留这个不递归的 glob 模式，是为了兼容仍然直接传入
    # ~/.codex/archived_sessions 的调用方。默认根目录是 ~/.codex，这样活跃
    # 会话和归档会话都能进入同一个来源，且不会意外暴露目录下的其他文件。
    paths = {
        *root.glob("rollout-*.jsonl"),
        *root.glob("archived_sessions/rollout-*.jsonl"),
        *root.glob("sessions/**/rollout-*.jsonl"),
    }
    return sorted(paths)
