"""Chat providers for extraction, plus a scripted one for tests.

Deliberately NOT using structured-output/strict modes. Those make the provider
enforce the schema server-side, which would make our own compliance rate
meaningless — it would measure the vendor's constrained decoder, not the model's
ability to follow a schema. Since that rate is the number the résumé quotes, and
the same number the fine-tuned model will be judged against, the request asks for
JSON and we validate it ourselves.
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
    role: str
    content: str


@dataclass(slots=True)
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0


class ChatProvider(Protocol):
    name: str
    model: str

    async def complete(self, messages: list[Message]) -> Completion: ...


class OpenAIChatProvider:
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        timeout: float = 90.0,
    ) -> None:
        if not api_key:
            raise ValueError("an OpenAI API key is required")
        self.model = model
        self._key = api_key
        self._timeout = timeout

    async def complete(self, messages: list[Message]) -> Completion:
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
    name = "gemini"

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        timeout: float = 90.0,
    ) -> None:
        if not api_key:
            raise ValueError("a Gemini API key is required")
        self.model = model
        self._key = api_key
        self._timeout = timeout

    async def complete(self, messages: list[Message]) -> Completion:
        # Gemini takes the system instruction separately and uses "model" for
        # the assistant role.
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
    """Call a local ``mlx_lm.server`` over its OpenAI-compatible endpoint."""

    name = "mlx"

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080/v1",
        model: str = "mlx-community/Qwen2.5-3B-Instruct-4bit",
        timeout: float = 180.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._timeout = timeout
        self._transport = transport

    async def complete(self, messages: list[Message]) -> Completion:
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport
        ) as client:
            response = await client.post(
                self._url,
                json={
                    # mlx_lm.server maps this alias to the model and adapter
                    # selected when the process started. ``self.model`` is the
                    # truthful model label recorded on the resulting T2 card.
                    "model": "default_model",
                    "messages": [
                        {"role": message.role, "content": message.content}
                        for message in messages
                    ],
                    # Keep decoding unconstrained so first-attempt schema
                    # compliance remains comparable to the hosted teacher.
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
    """Split chat messages into Claude CLI's system prompt and stdin prompt."""
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
    """Count uncached and cached prompt tokens on the same basis as other APIs."""
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
    """

    name = "claude-cli"

    def __init__(
        self,
        model: str = "sonnet",
        timeout: float = 180.0,
        executable: str = "claude",
    ) -> None:
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
    """

    replies: list[str]
    name: str = "scripted"
    model: str = "scripted"
    calls: list[list[Message]] = field(default_factory=list)

    async def complete(self, messages: list[Message]) -> Completion:
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
) -> ChatProvider:
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
        )
    raise ValueError(
        f"unknown provider {kind!r}; use openai, gemini, claude-cli, or mlx"
    )
