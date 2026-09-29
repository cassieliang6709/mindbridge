"""Latency benchmark for the temporal_query read path.

The engine benchmark in evals/eval_memory_engine.py answers "does retrieval
return the right thing". This one answers "how long does it take, and which
component owns that time" — the question an agent runtime has to answer before
it can promise anything about a turn's budget.

Four paths are timed separately rather than as one end-to-end number, because
they fail and improve for different reasons:

  embed         — the embedding provider alone. Network or model bound.
  search        — pgvector alone, given an embedding, with no access bookkeeping.
                  Index and corpus bound.
  search_access — the same search as the product path runs it, including the
                  UPDATE that bumps access_count. The gap between the two is the
                  cost of writing on a read.
  miss          — full temporal_query with every cache layer cleared first.
                  This is the number a cold agent turn actually pays.
  redis_hit     — the same query with only the in-process LRU cleared, so the
                  answer comes from Redis. This is what a *second process*
                  sees, which is the only reason the Redis layer exists.
  lru_hit       — the same query repeated inside one process.

Reporting only the mean would hide the case that matters: an agent turn is as
slow as its slowest tool call, so P95 is the number that decides whether a
budget holds. Percentiles are computed by nearest-rank on the raw samples; with
a sample this small an interpolated percentile would imply precision the run
does not have.

    docker compose up -d db redis
    python -m evals.bench_retrieval            # writes evals/retrieval_latency.json
    python -m evals.bench_retrieval --rounds 5

Every result records the embedder, the corpus size and whether Redis was
attached. Latency without those three is not comparable across runs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from api.embeddings import build_embedder
from api.models import TemporalQueryRequest
from api.service import MemoryService
from api.settings import get_settings

RESULTS_PATH = Path(__file__).with_name("retrieval_latency.json")

# Fixed rather than sampled from the database. A query set that drifts with the
# corpus makes two runs incomparable, which is exactly what a latency baseline
# must not do. These are shaped like what an agent actually asks before
# answering: preferences, tooling decisions, working style.
QUERIES: tuple[str, ...] = (
    "what test framework does the user prefer",
    "how does the user want commit messages written",
    "which package manager should I use for Python projects",
    "does the user want type hints everywhere",
    "what is the user's preferred code review style",
    "how should I format SQL in this project",
    "what database does the user default to",
    "does the user prefer async or sync Python",
    "how does the user feel about comments in code",
    "what is the user's stance on dependency upgrades",
    "which editor and terminal setup does the user run",
    "how does the user structure project directories",
    "what does the user expect in a pull request description",
    "how should errors be logged in this codebase",
    "does the user want docstrings in Chinese and English",
    "what naming convention does the user use for tests",
    "how does the user prefer to handle secrets and env files",
    "what is the user's deployment target",
    "does the user want Docker for local development",
    "how does the user schedule recurring jobs",
    "what did the user decide about caching",
    "which model provider does the user use for embeddings",
    "how does the user want migrations applied",
    "what is the user's policy on breaking changes",
    "does the user prefer monorepo or separate repos",
    "how does the user track open threads across sessions",
    "what does the user consider done for a feature",
    "how does the user want long functions refactored",
    "what is the user's preferred way to profile performance",
    "does the user review AI-generated code before merging",
)

# Discarded before measuring. The first call pays for pool warm-up, the
# embedder's HTTP connection and any lazy import, none of which a steady-state
# agent turn pays.
WARMUP_ITERATIONS = 3


@dataclass(slots=True)
class Timings:
    """Raw millisecond samples for one timed path.

    中文：某一条计时路径的原始毫秒样本。
    """

    label: str
    samples: list[float]

    def summary(self) -> dict[str, Any]:
        """Reduce the samples to the percentiles a budget is written against.

        中文：将样本归约为可用于预算判断的分位数指标。
        """
        ordered = sorted(self.samples)
        return {
            "label": self.label,
            "n": len(ordered),
            "p50_ms": round(_nearest_rank(ordered, 0.50), 2),
            "p95_ms": round(_nearest_rank(ordered, 0.95), 2),
            "max_ms": round(ordered[-1], 2),
            "mean_ms": round(statistics.fmean(ordered), 2),
        }


def _nearest_rank(ordered: list[float], quantile: float) -> float:
    """Return the nearest-rank percentile of an already-sorted sample.

    中文：返回已排序样本的最近秩分位数。

    Args:
        ordered: Ascending samples; must not be empty.
        quantile: Target quantile in [0, 1].

    Returns:
        An observed sample value, never an interpolated one.
    """
    if not ordered:
        raise ValueError("cannot take a percentile of an empty sample")
    rank = max(1, min(len(ordered), int(-(-quantile * len(ordered) // 1))))
    return ordered[rank - 1]


async def _time(fn: Callable[[], Awaitable[Any]]) -> float:
    """Await one call and return its wall-clock duration in milliseconds.

    中文：执行一次调用并返回其墙钟耗时（毫秒）。
    """
    start = time.perf_counter()
    await fn()
    return (time.perf_counter() - start) * 1000


async def _clear_caches(service: MemoryService) -> None:
    """Drop every cache layer so the next query pays the full cost.

    中文：清空所有缓存层，使下一次查询付出完整代价。

    A miss measured against a warm LRU is not a miss. The in-process LRU has to
    be cleared as well as Redis, because it sits in front of it.
    """
    service.cache._lru.clear()  # noqa: SLF001 - benchmark needs the cold path
    await service.cache.invalidate_namespace("temporal_query")


def _clear_lru(service: MemoryService) -> None:
    """Drop only the in-process layer, leaving Redis populated.

    中文：仅清空进程内 LRU，保留 Redis 内容。

    This is how a second process starts: it shares Redis but has its own empty
    LRU, so measuring a Redis hit means clearing one layer and not the other.
    """
    service.cache._lru.clear()  # noqa: SLF001 - benchmark needs the cross-process path


async def _ab_connection_reuse(
    service: MemoryService, rounds: int
) -> dict[str, Any]:
    """A/B the embedder with and without a reused HTTP connection.

    中文：在同一次运行内对比嵌入器复用连接与每次新建连接的耗时。

    Both arms run in one process, against one corpus, interleaved, because the
    only honest way to attribute a saving is to measure the alternative under
    the same conditions rather than against a number from an earlier run. The
    "per_call" arm closes the transport after every call, which is exactly what
    opening a client inside `embed()` used to do.

    The two arms need two embedder instances. Sharing one and closing it after
    the per-call arm makes the *next* shared call pay for the reconnect, which
    inverts the result: measured that way the "per-call" arm looks 29% faster,
    because it is the one running on the connection the shared arm just opened.

    中文：两臂必须使用两个嵌入器实例。若共用一个并在 per-call 之后关闭，
    下一次 shared 调用就会承担重连开销，结论会完全反过来。

    Args:
        service: A started service whose embedder supplies the shared arm.
        rounds: Passes over the fixed query set.

    Returns:
        A summary for each arm plus the measured saving at P50.
    """
    shared = Timings("shared_connection", [])
    per_call = Timings("per_call_connection", [])
    # A second instance of the same provider, so closing it after every call
    # cannot disturb the connection the shared arm is reusing.
    cold_embedder = build_embedder(get_settings())

    try:
        for _ in range(rounds):
            for query in QUERIES:
                shared.samples.append(
                    await _time(lambda: service.embedder.embed([query]))
                )
                # Interleaved, not run in two blocks, so a drifting machine load
                # hits both arms equally instead of only the second one.
                per_call.samples.append(
                    await _time(lambda: cold_embedder.embed([query]))
                )
                await cold_embedder.aclose()
    finally:
        await cold_embedder.aclose()

    shared_summary = shared.summary()
    per_call_summary = per_call.summary()
    saved = per_call_summary["p50_ms"] - shared_summary["p50_ms"]
    return {
        "shared_connection": shared_summary,
        "per_call_connection": per_call_summary,
        "p50_saved_ms": round(saved, 2),
        "p50_saved_pct": round(100 * saved / per_call_summary["p50_ms"], 1),
    }


async def run(rounds: int) -> dict[str, Any]:
    """Time the four retrieval paths and return a publishable result block.

    中文：分别测量四条检索路径并返回可发布的结果块。

    Args:
        rounds: How many passes over the fixed query set to measure.

    Returns:
        A JSON-ready dict recording the environment and every percentile.
    """
    settings = get_settings()
    service = await MemoryService.start(settings)
    try:
        corpus = await service.vectors.count(include_superseded=False)

        for query in QUERIES[:WARMUP_ITERATIONS]:
            await service.temporal_query(TemporalQueryRequest(query_string=query))

        embed = Timings("embed", [])
        search = Timings("search", [])
        search_access = Timings("search_access", [])
        miss = Timings("miss", [])
        redis_hit = Timings("redis_hit", [])
        lru_hit = Timings("lru_hit", [])

        for _ in range(rounds):
            for query in QUERIES:
                # Component timings first, on their own, so the end-to-end
                # number can be attributed instead of just reported.
                start = time.perf_counter()
                [embedding] = await service.embedder.embed([query])
                embed.samples.append((time.perf_counter() - start) * 1000)

                search.samples.append(
                    await _time(
                        lambda: service.vectors.search(
                            embedding, top_k=5, record_access=False
                        )
                    )
                )
                search_access.samples.append(
                    await _time(
                        lambda: service.vectors.search(
                            embedding, top_k=5, record_access=True
                        )
                    )
                )

                await _clear_caches(service)
                request = TemporalQueryRequest(query_string=query, top_k=5)
                miss.samples.append(
                    await _time(lambda: service.temporal_query(request))
                )
                # Redis now holds this key; a fresh process would have an empty
                # LRU in front of it, so clearing only the LRU reproduces that.
                _clear_lru(service)
                redis_hit.samples.append(
                    await _time(lambda: service.temporal_query(request))
                )
                # And now the LRU is populated too.
                lru_hit.samples.append(
                    await _time(lambda: service.temporal_query(request))
                )

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "environment": {
                "embedder": service.embedder.name,
                "embedding_dim": settings.embedding_dim,
                "redis_attached": service.cache.enabled,
                "semantic_cache": service.cache.semantic_enabled,
                "open_memories": corpus,
                "queries": len(QUERIES),
                "rounds": rounds,
            },
            "connection_reuse": await _ab_connection_reuse(service, rounds),
            "paths": {
                timing.label: timing.summary()
                for timing in (
                    embed,
                    search,
                    search_access,
                    miss,
                    redis_hit,
                    lru_hit,
                )
            },
        }
    finally:
        await service.close()


def main() -> None:
    """Run the benchmark from the command line and write the results file.

    中文：从命令行运行基准测试并写出结果文件。
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rounds",
        type=int,
        default=3,
        help="passes over the fixed query set (default: 3)",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="print results without updating retrieval_latency.json",
    )
    args = parser.parse_args()

    result = asyncio.run(run(args.rounds))
    print(json.dumps(result, indent=2))
    if not args.no_write:
        RESULTS_PATH.write_text(json.dumps(result, indent=2) + "\n")
        print(f"\nwrote {RESULTS_PATH}")


if __name__ == "__main__":
    main()
