"""Append-only training set, plus the compliance stats over it.

The file this writes is the deliverable of stage one: the (prompt, JSON) pairs
that stage two fine-tunes Qwen2.5-3B on, and the per-attempt record that every
schema-compliance figure must be computed from.

中文说明：本文件是训练集的追加写入器，以及基于该训练集计算的合规率统计。此处
写出的文件是第一阶段的最终交付物——用于第二阶段微调 Qwen2.5-3B 的 (prompt,
JSON) 样本对，以及每一次尝试的完整记录；任何合规率数字都必须从这份逐次尝试的
记录中计算得出，不能凭空填写。
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
    """Appends every extraction attempt to disk, and successes to the training set.

    中文：把每一次抽取尝试追加写入 attempts 日志，并把成功的尝试额外写入训练集。

    Attributes:
        dataset_path: Where successful (prompt, JSON) training pairs are appended.
            成功抽取的 (prompt, JSON) 训练样本追加写入的位置。
        attempts_path: Where every attempt, successful or not, is appended.
            每一次尝试（无论成功与否）追加写入的位置。
    """

    dataset_path: Path = DEFAULT_DATASET
    attempts_path: Path = DEFAULT_ATTEMPTS_LOG

    def __post_init__(self) -> None:
        """Ensure the parent directories for both output files exist.

        中文：确保两个输出文件的父目录都已存在。
        """
        self.dataset_path.parent.mkdir(parents=True, exist_ok=True)
        self.attempts_path.parent.mkdir(parents=True, exist_ok=True)

    def existing_dates(self) -> set[str]:
        """Dates already captured, so a re-run does not duplicate a pair.

        中文：返回已经写入训练集的日期集合，避免重复运行时产生重复样本。

        Returns:
            The set of "date" values already present in the dataset file.
            数据集文件中已经出现过的所有 "date" 字段值组成的集合。
        """
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
        """Record the attempts always; record a training pair only on success.

        中文：无论成功与否都记录本次尝试；只有成功时才额外写入一条训练样本。

        Args:
            result: The full result of one day's extraction, including every
                attempt made. 一天抽取的完整结果，包含所有尝试记录。

        Returns:
            True if a training pair was written (the extraction succeeded),
            False otherwise. 如果写入了训练样本（即抽取成功）返回 True，否则
            返回 False。
        """
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

    中文：基于每一次已记录的抽取计算合规率。`first_attempt_rate`（首次尝试合规
    率）才是诚实的核心指标——它衡量模型不经任何修正就产出符合 schema 的对象的
    频率。`eventual_rate`（最终合规率）把修复循环也算进去了，数字必然更好看，
    所以两者都会输出并明确命名，避免被混淆或误引用。

    Args:
        attempts_path: Path to the JSONL log of every extraction attempt.
            记录每一次抽取尝试的 JSONL 日志文件路径。

    Returns:
        A dict of aggregate compliance figures, including a per-prompt-version
        breakdown and the most common failure kinds.
        一份聚合后的合规率统计字典，包含按 prompt 版本拆分的数据，以及最常见
        的失败类型。
    """
    if not attempts_path.exists():
        return {"days": 0}

    days = first_ok = eventual_ok = fenced = 0
    attempts_total = 0
    input_tokens = output_tokens = 0
    error_kinds: dict[str, int] = {}
    # Compliance is also broken out per prompt version: a prompt change can move
    # the rate, so one pooled number across versions would describe neither.
    # 中文：合规率还会按 prompt 版本单独拆分——prompt 一旦改动就可能影响合规率，
    # 如果把不同版本的数据混在一起算出一个数字，那这个数字对任何一个版本都不
    # 准确。
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
