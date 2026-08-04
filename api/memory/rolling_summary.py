"""T2 — rolling summary: one structured card per day or week."""

from __future__ import annotations

import json

import asyncpg

from ..models import CardScope, NarrativeUpdate, SummaryCard, SummaryCardCreate
from .tokens import count_tokens

_CARD_COLUMNS = """
    id, session_id, period, summary, developer_behavior_facts, token_count,
    created_at, updated_at, narrative, open_threads, generated_by, model,
    extracted_at
"""


def _row_to_card(row: asyncpg.Record) -> SummaryCard:
    data = dict(row)
    # asyncpg returns jsonb as a string unless a codec is registered.
    for key in ("developer_behavior_facts", "open_threads"):
        value = data.get(key)
        if isinstance(value, str):
            data[key] = json.loads(value)
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
            RETURNING {_CARD_COLUMNS}
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
        scope: CardScope = "day",
    ) -> list[SummaryCard]:
        """List cards, scoped explicitly.

        `scope` exists because session-scoped and day-scoped cards live in the
        same table. Passing session_id=None used to mean "no filter", which
        returned both — so adding per-session cards would have flooded the
        diary's day list with hundreds of session rows. The caller now has to
        say which it wants, and "day" is the default because that is the
        product surface.
        """
        rows = await self._pool.fetch(
            f"""
            SELECT {_CARD_COLUMNS}
            FROM rolling_summaries
            WHERE ($1::text IS NULL OR session_id = $1)
              AND (
                    $3 = 'all'
                 OR ($3 = 'day' AND session_id IS NULL)
                 OR ($3 = 'session' AND session_id IS NOT NULL)
              )
            ORDER BY period DESC, session_id NULLS FIRST
            LIMIT $2
            """,
            session_id,
            limit,
            scope,
        )
        return [_row_to_card(row) for row in rows]

    async def get(self, period: str, session_id: str | None = None) -> SummaryCard | None:
        row = await self._pool.fetchrow(
            f"""
            SELECT {_CARD_COLUMNS}
            FROM rolling_summaries
            WHERE period = $1
              AND (($2::text IS NULL AND session_id IS NULL) OR session_id = $2)
            """,
            period,
            session_id,
        )
        return _row_to_card(row) if row is not None else None

    async def known_periods(self) -> list[str]:
        """Every period that has a day card, oldest first."""
        rows = await self._pool.fetch(
            """
            SELECT period FROM rolling_summaries
            WHERE session_id IS NULL
            ORDER BY period ASC
            """
        )
        return [row["period"] for row in rows]

    async def set_narrative(self, update: NarrativeUpdate) -> SummaryCard | None:
        """Layer M2 prose onto an existing card.

        The rule-based `summary` is left untouched. If extraction is later found
        to be wrong, or the model is swapped, the reproducible headline is still
        there — and `generated_by` tells the UI which of the two it is showing.
        """
        facts = json.dumps(update.highlights, ensure_ascii=False)
        threads = json.dumps(update.open_threads, ensure_ascii=False)
        row = await self._pool.fetchrow(
            f"""
            UPDATE rolling_summaries
            SET narrative = $2,
                developer_behavior_facts = CASE
                    WHEN jsonb_array_length($3::jsonb) > 0
                    THEN $3::jsonb ELSE developer_behavior_facts
                END,
                open_threads = $4::jsonb,
                generated_by = $5,
                model = $6,
                extracted_at = now(),
                updated_at = now()
            WHERE period = $1
              AND (($7::text IS NULL AND session_id IS NULL) OR session_id = $7)
            RETURNING {_CARD_COLUMNS}
            """,
            update.period,
            update.narrative,
            facts,
            threads,
            update.generated_by,
            update.model,
            update.session_id,
        )
        return _row_to_card(row) if row is not None else None
