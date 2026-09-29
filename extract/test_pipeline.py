"""Offline tests for the extraction contract and the repair loop.

    python -m extract.test_pipeline

Uses ScriptedProvider, so the failure paths that matter — invalid JSON, extra
keys, an emotional claim, a transient "preference" — are exercised on demand
rather than hoped for against a live model. No key, no network, no spend.

中文说明：本文件是针对抽取契约和修复循环的离线测试，运行方式是
`python -m extract.test_pipeline`（不是 pytest/unittest，而是本文件自带的
一套轻量级检查工具，见下方 `check()` 和 `main()`）。测试使用 ScriptedProvider
按脚本返回预设回复，这样那些重要的失败路径——非法 JSON、多余字段、情绪化
断言、伪装成偏好的一次性任务——都可以按需触发，而不是寄希望于真实模型偶然
出现这些情况。全程不需要密钥、不联网、不花钱。
"""

from __future__ import annotations

import asyncio
import json

import httpx

from .pipeline import extract_day, training_pair
from .prompts import build_day_input
from .providers import (
    Message,
    MLXChatProvider,
    ScriptedProvider,
    _claude_cli_input_tokens,
    _claude_cli_messages,
)
from .schemas import DiaryDraft

# A minimal but fully schema-valid DiaryDraft, reused as the "good" fixture
# across every check below and mutated where a test needs an invalid variant.
# 中文：一份最小但完全符合 schema 的 DiaryDraft，作为下面所有检查共用的"合法"
# 基准数据；需要非法变体时会在它的基础上做修改。
VALID = {
    "narrative": (
        "You spent the day on the retrieval path, standing up the endpoint and "
        "clearing the errors it threw. You were still editing after 05:00."
    ),
    "highlights": ["Stood up /retrieve", "Cleared three 500s"],
    "preferences": [
        {
            "content": "Prefer uv over pip for Python projects",
            "category": "tool_preference",
            "confidence": 0.9,
            "evidence": "asked to switch package management from pip to uv",
        }
    ],
    "open_threads": ["Topological sort still unresolved"],
}


def _day():
    """Build a small, fixed DayInput used across the repair-loop checks below.

    中文：构造一个用于下方修复循环各项检查的、固定的小型 DayInput。

    Returns:
        A DayInput for a single fixed date with one user/assistant turn pair.
        一个固定日期、只有一对用户/助手发言的 DayInput。
    """
    return build_day_input(
        "2026-08-04",
        ["Tool calls: Bash x12"],
        [("user", "switch from pip to uv"), ("assistant", "done")],
    )


def check(name: str, condition: bool, detail: str = "") -> bool:
    """Print a PASS/FAIL line for one check and return whether it passed.

    This file is run directly (`python -m extract.test_pipeline`), not through
    pytest/unittest, so each check is a plain boolean folded into `ok` by the
    caller rather than a separate test function/assertion.

    中文：为一项检查打印一行 PASS/FAIL，并返回该检查是否通过。本文件是直接
    运行的（`python -m extract.test_pipeline`），不走 pytest/unittest，所以
    每一项检查只是一个布尔值，由调用方汇总进 `ok`，而不是独立的测试函数或
    断言。

    Args:
        name: Short label for this check, printed alongside PASS/FAIL.
            该检查的简短标签，会和 PASS/FAIL 一起打印出来。
        condition: Whether the check passed. 该检查是否通过。
        detail: Optional extra context to print when useful.
            可选的额外上下文信息，便于调试时查看。

    Returns:
        The `condition` value, unchanged, for the caller to fold into `ok`.
        原样返回 `condition` 的值，供调用方汇总进 `ok`。
    """
    print(f"  {'PASS' if condition else 'FAIL'}  {name}{f' — {detail}' if detail else ''}")
    return condition


async def main() -> int:
    """Run every check in this file and print ALL PASS / FAILURES ABOVE.

    This is the entry point for `python -m extract.test_pipeline`. It is a
    plain async function rather than a unittest.TestCase, by design — see the
    module docstring.

    中文：本文件所有检查的入口函数，对应 `python -m extract.test_pipeline`
    的运行入口。它是一个普通的 async 函数，而不是 unittest.TestCase，这是
    有意为之的设计（详见模块顶部的说明）。

    Returns:
        0 if every check passed, 1 if any check failed.
        全部检查通过返回 0，只要有一项失败就返回 1。
    """
    print("schema contract")
    ok = True

    # Extra keys must fail: a model inventing a field has not followed the schema.
    # 中文：多余字段必须校验失败——一个凭空编造字段的模型就是没有遵循 schema。
    try:
        DiaryDraft.model_validate({**VALID, "mood": "great"})
        ok &= check("extra key rejected", False, "it was accepted")
    except Exception:
        ok &= check("extra key rejected", True)

    # Emotional inference must fail even though the prompt forbids it.
    # 中文：即使 prompt 里已经禁止了，情绪推断类的表述也必须在这里再次被拦截。
    try:
        DiaryDraft.model_validate(
            {**VALID, "narrative": "You seemed frustrated by the failing tests all day long."}
        )
        ok &= check("emotional claim rejected", False, "it was accepted")
    except Exception:
        ok &= check("emotional claim rejected", True)

    # A today-only task dressed as a preference must fail.
    # 中文：把"今天的任务"包装成"偏好"的写法必须校验失败。
    try:
        DiaryDraft.model_validate(
            {
                **VALID,
                "preferences": [
                    {
                        "content": "Fix the 500 in /retrieve",
                        "category": "other",
                        "confidence": 0.9,
                        "evidence": "the endpoint returned 500",
                    }
                ],
            }
        )
        ok &= check("transient task rejected as preference", False, "it was accepted")
    except Exception:
        ok &= check("transient task rejected as preference", True)

    ok &= check("valid draft accepted", DiaryDraft.model_validate(VALID) is not None)

    print("\nClaude CLI adapter")
    system, prompt = _claude_cli_messages(
        [
            Message("system", "return JSON"),
            Message("user", "first request"),
            Message("assistant", "bad reply"),
            Message("user", "repair it"),
        ]
    )
    ok &= check("system prompt stays separate", system == "return JSON")
    ok &= check("stdin preserves repair turns", prompt.count("<user>") == 2)
    ok &= check("stdin preserves assistant reply", "<assistant>" in prompt)
    ok &= check(
        "cached prompt tokens count toward input",
        _claude_cli_input_tokens(
            {
                "input_tokens": 2,
                "cache_creation_input_tokens": 100,
                "cache_read_input_tokens": 900,
            }
        )
        == 1002,
    )

    print("\nMLX server adapter")

    async def mlx_response(request: httpx.Request) -> httpx.Response:
        """Fake the local MLX server's HTTP response for MockTransport.

        中文：为 MockTransport 伪造本地 MLX 服务器的 HTTP 响应。

        Args:
            request: The outgoing request MLXChatProvider made.
                MLXChatProvider 发出的请求。

        Returns:
            A canned 200 response containing the VALID draft as JSON.
            一个固定的 200 响应，内容是以 JSON 形式返回的 VALID 草稿。
        """
        payload = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert payload["model"] == "default_model"
        assert payload["messages"][0]["role"] == "system"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(VALID)}}],
                "usage": {"prompt_tokens": 123, "completion_tokens": 45},
            },
        )

    mlx = MLXChatProvider(transport=httpx.MockTransport(mlx_response))
    completion = await mlx.complete(
        [Message("system", "return JSON"), Message("user", "summarise today")]
    )
    ok &= check("local endpoint response returned", json.loads(completion.text) == VALID)
    ok &= check("local token usage preserved", completion.input_tokens == 123)

    print("\nrepair loop")

    # 1. Valid on the first attempt.
    # 中文：第一次尝试就合法。
    provider = ScriptedProvider([json.dumps(VALID)])
    result = await extract_day(provider, _day())
    ok &= check("first-attempt success", result.ok and result.first_attempt_valid)
    ok &= check("one call made", len(provider.calls) == 1, f"{len(provider.calls)}")

    # 2. Not JSON, then valid. Success, but NOT first-attempt valid.
    # 中文：第一次不是 JSON，第二次才合法。最终成功，但不算首次尝试合规。
    provider = ScriptedProvider(["I'm afraid I can't do that.", json.dumps(VALID)])
    result = await extract_day(provider, _day())
    ok &= check("recovers from non-JSON", result.ok)
    ok &= check(
        "recovery is not counted as first-attempt valid",
        not result.first_attempt_valid,
    )
    ok &= check("two attempts recorded", len(result.attempts) == 2)
    repair_text = provider.calls[1][-1].content
    ok &= check(
        "repair prompt names the failure",
        "Invalid JSON" in repair_text,
        repair_text[:60],
    )

    # 3. Schema violation, then valid — repair prompt must name the field.
    # 中文：第一次违反 schema，第二次才合法——修复提示必须指出具体是哪个字段。
    bad = {**VALID, "narrative": "too short"}
    provider = ScriptedProvider([json.dumps(bad), json.dumps(VALID)])
    result = await extract_day(provider, _day())
    ok &= check("recovers from schema violation", result.ok)
    ok &= check(
        "repair prompt names the field",
        "narrative" in provider.calls[1][-1].content,
    )

    # 4. A fenced but otherwise valid reply counts as first-attempt valid,
    #    because a code fence is formatting, not a schema failure.
    # 中文：被代码围栏包裹但内容合法的回复，仍然算作首次尝试合规——因为
    #    代码围栏只是格式问题，不算 schema 失败。
    provider = ScriptedProvider(["```json\n" + json.dumps(VALID) + "\n```"])
    result = await extract_day(provider, _day())
    ok &= check("fenced JSON accepted", result.ok and result.first_attempt_valid)
    ok &= check("fence recorded for later analysis", result.attempts[0].unfenced)

    # 5. Never valid: gives up, reports failure, produces no training pair.
    # 中文：始终不合法：放弃重试，报告失败，且不产生训练样本。
    provider = ScriptedProvider(["nope", "still nope", "nope again"])
    result = await extract_day(provider, _day(), max_attempts=3)
    ok &= check("gives up after max attempts", not result.ok)
    ok &= check("exactly three attempts", len(result.attempts) == 3)
    ok &= check("no training pair from a failure", training_pair(result) is None)

    print("\ntraining pair")
    provider = ScriptedProvider(["```json\n" + json.dumps(VALID) + "\n```"])
    result = await extract_day(provider, _day())
    pair = training_pair(result)
    assert pair is not None
    ok &= check("pair has prompt messages", len(pair["messages"]) == 2)  # type: ignore[arg-type]
    ok &= check(
        "completion is the validated object, not the fenced raw text",
        pair["completion"] == json.loads(DiaryDraft.model_validate(VALID).model_dump_json()),
    )

    print("\nprompt budget")
    big = [("user", "x" * 5000) for _ in range(400)]
    day = build_day_input("2026-08-04", ["fact"], big, max_input_tokens=4000)
    ok &= check(
        "input respects the token budget",
        day.estimated_tokens() <= 4200,
        f"{day.estimated_tokens()} tokens",
    )
    ok &= check("sample is smaller than the day", day.sampled_turns < day.total_turns)

    print()
    print("ALL PASS" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
