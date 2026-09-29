"""Pydantic v2 models shared by the REST API, the MCP server and the evals."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MemoryNamespace = Literal["operational", "reflective"]
RankingMode = Literal["temporal", "semantic"]

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
# 中文：日卡与会话卡共用一张表，读取时必须明确所需范围，避免混入另一类卡片。
CardScope = Literal["day", "session", "all"]
PatternStatus = Literal["pending", "confirmed", "edited", "rejected"]
PatternDecision = Literal["confirm", "edit", "reject"]
MemoryMutationAction = Literal["archive", "edit"]


class Turn(BaseModel):
    """One raw prompt or response in T1."""

    # 中文：T1 中的一条原始提示、回复或工具调用记录。

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
    # Input accepted when an API client appends one T1 turn.
    # 中文：API 客户端向 T1 追加单条记录时使用的输入模型。
    role: Literal["user", "assistant", "tool"]
    content: str = Field(min_length=1)
    tool: str | None = Field(
        default=None,
        description="Which client produced the turn, e.g. claude-code, codex-cli.",
    )


class SessionBuffer(BaseModel):
    """T1 read model: the live window plus what has aged out of it."""

    # 中文：T1 的读取结果，包含当前窗口与已被挤出窗口的记录数量。

    session_id: str
    window: int
    turns: list[Turn]
    tokens_in_window: int
    evicted_count: int


class SummaryCard(BaseModel):
    """T2: one structured card per day."""

    # 中文：T2 的结构化摘要卡；日卡与会话卡都使用该模型。

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
    # Input used to create or replace a T2 summary card.
    # 中文：创建或替换 T2 摘要卡时使用的输入模型。
    period: str = Field(description="e.g. 2026-08-04 or 2026-W32.")
    summary: str = Field(min_length=1)
    developer_behavior_facts: list[str] = Field(default_factory=list)
    session_id: str | None = None


class NarrativeUpdate(BaseModel):
    """M2 output layered onto an existing rule-based card."""

    # 中文：叠加到既有规则摘要卡上的 M2 模型输出。

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

    # 中文：不含向量嵌入的 T3 记忆记录。

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
        """Whether this memory has not been closed or superseded.

        中文：判断该记忆是否仍然有效；有效记忆的 ``valid_at`` 为空。
        """
        return self.valid_at is None


class MemoryWithDecay(MemoryRecord):
    """A T3 row plus the decay weight it currently carries.

    Listing has no query, so there is no cosine term and no score — only the
    time component. Keeping them separate stops a timeline reading as if it
    were a relevance ranking.
    """

    # 中文：时间线读取的 T3 记录，只携带时间衰减权重，不携带查询相关度。

    age_days: float
    decay_multiplier: float


class MemoryHit(MemoryRecord):
    """A T3 row with the scores that put it in the result set."""

    # 中文：检索命中的 T3 记录，附带余弦相似度、时间衰减与最终分数。

    cosine_similarity: float
    age_days: float
    decay_multiplier: float
    score: float


class UpsertPreferenceRequest(BaseModel):
    # Input for deduplicated creation or refresh of a durable T3 preference.
    # 中文：创建或刷新持久 T3 偏好时使用的去重写入输入模型。
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
        """Keep operational and reflective memory categories separate.

        中文：校验操作型与反思型记忆的类别边界，并要求反思型记忆获得用户确认。

        Raises:
            ValueError: If a category belongs to the wrong namespace or a
                reflective memory lacks explicit user confirmation.
        """
        if self.namespace == "reflective":
            if self.category not in REFLECTIVE_CATEGORIES:
                raise ValueError("reflective memory needs a reflective category")
            if not self.confirmed_by_user:
                raise ValueError("reflective memory requires explicit user confirmation")
        elif self.category not in OPERATIONAL_CATEGORIES:
            raise ValueError("operational memory needs an operational category")
        return self


class UpsertPreferenceResult(BaseModel):
    # Result returned after a T3 preference was inserted, refreshed, or superseded.
    # 中文：T3 偏好插入、刷新或替换后的返回结果。
    action: UpsertAction
    record: MemoryRecord
    matched_id: int | None = None
    matched_similarity: float | None = None
    reason: str


class MemoryMutationRequest(BaseModel):
    """Mutable memory operations for the Memory Garden path."""

    # 中文：Memory Garden 中归档或编辑记忆的请求。

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
        """Require replacement wording for an edit operation.

        中文：编辑操作必须提供替换后的记忆内容。

        Raises:
            ValueError: If an edit request has no replacement content.
        """
        if self.action == "edit" and not self.content:
            raise ValueError("edit action requires content")
        return self


class MemoryMutationResult(BaseModel):
    """Result of archive/edit operations.

    For `edit`, `target_id` points to the old record, `replacement_id` to the
    new one. For `archive`, both ids are the same.
    """

    # 中文：归档或编辑操作的结果；编辑会生成新记录，归档不会。

    action: MemoryMutationAction
    target_id: int
    replacement_id: int
    memory: MemoryWithDecay
    replacement_reason: str


class TemporalQueryRequest(BaseModel):
    # Input for a ranked T3 recall query.
    # 中文：带排序的 T3 回忆检索请求模型。
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
    ranking_mode: RankingMode = Field(
        default="temporal",
        description=(
            "'temporal' combines semantic relevance with time decay; "
            "'semantic' ranks by cosine relevance while retaining decay fields "
            "for audit."
        ),
    )


class TemporalQueryResult(BaseModel):
    # Ranked T3 recall response, including a prompt-ready context block.
    # 中文：T3 检索响应，包含排序后的命中结果和可直接放入提示词的上下文文本。
    query: str
    hits: list[MemoryHit]
    decay_rate_per_day: float
    cache_hit: bool = False
    context_block: str = Field(
        description="Pre-formatted text ready to paste into a prompt."
    )


class PatternEvidence(BaseModel):
    """One dated, inspectable observation supporting or challenging a pattern."""

    # 中文：支持或反驳某个模式的一条可追溯、带日期的观察记录。

    source_date: date
    summary: str = Field(min_length=4, max_length=400)
    source_id: str | None = Field(
        default=None,
        description="Optional T1/T2 id or other stable receipt reference.",
    )


class PatternCandidateCreate(BaseModel):
    """An inference waiting for the user, never a durable trait by itself."""

    # 中文：等待用户确认的推断，单独存在时绝不是持久的人格结论。

    description: str = Field(min_length=10, max_length=500)
    supporting_evidence: list[PatternEvidence] = Field(min_length=3, max_length=10)
    counter_evidence: list[PatternEvidence] = Field(default_factory=list, max_length=10)
    contexts: list[str] = Field(min_length=1, max_length=8)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def require_repeated_dates(self) -> "PatternCandidateCreate":
        """Require evidence across dates and discard blank contexts.

        中文：要求证据来自多个日期，并移除空白的上下文描述。

        Raises:
            ValueError: If evidence covers fewer than two dates or no nonblank
                context remains.
        """
        dates = {item.source_date for item in self.supporting_evidence}
        if len(dates) < 2:
            raise ValueError("a pattern candidate needs evidence from at least two dates")
        self.contexts = [context.strip() for context in self.contexts if context.strip()]
        if not self.contexts:
            raise ValueError("a pattern candidate needs at least one context")
        return self


class PatternCandidate(BaseModel):
    # Stored, reviewable reflective inference; it is not T3 memory by itself.
    # 中文：已保存、可供审核的反思型推断；它本身不是 T3 记忆。
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
    # User decision that confirms, edits, or rejects a Pattern Candidate.
    # 中文：用户对 Pattern Candidate 进行确认、编辑或拒绝的决定。
    decision: PatternDecision
    confirmed_content: str | None = Field(default=None, min_length=10, max_length=500)
    resolution_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_edited_wording(self) -> "PatternDecisionRequest":
        """Require user-approved wording when the decision is an edit.

        中文：当决定为编辑时，必须提供用户确认过的新表述。

        Raises:
            ValueError: If an edit decision has no confirmed replacement text.
        """
        if self.decision == "edit" and not self.confirmed_content:
            raise ValueError("edit requires confirmed_content")
        return self


class DailyReview(BaseModel):
    """One review surface joining T2, both T3 lanes and pending inference."""

    # 中文：汇总 T2、两类 T3 和记忆模式候选项的一日审核视图。

    period: str
    card: SummaryCard | None
    operational_memories: list[MemoryWithDecay]
    reflective_memories: list[MemoryWithDecay]
    pending_patterns: list[PatternCandidate]
