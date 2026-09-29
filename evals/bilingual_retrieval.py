"""Bilingual known-item retrieval over English preference memories.

The question: when a user asks in Chinese, or in Chinese with English terms
mixed in, does the embedder still find the English T3 statement the question
is about? Each query in `bilingual_retrieval/queries.json` names exactly one
target memory in `bilingual_retrieval/memories.json` and is phrased three ways
(`en`, `zh`, `mixed`). Every memory is ranked by cosine against the query — no
decay, no filters, no database — and the target's rank is recorded.

The dataset is synthetic: preferences of a fictional developer, mostly English,
grouped by topic so every target has close neighbours ("commit each feature
separately" next to "squash before merge"). Nothing in it comes from a real
user's memories, so anyone with ollama can reproduce the numbers:

    ollama pull bge-m3 && ollama pull nomic-embed-text
    uv run --with-requirements requirements.txt python -m evals.bilingual_retrieval

Writes evals/bilingual_retrieval_results.json with the model digests, ollama
version and dataset hashes, so a number can be traced to what produced it.

`--nomic-prefix` adds a second run for each nomic model with the
`search_query:` / `search_document:` prefixes nomic-embed-text was trained
with. The product path (`api/embeddings.py`) sends no prefix, so the plain run
is the one that describes MindBridge; the prefixed run shows how much of the
gap is the missing prefix rather than the model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from api.settings import get_settings

HERE = Path(__file__).parent
DATA_DIR = HERE / "bilingual_retrieval"
MEMORIES_PATH = DATA_DIR / "memories.json"
QUERIES_PATH = DATA_DIR / "queries.json"
RESULTS_PATH = HERE / "bilingual_retrieval_results.json"

CONDITIONS = ("en", "zh", "mixed")
DEFAULT_MODELS = ("bge-m3", "nomic-embed-text")
BATCH_SIZE = 32
TIMEOUT_SECONDS = 120.0


def load_dataset() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    memories = json.loads(MEMORIES_PATH.read_text(encoding="utf-8"))["memories"]
    queries = json.loads(QUERIES_PATH.read_text(encoding="utf-8"))["queries"]
    return memories, queries


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(component * component for component in vector))
    return [component / norm for component in vector]


def embed(
    client: httpx.Client, url: str, model: str, texts: list[str]
) -> list[list[float]]:
    """Fixed batch composition, so repeated runs send byte-identical requests."""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        response = client.post(
            f"{url}/api/embed",
            json={"model": model, "input": texts[start : start + BATCH_SIZE]},
        )
        response.raise_for_status()
        vectors.extend(response.json()["embeddings"])
    return [normalise(vector) for vector in vectors]


def rank_of(target: int, query: list[float], docs: list[list[float]]) -> int:
    """1-based rank of the target; ties are broken against the target."""
    scores = [sum(a * b for a, b in zip(query, doc)) for doc in docs]
    return 1 + sum(
        1
        for index, score in enumerate(scores)
        if index != target and score >= scores[target]
    )


def metrics(ranks: list[int]) -> dict[str, float | int]:
    n = len(ranks)
    return {
        "n": n,
        "recall@1": round(sum(rank <= 1 for rank in ranks) / n, 3),
        "recall@5": round(sum(rank <= 5 for rank in ranks) / n, 3),
        "recall@10": round(sum(rank <= 10 for rank in ranks) / n, 3),
        "mrr": round(sum(1 / rank for rank in ranks) / n, 3),
        "median_rank": float(statistics.median(ranks)),
    }


def model_digest(client: httpx.Client, url: str, model: str) -> str | None:
    response = client.get(f"{url}/api/tags")
    response.raise_for_status()
    for entry in response.json().get("models", []):
        if entry.get("name") in (model, f"{model}:latest"):
            return entry.get("digest")
    return None


def run_model(
    client: httpx.Client,
    url: str,
    model: str,
    prefix: bool,
    memories: list[dict[str, str]],
    queries: list[dict[str, str]],
) -> dict[str, Any]:
    doc_prefix = "search_document: " if prefix else ""
    query_prefix = "search_query: " if prefix else ""
    docs = embed(
        client, url, model, [doc_prefix + memory["text"] for memory in memories]
    )
    index = {memory["key"]: row for row, memory in enumerate(memories)}

    conditions: dict[str, Any] = {}
    per_query: dict[str, dict[str, int]] = {query["target"]: {} for query in queries}
    for condition in CONDITIONS:
        vectors = embed(
            client, url, model, [query_prefix + query[condition] for query in queries]
        )
        ranks = []
        for query, vector in zip(queries, vectors):
            rank = rank_of(index[query["target"]], vector, docs)
            ranks.append(rank)
            per_query[query["target"]][condition] = rank
        conditions[condition] = metrics(ranks)

    return {
        "model": model,
        "digest": model_digest(client, url, model),
        "prefix": prefix,
        "dim": len(docs[0]),
        "conditions": conditions,
        "per_query_rank": per_query,
    }


def print_table(runs: list[dict[str, Any]]) -> None:
    header = f"{'model':<34} {'cond':<6} {'n':>3} {'R@1':>6} {'R@5':>6} {'R@10':>6} {'MRR':>6} {'med':>6}"
    print(header)
    print("-" * len(header))
    for run in runs:
        label = run["model"] + (" +prefix" if run["prefix"] else "")
        for condition in CONDITIONS:
            m = run["conditions"][condition]
            print(
                f"{label:<34} {condition:<6} {m['n']:>3} {m['recall@1']:>6.3f} "
                f"{m['recall@5']:>6.3f} {m['recall@10']:>6.3f} {m['mrr']:>6.3f} "
                f"{m['median_rank']:>6.1f}"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument(
        "--nomic-prefix",
        action="store_true",
        help="also run each nomic model with search_query:/search_document: prefixes",
    )
    parser.add_argument("--out", type=Path, default=RESULTS_PATH)
    args = parser.parse_args()

    url = str(get_settings().ollama_url).rstrip("/")
    memories, queries = load_dataset()

    runs = []
    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        version = client.get(f"{url}/api/version").json().get("version")
        for model in args.models:
            runs.append(run_model(client, url, model, False, memories, queries))
            if args.nomic_prefix and model.startswith("nomic"):
                runs.append(run_model(client, url, model, True, memories, queries))

    report = {
        "benchmark": "bilingual_retrieval",
        "task": "known-item retrieval; one target memory per query; cosine over all memories",
        "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ollama_version": version,
        "dataset": {
            "memories": len(memories),
            "memories_zh": sum(memory["lang"] == "zh" for memory in memories),
            "queries": len(queries),
            "conditions": list(CONDITIONS),
            "memories_sha256": sha256(MEMORIES_PATH),
            "queries_sha256": sha256(QUERIES_PATH),
        },
        "runs": runs,
    }
    args.out.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print_table(runs)
    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    main()
