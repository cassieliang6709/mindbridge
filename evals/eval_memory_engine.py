"""MindBridge memory-engine benchmark.

Six measurements against a live Postgres+pgvector, split by whether they
depend on embedding quality:

Embedder-dependent (only published from a real semantic provider):
1. token_compression   — prompt tokens when a long history is replaced by the T2
   card plus the T3 memories a query actually recalls.
2. dedup_accuracy      — confusion matrix of the write-time cosine dedup over a
   labelled set of duplicate and distinct preference pairs.
5. conflict_detection  — whether the engine notices unaided that a new fact
   contradicts an older one.
6. cache_semantic      — hit rate, false-hit rate and the resulting saving for
   the M5 semantic query cache, swept across thresholds. A false hit is the
   error that matters: it answers one question with another question's
   memories, so the threshold is chosen for zero false hits, not best hit rate.

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

# --- M5 semantic cache fixtures ------------------------------------------

# A larger T3 so retrieval results actually differ between topics. With only a
# handful of rows every query returns nearly the same top-k and a false cache
# hit would look harmless — which would flatter the cache for the wrong reason.
CACHE_SEED_PREFERENCES: list[tuple[str, MemoryCategory]] = [
    ("Python 项目优先用 uv，不要用 pip", "tool_preference"),
    ("依赖锁文件用 uv pip compile 生成", "tool_preference"),
    ("Postgres 用 pgvector 存向量，索引用 ivfflat", "tool_preference"),
    ("测试先用 mock 数据验证流程，再接真实数据", "tool_preference"),
    ("周末不排会议", "schedule"),
    ("周六早上健身，不要安排任何事情", "schedule"),
    ("工作日的会议尽量排在下午", "schedule"),
    ("回答要直接，先给结论再解释", "coding_style"),
    ("代码注释用英文写", "coding_style"),
    ("commit message 用英文祈使句", "coding_style"),
    ("函数超过 40 行就拆开", "coding_style"),
    ("错误信息要说明怎么修，不要只说失败了", "coding_style"),
]

# Labelled query set, grouped by information need.
#
# Positives are pairs *within* a group: different wordings of one question,
# which a semantic cache SHOULD serve from one entry. Negatives are pairs
# across groups — different questions, which it MUST NOT. The groups are
# deliberately adjacent (weekend meetings vs. Saturday workout; comment
# language vs. commit-message language) so the negatives include the cases a
# threshold is most likely to get wrong, not just obviously unrelated text.
#
# Lengths are varied on purpose. The dedup threshold was tuned on stored
# preference *statements*, which are long sentences; a cache is keyed on
# *queries*, which are often four or five words. Whether an embedder holds up
# on short text is exactly the thing this measurement has to find out.
CACHE_QUERY_GROUPS: dict[str, list[str]] = {
    "package_manager": [
        "这个 Python 项目该用什么包管理器",
        "Python 依赖用什么工具装",
        "该用 pip 还是 uv 来装依赖",
        "what package manager should this Python project use",
        "安装第三方库的时候应该用哪个命令行工具",
    ],
    "weekend_meetings": [
        "周六下午能不能约一个代码评审会",
        "周末可以排会议吗",
        "周末的时间是怎么安排的",
        "can we schedule a review meeting over the weekend",
    ],
    "answer_style": [
        "回答的风格有什么要求",
        "回复应该写得多详细",
        "我喜欢什么样的回答方式",
        "how should replies be written and structured",
    ],
    "comment_language": [
        "代码注释用什么语言写",
        "注释应该写中文还是英文",
        "what language should code comments be written in",
    ],
    "commit_style": [
        "commit message 应该怎么写",
        "提交信息有什么格式规范",
        "how should I write commit messages",
    ],
    "vector_store": [
        "向量是存在什么地方的",
        "用什么数据库来存 embedding",
        "where are the embeddings stored",
    ],
    "workout_time": [
        "周六早上有什么固定安排",
        "健身时间一般是什么时候",
        "when during the week do I work out",
    ],
    "test_data": [
        "测试数据应该怎么准备",
        "验证流程的时候用真实数据还是假数据",
    ],
}

# Thresholds swept to pick the operating point. Coarse below 0.80 because
# nothing sane lives there, fine above it because that is where the decision is.
CACHE_THRESHOLD_SWEEP: list[float] = [
    0.70, 0.75, 0.80, 0.85, 0.88, 0.90, 0.92, 0.94, 0.95, 0.96, 0.97, 0.98, 0.99
]

# Deterministic interleaving of the query set. Paraphrases are separated so a
# hit has to survive other traffic, and the order is fixed so the number is
# reproducible rather than a property of one shuffle.
#
# Note what this does NOT measure: the exact-key hit rate is just this number
# minus one, because every query is replayed verbatim once per extra round.
# That figure describes the harness, not the cache, so the saving attributed to
# M5 is the *marginal* gain over an exact-key cache and never the total.
CACHE_WORKLOAD_ROUNDS = 2

# Minimum genuine should-hit pairs before a cache figure may be published.
# Matches the precedent train/eval_holdout.py already sets by refusing to
# publish a compliance rate on fewer than 30 holdout days, and the lesson in
# AGENTS.md that a 7-pair sample produced a threshold which collapsed on 170
# rows. Zero false hits over four hits is not evidence of anything.
CACHE_MIN_POSITIVE_PAIRS = 30


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


# --- 6. semantic query cache (M5, embedder-dependent) --------------------


def _cache_workload() -> list[tuple[str, str]]:
    """(group, query) in a fixed round-robin order.

    Round-robin rather than grouped, so a paraphrase never immediately follows
    the query it paraphrases. Testing a cache on back-to-back repeats measures
    almost nothing — the same mistake as testing dedup on identical strings.
    """
    columns = max(len(qs) for qs in CACHE_QUERY_GROUPS.values())
    order: list[tuple[str, str]] = []
    for _ in range(CACHE_WORKLOAD_ROUNDS):
        for index in range(columns):
            for group, queries in CACHE_QUERY_GROUPS.items():
                if index < len(queries):
                    order.append((group, queries[index]))
    return order


def _simulate(
    workload: list[tuple[str, str]],
    embeddings: dict[str, list[float]],
    truth: dict[str, list[int]],
    threshold: float,
    margin: float,
) -> dict[str, Any]:
    """Replay the workload against the cache's exact decision rule.

    Mirrors QueryCache.get / get_semantic: exact key first, then argmax over
    previously cached queries, gated on `threshold` and the ambiguity `margin`.
    Simulated rather than executed once per threshold so a 13-point sweep costs
    one embedding pass instead of thirteen; the chosen point is then confirmed
    against the live cache.
    """
    cached: list[str] = []  # queries with a live entry, newest first
    exact_seen: set[str] = set()
    exact_hits = semantic_hits = misses = 0
    false_hits = 0
    ambiguous = below = 0
    mistakes: list[dict[str, Any]] = []
    served_pairs: list[dict[str, Any]] = []

    for group, query in workload:
        if query in exact_seen:
            exact_hits += 1
            continue

        scored = sorted(
            ((_cosine(embeddings[query], embeddings[other]), other) for other in cached),
            key=lambda item: -item[0],
        )
        best = scored[0] if scored else None
        runner_up = scored[1][0] if len(scored) > 1 else None

        if best is not None and best[0] >= threshold:
            if runner_up is not None and (best[0] - runner_up) < margin:
                ambiguous += 1
            else:
                semantic_hits += 1
                same_group = _group_of(best[1]) == group
                same_result = truth[best[1]] == truth[query]
                # BOTH conditions, deliberately. Requiring only same_result
                # scores a hit as correct when two unrelated questions happen
                # to retrieve the same rows — which is not the cache working,
                # it is retrieval being degenerate, and it flattered an earlier
                # version of this measurement into publishing a number.
                correct = same_group and same_result
                served_pairs.append(
                    {
                        "query": query,
                        "served_from": best[1],
                        "cosine": round(best[0], 4),
                        "same_intent_group": same_group,
                        "same_result": same_result,
                    }
                )
                if not correct:
                    false_hits += 1
                    mistakes.append(
                        {
                            "asked": query,
                            "answered_with": best[1],
                            "cosine": round(best[0], 4),
                            "reason": (
                                "different question"
                                if not same_group
                                else "same question, different result"
                            ),
                            "expected_ids": truth[query],
                            "served_ids": truth[best[1]],
                        }
                    )
                continue
        elif best is not None:
            below += 1

        misses += 1
        exact_seen.add(query)
        cached.insert(0, query)

    served = exact_hits + semantic_hits
    total = len(workload)
    return {
        "threshold": round(threshold, 3),
        "margin": margin,
        "requests": total,
        "exact_hits": exact_hits,
        "semantic_hits": semantic_hits,
        "misses": misses,
        "hit_rate": round(served / total * 100.0, 1),
        "semantic_hit_rate": round(semantic_hits / total * 100.0, 1),
        "false_hits": false_hits,
        # Denominator is semantic hits, not all requests: this is the chance
        # that a semantic hit, once served, was the wrong question's answer.
        "false_hit_rate": (
            round(false_hits / semantic_hits * 100.0, 1) if semantic_hits else None
        ),
        "refused_ambiguous": ambiguous,
        "refused_below_threshold": below,
        "mistakes": mistakes,
        "served_pairs": served_pairs,
    }


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


_QUERY_GROUP: dict[str, str] = {
    query: group for group, queries in CACHE_QUERY_GROUPS.items() for query in queries
}

# The cache stores the normalised query (strip().lower()), so the live run has
# to look groups up by that form or every English query would fail to match its
# own group and be miscounted as a false hit.
_QUERY_GROUP_NORMALISED: dict[str, str] = {
    query.strip().lower(): group for query, group in _QUERY_GROUP.items()
}


def _group_of(query: str) -> str:
    return _QUERY_GROUP[query]


async def measure_semantic_cache(service: MemoryService) -> Measurement:
    """Can a semantic cache serve paraphrases without answering the wrong question?

    Ground truth is not a hand label. Two queries are "the same request" iff
    the engine returns the same memory ids for them, computed fresh against
    Postgres before any caching. That makes a false hit an objective fact —
    the cache returned a different answer than the query deserved — rather
    than an argument about whether two sentences mean the same thing.

    The intent groups are still used, but only to *build* the set and to report
    how many genuine paraphrases were caught.
    """
    await service.vectors.purge_all()
    for content, category in CACHE_SEED_PREFERENCES:
        await service.upsert_preference(
            UpsertPreferenceRequest(content=content, category=category)
        )

    workload = _cache_workload()
    queries = list(dict.fromkeys(query for _, query in workload))

    # Ground truth: the result each query gets on its own, no cache involved.
    truth: dict[str, list[int]] = {}
    vectors: dict[str, list[float]] = {}
    for query in queries:
        [embedding] = await service.embedder.embed([query])
        vectors[query] = embedding
        hits = await service.vectors.search(embedding, top_k=3)
        truth[query] = [hit.id for hit in hits]

    margin = service.settings.cache_semantic_margin
    sweep = [
        _simulate(workload, vectors, truth, threshold, margin)
        for threshold in CACHE_THRESHOLD_SWEEP
    ]

    # Three classes, not two. The middle one is the finding.
    #
    #   should_hit  same intent group AND same retrieved rows — one cache entry
    #               can legitimately serve both.
    #   must_not_hit different intent groups — serving one for the other returns
    #               another question's memories.
    #   unstable    same intent group, DIFFERENT retrieved rows. Neither the
    #               cache's fault nor its licence: retrieval itself does not
    #               agree with itself across paraphrases, so these pairs are
    #               uncacheable and they cap the achievable hit rate.
    #
    # An earlier version put same-result-different-group pairs in should_hit,
    # which let a degenerate embedder manufacture positives out of unrelated
    # questions that happened to retrieve the same rows.
    positive: list[tuple[float, str, str]] = []
    negative: list[tuple[float, str, str]] = []
    unstable: list[tuple[float, str, str]] = []
    for index, first in enumerate(queries):
        for second in queries[index + 1 :]:
            similarity = _cosine(vectors[first], vectors[second])
            if _group_of(first) != _group_of(second):
                negative.append((similarity, first, second))
            elif truth[first] == truth[second]:
                positive.append((similarity, first, second))
            else:
                unstable.append((similarity, first, second))
    positive.sort(reverse=True)
    negative.sort(reverse=True)
    unstable.sort(reverse=True)

    # Separable only if EVERY must-not-hit pair scores below EVERY should-hit
    # pair. The earlier form compared maxima, which only asked whether one
    # positive beat all negatives — satisfiable by a single lucky pair.
    separable = (
        bool(positive)
        and bool(negative)
        and negative[0][0] < min(score for score, _, _ in positive)
    )

    # Operating point: the lowest threshold with zero false hits, because a
    # lower threshold means more hits. If none is clean, take the least-bad and
    # say so — do not quietly publish the best-looking row.
    clean = [row for row in sweep if row["false_hits"] == 0 and row["semantic_hits"] > 0]
    if clean:
        chosen = min(clean, key=lambda row: row["threshold"])
        rationale = (
            f"lowest threshold in the sweep with zero false hits "
            f"({chosen['semantic_hits']} semantic hits over {chosen['requests']} requests)"
        )
    else:
        chosen = min(
            sweep,
            key=lambda row: (
                row["false_hit_rate"] if row["false_hit_rate"] is not None else 0.0,
                -row["threshold"],
            ),
        )
        rationale = (
            "no threshold in the sweep reached zero false hits; reporting the "
            "least-bad point. The semantic layer stays disabled by default."
        )

    # Confirm the simulation against the live cache at the chosen point, so the
    # published number describes the shipped code and not a model of it.
    previous_enabled = service.cache.semantic_enabled
    previous_threshold = service.cache.semantic_threshold
    live: dict[str, Any] = {"ran": False}
    if service.cache.enabled:
        service.cache.semantic_enabled = True
        service.cache.semantic_threshold = chosen["threshold"]
        await service.cache.invalidate_namespace("temporal_query")
        service.cache.reset_stats()
        live_false = 0
        live_semantic = 0
        for group, query in workload:
            trace: dict[str, object] = {}
            result = await service.temporal_query(
                TemporalQueryRequest(query_string=query, top_k=3), trace=trace
            )
            if trace.get("layer") == "semantic":
                live_semantic += 1
                # Same both-conditions rule as the simulation. Checking only
                # the ids would let the live run report zero false hits on a
                # pair the simulation counts as wrong, purely because two
                # unrelated questions retrieved the same rows.
                matched = str(trace.get("semantic_matched_query") or "")
                same_group = _QUERY_GROUP_NORMALISED.get(matched) == group
                if not same_group or [h.id for h in result.hits] != truth[query]:
                    live_false += 1
        live = {
            "ran": True,
            "threshold": chosen["threshold"],
            "semantic_hits": live_semantic,
            "false_hits": live_false,
            "stats": service.cache.stats(),
            "matches_simulation": (
                live_semantic == chosen["semantic_hits"]
                and live_false == chosen["false_hits"]
            ),
        }
        await service.cache.invalidate_namespace("temporal_query")
    service.cache.semantic_enabled = previous_enabled
    service.cache.semantic_threshold = previous_threshold
    service.cache.reset_stats()

    # Cost model. Stated in operations avoided, because that is what was
    # observed; converting it to money would need a price for work that is
    # currently done by a local model for free.
    requests = chosen["requests"]
    retrieval_avoided = (chosen["exact_hits"] + chosen["semantic_hits"]) / requests * 100.0
    # A semantic lookup needs the query embedding to find its neighbour, so the
    # semantic layer cannot save an embedding call. Only the exact layer can.
    embedding_avoided = chosen["exact_hits"] / requests * 100.0
    # The number M5 is actually allowed to claim. The exact-key portion existed
    # before M5 and is fixed by how often the workload repeats a query verbatim,
    # so folding it in would credit the semantic cache with the harness's
    # repetition rate.
    marginal_saving = chosen["semantic_hits"] / requests * 100.0

    blockers: list[str] = []
    if not separable:
        blockers.append(
            "should-hit and must-not-hit cosines overlap: no threshold separates them"
        )
    if chosen["false_hits"] != 0:
        blockers.append(f"{chosen['false_hits']} false hits at the chosen threshold")
    if len(positive) < CACHE_MIN_POSITIVE_PAIRS:
        blockers.append(
            f"only {len(positive)} should-hit pairs, below the {CACHE_MIN_POSITIVE_PAIRS} "
            "needed for the result to mean anything"
        )
    if marginal_saving <= 0.0:
        blockers.append("the semantic layer avoided no work over the exact-key cache")
    publishable = not blockers

    return Measurement(
        name="cache_semantic",
        value=round(chosen["semantic_hit_rate"], 1),
        unit="percent",
        detail={
            "queries": len(queries),
            "intent_groups": len(CACHE_QUERY_GROUPS),
            "requests": requests,
            "labelled_pairs": {
                "should_hit": len(positive),
                "must_not_hit": len(negative),
                "unstable_same_intent": len(unstable),
                "min_should_hit_to_publish": CACHE_MIN_POSITIVE_PAIRS,
                "ground_truth": (
                    "should_hit = same intent group AND identical top-3 memory "
                    "ids from a fresh query; must_not_hit = different intent "
                    "group; unstable_same_intent = same question asked two ways, "
                    "different rows retrieved — uncacheable by construction"
                ),
            },
            "separation": {
                "separable": separable,
                "min_cosine_should_hit": (
                    round(min(score for score, _, _ in positive), 4) if positive else None
                ),
                "max_cosine_should_hit": round(positive[0][0], 4) if positive else None,
                "max_cosine_must_not_hit": round(negative[0][0], 4) if negative else None,
                "worst_must_not_hit_pair": (
                    [negative[0][1], negative[0][2]] if negative else None
                ),
                "note": (
                    "separable is True only when every must-not-hit pair scores "
                    "below every should-hit pair. False means no threshold "
                    "exists and the sweep is only choosing how to be wrong."
                ),
            },
            "retrieval_stability": {
                "same_intent_pairs": len(positive) + len(unstable),
                "agreeing": len(positive),
                "percent_agreeing": (
                    round(len(positive) / (len(positive) + len(unstable)) * 100.0, 1)
                    if (positive or unstable)
                    else None
                ),
                "note": (
                    "How often two wordings of one question retrieve the same "
                    "rows at all. This is an upper bound on any semantic cache's "
                    "hit rate: below it, the cache cannot be right because the "
                    "thing it would cache is not stable."
                ),
            },
            "chosen_threshold": chosen["threshold"],
            "chosen_rationale": rationale,
            "ambiguity_margin": margin,
            "hit_rate_percent": chosen["hit_rate"],
            "semantic_hit_rate_percent": chosen["semantic_hit_rate"],
            "false_hit_rate_percent": chosen["false_hit_rate"],
            "false_hits": chosen["false_hits"],
            "example_false_hits": chosen["mistakes"][:5],
            "cost_model": {
                "marginal_saving_percent": round(marginal_saving, 1),
                "vector_searches_avoided_percent": round(retrieval_avoided, 1),
                "embedding_calls_avoided_percent": round(embedding_avoided, 1),
                "exact_hit_rate_is_a_harness_artifact": True,
                "note": (
                    "marginal_saving_percent is the only figure M5 may claim: "
                    "the share of requests the SEMANTIC layer answered that an "
                    "exact-key cache would have missed. The larger "
                    "vector_searches_avoided_percent includes exact-key hits, "
                    "which existed before M5 and whose rate is set by how often "
                    "this workload repeats a query verbatim "
                    f"(CACHE_WORKLOAD_ROUNDS={CACHE_WORKLOAD_ROUNDS}) — a "
                    "property of the test, not of the cache. "
                    "An exact hit skips the embedding call and the vector "
                    "search; a semantic hit skips only the vector search, "
                    "because the embedding is what finds the neighbour and has "
                    "already been paid for. Under the local ollama embedder "
                    "neither call is billed, so the dollar saving is zero by "
                    "construction — this is a latency and load figure, and it "
                    "is not the LLM API cost saving the name suggests."
                ),
            },
            "publishable": publishable,
            "publish_blockers": blockers,
            "live_confirmation": live,
            "sweep": [
                {key: row[key] for key in row if key not in {"mistakes", "served_pairs"}}
                for row in sweep
            ],
        },
        note=(
            "Ground truth is objective: two queries are the same request iff a "
            "fresh query returns the same memory ids. A false hit therefore "
            "means the cache demonstrably answered a different question."
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
    cache = by_name["cache_semantic"]

    # cacheCostSaving publishes only when the semantic cache measurement clears
    # its own bar: a real embedder, a threshold that produced zero false hits,
    # and a labelled set on which the should-hit and must-not-hit pairs are
    # actually separable. Otherwise it stays null and the page keeps saying
    # "in progress", which is the truth. A cache that sometimes returns another
    # question's memories has not saved anything — it has moved the cost from
    # the bill to the answer.
    cache_detail = cache.detail
    if semantic and cache_detail.get("publishable"):
        cache_saving = _percent(cache_detail["cost_model"]["marginal_saving_percent"])
    else:
        cache_saving = None

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
        # Owned by a script that does not exist yet (M2).
        "extractionJsonAccuracy": previous_metrics.get("extractionJsonAccuracy"),
        "localExtractionCostDelta": previous_metrics.get("localExtractionCostDelta"),
        # Owned by this script as of M5.
        "cacheCostSaving": cache_saving,
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
            "cache_semantic_enabled": service.settings.cache_semantic_enabled,
            "cache_semantic_threshold": service.settings.cache_semantic_threshold,
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
            await measure_semantic_cache(service),
        ]
        await service.vectors.purge_all()
        payload = write_results(measurements, service, semantic)
    finally:
        await service.close()

    print(f"embedder: {payload['environment']['embedder']} "
          f"(semantic={semantic}) · tokenizer: {payload['environment']['tokenizer']}")
    for measurement in measurements:
        print(f"  {measurement.name:20s} {measurement.value}{measurement.unit[:1]}")
    cache_detail = next(
        m.detail for m in measurements if m.name == "cache_semantic"
    )
    separation = cache_detail["separation"]
    print(
        "\nsemantic cache @ threshold "
        f"{cache_detail['chosen_threshold']} (margin {cache_detail['ambiguity_margin']}): "
        f"hit {cache_detail['hit_rate_percent']}% "
        f"(semantic {cache_detail['semantic_hit_rate_percent']}%), "
        f"false-hit {cache_detail['false_hit_rate_percent']}% "
        f"over {cache_detail['requests']} requests / "
        f"{cache_detail['labelled_pairs']['should_hit']} should-hit + "
        f"{cache_detail['labelled_pairs']['must_not_hit']} must-not-hit pairs"
    )
    print(
        f"  separable={separation['separable']}  "
        f"should-hit cosines {separation['min_cosine_should_hit']}..{separation['max_cosine_should_hit']}  "
        f"max must-not-hit={separation['max_cosine_must_not_hit']}"
    )
    stability = cache_detail["retrieval_stability"]
    print(
        f"  retrieval stability: {stability['agreeing']}/{stability['same_intent_pairs']} "
        f"({stability['percent_agreeing']}%) of same-intent pairs retrieve the same rows"
    )
    print(
        f"  marginal saving attributable to M5: "
        f"{cache_detail['cost_model']['marginal_saving_percent']}% "
        f"(total incl. exact-key hits {cache_detail['cost_model']['vector_searches_avoided_percent']}%, "
        "which the harness's repeat rate decides)"
    )
    print(f"  {cache_detail['chosen_rationale']}")
    if cache_detail["publishable"]:
        print("  cacheCostSaving published.")
    else:
        print("  cacheCostSaving left null:")
        for blocker in cache_detail["publish_blockers"]:
            print(f"    - {blocker}")
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
