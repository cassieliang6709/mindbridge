"""Source-labelled retrieval quality evaluation for MindBridge T3 memory.

This evaluator bypasses both query caches and access bookkeeping. An eval run
must observe the current ranking without teaching the ranking from the queries
it repeats. Only user-confirmed judgements contribute to metrics; draft cases
still produce ranked candidates for review, but their scores remain null.

    python -m evals.retrieval_quality
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from api.models import MemoryCategory, MemoryHit, MemoryNamespace, RankingMode
from api.service import MemoryService

EVALUATOR_VERSION = "retrieval-quality.v1"
DEFAULT_CASES_PATH = Path(__file__).with_name("retrieval_quality_cases.json")
DEFAULT_OUTPUT_PATH = Path(__file__).with_name("retrieval_quality_latest.json")

JudgementStatus = Literal["draft", "confirmed"]


class CaseRequest(BaseModel):
    """Optional product filters applied to one quality probe."""

    top_k: int = Field(default=5, ge=1, le=50)
    time_window_days: int | None = Field(default=None, ge=1)
    categories: list[MemoryCategory] | None = None
    namespaces: list[MemoryNamespace] | None = None
    project: str | None = None
    include_superseded: bool = False
    ranking_mode: RankingMode = "temporal"


class LabelProvenance(BaseModel):
    """Stable pointer explaining where an eval case came from."""

    source_type: Literal["agent_run", "manual"]
    source_ref: str = Field(min_length=1)
    captured_at: datetime


class RetrievalJudgement(BaseModel):
    """Human labels over an explicit candidate pool.

    `judged_memory_ids` is as important as the positives. A new ranker may
    return an unseen memory; treating that unjudged row as irrelevant would
    manufacture an improvement or regression. Metrics are therefore null until
    every returned candidate has a judgement.
    """

    status: JudgementStatus = "draft"
    judged_memory_ids: list[int] = Field(default_factory=list)
    relevant_memory_ids: list[int] = Field(default_factory=list)
    labelled_by: str | None = None
    labelled_at: datetime | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_confirmed_label(self) -> "RetrievalJudgement":
        self.judged_memory_ids = sorted(set(self.judged_memory_ids))
        self.relevant_memory_ids = sorted(set(self.relevant_memory_ids))
        if self.status == "draft":
            return self
        if not self.labelled_by or self.labelled_at is None:
            raise ValueError("confirmed judgements need labelled_by and labelled_at")
        if not self.judged_memory_ids:
            raise ValueError("confirmed judgements need a non-empty candidate pool")
        if not set(self.relevant_memory_ids).issubset(self.judged_memory_ids):
            raise ValueError("relevant_memory_ids must be part of the judged pool")
        return self


class ReviewCandidate(BaseModel):
    """Candidate captured from the originating run for human review."""

    memory_id: int
    content: str
    score: float


class RetrievalCase(BaseModel):
    """One query plus its independently reviewable relevance label."""

    case_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    request: CaseRequest = Field(default_factory=CaseRequest)
    provenance: LabelProvenance
    judgement: RetrievalJudgement = Field(default_factory=RetrievalJudgement)
    review_candidates: list[ReviewCandidate] = Field(default_factory=list)


class RetrievalDataset(BaseModel):
    """Versioned local dataset used for comparable retrieval runs."""

    schema_version: Literal["retrieval-quality.v1"]
    name: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    created_at: datetime
    notes: str | None = None
    cases: list[RetrievalCase]

    @model_validator(mode="after")
    def reject_duplicate_case_ids(self) -> "RetrievalDataset":
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id values must be unique")
        return self


def load_dataset(path: Path) -> tuple[RetrievalDataset, str]:
    """Validate a dataset and return its canonical SHA-256 receipt."""

    dataset = RetrievalDataset.model_validate_json(path.read_text(encoding="utf-8"))
    canonical = json.dumps(
        dataset.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return dataset, hashlib.sha256(canonical).hexdigest()


def score_case(case: RetrievalCase, hits: list[MemoryHit]) -> dict[str, object]:
    """Score one confirmed judgement, or return explicit nulls for a draft."""

    ranked_ids = [hit.id for hit in hits]
    judged = set(case.judgement.judged_memory_ids)
    relevant = set(case.judgement.relevant_memory_ids)
    base: dict[str, object] = {
        "status": case.judgement.status,
        "judged_memory_ids": case.judgement.judged_memory_ids,
        "relevant_memory_ids": case.judgement.relevant_memory_ids,
        "returned_memory_ids": ranked_ids,
        "judgement_coverage_at_k": None,
        "has_relevant_hit": None,
        "precision_at_k": None,
        "reciprocal_rank": None,
    }
    if case.judgement.status != "confirmed":
        return base

    coverage = (
        len(judged.intersection(ranked_ids)) / len(ranked_ids) if ranked_ids else 1.0
    )
    base["judgement_coverage_at_k"] = round(coverage, 4)
    if coverage < 1.0:
        return base

    retrieved = relevant.intersection(ranked_ids)
    first_rank = next(
        (
            index
            for index, memory_id in enumerate(ranked_ids, start=1)
            if memory_id in relevant
        ),
        None,
    )
    base.update(
        {
            "has_relevant_hit": bool(retrieved),
            "precision_at_k": round(
                len(retrieved) / len(ranked_ids), 4
            ) if ranked_ids else 0.0,
            "reciprocal_rank": round(1 / first_rank, 4) if first_rank else 0.0,
        }
    )
    return base


def aggregate_metrics(case_scores: list[dict[str, object]]) -> dict[str, object]:
    """Aggregate only confirmed labels, keeping draft counts visible."""

    confirmed = [score for score in case_scores if score["status"] == "confirmed"]
    fully_judged = [
        score for score in confirmed if score["judgement_coverage_at_k"] == 1.0
    ]

    def mean(values: list[float]) -> float | None:
        return round(sum(values) / len(values), 4) if values else None

    return {
        "total_cases": len(case_scores),
        "confirmed_cases": len(confirmed),
        "draft_cases": len(case_scores) - len(confirmed),
        "fully_judged_cases": len(fully_judged),
        "judgement_coverage_at_k": mean(
            [float(score["judgement_coverage_at_k"]) for score in confirmed]
        ),
        "hit_rate_at_k": mean(
            [1.0 if score["has_relevant_hit"] else 0.0 for score in fully_judged]
        ),
        "precision_at_k": mean(
            [float(score["precision_at_k"]) for score in fully_judged]
        ),
        "mrr": mean(
            [float(score["reciprocal_rank"]) for score in fully_judged]
        ),
    }


def _hit_payload(hit: MemoryHit) -> dict[str, object]:
    return {
        "memory_id": hit.id,
        "content": hit.content,
        "namespace": hit.namespace,
        "category": hit.category,
        "project": hit.project,
        "created_at": hit.created_at.isoformat(),
        "cosine_similarity": round(hit.cosine_similarity, 6),
        "decay_multiplier": round(hit.decay_multiplier, 6),
        "score": round(hit.score, 6),
    }


async def evaluate(
    dataset: RetrievalDataset,
    dataset_sha256: str,
    service: MemoryService | None = None,
    ranking_mode: RankingMode | None = None,
) -> dict[str, object]:
    """Run one pgvector ranking mode without cache or access-count writes.

    ``ranking_mode`` is an optional run-wide override for reproducible A/B
    comparisons. Without it, each case uses the mode stored in its request.
    """

    owns_service = service is None
    service = service or await MemoryService.start()
    case_results: list[dict[str, object]] = []
    try:
        for case in dataset.cases:
            [embedding] = await service.embedder.embed([case.query])
            request = case.request
            effective_ranking_mode = ranking_mode or request.ranking_mode
            hits = await service.vectors.search(
                embedding,
                top_k=request.top_k,
                time_window_days=request.time_window_days,
                categories=request.categories,
                namespaces=request.namespaces,
                include_superseded=request.include_superseded,
                project=request.project,
                ranking_mode=effective_ranking_mode,
                record_access=False,
            )
            score = score_case(case, hits)
            case_results.append(
                {
                    "case_id": case.case_id,
                    "query": case.query,
                    "ranking_mode": effective_ranking_mode,
                    "provenance": case.provenance.model_dump(mode="json"),
                    "label": case.judgement.model_dump(mode="json"),
                    "metrics": score,
                    "hits": [_hit_payload(hit) for hit in hits],
                }
            )

        modes = {result["ranking_mode"] for result in case_results}
        evaluated_mode = next(iter(modes)) if len(modes) == 1 else "mixed"
        implementation = {
            "temporal": "pgvector_cosine_time_decay",
            "semantic": "pgvector_cosine",
        }.get(evaluated_mode, "pgvector_per_case")

        return {
            "evaluator_version": EVALUATOR_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "dataset": {
                "name": dataset.name,
                "schema_version": dataset.schema_version,
                "sha256": dataset_sha256,
            },
            "retrieval": {
                "implementation": implementation,
                "ranking_mode": evaluated_mode,
                "cache": "bypassed",
                "record_access": False,
                "embedder": service.embedder.name,
                "corpus_size": await service.vectors.count(),
                "decay_rate_per_day": service.settings.decay_rate_per_day,
            },
            "metrics": aggregate_metrics(
                [result["metrics"] for result in case_results]
            ),
            "cases": case_results,
        }
    finally:
        if owns_service:
            await service.close()


def save_dataset(path: Path, dataset: RetrievalDataset) -> None:
    """Atomically persist validated labels to the fixed local dataset path."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(dataset.model_dump(mode="json"), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def save_result(path: Path, result: dict[str, object]) -> None:
    """Atomically replace the generated baseline artifact."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument(
        "--ranking-mode",
        choices=("temporal", "semantic"),
        default=None,
        help="Override every case for a temporal-vs-semantic A/B run.",
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    dataset, receipt = load_dataset(args.cases)
    result = await evaluate(dataset, receipt, ranking_mode=args.ranking_mode)
    save_result(args.output, result)
    metrics = result["metrics"]
    print(
        f"wrote {args.output} | confirmed={metrics['confirmed_cases']} "
        f"draft={metrics['draft_cases']} | dataset={receipt[:12]}"
    )


if __name__ == "__main__":
    asyncio.run(_main())
