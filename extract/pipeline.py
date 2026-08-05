"""Extract one day: call, validate, repair, record.

Every attempt is recorded, not just the successful one. The compliance rate the
résumé quotes is "fraction valid on the FIRST attempt" — counting a success that
took three repairs would describe the retry loop rather than the model, and the
fine-tuned model in stage two has to be judged on the same basis.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import ValidationError

from .prompts import PROMPT_VERSION, DayInput, repair_prompt, system_prompt
from .providers import ChatProvider, Message
from .schemas import DiaryDraft

logger = logging.getLogger(__name__)

# Models sometimes wrap JSON in a fence despite being told not to. Stripping it
# is a formatting nicety, so it is NOT counted as a schema failure — but it is
# recorded, because a fine-tuned model should stop needing it.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


@dataclass(slots=True)
class Attempt:
    index: int
    raw: str
    ok: bool
    errors: str | None = None
    unfenced: bool = False
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class ExtractionResult:
    date: str
    draft: DiaryDraft | None
    session_id: str | None = None
    attempts: list[Attempt] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    prompt_version: str = PROMPT_VERSION
    prompt_messages: list[Message] = field(default_factory=list)
    extracted_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def ok(self) -> bool:
        return self.draft is not None

    @property
    def first_attempt_valid(self) -> bool:
        return bool(self.attempts) and self.attempts[0].ok

    @property
    def total_input_tokens(self) -> int:
        return sum(attempt.input_tokens for attempt in self.attempts)

    @property
    def total_output_tokens(self) -> int:
        return sum(attempt.output_tokens for attempt in self.attempts)


def _strip_fence(text: str) -> tuple[str, bool]:
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1), True
    return text, False


def _format_errors(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "(root)"
        lines.append(f"- {location}: {item['msg']}")
    return "\n".join(lines)


async def extract_day(
    provider: ChatProvider,
    day: DayInput,
    *,
    max_attempts: int = 3,
    session_id: str | None = None,
) -> ExtractionResult:
    """Ask for a day's diary, validating and repairing until it fits."""
    messages = [
        Message("system", system_prompt()),
        Message("user", day.render()),
    ]
    result = ExtractionResult(
        date=day.date,
        draft=None,
        session_id=session_id,
        provider=provider.name,
        model=provider.model,
        prompt_messages=list(messages),
    )

    for index in range(1, max_attempts + 1):
        completion = await provider.complete(messages)
        raw, unfenced = _strip_fence(completion.text)

        try:
            # model_validate_json reports a parse failure as a ValidationError
            # too ("Invalid JSON: ..."), so this one branch covers both a reply
            # that is not JSON and one that is JSON of the wrong shape.
            draft = DiaryDraft.model_validate_json(raw)
        except ValidationError as error:
            errors = _format_errors(error)
        else:
            result.attempts.append(
                Attempt(
                    index=index,
                    raw=raw,
                    ok=True,
                    unfenced=unfenced,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                )
            )
            result.draft = draft
            return result

        logger.warning(
            "%s attempt %d/%d failed schema for %s:\n%s",
            provider.model,
            index,
            max_attempts,
            day.date,
            errors,
        )
        result.attempts.append(
            Attempt(
                index=index,
                raw=raw,
                ok=False,
                errors=errors,
                unfenced=unfenced,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
            )
        )
        messages = [
            *messages,
            Message("assistant", completion.text),
            Message("user", repair_prompt(raw, errors)),
        ]

    return result


def training_pair(result: ExtractionResult) -> dict[str, object] | None:
    """One (prompt, completion) pair for stage two's fine-tune.

    Only successful extractions become training data, and the stored completion
    is the VALIDATED object re-serialised — not the model's raw text. Training on
    raw output would teach the next model to reproduce the same fence-and-repair
    habits we are trying to remove.
    """
    if result.draft is None:
        return None
    return {
        "date": result.date,
        "session_id": result.session_id,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in result.prompt_messages
        ],
        "completion": json.loads(result.draft.model_dump_json()),
        "meta": {
            "provider": result.provider,
            "model": result.model,
            "attempts": len(result.attempts),
            "first_attempt_valid": result.first_attempt_valid,
            "extracted_at": result.extracted_at.isoformat(),
        },
    }
