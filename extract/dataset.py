"""Append-only training set, plus the compliance stats over it.

The file this writes is the deliverable of stage one: the (prompt, JSON) pairs
that stage two fine-tunes Qwen2.5-7B on, and the per-attempt record that the
91.5%-style compliance figure must be computed from.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .pipeline import ExtractionResult, training_pair

DEFAULT_DATASET = Path("train/dataset/extraction.jsonl")
DEFAULT_ATTEMPTS_LOG = Path("train/dataset/attempts.jsonl")


@dataclass(slots=True)
class DatasetWriter:
    dataset_path: Path = DEFAULT_DATASET
    attempts_path: Path = DEFAULT_ATTEMPTS_LOG

    def __post_init__(self) -> None:
        self.dataset_path.parent.mkdir(parents=True, exist_ok=True)
        self.attempts_path.parent.mkdir(parents=True, exist_ok=True)

    def existing_dates(self) -> set[str]:
        """Dates already captured, so a re-run does not duplicate a pair."""
        if not self.dataset_path.exists():
            return set()
        dates: set[str] = set()
        with self.dataset_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    dates.add(json.loads(line)["date"])
                except (json.JSONDecodeError, KeyError):
                    continue
        return dates

    def append(self, result: ExtractionResult) -> bool:
        """Record the attempts always; record a training pair only on success."""
        with self.attempts_path.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "date": result.date,
                        "session_id": result.session_id,
                        "provider": result.provider,
                        "model": result.model,
                        "prompt_version": result.prompt_version,
                        "ok": result.ok,
                        "first_attempt_valid": result.first_attempt_valid,
                        "attempts": [
                            {
                                "index": attempt.index,
                                "ok": attempt.ok,
                                "errors": attempt.errors,
                                "unfenced": attempt.unfenced,
                                "input_tokens": attempt.input_tokens,
                                "output_tokens": attempt.output_tokens,
                            }
                            for attempt in result.attempts
                        ],
                        "extracted_at": result.extracted_at.isoformat(),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        pair = training_pair(result)
        if pair is None:
            return False
        with self.dataset_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(pair, ensure_ascii=False) + "\n")
        return True


def compliance_stats(attempts_path: Path = DEFAULT_ATTEMPTS_LOG) -> dict[str, object]:
    """Compliance over every recorded extraction.

    `first_attempt_rate` is the honest headline: how often the model produced a
    schema-valid object without being corrected. `eventual_rate` includes the
    repair loop and will always look better, so both are reported and named.
    """
    if not attempts_path.exists():
        return {"days": 0}

    days = first_ok = eventual_ok = fenced = 0
    attempts_total = 0
    input_tokens = output_tokens = 0
    error_kinds: dict[str, int] = {}
    # Compliance is also broken out per prompt version: a prompt change can move
    # the rate, so one pooled number across versions would describe neither.
    by_version: dict[str, dict[str, int]] = {}

    with attempts_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            record = json.loads(line)
            days += 1
            first_ok += bool(record.get("first_attempt_valid"))
            eventual_ok += bool(record.get("ok"))
            version = str(record.get("prompt_version") or "v1-unversioned")
            bucket = by_version.setdefault(version, {"days": 0, "first_ok": 0})
            bucket["days"] += 1
            bucket["first_ok"] += bool(record.get("first_attempt_valid"))
            for attempt in record.get("attempts", []):
                attempts_total += 1
                fenced += bool(attempt.get("unfenced"))
                input_tokens += attempt.get("input_tokens") or 0
                output_tokens += attempt.get("output_tokens") or 0
                if attempt.get("errors"):
                    for line_ in str(attempt["errors"]).splitlines():
                        field = line_.split(":")[0].removeprefix("- ").strip()
                        if field:
                            error_kinds[field] = error_kinds.get(field, 0) + 1

    return {
        "days": days,
        "first_attempt_valid": first_ok,
        "first_attempt_rate": round(first_ok / days, 4) if days else None,
        "eventually_valid": eventual_ok,
        "eventual_rate": round(eventual_ok / days, 4) if days else None,
        "attempts_total": attempts_total,
        "attempts_per_day": round(attempts_total / days, 3) if days else None,
        "replies_needing_fence_strip": fenced,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "by_prompt_version": {
            version: {
                **counts,
                "first_attempt_rate": round(counts["first_ok"] / counts["days"], 4),
            }
            for version, counts in sorted(by_version.items())
        },
        "most_common_failures": dict(
            sorted(error_kinds.items(), key=lambda item: -item[1])[:8]
        ),
    }
