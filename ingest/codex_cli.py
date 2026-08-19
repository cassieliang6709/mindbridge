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
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
import re
from datetime import datetime
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
_ROLES = {"user": "user", "assistant": "assistant"}


def default_root() -> Path:
    return Path.home() / ".codex"


def session_id_for(path: Path) -> str:
    match = _FILENAME_RE.match(path.stem)
    if match:
        return match.group("uuid")
    return path.stem


def _flatten_content(content: Any) -> str:
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
    """Signature mirrors the Claude Code reader so the runner stays source-agnostic."""
    # Codex rollouts have no sidechains, and one message is one record, so
    # there is no multi-record group to hold back.
    del include_sidechains, assume_complete

    size = path.stat().st_size
    restarted = False
    if start_offset > size:
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
            cwd = payload.get("cwd")
            if isinstance(cwd, str) and cwd:
                current_cwd = cwd
            continue

        # Newer rollouts emit `custom_tool_call` where older ones emitted
        # `function_call`; accept both so a format change does not silently
        # zero out the tool tallies.
        if kind in ("function_call", "custom_tool_call", "local_shell_call"):
            name = payload.get("name") or payload.get("tool_name")
            if isinstance(name, str):
                # Attach to the next assistant turn, mirroring how Claude Code
                # carries tool_use blocks inside the assistant message.
                pending_tools.append(name)
            continue

        if kind == "reasoning" and not include_thinking:
            continue
        if kind in ("function_call_output", "custom_tool_call_output") and not include_tool_io:
            continue

        if kind != "message":
            # user_message / agent_message duplicate `message`; token_count,
            # task_started and friends are bookkeeping.
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
    root = root or default_root()
    if not root.exists():
        return []
    # Keep the flat glob for callers that still pass ~/.codex/archived_sessions
    # directly. The default root is ~/.codex so new, still-active sessions and
    # archived sessions enter the same source without exposing any other file.
    paths = {
        *root.glob("rollout-*.jsonl"),
        *root.glob("archived_sessions/rollout-*.jsonl"),
        *root.glob("sessions/**/rollout-*.jsonl"),
    }
    return sorted(paths)
