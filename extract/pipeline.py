"""Extract one day: call, validate, repair, record.

Every attempt is recorded, not just the successful one. The compliance rate the
résumé quotes is "fraction valid on the FIRST attempt" — counting a success that
took three repairs would describe the retry loop rather than the model, and the
fine-tuned model in stage two has to be judged on the same basis.

中文说明：本文件负责单日的抽取流程——调用模型、校验结果、必要时修复、并记录
每一次尝试。这里记录的是每一次尝试，而不只是最终成功的那一次。简历中引用的
合规率指的是"首次尝试即合规的比例"——如果把经过三次修复才成功的样本也算作
合规，衡量的就是修复循环本身而不是模型的能力，而第二阶段微调出的模型也必须
按同一标准评判。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pydantic import ValidationError

from .prompts import PROMPT_VERSION, DayInput, repair_prompt, system_prompt
from .providers import ChatProvider, Message
from .schemas import DiaryDraft

logger = logging.getLogger(__name__)

# Models sometimes wrap JSON in a fence despite being told not to. Stripping it
# is a formatting nicety, so it is NOT counted as a schema failure — but it is
# recorded, because a fine-tuned model should stop needing it.
# 中文：模型有时会在被明确要求不要这样做的情况下，仍然用代码围栏包裹 JSON。
# 去掉围栏只是格式上的处理，不算作 schema 失败——但会被记录下来，因为微调后
# 的模型理应不再需要这个习惯。
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


@dataclass(slots=True)
class Attempt:
    """One call-and-validate attempt within a single day's extraction.

    中文：单日抽取过程中的一次"调用并校验"尝试记录。

    Attributes:
        index: 1-based position of this attempt within the retry loop.
            本次尝试在重试循环中的序号（从 1 开始）。
        raw: The model's reply text, after fence-stripping.
            模型回复的原始文本（已去除代码围栏）。
        ok: Whether this attempt's reply validated against the schema.
            本次回复是否通过了 schema 校验。
        errors: Human-readable validation errors, if any.
            校验失败时的可读错误信息（如果有）。
        unfenced: Whether the reply had to have a code fence stripped.
            本次回复是否需要去除代码围栏。
        input_tokens: Input tokens billed for this attempt.
            本次尝试计费的输入 token 数。
        output_tokens: Output tokens billed for this attempt.
            本次尝试计费的输出 token 数。
    """

    index: int
    raw: str
    ok: bool
    errors: str | None = None
    unfenced: bool = False
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(slots=True)
class ExtractionResult:
    """The full outcome of extracting one day, including every attempt made.

    中文：一天抽取过程的完整结果，包含所有尝试记录。

    Attributes:
        date: The local date this extraction covers.
            本次抽取覆盖的本地日期。
        draft: The validated diary, or None if every attempt failed.
            校验通过的日记草稿；如果所有尝试都失败则为 None。
        session_id: The session this covers, or None for a whole-day card.
            本次覆盖的会话 ID；如果是整天的卡片则为 None。
        attempts: Every attempt made, in order.
            按顺序记录的所有尝试。
        provider: Name of the provider used (e.g. "openai", "mlx").
            使用的服务商名称（例如 "openai"、"mlx"）。
        model: Name of the model used.
            使用的模型名称。
        prompt_version: The PROMPT_VERSION in effect when this ran.
            本次运行时生效的 PROMPT_VERSION。
        prompt_messages: The initial system+user messages sent to the model.
            发送给模型的初始 system + user 消息。
        extracted_at: UTC timestamp of when this extraction ran.
            本次抽取运行的 UTC 时间戳。
    """

    date: str
    draft: DiaryDraft | None
    session_id: str | None = None
    attempts: list[Attempt] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    prompt_version: str = PROMPT_VERSION
    prompt_messages: list[Message] = field(default_factory=list)
    extracted_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def ok(self) -> bool:
        """Whether the extraction eventually produced a valid draft.

        中文：本次抽取是否最终产出了一份合法的草稿（可能经过修复）。
        """
        return self.draft is not None

    @property
    def first_attempt_valid(self) -> bool:
        """Whether the very first attempt validated, with no repair needed.

        中文：第一次尝试是否就通过了校验，完全不需要修复。
        """
        return bool(self.attempts) and self.attempts[0].ok

    @property
    def total_input_tokens(self) -> int:
        """Sum of input tokens across every attempt, including repairs.

        中文：所有尝试（包括修复重试）的输入 token 总和。
        """
        return sum(attempt.input_tokens for attempt in self.attempts)

    @property
    def total_output_tokens(self) -> int:
        """Sum of output tokens across every attempt, including repairs.

        中文：所有尝试（包括修复重试）的输出 token 总和。
        """
        return sum(attempt.output_tokens for attempt in self.attempts)


def _strip_fence(text: str) -> tuple[str, bool]:
    """Remove a wrapping ```json ... ``` code fence if present.

    中文：如果回复被 ```json ... ``` 代码围栏包裹，则去除围栏。

    Args:
        text: The raw model reply. 模型的原始回复文本。

    Returns:
        A tuple of (unwrapped text, whether a fence was found and stripped).
        一个元组：(去除围栏后的文本, 是否发现并去除了围栏)。
    """
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1), True
    return text, False


def _format_errors(error: ValidationError) -> str:
    """Render a pydantic ValidationError as short, model-readable lines.

    中文：把 pydantic 的 ValidationError 渲染成简短、便于模型阅读的多行文本。

    Args:
        error: The validation error raised by pydantic.
            pydantic 抛出的校验错误。

    Returns:
        One "- field: message" line per error, joined with newlines.
        每个错误一行 "- 字段: 错误信息"，用换行符连接。
    """
    lines = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "(root)"
        lines.append(f"- {location}: {item['msg']}")
    return "\n".join(lines)


async def extract_day(
    provider: ChatProvider,
    day: DayInput,
    *,
    max_attempts: int = 3,
    session_id: str | None = None,
) -> ExtractionResult:
    """Ask for a day's diary, validating and repairing until it fits.

    中文：请求模型生成某一天的日记，并在需要时不断校验、修复，直到结果合法或
    达到最大尝试次数。

    Args:
        provider: The chat provider to call. 用于发起请求的对话服务商。
        day: The compressed view of the day to send. 压缩后待发送的一天数据。
        max_attempts: Maximum number of call-and-validate attempts.
            最大的"调用并校验"尝试次数。
        session_id: The session this covers, or None for a whole-day card.
            本次覆盖的会话 ID；如果是整天的卡片则为 None。

    Returns:
        The full extraction result, including every attempt made.
        完整的抽取结果，包含所有尝试记录。
    """
    messages = [
        Message("system", system_prompt()),
        Message("user", day.render()),
    ]
    result = ExtractionResult(
        date=day.date,
        draft=None,
        session_id=session_id,
        provider=provider.name,
        model=provider.model,
        prompt_messages=list(messages),
    )

    for index in range(1, max_attempts + 1):
        completion = await provider.complete(messages)
        raw, unfenced = _strip_fence(completion.text)

        try:
            # model_validate_json reports a parse failure as a ValidationError
            # too ("Invalid JSON: ..."), so this one branch covers both a reply
            # that is not JSON and one that is JSON of the wrong shape.
            # 中文：model_validate_json 对"无法解析的 JSON"和"结构不对的 JSON"
            # 都会抛出 ValidationError（前者的信息形如 "Invalid JSON: ..."），
            # 所以这一个 except 分支同时覆盖了这两种情况，不需要分别处理。
            draft = DiaryDraft.model_validate_json(raw)
        except ValidationError as error:
            errors = _format_errors(error)
        else:
            result.attempts.append(
                Attempt(
                    index=index,
                    raw=raw,
                    ok=True,
                    unfenced=unfenced,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                )
            )
            result.draft = draft
            return result

        logger.warning(
            "%s attempt %d/%d failed schema for %s:\n%s",
            provider.model,
            index,
            max_attempts,
            day.date,
            errors,
        )
        result.attempts.append(
            Attempt(
                index=index,
                raw=raw,
                ok=False,
                errors=errors,
                unfenced=unfenced,
                input_tokens=completion.input_tokens,
                output_tokens=completion.output_tokens,
            )
        )
        messages = [
            *messages,
            Message("assistant", completion.text),
            Message("user", repair_prompt(raw, errors)),
        ]

    return result


def training_pair(result: ExtractionResult) -> dict[str, object] | None:
    """One (prompt, completion) pair for stage two's fine-tune.

    Only successful extractions become training data, and the stored completion
    is the VALIDATED object re-serialised — not the model's raw text. Training on
    raw output would teach the next model to reproduce the same fence-and-repair
    habits we are trying to remove.

    中文：为第二阶段微调准备的一条 (prompt, completion) 样本。只有成功的抽取
    才会变成训练数据，并且存储的 completion 是经过校验后重新序列化的对象——
    而不是模型的原始回复文本。如果直接用原始输出训练，会让下一个模型学会同样
    的"加围栏再被修复"的坏习惯，而这正是我们想消除的。

    Args:
        result: The full extraction result to convert.
            待转换的完整抽取结果。

    Returns:
        A dict with "date", "session_id", "messages", "completion" and "meta"
        keys, or None if the extraction never produced a valid draft.
        包含 "date"、"session_id"、"messages"、"completion"、"meta" 字段的
        字典；如果抽取从未产出合法草稿则返回 None。
    """
    if result.draft is None:
        return None
    return {
        "date": result.date,
        "session_id": result.session_id,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in result.prompt_messages
        ],
        "completion": json.loads(result.draft.model_dump_json()),
        "meta": {
            "provider": result.provider,
            "model": result.model,
            "attempts": len(result.attempts),
            "first_attempt_valid": result.first_attempt_valid,
            "extracted_at": result.extracted_at.isoformat(),
        },
    }
