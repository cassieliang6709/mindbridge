"""Stage two, Apple silicon path: LoRA fine-tune with mlx-lm on this Mac.

`train_qlora.py` is the CUDA path and says so in its own docstring — bitsandbytes
4-bit needs CUDA and will not run on an M1. This module is the local equivalent:
it fine-tunes an already-4-bit MLX checkpoint (`mlx-community/*-4bit`) on the
same captured pairs, on the same date-based split, so the holdout number it
produces is comparable to the teacher's and to the CUDA run.

    # 1. convert the pairs into the layout mlx-lm expects, and stop
    python -m train.train_mlx --prepare-only

    # 2. convert and train
    python -m train.train_mlx --model mlx-community/Qwen2.5-3B-Instruct-4bit

    # 3. evaluate — the number only exists after this
    python -m train.eval_mlx --model mlx-community/Qwen2.5-3B-Instruct-4bit \\
        --adapter train/outputs/mlx-adapters

Two things about this data that the defaults get wrong, and that cost a run each
if ignored:

TRUNCATION DIRECTION. mlx-lm's batcher truncates any over-length sequence from
the RIGHT (`tuner/trainer.py`: `batch[j][:truncated_length]`). Our sequences are
a short system prompt, a very long transcript, then the JSON answer — so
right-truncation deletes exactly the tokens we are trying to teach, and the run
silently learns nothing. This module therefore pre-truncates the TRANSCRIPT
SAMPLE section of the user turn, middle-out, so the answer always survives. Rows
that still do not fit are dropped and counted rather than quietly mangled.

PROMPT MASKING. Without `--mask-prompt` the loss is dominated by reproducing
thousands of transcript tokens the model will always be given at inference time.
It is on by default here; `--no-mask-prompt` turns it off.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from train.prepare_dataset import HOLDOUT_OUT, TRAIN_OUT, _bucket, load_pairs

# mlx-lm requires train.jsonl and valid.jsonl to sit together in one directory.
# It is written fresh on every run and is derived data, so it stays out of
# train/dataset/, which is append-only capture owned by stage one.
MLX_DATA = Path("train/mlx_data")
OUTPUT_DIR = Path("train/outputs/mlx-adapters")

DEFAULT_MODEL = "mlx-community/Qwen2.5-3B-Instruct-4bit"

# The header the extraction prompt puts in front of the transcript. Everything
# before it (DATE, FACTS) is short and load-bearing, so truncation only ever
# eats into what follows.
TRANSCRIPT_MARKER = "TRANSCRIPT SAMPLE"
ELLIPSIS = "\n\n[... transcript truncated to fit the training window ...]\n\n"


def load_model_tokenizer(model: str):
    """Tokenizer for a local model directory or a Hugging Face repo id.

    Preparation needs the tokenizer and nothing else, so a local directory
    holding only the tokenizer files is enough to run --prepare-only while the
    weights are still downloading.
    """
    from mlx_lm.utils import load_tokenizer

    path = Path(model)
    if not path.exists():
        from huggingface_hub import snapshot_download

        path = Path(snapshot_download(model))
    return load_tokenizer(path)


def split_pairs(holdout_frac: float) -> tuple[list[dict], list[dict]]:
    """The same date-bucketed split prepare_dataset performs.

    Prefers the files prepare_dataset already wrote; falls back to computing the
    split in memory from extraction.jsonl so this script never has to write into
    train/dataset/ itself.
    """
    if TRAIN_OUT.exists() and HOLDOUT_OUT.exists():
        def read(path: Path) -> list[dict]:
            return [
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        return read(TRAIN_OUT), read(HOLDOUT_OUT)

    import hashlib

    pairs = load_pairs()
    by_key: dict[tuple[str, str], dict] = {}
    for pair in pairs:
        session = pair.get("session_id")
        if session is None:
            prompt = pair["messages"][-1]["content"] if pair.get("messages") else ""
            session = hashlib.blake2b(prompt.encode(), digest_size=8).hexdigest()
        by_key[(pair["date"], session)] = pair
    holdout_dates = {date for date, _ in by_key if _bucket(date) < holdout_frac}
    train = [p for (d, _), p in sorted(by_key.items()) if d not in holdout_dates]
    holdout = [p for (d, _), p in sorted(by_key.items()) if d in holdout_dates]
    return train, holdout


def fit_user_turn(text: str, budget_tokens: int, tokenizer) -> str | None:
    """Shrink the transcript sample so the whole row fits in `budget_tokens`.

    Keeps the head and the tail of the transcript and drops the middle: the
    opening turns establish what the day was about and the closing turns say
    where it ended, while the middle is the most redundant part. Returns None if
    even the non-transcript preamble is over budget, which means the row cannot
    be trained on honestly and should be dropped.
    """
    if len(tokenizer.encode(text)) <= budget_tokens:
        return text

    marker = text.find(TRANSCRIPT_MARKER)
    if marker == -1:
        marker = 0
    preamble, body = text[:marker], text[marker:]
    if len(tokenizer.encode(preamble + ELLIPSIS)) > budget_tokens:
        return None

    room = budget_tokens - len(tokenizer.encode(preamble + ELLIPSIS))
    ids = tokenizer.encode(body)
    if len(ids) <= room:
        return preamble + body
    head, tail = ids[: room // 2], ids[-(room - room // 2) :]
    return preamble + tokenizer.decode(head) + ELLIPSIS + tokenizer.decode(tail)


def to_mlx_row(row: dict, max_seq_length: int, tokenizer) -> dict | None:
    """One captured pair as an mlx-lm `{"messages": [...]}` record.

    The assistant turn is the compact JSON of the *validated* object, never the
    teacher's raw text — training on the raw text would teach the student the
    teacher's code fences and its occasional schema misses. Same choice
    train_qlora.to_chat makes, kept identical so the two paths train on the same
    target.
    """
    completion = json.dumps(row["completion"], ensure_ascii=False)
    messages = [dict(m) for m in row["messages"]]

    # Reserve room for the answer plus the chat template's control tokens.
    overhead = len(tokenizer.encode(completion)) + 64
    fixed = sum(
        len(tokenizer.encode(m["content"])) for m in messages if m["role"] != "user"
    )
    budget = max_seq_length - overhead - fixed
    if budget <= 0:
        return None

    user_indices = [i for i, m in enumerate(messages) if m["role"] == "user"]
    if not user_indices:
        return None
    last_user = user_indices[-1]
    fitted = fit_user_turn(messages[last_user]["content"], budget, tokenizer)
    if fitted is None:
        return None
    messages[last_user]["content"] = fitted

    return {"messages": [*messages, {"role": "assistant", "content": completion}]}


def write_split(
    rows: list[dict], path: Path, max_seq_length: int, tokenizer
) -> tuple[int, int]:
    kept, dropped = 0, 0
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            record = to_mlx_row(row, max_seq_length, tokenizer)
            if record is None:
                dropped += 1
                continue
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            kept += 1
    return kept, dropped


def prepare(args: argparse.Namespace) -> tuple[int, int]:
    tokenizer = load_model_tokenizer(args.model)

    train_rows, holdout_rows = split_pairs(args.holdout_frac)
    if not train_rows:
        raise SystemExit(
            "no training pairs. Run stage one first, then "
            "python -m train.prepare_dataset"
        )

    # mlx-lm wants a validation file. The holdout is reserved for the reported
    # metric and must not steer training, so valid.jsonl is carved out of the
    # TRAIN side. Its only job is a loss curve to spot divergence.
    cut = max(1, int(len(train_rows) * args.valid_frac))
    valid_rows, fit_rows = train_rows[:cut], train_rows[cut:]

    MLX_DATA.mkdir(parents=True, exist_ok=True)
    kept_train, dropped_train = write_split(
        fit_rows, MLX_DATA / "train.jsonl", args.max_seq_length, tokenizer
    )
    kept_valid, dropped_valid = write_split(
        valid_rows, MLX_DATA / "valid.jsonl", args.max_seq_length, tokenizer
    )

    print(f"holdout reserved:   {len(holdout_rows)} pair(s) — not written here")
    print(f"train.jsonl:        {kept_train} kept, {dropped_train} dropped")
    print(f"valid.jsonl:        {kept_valid} kept, {dropped_valid} dropped")
    print(f"max_seq_length:     {args.max_seq_length}")
    if dropped_train + dropped_valid:
        print(
            f"  {dropped_train + dropped_valid} row(s) could not fit even after "
            "middle-out truncation and were dropped rather than mangled."
        )
    return kept_train, kept_valid


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m train.train_mlx")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--adapter-path", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--holdout-frac", type=float, default=0.2)
    parser.add_argument(
        "--valid-frac",
        type=float,
        default=0.15,
        help="Share of the TRAIN side used as mlx-lm's validation file.",
    )
    parser.add_argument(
        "--max-seq-length",
        type=int,
        default=4096,
        help=(
            "Tokens per example. Memory scales with this; 4096 is what fits "
            "alongside a 3B 4-bit model in 16 GB unified memory."
        ),
    )
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument(
        "--grad-accumulation-steps",
        type=int,
        default=4,
        help="Effective batch = batch-size x this, without the memory cost.",
    )
    parser.add_argument(
        "--num-layers",
        type=int,
        default=8,
        help=(
            "How many transformer blocks get LoRA adapters, counted from the "
            "top. Fewer layers means less optimiser state to hold."
        ),
    )
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument(
        "--epochs",
        type=float,
        default=2.0,
        help="Converted to --iters using the kept row count and effective batch.",
    )
    parser.add_argument(
        "--iters",
        type=int,
        default=None,
        help="Explicit iteration count; overrides --epochs.",
    )
    parser.add_argument("--steps-per-report", type=int, default=5)
    parser.add_argument("--steps-per-eval", type=int, default=50)
    parser.add_argument("--save-every", type=int, default=50)
    parser.add_argument("--val-batches", type=int, default=5)
    parser.add_argument("--seed", type=int, default=3407)
    parser.add_argument(
        "--no-mask-prompt",
        action="store_true",
        help="Train on the transcript tokens too. Usually a waste of the run.",
    )
    parser.add_argument(
        "--no-grad-checkpoint",
        action="store_true",
        help="Faster per step, and the main reason a run runs out of memory.",
    )
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()

    kept_train, _ = prepare(args)
    if args.prepare_only:
        print(f"\nwrote {MLX_DATA}/. Nothing trained.")
        return 0

    if kept_train == 0:
        raise SystemExit("no rows survived preparation; nothing to train on")
    if kept_train < 200:
        print(
            f"\nWARNING: {kept_train} training rows is under 200. Expect "
            "overfitting; treat any holdout number from this run as a smoke "
            "test, not a result, and do not quote it."
        )

    effective_batch = args.batch_size * args.grad_accumulation_steps
    iters = args.iters or max(
        1, int(round(args.epochs * kept_train / effective_batch))
    )

    command = [
        sys.executable, "-m", "mlx_lm", "lora",
        "--model", args.model,
        "--train",
        "--data", str(MLX_DATA),
        "--fine-tune-type", "lora",
        "--adapter-path", str(args.adapter_path),
        "--num-layers", str(args.num_layers),
        "--batch-size", str(args.batch_size),
        "--grad-accumulation-steps", str(args.grad_accumulation_steps),
        "--iters", str(iters),
        "--learning-rate", str(args.learning_rate),
        "--max-seq-length", str(args.max_seq_length),
        "--steps-per-report", str(args.steps_per_report),
        "--steps-per-eval", str(args.steps_per_eval),
        "--save-every", str(args.save_every),
        "--val-batches", str(args.val_batches),
        "--seed", str(args.seed),
    ]
    if not args.no_mask_prompt:
        command.append("--mask-prompt")
    if not args.no_grad_checkpoint:
        command.append("--grad-checkpoint")

    print(f"\n{iters} iters at effective batch {effective_batch} "
          f"(~{args.epochs:g} epoch(s) over {kept_train} rows)")
    print(" ".join(command) + "\n")

    started = time.perf_counter()
    result = subprocess.run(command)
    elapsed = time.perf_counter() - started
    if result.returncode != 0:
        print(f"\nmlx_lm.lora exited {result.returncode} after {elapsed:.0f}s")
        return result.returncode

    print(f"\nadapter saved to {args.adapter_path} in {elapsed:.0f}s")
    print(
        "\nNext, and only then is there a number to quote:\n"
        f"    python -m train.eval_mlx --model {args.model} "
        f"--adapter {args.adapter_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
