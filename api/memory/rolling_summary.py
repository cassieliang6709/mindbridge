"""T2 — rolling summary: one structured card per day, session, or week.

中文说明：T2 将可重建的结构化摘要按日期或会话保存为卡片；模型叙事是附加层，
不会覆盖规则生成的事实来源。
"""

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
    """Decode a database row into a validated SummaryCard.

    中文：在存储边界解码 JSON 字段，并构造经过校验的 SummaryCard。
    """
    data = dict(row)
    # asyncpg returns jsonb as a string unless a codec is registered.
    # 中文：未注册 codec 时 asyncpg 返回 JSON 字符串，因此在这里统一解码。
    for key in ("developer_behavior_facts", "open_threads"):
        value = data.get(key)
        if isinstance(value, str):
            data[key] = json.loads(value)
    return SummaryCard(**data)


class RollingSummaryStore:
    """Persist structured T2 cards without hiding their reproducible source.

    中文：持久化结构化 T2 卡片，同时保留可复现的规则摘要与模型叙事边界。
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        """Create the store around an open PostgreSQL connection pool.

        中文：使用已打开的 PostgreSQL 连接池创建 T2 存储。
        """
        self._pool = pool

    async def upsert(self, card: SummaryCardCreate) -> SummaryCard:
        """Replace a period's card when its deterministic batch is rerun.

        中文：同一日期或会话的确定性批处理重跑时替换原卡，而不是生成重复卡片。
        """
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

        中文：日卡与会话卡共享一张表，因此调用方必须明确 scope；默认 day
        保护日记产品面，避免数百张会话卡混入日卡列表。
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
        """Read one day or session card by its stable scope key.

        中文：按 period 与可选 session_id 组成的稳定作用域键读取一张卡片。
        """
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
        """Return every period with a day card, oldest first.

        中文：返回所有存在日卡的时间段，按最早到最新排序。
        """
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

        中文：模型叙事只作为附加层写入，规则生成的 summary 保持不变；即使模型
        或抽取结果后来被证明有误，产品仍能展示可复现标题及其生成来源。
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
