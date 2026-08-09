"""Stage two evaluation, Apple silicon path: holdout accuracy for a local MLX model.

    # the tuned adapter
    python -m train.eval_mlx --model mlx-community/Qwen2.5-3B-Instruct-4bit \\
        --adapter train/outputs/mlx-adapters

    # the same model with no adapter, to see what the fine-tune actually bought
    python -m train.eval_mlx --model mlx-community/Qwen2.5-3B-Instruct-4bit \\
        --baseline

Reports extractionJsonAccuracy under exactly the definition eval_holdout.py
uses for the hosted teacher: the share of holdout pairs whose FIRST reply
validates against DiaryDraft. The check itself is imported from
train.eval_holdout.validate_reply rather than reimplemented, so the two numbers
cannot drift apart. There is no repair loop here — a repaired reply is a
different metric and is never folded into this one.

The teacher's rate on the *same* holdout pairs is read out of the captured
meta.first_attempt_valid flags and printed alongside, because the only honest
comparison is on identical days.

This script never writes evals/results.json. That file is the landing page's
source and belongs to eval_holdout.py, which serves an OpenAI-compatible
endpoint and has the --min-holdout guard. A number measured on this Mac gets
reported in prose, by a human who saw it happen.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from train.eval_holdout import validate_reply
from train.train_mlx import DEFAULT_MODEL, fit_user_turn, split_pairs


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m train.eval_mlx")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--adapter",
        type=Path,
        default=None,
        help="Adapter directory from train_mlx. Omit (or --baseline) for the "
        "untuned model.",
    )
    parser.add_argument(
        "--baseline",
        action="store_true",
        help="Ignore --adapter and evaluate the base model.",
    )
    parser.add_argument("--holdout-frac", type=float, default=0.2)
    parser.add_argument(
        "--max-prompt-tokens",
        type=int,
        default=4096,
        help="Truncate long prompts the same way training did. Keep this equal "
        "to the --max-seq-length used for training or the model is being "
        "tested on inputs it never saw the shape of.",
    )
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument(
        "--temp",
        type=float,
        default=0.2,
        help="Matches eval_holdout's temperature for the hosted teacher.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=3407,
        help="MLX sampling seed so the reported holdout run is reproducible.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Evaluate only the first N holdout pairs. For smoke tests.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Optional JSON file for the per-row detail. Never results.json.",
    )
    args = parser.parse_args()

    if args.out and args.out.resolve() == Path("evals/results.json").resolve():
        raise SystemExit("refusing to write evals/results.json from this script")

    _, holdout = split_pairs(args.holdout_frac)
    if not holdout:
        raise SystemExit("holdout set is empty; run stage one to capture pairs")
    if args.limit:
        holdout = holdout[: args.limit]

    import mlx.core as mx
    from mlx_lm import generate, load
    from mlx_lm.sample_utils import make_sampler

    mx.random.seed(args.seed)
    adapter = None if args.baseline else args.adapter
    label = "base model" if adapter is None else f"adapter {adapter}"
    print(f"loading {args.model} ({label})")
    model, tokenizer = load(
        args.model, adapter_path=str(adapter) if adapter else None
    )
    sampler = make_sampler(temp=args.temp)

    print(f"evaluating {len(holdout)} holdout pair(s)\n")
    results = []
    truncated = 0
    for index, row in enumerate(holdout, start=1):
        messages = [dict(m) for m in row["messages"]]
        user_positions = [i for i, m in enumerate(messages) if m["role"] == "user"]
        if user_positions:
            last = user_positions[-1]
            fixed = sum(
                len(tokenizer.encode(m["content"]))
                for m in messages
                if m["role"] != "user"
            )
            budget = args.max_prompt_tokens - fixed - 64
            original = messages[last]["content"]
            fitted = fit_user_turn(original, max(budget, 1), tokenizer)
            if fitted is None:
                fitted = original
            if fitted != original:
                truncated += 1
            messages[last]["content"] = fitted

        prompt = tokenizer.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False
        )
        started = time.perf_counter()
        text = generate(
            model,
            tokenizer,
            prompt=prompt,
            max_tokens=args.max_tokens,
            sampler=sampler,
            verbose=False,
        )
        seconds = time.perf_counter() - started

        valid, errors = validate_reply(text)
        teacher_valid = bool(row.get("meta", {}).get("first_attempt_valid"))
        results.append(
            {
                "date": row["date"],
                "valid": valid,
                "errors": errors,
                "teacher_first_attempt_valid": teacher_valid,
                "output_tokens": len(tokenizer.encode(text)),
                "seconds": round(seconds, 2),
            }
        )
        mark = "ok  " if valid else "FAIL"
        print(f"  [{index}/{len(holdout)}] {mark} {row['date']}  {seconds:.1f}s")
        if not valid:
            print(f"        {errors}")

    valid_count = sum(1 for r in results if r["valid"])
    accuracy = valid_count / len(results)
    teacher_count = sum(1 for r in results if r["teacher_first_attempt_valid"])
    wall = sum(r["seconds"] for r in results)
    out_tokens = sum(r["output_tokens"] for r in results)

    print(f"\nextractionJsonAccuracy ({label}, first attempt, no repair)")
    print(f"  local:   {valid_count}/{len(results)} ({accuracy:.1%})")
    print(
        f"  teacher: {teacher_count}/{len(results)} "
        f"({teacher_count / len(results):.1%}) on the same pairs"
    )
    if truncated:
        print(
            f"\n{truncated}/{len(results)} prompt(s) were truncated to "
            f"{args.max_prompt_tokens} tokens, same as training."
        )
    print(
        f"\n{out_tokens} output tokens in {wall:.0f}s "
        f"({out_tokens / wall:.1f} tok/s aggregate) on this machine"
    )
    print(
        "\nThis writes nothing. evals/results.json is written only by "
        "train.eval_holdout --write-results."
    )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(
                {
                    "model": args.model,
                    "adapter": str(adapter) if adapter else None,
                    "pairs": len(results),
                    "first_attempt_valid": valid_count,
                    "extractionJsonAccuracy": f"{accuracy:.1%}",
                    "teacher_first_attempt_valid": teacher_count,
                    "max_prompt_tokens": args.max_prompt_tokens,
                    "prompts_truncated": truncated,
                    "seed": args.seed,
                    "temperature": args.temp,
                    "wall_seconds": round(wall, 1),
                    "per_row": results,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"detail written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
