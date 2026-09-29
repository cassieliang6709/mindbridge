"""Split the captured pairs into train and holdout, and report readiness.

    python -m train.prepare_dataset --report
    python -m train.prepare_dataset --holdout-frac 0.2

The split is BY DATE and deterministic (hash of the date), not random per row:
a day's pairs must never straddle the split, or the model would be evaluated on
a day it partly memorised. Re-running with more data keeps the existing
assignment, so a holdout day stays a holdout day.

中文说明：按日期(而不是按单条记录随机)把已采集数据拆为训练集和留出集。一天内的
多个 session 共享上下文,若跨集合会让模型在测试日的一部分上训练,得到虚假的高分。
日期的稳定 hash 使新数据到来后旧日期仍留在原集合,因此留出集持续代表未见日期。
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
# 中文：数据集同时可能包含本地模型的输出;只能用教师模型结果作训练目标,否则学生会
# 学到自己的错误,而不是教师定义的抽取标准。
TEACHER_MODELS = frozenset({"sonnet"})


def _bucket(date: str) -> float:
    """Return a stable 0..1 bucket for a date, so its split never shifts.

    中文：为日期生成稳定的 0..1 分桶值,确保其训练/留出归属不会随重跑改变。

    Args:
        date: ISO-like date string used as the deterministic split key.
            用作确定性切分键的日期字符串。

    Returns:
        A deterministic value from zero (inclusive) to one (exclusive).
        大于等于 0 且小于 1 的确定性数值。
    """
    digest = hashlib.blake2b(date.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") / 2**64


def load_pairs(path: Path = DATASET) -> list[dict]:
    """Load non-empty JSONL capture rows from ``path``.

    中文：从 ``path`` 加载所有非空 JSONL 采集行。

    Args:
        path: Capture file to read. 要读取的采集文件。

    Returns:
        Parsed capture rows, or an empty list when the file is absent.
        解析后的采集行;文件不存在时返回空列表。
    """
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
    """Create a deterministic date-based train/holdout split or report it.

    中文：创建确定性的按日期训练/留出切分,或只输出其就绪情况。

    Returns:
        Zero on success, or one when there are no captured pairs.
        成功返回 0;没有采集数据时返回 1。
    """
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
    # 中文：只保留教师模型行。若本地模型输出也混入训练目标,学生会学习并放大它自己
    # 的 schema 或偏好判断错误,评测也不再能说明微调是否优于教师基线。
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
    # 中文：依然必须按日期切分,不能按 pair 切分;同一天 session 共享上下文,跨集合
    # 会造成训练数据泄漏到留出评测。
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
