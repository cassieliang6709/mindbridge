"""The extraction contract.

This is the whole point of M2: the model must return exactly this shape, and
anything else is a failure we can count. `extra="forbid"` is deliberate — a
model that invents a field has not followed the schema, and silently ignoring it
would inflate the compliance metric the résumé quotes.

Constraints are enforced here rather than trusted from the prompt, so the
compliance rate measures the model, not our leniency.

中文说明：这里是 M2 抽取结果的可执行合同。额外字段、瞬时任务和情绪推断都会
被明确拒绝，因此“schema 合规率”衡量的是模型是否遵守合同，而不是校验器有多
宽松；它仍然不等于抽取内容本身的质量。
"""

from __future__ import annotations

import re

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Mirrors api.models.MemoryCategory so an extracted preference can go straight
# into T3 through Path B without a translation step.
# 中文：与 API 的记忆分类保持一致，使抽取结果无需转换即可走 Path B 写入 T3。
PreferenceCategory = Literal[
    "coding_style",
    "tool_preference",
    "behavioral_fact",
    "schedule",
    "other",
]


class ExtractedPreference(BaseModel):
    """A durable fact about the user, worth remembering past today."""

    # 中文：值得跨日期保留的用户事实。中文放在普通注释而不是 class docstring
    # 中，因为 Pydantic 会把后者写入 model_json_schema()，进而改变真实 prompt。
    model_config = ConfigDict(extra="forbid")

    content: str = Field(
        min_length=4,
        max_length=200,
        description=(
            "The preference as a standalone statement, understandable without "
            "the conversation it came from."
        ),
    )
    category: PreferenceCategory
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: str = Field(
        min_length=4,
        max_length=400,
        description="What in the transcript supports this, quoted or paraphrased.",
    )
    project: str | None = Field(
        default=None,
        description=(
            "The project this preference is scoped to, exactly as given on the "
            "PROJECT line. Omit when the preference holds across every project."
        ),
    )

    @field_validator("content")
    @classmethod
    def reject_transient(cls, value: str) -> str:
        """Reject one-off task descriptions dressed up as preferences.

        "Fix the 500 in /retrieve" is today's work, not a durable preference. If
        it reaches T3 it will be recalled for months as if it still mattered, so
        it is cheaper to fail validation and let the repair loop try again.

        The imperative list alone was not enough. A local 3B model passed schema
        validation while writing the day's activity into long-term memory —
        "You added a LinkedIn profile link to the website", at confidence 1.00.
        Two more shapes are therefore rejected:

        - Past-tense narration of a specific act ("You added…", "You fixed…"),
          which describes an event, not a standing preference.
        - Present progressive ("You are testing…", "You are updating…"), which
          describes what is happening now and will be false next week.

        Durable phrasings survive untouched: "Prefers…", "Keeps…", "Wants…",
        "You have a preference for…", "Uses X rather than Y".

        中文：拒绝把一次性任务、已完成事件或正在进行的活动伪装成长期偏好。
        一旦瞬时内容进入 T3，它可能在数月后仍被错误召回，因此宁可让校验失败
        并触发修复循环。

        Args:
            value: Candidate preference text.

        Returns:
            The stripped text when it describes a durable preference.

        Raises:
            ValueError: If the text looks transient or event-like.
        """
        lowered = value.lower().strip()

        transient_starts = ("fix ", "debug ", "finish ", "today ", "continue ")
        if lowered.startswith(transient_starts):
            raise ValueError(
                "content looks like a one-off task, not a durable preference; "
                "state a lasting habit or requirement instead"
            )

        # "You are <verb>ing" / "You're <verb>ing" — an activity in progress.
        # 中文：现在进行时描述当前活动，不是长期偏好。
        if re.match(r"^(you\s+are|you're|they\s+are|user\s+is)\s+\w+ing\b", lowered):
            raise ValueError(
                "content describes an activity in progress, not a durable "
                "preference; say what the user consistently prefers instead of "
                "what they are doing right now"
            )

        # "You added / created / updated / implemented …" — a completed act.
        # 中文：过去时的具体动作是事件叙述，不是持续成立的习惯。
        past_acts = (
            "added", "created", "updated", "implemented", "wrote", "built",
            "changed", "removed", "deleted", "renamed", "moved", "installed",
            "configured", "deployed", "ran", "tested", "fixed", "refactored",
        )
        match = re.match(r"^(?:you|the user|user)\s+(\w+)\b", lowered)
        if match and match.group(1) in past_acts:
            raise ValueError(
                f"content narrates a specific action ('{match.group(1)}'), not a "
                "durable preference; state the lasting habit it implies, or "
                "return no preference at all"
            )

        # Chinese todo markers. The hosted teacher never produced one
        # (0 of 573 T3 rows); the local models do — they write the session's
        # next task into T3 as if it were a standing preference.
        # 中文：托管教师的历史输出未触发中文待办词，但本地模型会把下一步任务
        # 写成偏好；因此守卫必须覆盖真实会出现的中文输入。
        cn_todo = ("下一步的", "下一步要", "接下来要", "当前最重要",
                   "待办", "尚未完成", "还没做完", "正在调试", "正在跑")
        for marker in cn_todo:
            if marker in value:
                raise ValueError(
                    f"content reads as a task in flight ({marker!r}), not a "
                    "durable preference"
                )

        return value.strip()


class DiaryDraft(BaseModel):
    """What the model returns for one day."""

    # 中文：模型针对一天返回的完整结果。这里同样不用中文 class docstring，
    # 以免改变被内嵌进 prompt 的 JSON schema。
    model_config = ConfigDict(extra="forbid")

    narrative: str = Field(
        min_length=40,
        max_length=900,
        description=(
            "Two to four sentences addressed to the user as 'you', describing "
            "what the day's work actually was."
        ),
    )
    highlights: list[str] = Field(
        min_length=1,
        max_length=6,
        description="Concrete things that happened, one clause each.",
    )
    preferences: list[ExtractedPreference] = Field(
        max_length=5,
        description="Durable facts learned today. Empty is a valid answer.",
    )
    open_threads: list[str] = Field(
        max_length=5,
        description="Work left unfinished, phrased as what remains to be done.",
    )

    @field_validator("narrative")
    @classmethod
    def reject_emotional_claims(cls, value: str) -> str:
        """Keep the narrative to observable work, not inferred feelings.

        The diary states behaviour with its evidence and never diagnoses mood —
        that boundary is what lets the product avoid clinical framing. A model
        will drift into "you seemed frustrated" unless it is stopped, and the
        prompt alone is not a guarantee.

        中文：日记只能描述可观察行为，不能把文本诊断成情绪状态；prompt 中的
        要求不是强保证，因此在 schema 边界再次执行校验。

        Args:
            value: Candidate narrative text.

        Returns:
            The stripped narrative when no banned claim is present.

        Raises:
            ValueError: If the narrative infers an emotional state.
        """
        banned = (
            "you felt",
            "you were feeling",
            "you seemed",
            "you appeared",
            "frustrated",
            "anxious",
            "stressed",
            "burnt out",
            "burned out",
            "exhausted",
        )
        lowered = value.lower()
        for phrase in banned:
            if phrase in lowered:
                raise ValueError(
                    f"narrative must not infer emotional state (found "
                    f"{phrase!r}); describe observable work instead"
                )
        return value.strip()

    @field_validator("highlights", "open_threads")
    @classmethod
    def clean_lines(cls, value: list[str]) -> list[str]:
        """Strip each list item and reject empty entries.

        中文：清理列表条目的首尾空白，并拒绝空条目。

        Args:
            value: Highlight or open-thread strings.

        Returns:
            A list whose entries are stripped.

        Raises:
            ValueError: If any entry becomes empty after stripping.
        """
        cleaned = [line.strip() for line in value if line and line.strip()]
        if len(cleaned) != len(value):
            raise ValueError("list contains empty entries")
        return cleaned


def json_schema() -> dict[str, object]:
    """Return the provider-facing DiaryDraft JSON schema.

    中文：返回可交给支持结构化输出的模型服务商的 DiaryDraft JSON schema。

    Returns:
        The current DiaryDraft JSON schema.
    """
    return DiaryDraft.model_json_schema()
