"""Versioned tool definitions exposed to the local agent model.

The registry is the contract between model output and Python execution. The
model sees JSON Schema derived from the same Pydantic type used to validate its
arguments, so a prompt example cannot silently drift from executable code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentTemporalQueryRequest(BaseModel):
    """The only retrieval input the local planner may choose.

    Retrieval scope and ranking are fixed by the executor, so the model cannot
    accidentally hide relevant memories by guessing a category, namespace,
    project, date window, or small result budget.
    """

    model_config = ConfigDict(extra="forbid")

    query_string: str = Field(
        min_length=40,
        description=(
            "A cross-lingual memory-search query of at least 40 characters. Start "
            "with an English translation of the user's topic, preserve the original "
            "question, then add neutral English facets describing what relevant "
            "memories might discuss. Never invent the answer, demographics, or the "
            "user's preferences."
        ),
    )


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One model-visible tool and its executable input contract."""

    name: str
    description: str
    input_model: type[BaseModel]
    risk: Literal["read", "write"]

    def ollama_definition(self) -> dict[str, object]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }


class ToolRegistry:
    """Small explicit registry; unknown model-selected tools fail closed."""

    def __init__(self, version: str, tools: list[ToolSpec]) -> None:
        self.version = version
        self._tools = {tool.name: tool for tool in tools}
        if len(self._tools) != len(tools):
            raise ValueError("tool names must be unique")

    def definitions(self) -> list[dict[str, object]]:
        return [tool.ollama_definition() for tool in self._tools.values()]

    def require(self, name: str) -> ToolSpec:
        try:
            return self._tools[name]
        except KeyError as error:
            raise ValueError(f"model selected unknown tool {name!r}") from error


MEMORY_READ_TOOLS = ToolRegistry(
    version="mindbridge-memory-read.v2",
    tools=[
        ToolSpec(
            name="temporal_query",
            description=(
                "Search the user's saved long-term memories and preferences. "
                "Use this when the task asks what the user previously decided, "
                "preferred, learned, or worked on. Pass only a self-contained, "
                "cross-lingual retrieval query. Start with an English translation "
                "of the topic, retain the original wording, and add neutral English "
                "facets without guessing the answer."
            ),
            input_model=AgentTemporalQueryRequest,
            risk="read",
        )
    ],
)
