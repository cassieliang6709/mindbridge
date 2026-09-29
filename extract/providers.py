"""Chat providers for extraction, plus a scripted one for tests.

Deliberately NOT using structured-output/strict modes. Those make the provider
enforce the schema server-side, which would make our own compliance rate
meaningless — it would measure the vendor's constrained decoder, not the model's
ability to follow a schema. Since that rate is the number the résumé quotes, and
the same number the fine-tuned model will be judged against, the request asks for
JSON and we validate it ourselves.

中文说明：本文件提供各个对话服务商的适配器，以及一个用于测试的"脚本化"服务商。
这里刻意不使用结构化输出（strict/schema-enforced）模式。那类模式会让服务商在
服务端强制保证输出符合 schema，这样一来我们自己统计的合规率就失去了意义——
衡量的会是厂商的约束解码器，而不是模型本身遵循 schema 的能力。既然这个合规率
是简历中引用的数字，也是第二阶段微调模型将被评判的同一个标准，那么请求就只
要求模型返回 JSON，校验工作完全由我们自己来做。
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from typing import Protocol

import httpx


@dataclass(slots=True)
class Message:
    """One chat turn sent to or received from a provider.

    中文：发送给服务商或从服务商收到的一条对话消息。

    Attributes:
        role: "system", "user", or "assistant". 角色："system"、"user" 或
            "assistant"。
        content: The message text. 消息文本内容。
    """

    role: str
    content: str


@dataclass(slots=True)
class Completion:
    """A provider's reply, plus the token usage it reported.

    中文：服务商的回复内容，以及它报告的 token 用量。

    Attributes:
        text: The reply text. 回复文本。
        input_tokens: Reported input/prompt tokens for this call.
            本次调用报告的输入（prompt）token 数。
        output_tokens: Reported output/completion tokens for this call.
            本次调用报告的输出（completion）token 数。
    """

    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class ChatProvider(Protocol):
    """The common interface every provider (real or scripted) must satisfy.

    中文：所有服务商（无论是真实的还是脚本化的）都必须满足的通用接口。
    """

    name: str
    model: str

    async def complete(self, messages: list[Message]) -> Completion:
        """Send the given messages and return the provider's reply.

        中文：发送给定的消息列表，并返回服务商的回复。

        Args:
            messages: The conversation so far, in order.
                目前为止的对话记录，按顺序排列。

        Returns:
            The provider's completion, with token usage if available.
            服务商的回复，如果可用则包含 token 用量。
        """
        ...


class OpenAIChatProvider:
    """Calls OpenAI's chat completions endpoint, requesting plain JSON output.

    中文：调用 OpenAI 的 chat completions 接口，请求返回普通 JSON。
    """

    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout: float = 90.0,
    ) -> None:
        """Configure the provider with an API key, model, and timeout.

        中文：使用 API key、模型名和超时时间配置该服务商。

        Args:
            api_key: OpenAI API key. Required. OpenAI 的 API key，必填。
            model: Model name to call. 要调用的模型名称。
            timeout: Request timeout in seconds. 请求超时时间（秒）。

        Raises:
            ValueError: If no API key is given.
                如果没有提供 API key。
        """
        if not api_key:
            raise ValueError("an OpenAI API key is required")
        self.model = model
        self._key = api_key
        self._timeout = timeout

    async def complete(self, messages: list[Message]) -> Completion:
        """Call the OpenAI chat completions endpoint and parse its reply.

        中文：调用 OpenAI 的 chat completions 接口并解析其返回结果。

        Args:
            messages: The conversation so far, in order.
                目前为止的对话记录，按顺序排列。

        Returns:
            The completion text and reported token usage.
            回复文本及报告的 token 用量。
        """
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self._key}"},
                json={
                    "model": self.model,
                    "messages": [
                        {"role": message.role, "content": message.content}
                        for message in messages
                    ],
                    # json_object guarantees parseable JSON but not our shape,
                    # which is exactly the boundary we want to measure.
                    # 中文：json_object 模式只保证返回的是可解析的 JSON，但不
                    # 保证符合我们的 schema 形状——这条边界正是我们想要衡量的
                    # 合规率所在。
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2,
                },
            )
            response.raise_for_status()
            payload = response.json()
        usage = payload.get("usage") or {}
        return Completion(
            text=payload["choices"][0]["message"]["content"],
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )


class GeminiChatProvider:
    """Calls Gemini's generateContent endpoint, requesting plain JSON output.

    中文：调用 Gemini 的 generateContent 接口，请求返回普通 JSON。
    """

    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        timeout: float = 90.0,
    ) -> None:
        """Configure the provider with an API key, model, and timeout.

        中文：使用 API key、模型名和超时时间配置该服务商。

        Args:
            api_key: Gemini API key. Required. Gemini 的 API key，必填。
            model: Model name to call. 要调用的模型名称。
            timeout: Request timeout in seconds. 请求超时时间（秒）。

        Raises:
            ValueError: If no API key is given.
                如果没有提供 API key。
        """
        if not api_key:
            raise ValueError("a Gemini API key is required")
        self.model = model
        self._key = api_key
        self._timeout = timeout

    async def complete(self, messages: list[Message]) -> Completion:
        """Call the Gemini generateContent endpoint and parse its reply.

        中文：调用 Gemini 的 generateContent 接口并解析其返回结果。

        Args:
            messages: The conversation so far, in order.
                目前为止的对话记录，按顺序排列。

        Returns:
            The completion text and reported token usage.
            回复文本及报告的 token 用量。
        """
        # Gemini takes the system instruction separately and uses "model" for
        # the assistant role.
        # 中文：Gemini 把 system 指令单独作为一个字段传递，并且用 "model"
        # 而不是 "assistant" 来表示助手角色。
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        contents = [
            {
                "role": "model" if message.role == "assistant" else "user",
                "parts": [{"text": message.content}],
            }
            for message in messages
            if message.role != "system"
        ]
        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{self.model}:generateContent"
        )
        body: dict[str, object] = {
            "contents": contents,
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                url, headers={"x-goog-api-key": self._key}, json=body
            )
            response.raise_for_status()
            payload = response.json()

        candidate = payload["candidates"][0]
        text = "".join(
            part.get("text", "") for part in candidate["content"]["parts"]
        )
        usage = payload.get("usageMetadata") or {}
        return Completion(
            text=text,
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
        )


class MLXChatProvider:
    """Call a local ``mlx_lm.server`` over its OpenAI-compatible endpoint.

    中文：通过 OpenAI 兼容接口调用本地运行的 ``mlx_lm.server``。
    """

    name = "mlx"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080/v1",
        model: str = "mlx-community/Qwen2.5-3B-Instruct-4bit",
        timeout: float = 180.0,
        transport: httpx.AsyncBaseTransport | None = None,
        wire_model: str = "default_model",
    ) -> None:
        """Configure the provider with the local server's URL and model.

        中文：使用本地服务器的 URL 和模型名配置该服务商。

        Args:
            base_url: OpenAI-compatible base URL of the local server.
                本地服务器的 OpenAI 兼容 base URL。
            model: Display/logging name of the model being served.
                用于展示和日志记录的模型名称。
            timeout: Request timeout in seconds. 请求超时时间（秒）。
            transport: Optional httpx transport override, used by tests to
                mock the HTTP call. 可选的 httpx transport 覆盖项，供测试
                模拟 HTTP 调用时使用。
            wire_model: The model name actually sent in the request body.
                实际写入请求体中的模型名称。
        """
        self.model = model
        # What goes on the wire. mlx_lm.server ignores the real name and only
        # answers to the alias for whatever model+adapter it was started with,
        # so that alias is the default. Servers that route by name (ollama,
        # vLLM) 404 on the alias and need the real one passed explicitly.
        # 中文：这是实际发送到网络上的模型名。mlx_lm.server 会忽略真实的模型
        # 名，只认它启动时所用 model+adapter 对应的别名，所以默认值就是这个
        # 别名。而按名称路由的服务器（如 ollama、vLLM）遇到这个别名会返回
        # 404，需要显式传入真实的模型名。
        self.wire_model = wire_model
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout = timeout
        self._transport = transport

    async def complete(self, messages: list[Message]) -> Completion:
        """Call the local server's chat completions endpoint and parse its reply.

        中文：调用本地服务器的 chat completions 接口并解析其返回结果。

        Args:
            messages: The conversation so far, in order.
                目前为止的对话记录，按顺序排列。

        Returns:
            The completion text and reported token usage.
            回复文本及报告的 token 用量。
        """
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            response = await client.post(
                self._url,
                json={
                    "model": self.wire_model,
                    "messages": [
                        {"role": message.role, "content": message.content}
                        for message in messages
                    ],
                    # Keep decoding unconstrained so first-attempt schema
                    # compliance remains comparable to the hosted teacher.
                    # 中文：保持解码不受约束，这样"首次尝试合规率"才能和托管
                    # 的教师模型放在一起比较。
                    "temperature": 0.2,
                    "max_tokens": 1200,
                },
            )
            response.raise_for_status()
            payload = response.json()

        usage = payload.get("usage") or {}
        return Completion(
            text=payload["choices"][0]["message"]["content"],
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
        )


def _claude_cli_messages(messages: list[Message]) -> tuple[str, str]:
    """Split chat messages into Claude CLI's system prompt and stdin prompt.

    中文：把对话消息拆分成 Claude CLI 所需的 system 提示词和标准输入 prompt。

    Args:
        messages: The conversation so far, in order.
            目前为止的对话记录，按顺序排列。

    Returns:
        A tuple of (system prompt text, stdin prompt text with <role> tags).
        一个元组：(system 提示词文本, 带有 <role> 标签的标准输入 prompt 文本)。

    Raises:
        ValueError: If a message uses a role other than system/user/assistant.
            如果消息使用了 system/user/assistant 以外的角色。
    """
    system = "\n\n".join(
        message.content for message in messages if message.role == "system"
    )
    turns: list[str] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role not in {"user", "assistant"}:
            raise ValueError(f"unsupported Claude CLI role: {message.role!r}")
        turns.append(
            f"<{message.role}>\n{message.content}\n</{message.role}>"
        )
    return system, "\n\n".join(turns)


def _claude_cli_input_tokens(usage: dict[str, object]) -> int:
    """Count uncached and cached prompt tokens on the same basis as other APIs.

    中文：把未缓存和已缓存的 prompt token 都计入输入 token，使其与其他 API
    的统计口径保持一致。

    Args:
        usage: The "usage" dict from the Claude CLI's JSON envelope.
            Claude CLI JSON 返回体中的 "usage" 字典。

    Returns:
        The total input tokens across ordinary, cache-creation, and
        cache-read fields. 普通输入、缓存创建、缓存读取三类字段相加得到的
        总输入 token 数。
    """
    return sum(
        int(usage.get(field) or 0)
        for field in (
            "input_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        )
    )


class ClaudeCodeCLIProvider:
    """Use the signed-in Claude Code CLI without copying an API key.

    Transcript excerpts are passed on stdin rather than the command line so
    they do not appear in shell history or the process list. Tools and session
    persistence are disabled: this adapter asks Claude for text only and keeps
    MindBridge's own validation/repair loop as the source of truth.

    中文：使用已登录的 Claude Code CLI，无需另外复制一份 API key。转录内容
    通过标准输入传递，而不是命令行参数，这样就不会出现在 shell 历史记录或
    进程列表里。工具调用和会话持久化都被禁用：这个适配器只向 Claude 请求
    纯文本，MindBridge 自己的校验/修复循环才是真正的事实来源。
    """

    name = "claude-cli"

    def __init__(
        self,
        model: str = "sonnet",
        timeout: float = 180.0,
        executable: str = "claude",
    ) -> None:
        """Locate the Claude CLI executable and configure the provider.

        中文：定位 Claude CLI 可执行文件，并配置该服务商。

        Args:
            model: Model alias to pass to the CLI. 传给 CLI 的模型别名。
            timeout: Subprocess timeout in seconds. 子进程超时时间（秒）。
            executable: Name or path of the CLI executable to look up.
                要查找的 CLI 可执行文件名或路径。

        Raises:
            ValueError: If the CLI executable cannot be found on PATH.
                如果在 PATH 中找不到该 CLI 可执行文件。
        """
        resolved = shutil.which(executable)
        if resolved is None:
            raise ValueError(
                "Claude Code CLI was not found. Install it and run `claude` once "
                "to sign in."
            )
        self.model = model
        self._executable = resolved
        self._timeout = timeout

    async def complete(self, messages: list[Message]) -> Completion:
        """Run the Claude CLI as a subprocess and parse its JSON envelope.

        中文：把 Claude CLI 作为子进程运行，并解析其 JSON 返回体。

        Args:
            messages: The conversation so far, in order.
                目前为止的对话记录，按顺序排列。

        Returns:
            The completion text and reported token usage.
            回复文本及报告的 token 用量。

        Raises:
            RuntimeError: If the CLI times out, exits non-zero, returns an
                invalid JSON envelope, or does not return a text result.
                如果 CLI 超时、非零退出、返回无效的 JSON 返回体，或没有
                返回文本结果。
        """
        system, prompt = _claude_cli_messages(messages)
        command = [
            self._executable,
            "-p",
            "--no-session-persistence",
            "--safe-mode",
            "--tools",
            "",
            "--output-format",
            "json",
            "--model",
            self.model,
        ]
        if system:
            command.extend(["--system-prompt", system])

        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(prompt.encode("utf-8")), timeout=self._timeout
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(
                f"Claude Code CLI timed out after {self._timeout:.0f}s"
            ) from None

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()[:500]
            raise RuntimeError(
                f"Claude Code CLI exited {process.returncode}"
                + (f": {detail}" if detail else "")
            )

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            raise RuntimeError("Claude Code CLI returned an invalid JSON envelope") from None

        result = payload.get("result")
        if payload.get("is_error") or not isinstance(result, str):
            raise RuntimeError("Claude Code CLI did not return a text result")

        usage = payload.get("usage") or {}
        return Completion(
            text=result,
            input_tokens=_claude_cli_input_tokens(usage),
            output_tokens=usage.get("output_tokens", 0),
        )


@dataclass
class ScriptedProvider:
    """Returns canned replies in order. Used to test the repair loop offline.

    Exists so the validation-and-retry machinery can be verified without
    spending money or needing a key — including the failure paths, which are
    hard to trigger on demand against a real model.

    中文：按顺序返回预先设定好的回复。用于离线测试修复循环。它的存在是为了
    能够验证校验与重试机制，而不需要花钱或申请 key——包括那些很难在真实模型
    上按需触发的失败路径。
    """

    replies: list[str]
    name: str = "scripted"
    model: str = "scripted"
    calls: list[list[Message]] = field(default_factory=list)

    async def complete(self, messages: list[Message]) -> Completion:
        """Record the call and pop the next scripted reply off the queue.

        中文：记录本次调用，并从预设队列中取出下一条回复。

        Args:
            messages: The conversation so far, in order.
                目前为止的对话记录，按顺序排列。

        Returns:
            A Completion built from the next scripted reply.
            由下一条预设回复构造出的 Completion。

        Raises:
            AssertionError: If there are no more scripted replies left.
                如果预设的回复已经用完。
        """
        self.calls.append(list(messages))
        if not self.replies:
            raise AssertionError("ScriptedProvider ran out of replies")
        return Completion(text=self.replies.pop(0), input_tokens=1, output_tokens=1)


def build_provider(
    kind: str,
    api_key: str | None,
    model: str | None,
    *,
    base_url: str | None = None,
    timeout: float | None = None,
    wire_model: str | None = None,
) -> ChatProvider:
    """Construct the concrete provider for the given kind.

    中文：根据给定的类型名构造对应的具体服务商实例。

    Args:
        kind: One of "openai", "gemini", "claude-cli", "mlx".
            取值为 "openai"、"gemini"、"claude-cli"、"mlx" 之一。
        api_key: API key for hosted providers, or None. 托管服务商所需的
            API key，本地服务商可为 None。
        model: Model name override, or None to use the provider's default.
            模型名称覆盖值；为 None 时使用该服务商的默认值。
        base_url: Base URL override for the "mlx" provider.
            "mlx" 服务商的 base URL 覆盖值。
        timeout: Timeout override for the "mlx" provider.
            "mlx" 服务商的超时时间覆盖值。
        wire_model: Wire model name override for the "mlx" provider.
            "mlx" 服务商实际发送到网络上的模型名覆盖值。

    Returns:
        A configured provider ready to call.
        一个已配置好、可直接调用的服务商实例。

    Raises:
        ValueError: If `kind` is not one of the supported provider names.
            如果 `kind` 不是受支持的服务商名称之一。
    """
    if kind == "openai":
        return OpenAIChatProvider(api_key or "", model or "gpt-4o-mini")
    if kind == "gemini":
        return GeminiChatProvider(api_key or "", model or "gemini-2.5-flash")
    if kind == "claude-cli":
        return ClaudeCodeCLIProvider(model or "sonnet")
    if kind == "mlx":
        return MLXChatProvider(
            base_url or "http://127.0.0.1:8080/v1",
            model or "mlx-community/Qwen2.5-3B-Instruct-4bit",
            timeout or 180.0,
            wire_model=wire_model or "default_model",
        )
    raise ValueError(
        f"unknown provider {kind!r}; use openai, gemini, claude-cli, or mlx"
    )
