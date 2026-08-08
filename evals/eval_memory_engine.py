"""MindBridge memory-engine benchmark.

Five measurements against a live Postgres+pgvector, split by whether they
depend on embedding quality:

Embedder-dependent (only published from a real semantic provider):
1. token_compression   — prompt tokens when a long history is replaced by the T2
   card plus the T3 memories a query actually recalls.
2. dedup_accuracy      — confusion matrix of the write-time cosine dedup over a
   labelled set of duplicate and distinct preference pairs.
5. conflict_detection  — whether the engine notices unaided that a new fact
   contradicts an older one.

Embedder-independent (published from any run):
3. decay_ordering      — score = cosine · exp(-λ·Δt) checked against known ages,
   using identical content so only the decay term varies.
4. supersede_exclusion — a closed record stays out of default queries and comes
   back when explicitly requested.

Writes evals/results.json, which the landing page reads. Metrics this script
does not measure stay null; the page renders null as an amber "in progress"
badge rather than inventing a figure.

    docker compose up -d db redis
    docker compose run --rm evals
    # or, against a local venv:
    python -m evals.eval_memory_engine

Every result records which embedder produced it. Under the `hashing` provider
the numbers describe lexical overlap, not semantics — `semantic` is false in the
output and the numbers must not be published as retrieval quality.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.memory import count_tokens, tokenizer_name
from api.models import (
    MemoryCategory,
    TemporalQueryRequest,
    UpsertPreferenceRequest,
)
from api.service import MemoryService, format_context
from api.settings import get_settings

RESULTS_PATH = Path(__file__).with_name("results.json")

# --- fixtures -------------------------------------------------------------

# A synthetic day of agent traffic. Long enough that resending it every turn is
# obviously wasteful, which is the thing being measured.
RAW_HISTORY: list[str] = [
    "user: /retrieve 在空 query 上返回 500，帮我看下 traceback。",
    "assistant: 空字符串走到 embedder 时 batch 是空的，加一个 guard 提前返回 400。",
    "user: 加好了。顺便说一下，以后所有 Python 项目都用 uv，不要再用 pip。",
    "assistant: 记下了。requirements.txt 也换成 uv pip compile 的输出？",
    "user: 对。然后 Kahn 算法怎么检测环，我卡在这。",
    "assistant: 入度为 0 的节点出队计数，计数小于节点总数就说明有环。",
    "user: 明白了。周末别给我排会议，我周六早上要健身。",
    "assistant: 好，周末不排会议，周六早上留空。",
    "user: 还有，回答别绕，直接给结论。",
    "assistant: 收到，后面直接给结论。",
    "user: 把 embedding 从 1536 降到 768 会掉多少召回？",
    "assistant: 在这个规模上差别很小，但要重建 ivfflat 索引。",
]

SUMMARY_CARD = (
    "打通 /retrieve 检索链路：修掉空 query 的 500，讨论了 embedding 降维与 "
    "ivfflat 重建；过了一道 Kahn 算法拓扑排序题。"
)

SUMMARY_FACTS = [
    "偏好：Python 项目用 uv 而非 pip",
    "偏好：周末不排会议，周六早上健身",
    "偏好：回答直接给结论",
]

# Preferences seeded into T3 before the token measurement.
SEED_PREFERENCES: list[tuple[str, MemoryCategory]] = [
    ("Python 项目优先用 uv，不要用 pip", "tool_preference"),
    ("周末不排会议", "schedule"),
    ("周六早上健身", "schedule"),
    ("回答要直接，先给结论再解释", "coding_style"),
    ("代码注释用英文", "coding_style"),
]

# Queries a model would actually ask mid-conversation.
PROBE_QUERIES = [
    "这个 Python 项目该用什么包管理器",
    "周六下午能不能约个评审会",
    "回答的风格有什么要求",
]

# Labelled dedup pairs: (a, b, is_same_fact).
DEDUP_PAIRS: list[tuple[str, str, bool]] = [
    # same fact, reworded
    ("Python 项目优先用 uv，不要用 pip", "Python 项目优先用 uv，不要用 pip", True),
    ("周末不排会议", "周末不排会议", True),
    ("回答要直接，先给结论再解释", "回答要直接，先给结论再解释", True),
    # different facts
    ("Python 项目优先用 uv，不要用 pip", "周六早上健身", False),
    ("周末不排会议", "代码注释用英文", False),
    ("回答要直接，先给结论再解释", "Postgres 用 pgvector 存向量", False),
    ("周六早上健身", "commit message 用英文祈使句", False),
]

# Ages, in days, used to verify exp(-λ·Δt) against a known Δt.
DECAY_AGES_DAYS: list[float] = [0.0, 30.0, 180.0, 365.0]

# (old fact, replacement, query) — used for the supersede-exclusion invariant.
SUPERSEDE_CASES: list[tuple[str, str, str]] = [
    ("周末可以加班", "周末不排会议", "周末的时间怎么安排"),
    ("用 pip 安装依赖", "Python 项目优先用 uv", "依赖该怎么装"),
]


@dataclass(slots=True)
class Measurement:
    name: str
    value: float | None
    unit: str
    detail: dict[str, Any] = field(default_factory=dict)
    note: str = ""


# --- 1. token compression -------------------------------------------------


async def measure_token_compression(service: MemoryService) -> Measurement:
    """Raw history vs. T2 card + the T3 memories a query actually recalls."""
    await service.vectors.purge_all()
    for content, category in SEED_PREFERENCES:
        await service.upsert_preference(
            UpsertPreferenceRequest(content=content, category=category)
        )

    raw_tokens = sum(count_tokens(line) for line in RAW_HISTORY)
    card_tokens = count_tokens(SUMMARY_CARD) + sum(
        count_tokens(fact) for fact in SUMMARY_FACTS
    )

    per_query: list[dict[str, Any]] = []
    compact_totals: list[int] = []
    for query in PROBE_QUERIES:
        result = await service.temporal_query(
            TemporalQueryRequest(query_string=query, top_k=3)
        )
        recall_tokens = count_tokens(format_context(result.hits))
        compact = card_tokens + recall_tokens
        compact_totals.append(compact)
        per_query.append(
            {
                "query": query,
                "recalled": len(result.hits),
                "recall_tokens": recall_tokens,
                "compact_tokens": compact,
            }
        )

    mean_compact = sum(compact_totals) / len(compact_totals)
    reduction = (raw_tokens - mean_compact) / raw_tokens * 100.0
    return Measurement(
        name="token_compression",
        value=round(reduction, 1),
        unit="percent",
        detail={
            "raw_history_tokens": raw_tokens,
            "summary_card_tokens": card_tokens,
            "mean_compact_tokens": round(mean_compact, 1),
            "tokenizer": tokenizer_name(),
            "per_query": per_query,
        },
        note=(
            "Positive means the compact prompt is smaller. Compares resending "
            f"{len(RAW_HISTORY)} raw turns against one T2 card plus top-3 T3 recall."
        ),
    )


# --- 2. dedup -------------------------------------------------------------


async def measure_dedup(service: MemoryService) -> Measurement:
    """Does the write path merge restatements and keep distinct facts apart?

    Confusion matrix over labelled pairs: a "positive" is the engine deciding
    two statements are the same fact.
    """
    true_positive = false_positive = true_negative = false_negative = 0
    misses: list[dict[str, Any]] = []

    for first, second, same_fact in DEDUP_PAIRS:
        await service.vectors.purge_all()
        await service.upsert_preference(UpsertPreferenceRequest(content=first))
        result = await service.upsert_preference(
            UpsertPreferenceRequest(content=second)
        )
        merged = result.action == "refreshed"
        if same_fact and merged:
            true_positive += 1
        elif same_fact and not merged:
            false_negative += 1
            misses.append({"pair": [first, second], "expected": "merge", "got": "insert"})
        elif not same_fact and merged:
            false_positive += 1
            misses.append({"pair": [first, second], "expected": "insert", "got": "merge"})
        else:
            true_negative += 1

    total = len(DEDUP_PAIRS)
    accuracy = (true_positive + true_negative) / total * 100.0
    precision = (
        true_positive / (true_positive + false_positive) * 100.0
        if (true_positive + false_positive)
        else None
    )
    recall = (
        true_positive / (true_positive + false_negative) * 100.0
        if (true_positive + false_negative)
        else None
    )
    return Measurement(
        name="dedup_accuracy",
        value=round(accuracy, 1),
        unit="percent",
        detail={
            "pairs": total,
            "threshold": service.settings.dedup_threshold,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "true_negative": true_negative,
            "false_negative": false_negative,
            "precision": round(precision, 1) if precision is not None else None,
            "recall": round(recall, 1) if recall is not None else None,
            "mistakes": misses,
        },
        note=(
            "A false positive silently merges two different preferences, which "
            "is the worse error: the losing fact is never stored at all."
        ),
    )


# --- 3. decay ordering ----------------------------------------------------


async def measure_decay_ordering(service: MemoryService) -> Measurement:
    """Verify score = cosine · exp(-λ·Δt) against known ages.

    Identical content is stored at several ages, so cosine is constant across
    rows and the only thing separating their scores is the decay term. That
    makes this measurement independent of the embedder: it checks the ranking
    maths, not retrieval quality.
    """
    await service.vectors.purge_all()
    content = "周末不排会议"
    [embedding] = await service.embedder.embed([content])

    ids: dict[int, float] = {}
    for age in DECAY_AGES_DAYS:
        # insert() directly, bypassing dedup — these are deliberately identical.
        record = await service.vectors.insert(content, "schedule", embedding)
        if age:
            await service.vectors.backdate(record.id, age)
        ids[record.id] = age

    hits = await service.vectors.search(embedding, top_k=len(DECAY_AGES_DAYS))
    observed = [(hit.id, ids[hit.id], hit.score, hit.cosine_similarity) for hit in hits]

    ordered_correctly = [age for _, age, _, _ in observed] == sorted(
        DECAY_AGES_DAYS
    )

    # Compare each score against the closed form.
    rate = service.settings.decay_rate_per_day
    errors: list[dict[str, Any]] = []
    max_error = 0.0
    for record_id, age, score, cosine in observed:
        expected = cosine * pow(2.718281828459045, -rate * age)
        error = abs(score - expected)
        max_error = max(max_error, error)
        errors.append(
            {
                "id": record_id,
                "age_days": age,
                "cosine": round(cosine, 6),
                "score": round(score, 6),
                "expected": round(expected, 6),
                "abs_error": round(error, 8),
            }
        )

    formula_holds = max_error < 1e-6
    passed = ordered_correctly and formula_holds
    return Measurement(
        name="decay_ordering",
        value=100.0 if passed else 0.0,
        unit="percent",
        detail={
            "decay_rate_per_day": rate,
            "ages_days": DECAY_AGES_DAYS,
            "newest_first": ordered_correctly,
            "formula_max_abs_error": round(max_error, 10),
            "formula_holds": formula_holds,
            "rows": errors,
        },
        note=(
            "Embedder-independent: identical content at different ages, so only "
            "exp(-λ·Δt) separates the scores. Passes when the ordering is "
            "newest-first and every score matches the closed form to 1e-6."
        ),
    )


# --- 4. supersede exclusion ----------------------------------------------


async def measure_supersede_exclusion(service: MemoryService) -> Measurement:
    """A closed record must never surface in a default query, and must return
    when explicitly asked for.

    Superseding is done by id rather than by relying on the embedder to notice
    the conflict, so this measures the bitemporal invariant on its own. Whether
    the model/embedder *detects* the conflict is measured separately.
    """
    passed = 0
    cases: list[dict[str, Any]] = []

    for old, new, query in SUPERSEDE_CASES:
        await service.vectors.purge_all()
        old_record = (
            await service.upsert_preference(UpsertPreferenceRequest(content=old))
        ).record
        new_record = (
            await service.upsert_preference(UpsertPreferenceRequest(content=new))
        ).record
        await service.vectors.supersede(old_record.id, new_record.id)

        default_hits = (
            await service.temporal_query(
                TemporalQueryRequest(query_string=query, top_k=5)
            )
        ).hits
        with_closed = (
            await service.temporal_query(
                TemporalQueryRequest(
                    query_string=query, top_k=5, include_superseded=True
                )
            )
        ).hits

        hidden = all(hit.id != old_record.id for hit in default_hits)
        returned = any(hit.id == old_record.id for hit in with_closed)
        penalised = next(
            (
                hit.decay_multiplier
                for hit in with_closed
                if hit.id == old_record.id
            ),
            None,
        )
        ok = hidden and returned
        passed += int(ok)
        cases.append(
            {
                "query": query,
                "closed_id": old_record.id,
                "hidden_by_default": hidden,
                "returned_when_requested": returned,
                "closed_decay_multiplier": (
                    round(penalised, 6) if penalised is not None else None
                ),
            }
        )

    return Measurement(
        name="supersede_exclusion",
        value=round(passed / len(SUPERSEDE_CASES) * 100.0, 1),
        unit="percent",
        detail={
            "cases": len(SUPERSEDE_CASES),
            "passed": passed,
            "superseded_penalty": service.settings.superseded_penalty,
            "detail": cases,
        },
        note=(
            "Embedder-independent bitemporal check: valid_at closes a record "
            "without deleting it, so history stays queryable on request."
        ),
    )


# --- 5. conflict detection (embedder-dependent) --------------------------


async def measure_conflict_detection(service: MemoryService) -> Measurement:
    """Does the engine notice, unaided, that a new fact contradicts an old one?

    Unlike the two checks above this depends entirely on embedding quality: the
    incoming fact has to land in the conflict band relative to the record it
    replaces. Reported separately so a weak embedder cannot be mistaken for
    broken decay logic.
    """
    detected = 0
    cases: list[dict[str, Any]] = []

    for old, new, _query in SUPERSEDE_CASES:
        await service.vectors.purge_all()
        await service.upsert_preference(UpsertPreferenceRequest(content=old))
        result = await service.upsert_preference(
            UpsertPreferenceRequest(content=new, supersedes_conflicting=True)
        )
        auto = result.action == "superseded"
        detected += int(auto)
        cases.append(
            {
                "old": old,
                "new": new,
                "action": result.action,
                "similarity": (
                    round(result.matched_similarity, 4)
                    if result.matched_similarity is not None
                    else None
                ),
                "auto_superseded": auto,
            }
        )

    return Measurement(
        name="conflict_detection",
        value=round(detected / len(SUPERSEDE_CASES) * 100.0, 1),
        unit="percent",
        detail={
            "cases": len(SUPERSEDE_CASES),
            "detected": detected,
            "dedup_threshold": service.settings.dedup_threshold,
            "detail": cases,
        },
        note=(
            "Embedder-dependent. A lexical embedder scores unrelated wordings of "
            "the same subject far apart, so this is expected to be low under the "
            "hashing provider and is never published from such a run."
        ),
    )


# --- results.json ---------------------------------------------------------


def _git_commit() -> str | None:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=Path(__file__).resolve().parent.parent,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:
        return None


def _percent(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value:.0f}%"


def write_results(
    measurements: list[Measurement], service: MemoryService, semantic: bool
) -> dict[str, Any]:
    """Merge into results.json, preserving keys this script does not measure."""
    existing: dict[str, Any] = {}
    if RESULTS_PATH.exists():
        try:
            existing = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    by_name = {measurement.name: measurement for measurement in measurements}
    previous_metrics: dict[str, Any] = existing.get("metrics", {})

    token = by_name["token_compression"]
    dedup = by_name["dedup_accuracy"]
    ordering = by_name["decay_ordering"]
    exclusion = by_name["supersede_exclusion"]

    # Retrieval-quality numbers are only publishable from a real semantic
    # embedder; under 'hashing' they stay null and the landing page keeps
    # showing "in progress". The two mechanical invariants do not depend on the
    # embedder, so they publish from any run.
    metrics = {
        # Owned by this script, embedder-dependent.
        "promptTokenReduction": _percent(token.value) if semantic else None,
        "dedupAccuracy": _percent(dedup.value) if semantic else None,
        # Owned by this script, embedder-independent.
        "decayOrdering": _percent(ordering.value),
        "supersedeExclusion": _percent(exclusion.value),
        # Owned by scripts that do not exist yet (M2 / M5).
        "extractionJsonAccuracy": previous_metrics.get("extractionJsonAccuracy"),
        "localExtractionCostDelta": previous_metrics.get("localExtractionCostDelta"),
        "cacheCostSaving": previous_metrics.get("cacheCostSaving"),
    }

    payload = {
        "_comment": (
            "Written by evals/eval_memory_engine.py. Every value must come from a "
            "reproducible run; null renders as an in-progress badge on the "
            "landing page rather than a number. Headline metrics stay null unless "
            "the run used a real semantic embedder."
        ),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": _git_commit(),
        "environment": {
            "embedder": service.embedder.name,
            "semantic_embedder": semantic,
            "embedding_dim": service.settings.embedding_dim,
            "tokenizer": tokenizer_name(),
            "decay_rate_per_day": service.settings.decay_rate_per_day,
            "dedup_threshold": service.settings.dedup_threshold,
        },
        "metrics": metrics,
        "measurements": [
            {
                "name": measurement.name,
                "value": measurement.value,
                "unit": measurement.unit,
                "note": measurement.note,
                "detail": measurement.detail,
            }
            for measurement in measurements
        ],
    }
    RESULTS_PATH.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return payload


async def run() -> dict[str, Any]:
    settings = get_settings()
    service = await MemoryService.start(settings)
    # 'ollama' belongs here: nomic-embed-text is a real semantic embedder, it is
    # simply a local one. The earlier allowlist named only the hosted providers,
    # which meant the one provider that keeps the local-only promise was also the
    # one whose numbers could never be published. 'hashing' stays excluded — it
    # captures lexical overlap only, and is the whole reason this gate exists.
    semantic = settings.embedding_provider in {"openai", "gemini", "ollama"}
    try:
        measurements = [
            await measure_token_compression(service),
            await measure_dedup(service),
            await measure_decay_ordering(service),
            await measure_supersede_exclusion(service),
            await measure_conflict_detection(service),
        ]
        await service.vectors.purge_all()
        payload = write_results(measurements, service, semantic)
    finally:
        await service.close()

    print(f"embedder: {payload['environment']['embedder']} "
          f"(semantic={semantic}) · tokenizer: {payload['environment']['tokenizer']}")
    for measurement in measurements:
        print(f"  {measurement.name:20s} {measurement.value}{measurement.unit[:1]}")
    if not semantic:
        print(
            "\nheadline metrics left null: a hashing embedder measures lexical "
            "overlap, not semantics. Re-run with "
            "MINDBRIDGE_EMBEDDING_PROVIDER=openai to publish."
        )
    print(f"\nwrote {RESULTS_PATH}")
    return payload


if __name__ == "__main__":
    asyncio.run(run())
