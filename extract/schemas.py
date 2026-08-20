"""The extraction contract.

This is the whole point of M2: the model must return exactly this shape, and
anything else is a failure we can count. `extra="forbid"` is deliberate — a
model that invents a field has not followed the schema, and silently ignoring it
would inflate the compliance metric the résumé quotes.

Constraints are enforced here rather than trusted from the prompt, so the
compliance rate measures the model, not our leniency.
"""

from __future__ import annotations

import re

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Mirrors api.models.MemoryCategory so an extracted preference can go straight
# into T3 through Path B without a translation step.
PreferenceCategory = Literal[
    "coding_style",
    "tool_preference",
    "behavioral_fact",
    "schedule",
    "other",
]


class ExtractedPreference(BaseModel):
    """A durable fact about the user, worth remembering past today."""

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
        """
        lowered = value.lower().strip()

        transient_starts = ("fix ", "debug ", "finish ", "today ", "continue ")
        if lowered.startswith(transient_starts):
            raise ValueError(
                "content looks like a one-off task, not a durable preference; "
                "state a lasting habit or requirement instead"
            )

        # "You are <verb>ing" / "You're <verb>ing" — an activity in progress.
        if re.match(r"^(you\s+are|you're|they\s+are|user\s+is)\s+\w+ing\b", lowered):
            raise ValueError(
                "content describes an activity in progress, not a durable "
                "preference; say what the user consistently prefers instead of "
                "what they are doing right now"
            )

        # "You added / created / updated / implemented …" — a completed act.
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
        cleaned = [line.strip() for line in value if line and line.strip()]
        if len(cleaned) != len(value):
            raise ValueError("list contains empty entries")
        return cleaned


def json_schema() -> dict[str, object]:
    """Schema to hand the provider, for models that accept one."""
    return DiaryDraft.model_json_schema()
