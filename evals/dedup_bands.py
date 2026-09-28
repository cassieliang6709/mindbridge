"""Score every pair of open T3 rows under two ollama embedders, by cosine band.

This is the input to choosing a dedup threshold. The threshold itself is read,
not computed: print the pairs in a band with --show, mark each one duplicate or
distinct, and put the line above the highest distinct pair. Under bge-m3 that
reading, over the 129 pairs at >=0.80, gave 0.86 (highest distinct: 0.856).

    .venv/bin/python -m evals.dedup_bands
    .venv/bin/python -m evals.dedup_bands --show 0.80 0.86

Nothing is written. --show prints preference text, which is private: read it
in the terminal, do not commit it.

Pairs are compared across categories. Dedup only compares within one
namespace/category, so a line drawn here is conservative.
"""

from __future__ import annotations

import argparse
import asyncio
from itertools import pairwise

import httpx
import numpy as np

from api.db import create_pool
from api.settings import get_settings

BANDS = (0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.01)


async def load_open_rows() -> tuple[list[int], list[str]]:
    pool = await create_pool(get_settings())
    try:
        rows = await pool.fetch(
            "SELECT id, content FROM memory_vectors WHERE valid_at IS NULL ORDER BY id"
        )
    finally:
        await pool.close()
    return [row["id"] for row in rows], [row["content"] for row in rows]


def embed(texts: list[str], model: str) -> np.ndarray:
    url = str(get_settings().ollama_url).rstrip("/") + "/api/embed"
    out: list[list[float]] = []
    with httpx.Client(timeout=120) as client:
        for start in range(0, len(texts), 64):
            response = client.post(
                url, json={"model": model, "input": texts[start : start + 64]}
            )
            response.raise_for_status()
            out.extend(response.json()["embeddings"])
    matrix = np.asarray(out, dtype=np.float32)
    return matrix / np.linalg.norm(matrix, axis=1, keepdims=True)


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.dedup_bands")
    parser.add_argument("--models", nargs=2, default=["bge-m3", "nomic-embed-text"])
    parser.add_argument(
        "--show",
        nargs=2,
        type=float,
        metavar=("LOW", "HIGH"),
        help="print pairs whose first-model cosine is in [LOW, HIGH)",
    )
    args = parser.parse_args()
    primary, other = args.models

    ids, contents = asyncio.run(load_open_rows())
    sims = {}
    for model in args.models:
        vectors = embed(contents, model)
        sims[model] = vectors @ vectors.T
    i, j = np.triu_indices(len(ids), k=1)
    print(f"open rows: {len(ids)}   pairs: {len(i)}")

    for model in args.models:
        values = sims[model][i, j]
        counts = "  ".join(
            f"{lo:.2f}-{min(hi, 1.0):.2f}:{int(((values >= lo) & (values < hi)).sum())}"
            for lo, hi in pairwise(BANDS)
        )
        print(f"{model:>18}  {counts}")

    if args.show:
        low, high = args.show
        first = sims[primary][i, j]
        picked = np.where((first >= low) & (first < high))[0]
        for k in picked[np.argsort(-first[picked])]:
            a, b = i[k], j[k]
            print(
                f"\n{primary} {first[k]:.3f}  {other} {sims[other][a, b]:.3f}"
                f"  #{ids[a]} / #{ids[b]}\n  A: {contents[a]}\n  B: {contents[b]}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
