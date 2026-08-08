"""Rebuild T2 narratives and T3 preferences from the captured dataset.

    .venv/bin/python -m scripts.replay_extractions --dry-run
    .venv/bin/python -m scripts.replay_extractions --apply

Every extraction ever made was written to train/dataset/extraction.jsonl before
it touched the database, so that file — not Postgres — is the durable record.
This replays it: no model is called, nothing is re-generated, and the result is
byte-identical prose to what the model originally produced.

Two reasons this exists beyond disaster recovery:

- The database can be rebuilt from scratch (transcripts -> ingest -> replay)
  without spending a single API call, which makes the whole store disposable.
- Replaying through upsert_preference means preferences land under whatever
  embedder and dedup threshold are configured NOW. Rows first written under the
  hashing embedder, which never deduplicated, come back deduplicated.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path

from api.models import NarrativeUpdate, UpsertPreferenceRequest
from api.service import MemoryService
from api.settings import get_settings

DATASET = Path("train/dataset/extraction.jsonl")


def load_pairs() -> list[dict]:
    if not DATASET.exists():
        return []
    rows = []
    with DATASET.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    # Later extractions for the same card win.
    by_key: dict[tuple[str, str | None], dict] = {}
    for row in rows:
        by_key[(row["date"], row.get("session_id"))] = row
    return list(by_key.values())


async def run(args: argparse.Namespace) -> int:
    pairs = load_pairs()
    if not pairs:
        print(f"no pairs at {DATASET}")
        return 1

    print(f"{len(pairs)} unique extraction(s) to replay")
    if args.dry_run:
        days = sum(1 for p in pairs if p.get("session_id") is None)
        prefs = sum(len(p["completion"].get("preferences", [])) for p in pairs)
        print(f"  {days} day card(s), {len(pairs) - days} session card(s)")
        print(f"  {prefs} preference(s) would go through upsert_preference")
        print("\nDRY RUN — nothing written.")
        return 0

    settings = get_settings()
    service = await MemoryService.start(settings)
    actions: Counter[str] = Counter()
    missing = 0

    try:
        for pair in pairs:
            draft = pair["completion"]
            meta = pair.get("meta", {})
            updated = await service.summaries.set_narrative(
                NarrativeUpdate(
                    period=pair["date"],
                    session_id=pair.get("session_id"),
                    narrative=draft["narrative"],
                    highlights=draft.get("highlights", []),
                    open_threads=draft.get("open_threads", []),
                    generated_by=(
                        f"{meta.get('provider', 'unknown')}:"
                        f"{meta.get('model', 'unknown')}"
                    ),
                    model=meta.get("model", "unknown"),
                )
            )
            if updated is None:
                # The card no longer exists — its session may have aged out of
                # the transcripts. The pair stays in the dataset regardless.
                missing += 1
                continue
            actions["narratives"] += 1

            for preference in draft.get("preferences", []):
                if preference.get("confidence", 0) < args.min_confidence:
                    actions["skipped_low_confidence"] += 1
                    continue
                outcome = await service.upsert_preference(
                    UpsertPreferenceRequest(
                        content=preference["content"],
                        category=preference.get("category", "other"),
                    )
                )
                actions[outcome.action] += 1

        print("\nreplayed:")
        for key, count in sorted(actions.items()):
            print(f"  {key:24} {count}")
        if missing:
            print(f"  {'cards not found':24} {missing}")

        open_now = await service.vectors.count()
        print(f"\nT3 now holds {open_now} preference(s)")
        print(f"embedder: {service.embedder.name} / {settings.embedding_model}")
        print(f"dedup threshold: {settings.dedup_threshold}")
        return 0
    finally:
        await service.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m scripts.replay_extractions")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--min-confidence", type=float, default=0.6)
    args = parser.parse_args()
    if not (args.apply or args.dry_run):
        args.dry_run = True
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
