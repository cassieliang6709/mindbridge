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

中文说明：第二阶段评估,用于测量简历引用的两个数字。``extractionJsonAccuracy``
是留出集上第一次回复通过 ``DiaryDraft`` 校验的比例,与第一阶段教师模型的定义相同;
repair 的结果另行统计,绝不混入。``localExtractionCostDelta`` 用相同留出日的实测
token 和 GPU 时间换算成本,本地模型并非免费。只有 ``--write-results`` 才能写入
落地页数据,且小于 ``--min-holdout``(默认 30)时必须拒绝写入:极少样本的比例误差
远大于数字本身,不能被手填或当作可公开的指标。
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


def validate_reply(text: str) -> tuple[bool, str | None]:
    """Does this reply satisfy the extraction contract? The metric's definition.

    Lives here, and is imported by every evaluator (see train/eval_mlx.py),
    because "extractionJsonAccuracy" only means something if the teacher and the
    tuned model are judged by byte-identical rules. A second copy of this
    function is how a comparison quietly stops being a comparison.

    A code fence is stripped before validating — that is formatting, not a
    schema failure, and it is the same allowance stage one made for the teacher.

    中文：这是抽取指标的唯一 schema 判定标准,所有评估器都从这里导入它,避免教师
    与微调模型的衡量标准悄然漂移。代码围栏只是格式,并非 schema 失败,因此会先移除;
    与第一阶段对教师模型的宽容规则一致。

    Args:
        text: Raw reply returned by the model. 模型返回的原始回复。

    Returns:
        ``(valid, error_message)``: validity against ``DiaryDraft`` after
        code-fence handling, plus validation details on failure.
        ``(valid, error_message)``: 去除代码围栏后是否满足 ``DiaryDraft``,
        失败时附带校验详情。
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.startswith("json"):
            stripped = stripped[4:]

    try:
        DiaryDraft.model_validate_json(stripped)
    except ValidationError as error:
        return False, "; ".join(
            f"{'.'.join(str(p) for p in item['loc']) or '(root)'}: {item['msg']}"
            for item in error.errors()
        )
    return True, None


async def _one(
    client: httpx.AsyncClient,
    endpoint: str,
    model: str,
    row: dict,
) -> dict:
    """Evaluate one holdout pair through an OpenAI-compatible endpoint.

    中文：通过 OpenAI 兼容接口评估一条留出数据。

    Args:
        client: HTTP client used for the request. 发起请求的 HTTP 客户端。
        endpoint: Model API base URL. 模型 API 基础 URL。
        model: Model identifier. 模型标识符。
        row: Captured holdout pair. 采集的留出数据。

    Returns:
        Per-row validity, token, and timing measurements. 单条的合规、token 与耗时数据。
    """
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

    valid, errors = validate_reply(text)

    return {
        "date": row["date"],
        "valid": valid,
        "errors": errors,
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "seconds": round(time.perf_counter() - started, 3),
    }


async def run(args: argparse.Namespace) -> int:
    """Evaluate every holdout row and optionally publish measured metrics.

    中文：评估所有留出集行,并在满足保护条件时按需发布实测指标。

    Args:
        args: Parsed CLI flags. 解析后的命令行参数。

    Returns:
        Zero on successful evaluation, one for missing input, or two when a
        requested publication fails the minimum-holdout guard.
        评估成功返回 0;缺少输入返回 1;请求发布但未满足最小留出集保护时返回 2。
    """
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
        # Small samples can look perfect by chance. Metrics that feed a public
        # page must be measured, not manually supplied or rushed into existence.
        # 中文：小样本可能偶然显示完美分数。进入公开页面的指标必须实测,不能手填,
        # 也不能为了尽早出现数字而绕过最小留出集保护。
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
    """Parse CLI flags and run holdout evaluation.

    中文：解析命令行参数并运行留出集评估。

    Returns:
        Process exit code. 进程退出码。
    """
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
