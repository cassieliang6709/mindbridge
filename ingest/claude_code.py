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
TOOL_IO_MAX_CHARS = 600


def default_root() -> Path:
    return Path.home() / ".claude" / "projects"


def iter_lines(path: Path, start_offset: int = 0) -> Iterator[tuple[int, str]]:
    """Yield (offset_after_line, line) so a cursor can resume mid-file.

    JSONL here is append-only, so a byte offset is a safe resume point. Opened
    in binary and decoded per line, because a byte offset into a text-mode file
    is not portable.
    """
    with path.open("rb") as handle:
        handle.seek(start_offset)
        for raw in handle:
            offset = handle.tell()
            yield offset, raw.decode("utf-8", errors="replace")


def _parse_timestamp(value: Any) -> datetime | None:
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
    """Turn a content field into plain text plus the tool names it invoked."""
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
    """
    size = path.stat().st_size
    restarted = False
    if start_offset > size:
        # The file shrank: rotated or rewritten. Re-read from the top rather
        # than resuming into the middle of a different file.
        start_offset = 0
        restarted = True

    turns: list[ParsedTurn] = []
    offset = start_offset
    lines_read = skipped = malformed = 0

    # Records belonging to one assistant response, plus the byte offset where
    # that group started, so the cursor can stop before an unfinished group.
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
    if record.get("type") not in ("user", "assistant"):
        return None
    if record.get("isMeta"):
        # System-injected context, not something the user or model said.
        return None
    if record.get("isSidechain") and not include_sidechains:
        # Subagent traffic: useful for debugging, noise for a memory record.
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
    root = root or default_root()
    if not root.exists():
        return []
    return sorted(root.glob("**/*.jsonl"))
