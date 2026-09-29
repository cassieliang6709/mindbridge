from __future__ import annotations

import unittest
from datetime import datetime, timezone

from api.agent_runtime import MemoryAgentRuntime
from api.local_agent import (
    INSUFFICIENT_MEMORY_ANSWER,
    LocalMemoryAgent,
    ModelReply,
    ModelToolCall,
)
from api.models import MemoryHit, TemporalQueryRequest, TemporalQueryResult
from api.run_ledger import AgentRunLedger
from api.tool_registry import MEMORY_READ_TOOLS
from tests.test_run_ledger import _FakePool


def _hit(memory_id: int = 42) -> MemoryHit:
    return MemoryHit(
        id=memory_id,
        content="Keep resume project bullets concise and evidence-backed.",
        namespace="operational",
        category="other",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        valid_at=None,
        superseded_by=None,
        access_count=0,
        decay_factor=1.0,
        project=None,
        cosine_similarity=0.9,
        age_days=10.0,
        decay_multiplier=0.9,
        score=0.9,
    )


class _MemoryService:
    def __init__(self, hits: list[MemoryHit] | None = None) -> None:
        self.queries: list[TemporalQueryRequest] = []
        self._hits = hits or []

    async def temporal_query(
        self, request: TemporalQueryRequest
    ) -> TemporalQueryResult:
        self.queries.append(request)
        return TemporalQueryResult(
            query=request.query_string,
            hits=self._hits,
            decay_rate_per_day=0.01,
            context_block="Candidate memories.",
        )


class _ChatClient:
    model = "qwen-test"

    def __init__(self, final_content: str) -> None:
        self.final_content = final_content
        self.calls: list[tuple[list[dict[str, object]], list[dict[str, object]] | None]] = []

    async def chat(
        self,
        messages: list[dict[str, object]],
        *,
        tools: list[dict[str, object]] | None = None,
    ) -> ModelReply:
        self.calls.append((messages, tools))
        if tools is not None:
            return ModelReply(
                tool_calls=[
                    ModelToolCall(
                        call_id="call-1",
                        name="temporal_query",
                        arguments={
                            "query_string": (
                                "resume and CV project-experience writing preferences; "
                                "bullet structure, wording, metrics, and technical depth"
                            )
                        },
                    )
                ]
            )
        return ModelReply(content=self.final_content)


class _NoToolChatClient(_ChatClient):
    async def chat(
        self,
        messages: list[dict[str, object]],
        *,
        tools: list[dict[str, object]] | None = None,
    ) -> ModelReply:
        self.calls.append((messages, tools))
        if tools is not None:
            return ModelReply(content="Please clarify your preference question.")
        return ModelReply(content=self.final_content)


class ToolRegistryTests(unittest.TestCase):
    def test_schema_exposes_only_self_contained_query_string(self) -> None:
        [definition] = MEMORY_READ_TOOLS.definitions()
        function = definition["function"]
        self.assertEqual(function["name"], "temporal_query")
        parameters = function["parameters"]
        self.assertEqual(set(parameters["properties"]), {"query_string"})
        self.assertEqual(parameters["required"], ["query_string"])
        with self.assertRaises(ValueError):
            MEMORY_READ_TOOLS.require("temporal_query").input_model.model_validate(
                {"query_string": "resume preferences", "top_k": 3}
            )


class LocalMemoryAgentTests(unittest.IsolatedAsyncioTestCase):
    async def _run(
        self, final_content: str, hits: list[MemoryHit]
    ) -> tuple[object, _MemoryService, _ChatClient]:
        pool = _FakePool()
        ledger = AgentRunLedger(pool)  # type: ignore[arg-type]
        service = _MemoryService(hits)
        runtime = MemoryAgentRuntime(service, ledger)  # type: ignore[arg-type]
        chat = _ChatClient(final_content)
        agent = LocalMemoryAgent(runtime, ledger, chat)
        response = await agent.run("How should I write project experience?")
        return response, service, chat

    async def test_fixed_broad_semantic_request_and_known_selection_are_replayable(
        self,
    ) -> None:
        response, service, chat = await self._run(
            '```json\n{"answer":"Use concise, evidence-backed bullets.","relevant_memory_ids":[42]}\n```',
            [_hit()],
        )

        self.assertEqual(response.answer, "Use concise, evidence-backed bullets.")
        self.assertEqual(response.selected_tool, "temporal_query")
        self.assertEqual(response.selected_memory_ids, [42])
        self.assertEqual(response.hit_count, 1)
        self.assertEqual(response.snapshot.run.status, "completed")
        self.assertEqual(len(service.queries), 1)
        request = service.queries[0]
        self.assertIn("resume and CV project-experience", request.query_string)
        self.assertEqual(request.top_k, 30)
        self.assertEqual(request.ranking_mode, "semantic")
        self.assertFalse(request.include_superseded)
        self.assertEqual(len(chat.calls), 2)
        self.assertIsNotNone(chat.calls[0][1])
        self.assertIsNone(chat.calls[1][1])
        selection_messages = chat.calls[1][0]
        self.assertEqual(selection_messages[0]["role"], "system")
        self.assertIn('"task": "How should I write project experience?"', selection_messages[1]["content"])
        self.assertIn('"id": 42', selection_messages[1]["content"])
        self.assertEqual(
            [event.event_type for event in response.snapshot.events],
            [
                "run_input",
                "model_requested",
                "model_completed",
                "tool_requested",
                "tool_started",
                "tool_completed",
                "model_requested",
                "model_completed",
                "run_completed",
            ],
        )
        outcome = response.snapshot.events[-1].payload["outcome"]
        self.assertEqual(outcome["selected_memory_ids"], [42])
        self.assertEqual(outcome["hit_count"], 1)

    async def test_no_relevant_selection_abstains(self) -> None:
        response, _, _ = await self._run(
            '{"answer":"There is not enough relevant memory.","relevant_memory_ids":[]}',
            [_hit()],
        )

        self.assertEqual(response.answer, INSUFFICIENT_MEMORY_ANSWER)
        self.assertEqual(response.selected_memory_ids, [])
        self.assertEqual(response.hit_count, 0)

    async def test_unknown_id_and_invalid_json_fail_closed(self) -> None:
        for final_content in (
            '{"answer":"Use it.","relevant_memory_ids":[999]}',
            "not JSON",
        ):
            with self.subTest(final_content=final_content):
                response, _, _ = await self._run(final_content, [_hit()])
                self.assertEqual(response.answer, INSUFFICIENT_MEMORY_ANSWER)
                self.assertEqual(response.selected_memory_ids, [])
                self.assertEqual(response.hit_count, 0)

    async def test_planner_cannot_skip_memory_retrieval(self) -> None:
        pool = _FakePool()
        ledger = AgentRunLedger(pool)  # type: ignore[arg-type]
        service = _MemoryService([_hit()])
        runtime = MemoryAgentRuntime(service, ledger)  # type: ignore[arg-type]
        chat = _NoToolChatClient(
            '{"answer":"Use concise bullets.","relevant_memory_ids":[42]}'
        )
        agent = LocalMemoryAgent(runtime, ledger, chat)

        response = await agent.run("我的简历偏好是什么？")

        self.assertEqual(response.selected_memory_ids, [42])
        self.assertEqual(response.selected_tool, "temporal_query")
        self.assertEqual(len(service.queries), 1)
        request = service.queries[0]
        self.assertEqual(request.top_k, 30)
        self.assertEqual(request.ranking_mode, "semantic")
        self.assertIn("我的简历偏好是什么？", request.query_string)
        self.assertIn("durable user preferences", request.query_string)


if __name__ == "__main__":
    unittest.main()
