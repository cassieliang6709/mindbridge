"""Stage two: QLoRA fine-tune of Qwen2.5-7B on the captured pairs (Unsloth).

Runs on a rented CUDA GPU — Colab T4/A100 or RunPod. It will NOT run on the Mac
this project is developed on: bitsandbytes 4-bit needs CUDA, and MPS is not a
substitute. That is why stage one uses a hosted API.

    # on the GPU box, after copying train/dataset/ across
    pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
    pip install --no-deps trl peft accelerate bitsandbytes
    python -m train.train_qlora --epochs 2

A 7B model in 4-bit needs roughly 16 GB of VRAM to train at seq_len 4096. On a
16 GB T4 keep --max-seq-length at 2048 and --batch-size at 1.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TRAIN_FILE = Path("train/dataset/train.jsonl")
OUTPUT_DIR = Path("train/outputs/qwen2.5-7b-mindbridge-qlora")

BASE_MODEL = "unsloth/Qwen2.5-7B-Instruct-bnb-4bit"


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        raise SystemExit(
            f"{path} not found. Run: python -m train.prepare_dataset"
        )
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def to_chat(row: dict) -> dict:
    """One pair as a chat sequence.

    The target is the compact JSON of the validated object — no fence, no
    prose. Training on the teacher's raw text would teach the student to
    reproduce its fences and its occasional schema misses.
    """
    completion = json.dumps(row["completion"], ensure_ascii=False)
    return {"messages": [*row["messages"], {"role": "assistant", "content": completion}]}


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m train.train_qlora")
    parser.add_argument("--base-model", default=BASE_MODEL)
    parser.add_argument("--epochs", type=float, default=2.0)
    parser.add_argument("--max-seq-length", type=int, default=4096)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=2e-4)
    parser.add_argument("--lora-r", type=int, default=16)
    parser.add_argument("--output", type=Path, default=OUTPUT_DIR)
    parser.add_argument(
        "--merge-16bit",
        action="store_true",
        help="Also save a merged fp16 copy, which is what vLLM serves.",
    )
    args = parser.parse_args()

    rows = load_rows(TRAIN_FILE)
    print(f"{len(rows)} training pairs")
    if len(rows) < 200:
        print(
            "WARNING: under 200 pairs. Expect overfitting; treat any holdout "
            "number from this run as provisional, and do not quote it."
        )

    # Imported here so --help works on a machine without CUDA.
    from datasets import Dataset  # type: ignore[import-not-found]
    from trl import SFTConfig, SFTTrainer  # type: ignore[import-not-found]
    from unsloth import FastLanguageModel  # type: ignore[import-not-found]

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.base_model,
        max_seq_length=args.max_seq_length,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=args.lora_r,
        lora_alpha=args.lora_r * 2,
        lora_dropout=0.0,
        bias="none",
        # Attention and MLP projections. Restricting to attention only saves
        # little memory here and measurably hurts JSON-shape adherence.
        target_modules=[
            "q_proj",
            "k_proj",
            "v_proj",
            "o_proj",
            "gate_proj",
            "up_proj",
            "down_proj",
        ],
        use_gradient_checkpointing="unsloth",
        random_state=3407,
    )

    dataset = Dataset.from_list([to_chat(row) for row in rows])

    def formatting(batch: dict) -> list[str]:
        return [
            tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=False
            )
            for messages in batch["messages"]
        ]

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        formatting_func=formatting,
        args=SFTConfig(
            per_device_train_batch_size=args.batch_size,
            gradient_accumulation_steps=args.grad_accum,
            num_train_epochs=args.epochs,
            learning_rate=args.learning_rate,
            logging_steps=5,
            optim="adamw_8bit",
            warmup_ratio=0.05,
            lr_scheduler_type="linear",
            seed=3407,
            output_dir=str(args.output),
            report_to="none",
            max_seq_length=args.max_seq_length,
        ),
    )
    trainer.train()

    args.output.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(args.output))
    tokenizer.save_pretrained(str(args.output))
    print(f"adapter saved to {args.output}")

    if args.merge_16bit:
        merged = args.output.with_name(args.output.name + "-merged")
        model.save_pretrained_merged(
            str(merged), tokenizer, save_method="merged_16bit"
        )
        print(f"merged fp16 saved to {merged}")
        print(f"serve it:  vllm serve {merged} --max-model-len {args.max_seq_length}")

    print(
        "\nNext: evaluate on the holdout set and only then quote a number:\n"
        "    python -m train.eval_holdout --endpoint http://localhost:8000/v1 "
        f"--model {args.output.name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
