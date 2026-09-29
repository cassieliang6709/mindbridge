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

中文说明：本模块构造模型提示词，并把繁忙一天的对话压缩到明确的 token
预算内。用户发言优先保留，助手发言按全天均匀采样，Path A 计算出的事实则
原样传入；提示词会明确说明模型看到的是样本，而不是完整记录。
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
# 中文：纯工具标记没有提供 FACTS 之外的新信息，因此最后采样，把预算留给
# 真正包含语义的发言。
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
7. Every preference needs content, category, confidence and evidence. \
confidence is a number between 0 and 1. It says how settled the preference is, \
not how important: a decision the user stated and acted on is 0.8 or higher; \
something they were still weighing out loud is 0.5 or lower. Omitting \
confidence is the single most common way this reply gets rejected.
8. A preference is about the user, not about the system. "You check retrieval \
quality end to end before shipping" is about the user. "The dedup threshold is \
0.80" is a configuration value — it belongs in code and docs, and must not be \
returned as a preference.
9. Set "project" when a preference only holds inside the project named in the \
PROJECT line — a convention for one app, a rule for one repo. Leave it out when \
the preference follows the user everywhere, which is the more useful kind. If \
no PROJECT line is given, leave it out.

Return JSON matching this schema:
%s
"""

# Bumped whenever the prompt changes in a way that could move compliance.
# Recorded per attempt so a first-pass rate is never a silent blend of two
# different prompts — v1 never mentioned `confidence`, which the schema requires,
# and that alone accounted for 17 of 19 failures across the first 46 days.
# 中文：任何可能移动合规率的提示词改动都必须提升版本号；每次尝试都会记录
# 该版本，避免把不同提示词的结果混成一个无法解释的指标。
PROMPT_VERSION = "v4-scope-confidence-subject"


def system_prompt() -> str:
    """Render the system prompt with the current JSON schema.

    中文：把当前 DiaryDraft JSON schema 内嵌到系统提示词中。

    Returns:
        The exact system prompt sent to the model.
    """
    return SYSTEM_PROMPT % json.dumps(DiaryDraft.model_json_schema(), indent=2)


@dataclass(slots=True)
class DayInput:
    """The compressed view of a day that gets sent to the model.

    中文：发送给模型的单日压缩视图；project 仅在规则只适用于当前项目时使用。
    """

    date: str
    facts: list[str]
    user_turns: list[str]
    assistant_turns: list[str]
    total_turns: int
    sampled_turns: int
    project: str | None = None

    def render(self) -> str:
        """Render the date, facts, scope, and sampled transcript.

        中文：渲染日期、事实、项目作用域和采样后的对话文本。

        Returns:
            The user-message body sent to the model.
        """
        lines = [f"DATE: {self.date}"]
        if self.project:
            # The model decides whether a preference is project-scoped; it
            # cannot know what the project is called, because the name comes
            # from the working directory and never appears in the turns.
            # 中文：项目名来自工作目录，通常不会出现在对话正文中；显式提供后，
            # 模型才能判断一条偏好是项目内规则还是跨项目习惯。
            lines.append(f"PROJECT: {self.project}")
        lines += ["", "FACTS (exact, computed locally):"]
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
        """Estimate tokens for the system prompt and rendered day together.

        中文：估算系统提示词与单日正文合计的 token 数。

        Returns:
            The estimated request input size in tokens.
        """
        return count_tokens(system_prompt()) + count_tokens(self.render())


def build_day_input(
    date: str,
    facts: list[str],
    turns: list[tuple[str, str]],
    *,
    max_input_tokens: int = 12_000,
    per_turn_chars: int = 400,
    project: str | None = None,
) -> DayInput:
    """Compress a day's turns to fit a token budget.

    User turns are taken first and never dropped for assistant turns, because a
    preference is almost always something the user said. Assistant turns fill
    whatever budget remains.

    中文：用户发言优先保留，因为偏好通常由用户表达；助手发言只使用剩余预算，
    并在全天范围内采样。

    Args:
        date: Local date covered by the input.
        facts: Deterministic facts computed by Path A.
        turns: Full sequence of ``(role, text)`` pairs.
        max_input_tokens: Total input-token budget, including the system prompt.
        per_turn_chars: Maximum characters retained from one turn.
        project: Dominant project name, if the card has one.

    Returns:
        A compressed DayInput that stays within the requested budget estimate.
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
    # 中文：预留一部分预算给助手发言，避免模型只看到“要求做什么”，却看不到
    # “实际做了什么”，从而被迫猜测结果。
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
    # 中文：用户发言未用完的预算会归还给助手样本。
    budget = user_budget + assistant_reserve

    # Assistant turns are sampled evenly across the day rather than taken from
    # the front, so a long day is represented end to end.
    # 中文：助手发言在全天均匀采样，避免长对话只保留开头。
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
        project=project,
    )


def repair_prompt(raw: str, errors: str) -> str:
    """Follow-up turn after a schema failure.

    The model is shown its own output and the exact validation errors. Naming
    the failing field is what makes the second attempt usually succeed; a bare
    "that was invalid" tends to produce a differently invalid answer.

    中文：把模型上一次输出和精确校验错误一起返回；明确失败字段通常比泛泛地说
    “格式不对”更容易让下一次尝试修复成功。

    Args:
        raw: The model's previous reply after fence stripping.
        errors: Human-readable validation errors.

    Returns:
        The next user message for the repair attempt.
    """
    return (
        "Your previous reply did not satisfy the schema.\n\n"
        f"Your reply:\n{raw[:2000]}\n\n"
        f"Validation errors:\n{errors}\n\n"
        "Reply again with the corrected JSON object only. Fix exactly these "
        "problems and change nothing else."
    )
