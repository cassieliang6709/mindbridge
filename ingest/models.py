"""Shared shapes for Path A ingestion.

Claude Code and Codex CLI write very different JSONL, so each reader normalises
into ParsedTurn and nothing downstream needs to know which tool produced it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

SourceKind = Literal["claude-code", "codex-cli"]


class ParsedTurn(BaseModel):
    """One conversational turn, normalised across sources."""

    source: SourceKind
    session_id: str
    source_key: str = Field(
        description=(
            "Stable identity of the underlying record, unique across sources. "
            "Makes re-ingestion idempotent even if a cursor is lost."
        )
    )
    role: Literal["user", "assistant", "tool"]
    text: str
    created_at: datetime
    token_count: int = Field(
        description=(
            "Reported by the provider when available (assistant output_tokens), "
            "otherwise counted locally. See token_source."
        )
    )
    token_source: Literal["provider", "local"] = "local"
    tool_names: list[str] = Field(
        default_factory=list,
        description="Tools invoked in this turn, names only, no arguments.",
    )
    cwd: str | None = None
    project: str | None = Field(
        default=None, description="Basename of cwd — the human-facing project name."
    )
    git_branch: str | None = None
    redactions: int = 0


class FileCursor(BaseModel):
    """Where ingestion stopped in one transcript file."""

    source: SourceKind
    path: str
    bytes_read: int = 0
    turns_ingested: int = 0
    last_uuid: str | None = None
    updated_at: datetime | None = None


class ParseOutcome(BaseModel):
    """Result of reading one file from a byte offset."""

    path: str
    source: SourceKind
    turns: list[ParsedTurn]
    bytes_read: int
    lines_read: int = 0
    lines_skipped: int = 0
    malformed_lines: int = 0
    restarted: bool = Field(
        default=False,
        description="True when the file shrank, so it was re-read from the top.",
    )


class DayStats(BaseModel):
    sessions: int = 0
    turns: int = 0
    user_turns: int = 0
    assistant_turns: int = 0
    tokens: int = 0
    tool_counts: dict[str, int] = Field(default_factory=dict)
    projects: list[str] = Field(default_factory=list)
    git_branches: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    first_activity: datetime | None = None
    last_activity: datetime | None = None
    latest_late_activity: datetime | None = Field(
        default=None,
        description="Timestamp of the latest turn falling in the small hours.",
    )


class DayDigest(BaseModel):
    """A deterministic, rule-based day summary.

    IMPORTANT: nothing here is model-generated. Every line is computed from
    counts and timestamps, so it is reproducible and cannot hallucinate. Turning
    a day into prose, and extracting durable preferences from it, is M2 — until
    the local extractor exists this digest deliberately states only what it can
    prove from the transcript.
    """

    date: str
    summary: str
    facts: list[str]
    stats: DayStats
