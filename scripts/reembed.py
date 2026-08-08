"""Re-embed T3 with a different model, and optionally merge the duplicates
that the previous embedder could not see.

    # show what would change, touch nothing
    .venv/bin/python -m scripts.reembed --dry-run

    # re-embed in place (changes the vector column width)
    MINDBRIDGE_EMBEDDING_PROVIDER=ollama \\
    MINDBRIDGE_EMBEDDING_MODEL=nomic-embed-text \\
    MINDBRIDGE_EMBEDDING_DIM=768 \\
    .venv/bin/python -m scripts.reembed --apply

    # then merge near-duplicates at the new threshold
    ... --apply --merge-duplicates --threshold 0.80

Why this exists: the hashing fallback scores real duplicates 0.13-0.73, so
write-time dedup never fired and T3 accumulated many phrasings of one fact.
Switching embedder fixes new writes; this script fixes the rows already stored.

On the threshold: 0.80 was chosen by reading every proposed merge across 170 real
rows, not by picking a round number. nomic-embed-text puts *topically* related
preferences around 0.62-0.75 — everything here is "how Cassie likes to work", so
the space is compressed — and in that band the merges are mostly wrong. One at
0.686 would have merged "validate with mock data first" into "no fake data
anywhere", which are nearly opposite. At >=0.80 every proposed merge was a real
duplicate.

The asymmetry justifies erring high: a missed merge leaves visible clutter, while
a false merge silently closes a distinct preference and is hard to notice later.

Merging keeps the OLDEST row as canonical. created_at is what decay is measured
from, and it should reflect when the preference was first learned, not when it
was last rephrased — the same reasoning refresh() uses.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from api.db import create_pool
from api.embeddings import build_embedder, to_pgvector
from api.settings import get_settings


async def run(args: argparse.Namespace) -> int:
    settings = get_settings()
    embedder = build_embedder(settings)
    pool = await create_pool(settings)

    try:
        rows = await pool.fetch(
            """
            SELECT id, content, category, created_at, valid_at, access_count
            FROM memory_vectors ORDER BY id
            """
        )
        if not rows:
            print("T3 is empty; nothing to do")
            return 0

        current_dim = await pool.fetchval(
            """
            SELECT atttypmod FROM pg_attribute
            WHERE attrelid = 'memory_vectors'::regclass AND attname = 'embedding'
            """
        )
        print(f"rows: {len(rows)}")
        print(f"column: vector({current_dim})  ->  vector({settings.embedding_dim})")
        print(f"embedder: {embedder.name} / {settings.embedding_model}")

        print("\nembedding…")
        vectors: dict[int, list[float]] = {}
        batch = 32
        contents = [(row["id"], row["content"]) for row in rows]
        for start in range(0, len(contents), batch):
            chunk = contents[start : start + batch]
            embedded = await embedder.embed([text for _, text in chunk])
            for (row_id, _), vector in zip(chunk, embedded):
                vectors[row_id] = vector
            print(f"  {min(start + batch, len(contents))}/{len(contents)}")

        # --- find duplicates at the new threshold ---------------------------
        open_rows = [r for r in rows if r["valid_at"] is None]
        merges: list[tuple[int, int, float]] = []  # (duplicate, canonical, cos)
        canonical_of: dict[int, int] = {}
        for i, row in enumerate(open_rows):
            if row["id"] in canonical_of:
                continue
            for other in open_rows[i + 1 :]:
                if other["id"] in canonical_of:
                    continue
                if other["category"] != row["category"]:
                    continue
                a, b = vectors[row["id"]], vectors[other["id"]]
                cosine = sum(x * y for x, y in zip(a, b))
                if cosine >= args.threshold:
                    canonical_of[other["id"]] = row["id"]
                    merges.append((other["id"], row["id"], cosine))

        print(f"\nnear-duplicates at cosine >= {args.threshold}: {len(merges)}")
        for duplicate, canonical, cosine in merges[: args.show]:
            dup_text = next(r["content"] for r in rows if r["id"] == duplicate)
            can_text = next(r["content"] for r in rows if r["id"] == canonical)
            print(f"  {cosine:.3f}  m_{duplicate} -> m_{canonical}")
            print(f"          dup: {dup_text[:72]}")
            print(f"          keep: {can_text[:72]}")
        if len(merges) > args.show:
            print(f"  … and {len(merges) - args.show} more")

        if not args.apply:
            print(
                "\nDRY RUN — nothing written. Re-run with --apply to re-embed, "
                "and add --merge-duplicates to close the rows listed above."
            )
            return 0

        # --- rewrite the column --------------------------------------------
        print("\nrewriting the vector column…")
        async with pool.acquire() as connection:
            async with connection.transaction():
                await connection.execute(
                    "DROP INDEX IF EXISTS memory_vectors_embedding_idx"
                )
                await connection.execute(
                    f"ALTER TABLE memory_vectors "
                    f"ADD COLUMN embedding_new vector({settings.embedding_dim})"
                )
                for row_id, vector in vectors.items():
                    await connection.execute(
                        "UPDATE memory_vectors SET embedding_new = $2::vector "
                        "WHERE id = $1",
                        row_id,
                        to_pgvector(vector),
                    )
                await connection.execute(
                    "ALTER TABLE memory_vectors DROP COLUMN embedding"
                )
                await connection.execute(
                    "ALTER TABLE memory_vectors "
                    "RENAME COLUMN embedding_new TO embedding"
                )
                await connection.execute(
                    "ALTER TABLE memory_vectors ALTER COLUMN embedding SET NOT NULL"
                )
                await connection.execute(
                    "CREATE INDEX memory_vectors_embedding_idx ON memory_vectors "
                    "USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)"
                )
        print(f"  re-embedded {len(vectors)} rows at {settings.embedding_dim} dims")

        if args.merge_duplicates and merges:
            print("\nmerging duplicates…")
            async with pool.acquire() as connection:
                async with connection.transaction():
                    for duplicate, canonical, _ in merges:
                        await connection.execute(
                            "UPDATE memory_vectors "
                            "SET valid_at = now(), superseded_by = $2 WHERE id = $1",
                            duplicate,
                            canonical,
                        )
                        await connection.execute(
                            "UPDATE memory_vectors "
                            "SET access_count = access_count + 1 WHERE id = $1",
                            canonical,
                        )
            remaining = await pool.fetchval(
                "SELECT count(*) FROM memory_vectors WHERE valid_at IS NULL"
            )
            print(f"  closed {len(merges)}; {remaining} preferences still open")
        elif args.merge_duplicates:
            print("\nno duplicates to merge")

        print("\nRemember to set the same provider/dim for the API and MCP server,")
        print("or they will fail the startup width check.")
        return 0
    finally:
        await pool.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.reembed")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--merge-duplicates", action="store_true")
    parser.add_argument("--threshold", type=float, default=0.80)
    parser.add_argument("--show", type=int, default=12)
    args = parser.parse_args()
    if args.apply and args.dry_run:
        print("pick one of --apply / --dry-run", file=sys.stderr)
        return 2
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
