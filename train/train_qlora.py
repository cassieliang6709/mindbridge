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

中文说明：在租用的 CUDA GPU 上用 Unsloth 对 Qwen2.5-7B 做 QLoRA 微调。4-bit
bitsandbytes 依赖 CUDA,因此本机 Mac/MPS 不能运行这条路径。7B 模型在 4-bit、4096
序列长度下约需 16 GB 显存;16 GB T4 应保持较短序列和 batch size 1。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

TRAIN_FILE = Path("train/dataset/train.jsonl")
OUTPUT_DIR = Path("train/outputs/qwen2.5-7b-mindbridge-qlora")

BASE_MODEL = "unsloth/Qwen2.5-7B-Instruct-bnb-4bit"


def load_rows(path: Path) -> list[dict]:
    """Load JSONL training rows or explain the missing preparation step.

    中文：加载 JSONL 训练行;文件缺失时说明需要先执行的数据准备步骤。

    Args:
        path: Training JSONL path. 训练 JSONL 路径。

    Returns:
        One parsed dictionary per JSONL line. 每行对应一个解析后的字典。
    """
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

    中文：把采集数据转换成 chat 训练记录。目标只用通过校验的紧凑 JSON,不使用教师
    的原始回复,以免学生复制代码围栏、闲聊或偶发的 schema 错误。

    Args:
        row: Captured pair with messages and validated completion. 包含消息和
            已校验 completion 的采集数据。

    Returns:
        Chat record ending with the assistant JSON answer. 以 assistant JSON
        答案结尾的 chat 记录。
    """
    completion = json.dumps(row["completion"], ensure_ascii=False)
    return {"messages": [*row["messages"], {"role": "assistant", "content": completion}]}


def main() -> int:
    """Configure and run end-to-end Unsloth QLoRA fine-tuning.

    中文：配置并端到端运行 Unsloth QLoRA 微调。

    Returns:
        Zero after successful training; failures propagate or exit directly.
        训练成功返回 0;失败会直接退出或向上传播。
    """
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
        # A small training sample can overfit and make any holdout result look
        # better than it is; keep this warning aligned with the evaluation guard.
        # 中文：很小的训练集会过拟合,让留出集结果看起来优于真实能力;该警告与评估
        # 的最小样本保护保持一致。
        print(
            "WARNING: under 200 pairs. Expect overfitting; treat any holdout "
            "number from this run as provisional, and do not quote it."
        )

    # Imported here so --help works on a machine without CUDA.
    # 中文：延迟导入使没有 CUDA 的机器也能正常查看 ``--help``。
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
        # 中文：同时覆盖注意力和 MLP 投影。只训注意力层省不了多少显存,却会明显损害
        # JSON 结构遵循能力。
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
