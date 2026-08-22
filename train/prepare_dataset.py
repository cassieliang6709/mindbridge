"""Split the captured pairs into train and holdout, and report readiness.

    python -m train.prepare_dataset --report
    python -m train.prepare_dataset --holdout-frac 0.2

The split is BY DATE and deterministic (hash of the date), not random per row:
a day's pairs must never straddle the split, or the model would be evaluated on
a day it partly memorised. Re-running with more data keeps the existing
assignment, so a holdout day stays a holdout day.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

DATASET = Path("train/dataset/extraction.jsonl")
TRAIN_OUT = Path("train/dataset/train.jsonl")
HOLDOUT_OUT = Path("train/dataset/holdout.jsonl")

# Below this, a fine-tune will overfit and the holdout figure will be noise.
# The résumé cites ~1k pairs; one pair per day means this needs either months of
# history or several extractions per day (per-session cards).
MIN_PAIRS_FOR_TRAINING = 200


# Which models count as the teacher. A fine-tune learns the mapping in this
# file, so anything here becomes the standard the student is trained toward.
TEACHER_MODELS = frozenset({"sonnet"})


def _bucket(date: str) -> float:
    """Stable 0..1 position for a date, so the split never shifts."""
    digest = hashlib.blake2b(date.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


def load_pairs(path: Path = DATASET) -> list[dict]:
    if not path.exists():
        return []
    pairs = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                pairs.append(json.loads(line))
    return pairs


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m train.prepare_dataset")
    parser.add_argument("--holdout-frac", type=float, default=0.2)
    parser.add_argument(
        "--report", action="store_true", help="Print readiness and write nothing."
    )
    args = parser.parse_args()

    pairs = load_pairs()
    if not pairs:
        print(
            f"no pairs yet at {DATASET}.\n"
            "Run stage one first:\n"
            "    docker compose run --rm extract --missing --limit 5 "
            "--send-to-provider"
        )
        return 1

    # Teacher rows only. Every extraction lands in the same file regardless of
    # which model wrote it, so a run that measured a local model leaves its own
    # output sitting next to the teacher's. Training on that teaches the student
    # its own mistakes — the base 7B rows in this file include three of the
    # day's todos written into T3 as standing preferences.
    teacher = [
        pair for pair in pairs
        if (pair.get("meta") or {}).get("model") in TEACHER_MODELS
    ]
    if len(teacher) != len(pairs):
        skipped = len(pairs) - len(teacher)
        print(f"skipped {skipped} row(s) not written by the teacher")
    pairs = teacher

    # De-duplicate by (date, session), keeping the last extraction for each.
    # Keying on date alone silently collapsed every session card onto its day —
    # 86 captured pairs became 53, throwing away a third of the training data.
    # Legacy rows predate session_id, so they fall back to a hash of the prompt.
    by_key: dict[tuple[str, str], dict] = {}
    for pair in pairs:
        session = pair.get("session_id")
        if session is None:
            prompt = pair["messages"][-1]["content"] if pair.get("messages") else ""
            session = hashlib.blake2b(prompt.encode(), digest_size=8).hexdigest()
        by_key[(pair["date"], session)] = pair

    # Still split BY DATE, not by pair: sessions from one day share context, so
    # letting them straddle the split would leak train data into the holdout.
    holdout_dates = {
        date for date, _ in by_key if _bucket(date) < args.holdout_frac
    }
    train = [p for (date, _), p in sorted(by_key.items()) if date not in holdout_dates]
    holdout = [p for (date, _), p in sorted(by_key.items()) if date in holdout_dates]

    first_valid = sum(
        1 for pair in by_key.values() if pair["meta"]["first_attempt_valid"]
    )
    days = {date for date, _ in by_key}

    print(f"pairs on disk:      {len(pairs)}")
    print(f"unique pairs:       {len(by_key)}  across {len(days)} day(s)")
    print(f"train / holdout:    {len(train)} / {len(holdout)}")
    print(
        f"teacher first-pass: {first_valid}/{len(by_key)} "
        f"({first_valid / len(by_key):.0%}) — the bar the tuned model must clear"
    )
    if len(train) < MIN_PAIRS_FOR_TRAINING:
        print(
            f"\nNOT READY: {len(train)} training pairs is well under "
            f"{MIN_PAIRS_FOR_TRAINING}. A fine-tune on this would overfit and the "
            "holdout number would be noise. Keep running stage one daily, or "
            "extract per-session cards to raise the pairs-per-day."
        )

    if args.report:
        return 0

    for path, rows in ((TRAIN_OUT, train), (HOLDOUT_OUT, holdout)):
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"wrote {path} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
