"""Stage two evaluation: the two numbers the résumé quotes, measured.

    # against a vLLM server holding the tuned model
    python -m train.eval_holdout --endpoint http://localhost:8000/v1 \\
        --model qwen2.5-7b-mindbridge-qlora --write-results

Produces:

- extractionJsonAccuracy — share of holdout days where the model's FIRST reply
  validates against DiaryDraft. Same definition used for the teacher in stage
  one, so the two are comparable. Repairs are reported separately and never
  folded into this number.
- localExtractionCostDelta — measured token counts on both sides priced out:
  hosted API cost for the same holdout days versus the hourly cost of the GPU
  serving them. A local model is not free, and pretending otherwise is how a
  "90% cheaper" claim falls apart under questioning.

Writes into evals/results.json only with --write-results, so a number cannot
reach the landing page by accident.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import ValidationError

from extract.schemas import DiaryDraft
from train.prepare_dataset import HOLDOUT_OUT

RESULTS = Path("evals/results.json")

# Hosted list prices per 1M tokens, for the comparison side.
HOSTED_PRICES = {"gpt-4o-mini": (0.15, 0.60), "gemini-2.5-flash": (0.30, 2.50)}


async def _one(
    client: httpx.AsyncClient,
    endpoint: str,
    model: str,
    row: dict,
) -> dict:
    started = time.perf_counter()
    response = await client.post(
        f"{endpoint.rstrip('/')}/chat/completions",
        json={
            "model": model,
            "messages": row["messages"],
            "temperature": 0.2,
            "max_tokens": 1200,
        },
    )
    response.raise_for_status()
    payload = response.json()
    text = payload["choices"][0]["message"]["content"]
    usage = payload.get("usage") or {}

    # Strip a fence before validating: formatting, not a schema failure. Same
    # rule stage one applied to the teacher.
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]

    try:
        DiaryDraft.model_validate_json(stripped)
        valid, errors = True, None
    except ValidationError as error:
        valid = False
        errors = "; ".join(
            f"{'.'.join(str(p) for p in item['loc']) or '(root)'}: {item['msg']}"
            for item in error.errors()
        )

    return {
        "date": row["date"],
        "valid": valid,
        "errors": errors,
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "seconds": round(time.perf_counter() - started, 3),
    }


async def run(args: argparse.Namespace) -> int:
    if not HOLDOUT_OUT.exists():
        print(f"{HOLDOUT_OUT} not found. Run: python -m train.prepare_dataset")
        return 1
    rows = [
        json.loads(line)
        for line in HOLDOUT_OUT.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        print("holdout set is empty")
        return 1

    print(f"evaluating {len(rows)} holdout day(s) against {args.model}")
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        results = []
        for row in rows:
            try:
                results.append(await _one(client, args.endpoint, args.model, row))
            except Exception as error:  # noqa: BLE001 - report and keep going
                results.append(
                    {
                        "date": row["date"],
                        "valid": False,
                        "errors": f"request failed: {error}",
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "seconds": 0.0,
                    }
                )

    valid = sum(1 for r in results if r["valid"])
    accuracy = valid / len(results)
    input_tokens = sum(r["input_tokens"] for r in results)
    output_tokens = sum(r["output_tokens"] for r in results)
    wall_seconds = sum(r["seconds"] for r in results)

    hosted_price = HOSTED_PRICES.get(args.compare_model)
    hosted_cost = (
        input_tokens / 1e6 * hosted_price[0] + output_tokens / 1e6 * hosted_price[1]
        if hosted_price
        else None
    )
    # The local side is GPU rental for the wall time actually spent serving.
    local_cost = wall_seconds / 3600 * args.gpu_hourly

    print(f"\nfirst-attempt schema valid: {valid}/{len(results)} ({accuracy:.1%})")
    for result in results:
        if not result["valid"]:
            print(f"  FAILED {result['date']}: {result['errors']}")
    print(f"tokens: {input_tokens} in / {output_tokens} out")
    print(f"wall time: {wall_seconds:.1f}s on a ${args.gpu_hourly:.2f}/h GPU")
    if hosted_cost is not None:
        print(
            f"cost for the same work: hosted ${hosted_cost:.4f} vs "
            f"local ${local_cost:.4f}"
        )
        if hosted_cost > 0:
            delta = (hosted_cost - local_cost) / hosted_cost
            print(f"delta: {delta:+.1%} (negative means local was more expensive)")

    if not args.write_results:
        print(
            "\nNothing written. Re-run with --write-results to publish these to "
            "evals/results.json (and therefore to the landing page)."
        )
        return 0

    if len(rows) < args.min_holdout:
        print(
            f"\nREFUSING TO WRITE: {len(rows)} holdout day(s) is below "
            f"--min-holdout {args.min_holdout}. A rate over a handful of days "
            "has an error bar wider than the number itself, and it would go "
            "straight onto a public page. Collect more days first."
        )
        return 2

    payload = json.loads(RESULTS.read_text(encoding="utf-8"))
    payload["generatedAt"] = datetime.now(timezone.utc).isoformat()
    try:
        payload["commit"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - a missing git is not a failure here
        payload["commit"] = None
    payload["metrics"]["extractionJsonAccuracy"] = f"{accuracy:.1%}"
    if hosted_cost is not None and hosted_cost > 0:
        delta = (hosted_cost - local_cost) / hosted_cost
        payload["metrics"]["localExtractionCostDelta"] = f"{delta:+.0%}"
    payload["holdout"] = {
        "days": len(rows),
        "model": args.model,
        "compared_against": args.compare_model,
        "gpu_hourly_usd": args.gpu_hourly,
        "first_attempt_valid": valid,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "wall_seconds": round(wall_seconds, 1),
        "per_day": results,
    }
    RESULTS.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {RESULTS}. The landing page will now show these numbers.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m train.eval_holdout")
    parser.add_argument("--endpoint", default="http://localhost:8000/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument(
        "--compare-model",
        default="gpt-4o-mini",
        help="Hosted model to price the same work against.",
    )
    parser.add_argument(
        "--gpu-hourly",
        type=float,
        default=0.34,
        help="GPU rental $/hour for the local side (default: RunPod A10G-ish).",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--min-holdout",
        type=int,
        default=30,
        help="Refuse to publish a rate computed on fewer days than this.",
    )
    parser.add_argument(
        "--write-results",
        action="store_true",
        help="Write the measured numbers into evals/results.json.",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
