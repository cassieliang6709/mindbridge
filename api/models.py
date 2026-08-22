"""Pydantic v2 models shared by the REST API, the MCP server and the evals."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MemoryNamespace = Literal["operational", "reflective"]

MemoryCategory = Literal[
    "coding_style",
    "tool_preference",
    "behavioral_fact",
    "schedule",
    "confirmed_pattern",
    "value",
    "recurring_trigger",
    "helpful_strategy",
    "identity_hypothesis",
    "other",
]

OPERATIONAL_CATEGORIES = frozenset(
    {"coding_style", "tool_preference", "behavioral_fact", "schedule", "other"}
)
REFLECTIVE_CATEGORIES = frozenset(
    {
        "confirmed_pattern",
        "value",
        "recurring_trigger",
        "helpful_strategy",
        "identity_hypothesis",
    }
)

UpsertAction = Literal["inserted", "refreshed", "superseded"]

# Day cards and session cards share one table, so every read says which it wants.
CardScope = Literal["day", "session", "all"]
PatternStatus = Literal["pending", "confirmed", "edited", "rejected"]
PatternDecision = Literal["confirm", "edit", "reject"]
MemoryMutationAction = Literal["archive", "edit"]


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
    # Written by ingest, not by append(): a turn created through the REST API
    # has no project. Readers that summarise a day use it to name the project a
    # day was mostly spent in, so it has to survive the trip out of Postgres.
    project: str | None = None


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
    narrative: str | None = Field(
        default=None, description="Model-written prose, when M2 has run."
    )
    open_threads: list[str] = Field(default_factory=list)
    generated_by: str = Field(
        default="rule",
        description="'rule' for the computed card, else the model id that wrote it.",
    )
    model: str | None = None
    extracted_at: datetime | None = None


class SummaryCardCreate(BaseModel):
    period: str = Field(description="e.g. 2026-08-04 or 2026-W32.")
    summary: str = Field(min_length=1)
    developer_behavior_facts: list[str] = Field(default_factory=list)
    session_id: str | None = None


class NarrativeUpdate(BaseModel):
    """M2 output layered onto an existing rule-based card."""

    period: str
    narrative: str = Field(min_length=1)
    highlights: list[str] = Field(default_factory=list)
    open_threads: list[str] = Field(default_factory=list)
    generated_by: str = Field(min_length=1)
    model: str
    session_id: str | None = Field(
        default=None,
        description="None targets the day card; a value targets that session's.",
    )


class MemoryRecord(BaseModel):
    """T3 row without its embedding."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    content: str
    namespace: MemoryNamespace = "operational"
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
    project: str | None = Field(
        default=None,
        description=(
            "Which project this preference holds inside. None means it holds "
            "everywhere."
        ),
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
    namespace: MemoryNamespace = "operational"
    category: MemoryCategory = "other"
    confirmed_by_user: bool = Field(
        default=False,
        description=(
            "Required for reflective memory. It means the user confirmed the "
            "wording, not merely that a model inferred it."
        ),
    )
    decay_factor: float = Field(default=1.0, gt=0.0)
    project: str | None = Field(
        default=None,
        description=(
            "Scope this preference to one project. Leave unset for a "
            "preference that holds across all of them."
        ),
    )
    supersedes_conflicting: bool = Field(
        default=False,
        description=(
            "When true, a near-duplicate below the dedup threshold but above "
            "conflict_threshold is closed out and replaced by this record."
        ),
    )

    @model_validator(mode="after")
    def validate_namespace_boundary(self) -> "UpsertPreferenceRequest":
        if self.namespace == "reflective":
            if self.category not in REFLECTIVE_CATEGORIES:
                raise ValueError("reflective memory needs a reflective category")
            if not self.confirmed_by_user:
                raise ValueError("reflective memory requires explicit user confirmation")
        elif self.category not in OPERATIONAL_CATEGORIES:
            raise ValueError("operational memory needs an operational category")
        return self


class UpsertPreferenceResult(BaseModel):
    action: UpsertAction
    record: MemoryRecord
    matched_id: int | None = None
    matched_similarity: float | None = None
    reason: str


class MemoryMutationRequest(BaseModel):
    """Mutable memory operations for the Memory Garden path."""

    action: MemoryMutationAction
    content: str | None = Field(default=None, min_length=1)
    decay_factor: float | None = Field(
        default=None,
        gt=0.0,
        description=(
            "Optional override for the replacement record's decay factor. If omitted, "
            "the edited memory keeps the current decay_factor."
        ),
    )
    reason: str | None = Field(default=None, max_length=400)

    @model_validator(mode="after")
    def require_content_for_edit(self) -> "MemoryMutationRequest":
        if self.action == "edit" and not self.content:
            raise ValueError("edit action requires content")
        return self


class MemoryMutationResult(BaseModel):
    """Result of archive/edit operations.

    For `edit`, `target_id` points to the old record, `replacement_id` to the
    new one. For `archive`, both ids are the same.
    """

    action: MemoryMutationAction
    target_id: int
    replacement_id: int
    memory: MemoryWithDecay
    replacement_reason: str


class TemporalQueryRequest(BaseModel):
    query_string: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    time_window_days: int | None = Field(
        default=None,
        ge=1,
        description="Only consider records created within this many days.",
    )
    categories: list[MemoryCategory] | None = None
    namespaces: list[MemoryNamespace] | None = None
    project: str | None = Field(
        default=None,
        description=(
            "Current project for ranking. None applies no project-specific "
            "down-weighting."
        ),
    )
    include_superseded: bool = False


class TemporalQueryResult(BaseModel):
    query: str
    hits: list[MemoryHit]
    decay_rate_per_day: float
    cache_hit: bool = False
    context_block: str = Field(
        description="Pre-formatted text ready to paste into a prompt."
    )


class PatternEvidence(BaseModel):
    """One dated, inspectable observation supporting or challenging a pattern."""

    source_date: date
    summary: str = Field(min_length=4, max_length=400)
    source_id: str | None = Field(
        default=None,
        description="Optional T1/T2 id or other stable receipt reference.",
    )


class PatternCandidateCreate(BaseModel):
    """An inference waiting for the user, never a durable trait by itself."""

    description: str = Field(min_length=10, max_length=500)
    supporting_evidence: list[PatternEvidence] = Field(min_length=3, max_length=10)
    counter_evidence: list[PatternEvidence] = Field(default_factory=list, max_length=10)
    contexts: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_repeated_dates(self) -> "PatternCandidateCreate":
        dates = {item.source_date for item in self.supporting_evidence}
        if len(dates) < 2:
            raise ValueError("a pattern candidate needs evidence from at least two dates")
        self.contexts = [context.strip() for context in self.contexts if context.strip()]
        if not self.contexts:
            raise ValueError("a pattern candidate needs at least one context")
        return self


class PatternCandidate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    supporting_evidence: list[PatternEvidence]
    counter_evidence: list[PatternEvidence]
    contexts: list[str]
    confidence: float
    status: PatternStatus
    resolution_note: str | None = None
    confirmed_memory_id: int | None = None
    created_at: datetime
    updated_at: datetime


class PatternDecisionRequest(BaseModel):
    decision: PatternDecision
    confirmed_content: str | None = Field(default=None, min_length=10, max_length=500)
    resolution_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_edited_wording(self) -> "PatternDecisionRequest":
        if self.decision == "edit" and not self.confirmed_content:
            raise ValueError("edit requires confirmed_content")
        return self


class DailyReview(BaseModel):
    """One review surface joining T2, both T3 lanes and pending inference."""

    period: str
    card: SummaryCard | None
    operational_memories: list[MemoryWithDecay]
    reflective_memories: list[MemoryWithDecay]
    pending_patterns: list[PatternCandidate]
