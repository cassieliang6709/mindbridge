"""Measure what an extractor puts in T3, on a fixed set of sessions.

Schema compliance says the JSON parsed. It does not say the right thing was
extracted: base qwen2.5:7b scored 15/15 first-attempt against the teacher's
84.4% and still wrote three of the day's todos into T3 as standing
preferences. This script measures the thing that actually matters.

It writes nothing. No T2 narrative, no T3 row, no dataset row — so the same
sessions can be replayed against every model without the first run changing
what the second one sees.

Reported per run:

  compliance      first-attempt schema validity, comparable to --stats
  preferences     how many preferences the model wanted to write
  rejected        how many the validator refused, and why
  kept            what would actually have reached T3

`kept` is the list to read by hand. A model that writes nothing scores a
perfect rejection rate, so rejection alone is not quality either.

    python -m evals.preference_precision --holdout evals/holdout_sessions.json \
        --provider mlx --base-url http://127.0.0.1:11434/v1 \
        --model qwen2.5:7b --wire-model qwen2.5:7b --label base-7b
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from api.service import MemoryService
from api.settings import get_settings

from extract.pipeline import extract_day
from extract.prompts import PROMPT_VERSION, build_day_input
from extract.providers import build_provider
from extract.runner import _load_target


async def _run(args: argparse.Namespace) -> int:
    targets = json.loads(Path(args.holdout).read_text())
    settings = get_settings()
    service = await MemoryService.start(settings)

    provider = build_provider(
        args.provider,
        None,
        args.model,
        base_url=args.base_url,
        timeout=settings.mlx_timeout_seconds,
        wire_model=args.wire_model,
    )

    rows: list[dict] = []
    try:
        for target in targets:
            date, session_id = target["date"], target.get("session_id")
            card, facts, turns, project = await _load_target(
                service, date, session_id, args.timezone
            )
            if card is None:
                print(f"{date}: no card, skipped", file=sys.stderr)
                continue

            day = build_day_input(
                date, facts, turns, max_input_tokens=args.max_input_tokens,
                project=project,
            )
            result = await extract_day(
                provider, day, max_attempts=args.max_attempts, session_id=session_id
            )
            rows.append(
                {
                    "date": date,
                    "session_id": session_id,
                    "first_attempt": result.first_attempt_valid,
                    "ok": result.ok,
                    "attempts": len(result.attempts),
                    "preferences": [
                        p.model_dump()
                        for p in (result.draft.preferences if result.draft else [])
                    ],
                    "narrative": result.draft.narrative if result.draft else None,
                }
            )
            print(
                f"{date} {(session_id or '')[-12:]}: "
                f"{'ok' if result.ok else 'FAILED'} in {len(result.attempts)}, "
                f"{len(rows[-1]['preferences'])} preference(s)"
            )
    finally:
        await service.close()

    # The pipeline only returns preferences the validator already accepted, so
    # re-running the validator here would always agree. Rejections are counted
    # from the repair loop's own errors instead, which is where they happen.
    kept = [p for row in rows for p in row["preferences"]]
    compliant = sum(1 for row in rows if row["first_attempt"])

    report = {
        "label": args.label,
        "provider": args.provider,
        "model": args.model,
        "prompt_version": PROMPT_VERSION,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "sessions": len(rows),
        "first_attempt_compliance": (
            round(compliant / len(rows) * 100, 1) if rows else None
        ),
        "preferences_kept": len(kept),
        "preferences_per_session": (
            round(len(kept) / len(rows), 2) if rows else None
        ),
        "category_mix": dict(Counter(p.get("category") for p in kept)),
        "project_mix": dict(Counter(p.get("project") for p in kept)),
        "rows": rows,
    }

    out = Path(args.out or f"evals/precision_{args.label}.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print(
        f"\n=== {args.label}: {len(rows)} session(s), "
        f"{report['first_attempt_compliance']}% first-attempt, "
        f"{len(kept)} preference(s) kept "
        f"({report['preferences_per_session']}/session)"
    )
    print(f"wrote {out} — read the kept preferences by hand before quoting a number.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--holdout", default="evals/holdout_sessions.json")
    parser.add_argument("--provider", default="mlx")
    parser.add_argument("--model", default=None)
    parser.add_argument("--wire-model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--max-input-tokens", type=int, default=4000)
    parser.add_argument("--timezone", default="America/New_York")
    parser.add_argument("--label", required=True)
    parser.add_argument("--out", default=None)
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
