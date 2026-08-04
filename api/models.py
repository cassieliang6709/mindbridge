"""Pydantic v2 models shared by the REST API, the MCP server and the evals."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MemoryCategory = Literal[
    "coding_style",
    "tool_preference",
    "behavioral_fact",
    "schedule",
    "other",
]

UpsertAction = Literal["inserted", "refreshed", "superseded"]


class Turn(BaseModel):
    """One raw prompt or response in T1."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str
    role: Literal["user", "assistant", "tool"]
    content: str
    tool: str | None = None
    token_count: int
    created_at: datetime


class TurnCreate(BaseModel):
    role: Literal["user", "assistant", "tool"]
    content: str = Field(min_length=1)
    tool: str | None = Field(
        default=None,
        description="Which client produced the turn, e.g. claude-code, codex-cli.",
    )


class SessionBuffer(BaseModel):
    """T1 read model: the live window plus what has aged out of it."""

    session_id: str
    window: int
    turns: list[Turn]
    tokens_in_window: int
    evicted_count: int


class SummaryCard(BaseModel):
    """T2: one structured card per day."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str | None
    period: str = Field(description="ISO date for a daily card, or ISO week.")
    summary: str
    developer_behavior_facts: list[str]
    token_count: int
    created_at: datetime
    updated_at: datetime


class SummaryCardCreate(BaseModel):
    period: str = Field(description="e.g. 2026-08-04 or 2026-W32.")
    summary: str = Field(min_length=1)
    developer_behavior_facts: list[str] = Field(default_factory=list)
    session_id: str | None = None


class MemoryRecord(BaseModel):
    """T3 row without its embedding."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    content: str
    category: MemoryCategory
    created_at: datetime
    valid_at: datetime | None = Field(
        default=None,
        description="When the record stopped being true. None means still open.",
    )
    superseded_by: int | None = None
    access_count: int
    decay_factor: float = Field(
        description="Per-record multiplier on λ; 1.0 follows the global rate."
    )

    @property
    def is_open(self) -> bool:
        return self.valid_at is None


class MemoryWithDecay(MemoryRecord):
    """A T3 row plus the decay weight it currently carries.

    Listing has no query, so there is no cosine term and no score — only the
    time component. Keeping them separate stops a timeline reading as if it
    were a relevance ranking.
    """

    age_days: float
    decay_multiplier: float


class MemoryHit(MemoryRecord):
    """A T3 row with the scores that put it in the result set."""

    cosine_similarity: float
    age_days: float
    decay_multiplier: float
    score: float


class UpsertPreferenceRequest(BaseModel):
    content: str = Field(min_length=1)
    category: MemoryCategory = "other"
    decay_factor: float = Field(default=1.0, gt=0.0)
    supersedes_conflicting: bool = Field(
        default=False,
        description=(
            "When true, a near-duplicate below the dedup threshold but above "
            "conflict_threshold is closed out and replaced by this record."
        ),
    )


class UpsertPreferenceResult(BaseModel):
    action: UpsertAction
    record: MemoryRecord
    matched_id: int | None = None
    matched_similarity: float | None = None
    reason: str


class TemporalQueryRequest(BaseModel):
    query_string: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    time_window_days: int | None = Field(
        default=None,
        ge=1,
        description="Only consider records created within this many days.",
    )
    categories: list[MemoryCategory] | None = None
    include_superseded: bool = False


class TemporalQueryResult(BaseModel):
    query: str
    hits: list[MemoryHit]
    decay_rate_per_day: float
    cache_hit: bool = False
    context_block: str = Field(
        description="Pre-formatted text ready to paste into a prompt."
    )
