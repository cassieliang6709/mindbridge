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

    # De-duplicate by date, keeping the last extraction for a day.
    by_date: dict[str, dict] = {}
    for pair in pairs:
        by_date[pair["date"]] = pair

    holdout_dates = {
        date for date in by_date if _bucket(date) < args.holdout_frac
    }
    train = [pair for date, pair in sorted(by_date.items()) if date not in holdout_dates]
    holdout = [pair for date, pair in sorted(by_date.items()) if date in holdout_dates]

    first_valid = sum(
        1 for pair in by_date.values() if pair["meta"]["first_attempt_valid"]
    )

    print(f"pairs on disk:      {len(pairs)}")
    print(f"distinct days:      {len(by_date)}")
    print(f"train / holdout:    {len(train)} / {len(holdout)}")
    print(
        f"teacher first-pass: {first_valid}/{len(by_date)} "
        f"({first_valid / len(by_date):.0%}) — the bar the tuned model must clear"
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
