"""Offline tests for the extraction contract and the repair loop.

    python -m extract.test_pipeline

Uses ScriptedProvider, so the failure paths that matter — invalid JSON, extra
keys, an emotional claim, a transient "preference" — are exercised on demand
rather than hoped for against a live model. No key, no network, no spend.
"""

from __future__ import annotations

import asyncio
import json

from .pipeline import extract_day, training_pair
from .prompts import build_day_input
from .providers import ScriptedProvider
from .schemas import DiaryDraft

VALID = {
    "narrative": (
        "You spent the day on the retrieval path, standing up the endpoint and "
        "clearing the errors it threw. You were still editing after 05:00."
    ),
    "highlights": ["Stood up /retrieve", "Cleared three 500s"],
    "preferences": [
        {
            "content": "Prefer uv over pip for Python projects",
            "category": "tool_preference",
            "confidence": 0.9,
            "evidence": "asked to switch package management from pip to uv",
        }
    ],
    "open_threads": ["Topological sort still unresolved"],
}


def _day():
    return build_day_input(
        "2026-08-04",
        ["Tool calls: Bash x12"],
        [("user", "switch from pip to uv"), ("assistant", "done")],
    )


def check(name: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    return condition


async def main() -> int:
    print("schema contract")
    ok = True

    # Extra keys must fail: a model inventing a field has not followed the schema.
    try:
        DiaryDraft.model_validate({**VALID, "mood": "great"})
        ok &= check("extra key rejected", False, "it was accepted")
    except Exception:
        ok &= check("extra key rejected", True)

    # Emotional inference must fail even though the prompt forbids it.
    try:
        DiaryDraft.model_validate(
            {**VALID, "narrative": "You seemed frustrated by the failing tests all day long."}
        )
        ok &= check("emotional claim rejected", False, "it was accepted")
    except Exception:
        ok &= check("emotional claim rejected", True)

    # A today-only task dressed as a preference must fail.
    try:
        DiaryDraft.model_validate(
            {
                **VALID,
                "preferences": [
                    {
                        "content": "Fix the 500 in /retrieve",
                        "category": "other",
                        "confidence": 0.9,
                        "evidence": "the endpoint returned 500",
                    }
                ],
            }
        )
        ok &= check("transient task rejected as preference", False, "it was accepted")
    except Exception:
        ok &= check("transient task rejected as preference", True)

    ok &= check("valid draft accepted", DiaryDraft.model_validate(VALID) is not None)

    print("\nrepair loop")

    # 1. Valid on the first attempt.
    provider = ScriptedProvider([json.dumps(VALID)])
    result = await extract_day(provider, _day())
    ok &= check("first-attempt success", result.ok and result.first_attempt_valid)
    ok &= check("one call made", len(provider.calls) == 1, f"{len(provider.calls)}")

    # 2. Not JSON, then valid. Success, but NOT first-attempt valid.
    provider = ScriptedProvider(["I'm afraid I can't do that.", json.dumps(VALID)])
    result = await extract_day(provider, _day())
    ok &= check("recovers from non-JSON", result.ok)
    ok &= check(
        "recovery is not counted as first-attempt valid",
        not result.first_attempt_valid,
    )
    ok &= check("two attempts recorded", len(result.attempts) == 2)
    repair_text = provider.calls[1][-1].content
    ok &= check(
        "repair prompt names the failure",
        "Invalid JSON" in repair_text,
        repair_text[:60],
    )

    # 3. Schema violation, then valid — repair prompt must name the field.
    bad = {**VALID, "narrative": "too short"}
    provider = ScriptedProvider([json.dumps(bad), json.dumps(VALID)])
    result = await extract_day(provider, _day())
    ok &= check("recovers from schema violation", result.ok)
    ok &= check(
        "repair prompt names the field",
        "narrative" in provider.calls[1][-1].content,
    )

    # 4. A fenced but otherwise valid reply counts as first-attempt valid,
    #    because a code fence is formatting, not a schema failure.
    provider = ScriptedProvider(["```json\n" + json.dumps(VALID) + "\n```"])
    result = await extract_day(provider, _day())
    ok &= check("fenced JSON accepted", result.ok and result.first_attempt_valid)
    ok &= check("fence recorded for later analysis", result.attempts[0].unfenced)

    # 5. Never valid: gives up, reports failure, produces no training pair.
    provider = ScriptedProvider(["nope", "still nope", "nope again"])
    result = await extract_day(provider, _day(), max_attempts=3)
    ok &= check("gives up after max attempts", not result.ok)
    ok &= check("exactly three attempts", len(result.attempts) == 3)
    ok &= check("no training pair from a failure", training_pair(result) is None)

    print("\ntraining pair")
    provider = ScriptedProvider(["```json\n" + json.dumps(VALID) + "\n```"])
    result = await extract_day(provider, _day())
    pair = training_pair(result)
    assert pair is not None
    ok &= check("pair has prompt messages", len(pair["messages"]) == 2)  # type: ignore[arg-type]
    ok &= check(
        "completion is the validated object, not the fenced raw text",
        pair["completion"] == json.loads(DiaryDraft.model_validate(VALID).model_dump_json()),
    )

    print("\nprompt budget")
    big = [("user", "x" * 5000) for _ in range(400)]
    day = build_day_input("2026-08-04", ["fact"], big, max_input_tokens=4000)
    ok &= check(
        "input respects the token budget",
        day.estimated_tokens() <= 4200,
        f"{day.estimated_tokens()} tokens",
    )
    ok &= check("sample is smaller than the day", day.sampled_turns < day.total_turns)

    print()
    print("ALL PASS" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
