"""Shared shapes for Path A ingestion.

Claude Code and Codex CLI write very different JSONL, so each reader normalises
into ParsedTurn and nothing downstream needs to know which tool produced it.

中文说明：Claude Code 与 Codex CLI 的日志格式不同。两个读取器先把原始记录
转换为本模块的统一模型，后续摄取流程因此不需要区分数据来自哪个工具。
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

SourceKind = Literal["claude-code", "codex-cli"]


class ParsedTurn(BaseModel):
    """One conversational turn, normalised across sources."""

    # 中文：跨数据源统一的一轮对话。source_key 是幂等写入所依赖的稳定身份。
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

    @field_validator("text")
    @classmethod
    def strip_nul(cls, value: str) -> str:
        """Remove NUL bytes before text reaches Postgres.

        Tool output can contain 0x00 after reading binary data or raw process
        output. PostgreSQL text rejects that byte, so cleaning it in the shared
        model protects every reader at the same boundary.

        中文：工具读取二进制数据或原始进程输出时可能带入 NUL 字节，而
        PostgreSQL 的 text 无法存储它。在共享模型层清洗可以一次覆盖所有
        日志读取器。

        Args:
            value: Parsed transcript text.

        Returns:
            The text with every NUL byte removed.
        """
        return value.replace("\x00", "") if "\x00" in value else value


class FileCursor(BaseModel):
    """Where ingestion stopped in one transcript file."""

    # 中文：单个日志文件的断点；文件缩短时读取器会忽略旧偏移并从头读取。
    source: SourceKind
    path: str
    bytes_read: int = 0
    turns_ingested: int = 0
    last_uuid: str | None = None
    updated_at: datetime | None = None


class ParseOutcome(BaseModel):
    """Result of reading one file from a byte offset."""

    # 中文：一次增量读取的结果，同时记录新偏移和异常行统计。
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
    # 中文：构建每日摘要时使用的可复现聚合统计，不含模型推断结果。
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

    # 中文：这里的摘要完全由计数与时间戳确定性生成，不是模型写作。模型生成的
    # 叙事与长期偏好属于后续抽取阶段，不能伪装成本模型能够直接证明的事实。
    date: str
    summary: str
    facts: list[str]
    stats: DayStats
