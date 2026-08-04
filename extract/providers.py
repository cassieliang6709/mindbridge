"""Chat providers for extraction, plus a scripted one for tests.

Deliberately NOT using structured-output/strict modes. Those make the provider
enforce the schema server-side, which would make our own compliance rate
meaningless — it would measure the vendor's constrained decoder, not the model's
ability to follow a schema. Since that rate is the number the résumé quotes, and
the same number the fine-tuned model will be judged against, the request asks for
JSON and we validate it ourselves.
"""

from __future__ import annotations

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
    kind: str, api_key: str | None, model: str | None
) -> ChatProvider:
    if kind == "openai":
        return OpenAIChatProvider(api_key or "", model or "gpt-4o-mini")
    if kind == "gemini":
        return GeminiChatProvider(api_key or "", model or "gemini-2.5-flash")
    raise ValueError(f"unknown provider {kind!r}; use openai or gemini")
