"""Prompt construction, and the token budget that keeps a day affordable.

A busy day is ~900 turns. Sending all of it would cost real money per day and
mostly transmit tool chatter, so the input is compressed deliberately:

- Every user turn, truncated — these carry intent, which is where preferences
  live.
- A sample of assistant turns for context on what was actually done.
- The rule-based facts Path A already computed, verbatim. They are exact, so the
  model should not recompute counts and cannot get them wrong.

The compression is stated in the prompt so the model knows it is seeing a
sample, not the whole day.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from api.memory import count_tokens

from .schemas import DiaryDraft

# An assistant turn that is nothing but tool markers ("[tool:Bash]") carries no
# information the FACTS block does not already state exactly. Those are sampled
# last, so the budget goes to turns that say something.
_TOOL_ONLY_RE = re.compile(r"^(?:\[tool:[^\]]+\]\s*)+$")

SYSTEM_PROMPT = """\
You write one diary entry per day for a developer, from their AI coding \
assistant transcripts.

Rules:
1. Reply with a single JSON object and nothing else. No prose, no code fence.
2. Address the user as "you".
3. Describe only what the transcript shows. Never invent a file, number, or \
outcome. The FACTS block is already exact — do not restate its counts as if you \
derived them, and never contradict it.
4. State observable behaviour, never emotional state. "You were still editing \
at 05:33" is allowed. "You seemed tired" is not, and neither is any claim about \
how the user felt.
5. A preference is something still true next month — a tool choice, a working \
habit, a standing constraint. Today's task is not a preference. Return an empty \
preferences list rather than inventing one.
6. Quote or closely paraphrase the transcript in each preference's evidence \
field.

Return JSON matching this schema:
%s
"""


def system_prompt() -> str:
    return SYSTEM_PROMPT % json.dumps(DiaryDraft.model_json_schema(), indent=2)


@dataclass(slots=True)
class DayInput:
    """The compressed view of a day that gets sent to the model."""

    date: str
    facts: list[str]
    user_turns: list[str]
    assistant_turns: list[str]
    total_turns: int
    sampled_turns: int

    def render(self) -> str:
        lines = [f"DATE: {self.date}", "", "FACTS (exact, computed locally):"]
        lines.extend(f"- {fact}" for fact in self.facts)
        lines.append("")
        lines.append(
            f"TRANSCRIPT SAMPLE ({self.sampled_turns} of {self.total_turns} "
            "turns; long turns truncated):"
        )
        for text in self.user_turns:
            lines.append(f"[you] {text}")
        for text in self.assistant_turns:
            lines.append(f"[assistant] {text}")
        return "\n".join(lines)

    def estimated_tokens(self) -> int:
        return count_tokens(system_prompt()) + count_tokens(self.render())


def build_day_input(
    date: str,
    facts: list[str],
    turns: list[tuple[str, str]],
    *,
    max_input_tokens: int = 12_000,
    per_turn_chars: int = 400,
) -> DayInput:
    """Compress a day's turns to fit a token budget.

    User turns are taken first and never dropped for assistant turns, because a
    preference is almost always something the user said. Assistant turns fill
    whatever budget remains.
    """
    user_texts: list[str] = []
    assistant_texts: list[str] = []

    for role, text in turns:
        trimmed = text.strip().replace("\n", " ")
        if not trimmed:
            continue
        if len(trimmed) > per_turn_chars:
            trimmed = trimmed[:per_turn_chars] + "…"
        if role == "user":
            user_texts.append(trimmed)
        elif not _TOOL_ONLY_RE.match(trimmed):
            assistant_texts.append(trimmed)

    budget = max_input_tokens - count_tokens(system_prompt())
    budget -= count_tokens("\n".join(facts)) + 200  # headers and labels

    # Reserve part of the budget for assistant turns. Without this, a talkative
    # day fills the whole budget with user turns and the model never sees what
    # was actually done — only what was asked for. It then has to guess
    # outcomes, which is the fastest route to an invented detail.
    assistant_reserve = int(budget * 0.35) if assistant_texts else 0
    user_budget = budget - assistant_reserve

    kept_user: list[str] = []
    for text in user_texts:
        cost = count_tokens(text) + 4
        if cost > user_budget:
            break
        kept_user.append(text)
        user_budget -= cost
    # Anything the user turns did not need goes back to the assistant sample.
    budget = user_budget + assistant_reserve

    # Assistant turns are sampled evenly across the day rather than taken from
    # the front, so a long day is represented end to end.
    kept_assistant: list[str] = []
    if assistant_texts and budget > 0:
        stride = max(1, len(assistant_texts) // 40)
        for text in assistant_texts[::stride]:
            cost = count_tokens(text) + 4
            if cost > budget:
                break
            kept_assistant.append(text)
            budget -= cost

    return DayInput(
        date=date,
        facts=facts,
        user_turns=kept_user,
        assistant_turns=kept_assistant,
        total_turns=len(turns),
        sampled_turns=len(kept_user) + len(kept_assistant),
    )


def repair_prompt(raw: str, errors: str) -> str:
    """Follow-up turn after a schema failure.

    The model is shown its own output and the exact validation errors. Naming
    the failing field is what makes the second attempt usually succeed; a bare
    "that was invalid" tends to produce a differently invalid answer.
    """
    return (
        "Your previous reply did not satisfy the schema.\n\n"
        f"Your reply:\n{raw[:2000]}\n\n"
        f"Validation errors:\n{errors}\n\n"
        "Reply again with the corrected JSON object only. Fix exactly these "
        "problems and change nothing else."
    )
