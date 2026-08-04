"""T2 — rolling summary: one structured card per day or week."""

from __future__ import annotations

import json

import asyncpg

from ..models import SummaryCard, SummaryCardCreate
from .tokens import count_tokens


def _row_to_card(row: asyncpg.Record) -> SummaryCard:
    data = dict(row)
    facts = data.get("developer_behavior_facts")
    # asyncpg returns jsonb as a string unless a codec is registered.
    if isinstance(facts, str):
        data["developer_behavior_facts"] = json.loads(facts)
    return SummaryCard(**data)


class RollingSummaryStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert(self, card: SummaryCardCreate) -> SummaryCard:
        """Re-running a period's batch replaces that card instead of duplicating it."""
        facts = json.dumps(card.developer_behavior_facts, ensure_ascii=False)
        tokens = count_tokens(card.summary) + sum(
            count_tokens(fact) for fact in card.developer_behavior_facts
        )
        conflict = (
            "(session_id, period) WHERE session_id IS NOT NULL"
            if card.session_id is not None
            else "(period) WHERE session_id IS NULL"
        )
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO rolling_summaries
                (session_id, period, summary, developer_behavior_facts, token_count)
            VALUES ($1, $2, $3, $4::jsonb, $5)
            ON CONFLICT {conflict} DO UPDATE SET
                summary = EXCLUDED.summary,
                developer_behavior_facts = EXCLUDED.developer_behavior_facts,
                token_count = EXCLUDED.token_count,
                updated_at = now()
            RETURNING id, session_id, period, summary, developer_behavior_facts,
                      token_count, created_at, updated_at
            """,
            card.session_id,
            card.period,
            card.summary,
            facts,
            tokens,
        )
        assert row is not None
        return _row_to_card(row)

    async def list_cards(
        self,
        session_id: str | None = None,
        limit: int = 30,
    ) -> list[SummaryCard]:
        rows = await self._pool.fetch(
            """
            SELECT id, session_id, period, summary, developer_behavior_facts,
                   token_count, created_at, updated_at
            FROM rolling_summaries
            WHERE ($1::text IS NULL OR session_id = $1)
            ORDER BY period DESC
            LIMIT $2
            """,
            session_id,
            limit,
        )
        return [_row_to_card(row) for row in rows]

    async def get(self, period: str, session_id: str | None = None) -> SummaryCard | None:
        row = await self._pool.fetchrow(
            """
            SELECT id, session_id, period, summary, developer_behavior_facts,
                   token_count, created_at, updated_at
            FROM rolling_summaries
            WHERE period = $1
              AND (($2::text IS NULL AND session_id IS NULL) OR session_id = $2)
            """,
            period,
            session_id,
        )
        return _row_to_card(row) if row is not None else None
