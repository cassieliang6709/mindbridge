"""MemoryService — the one place the tiers are orchestrated.

Both transports call this: the REST API in api/main.py and the MCP tools in
mcp_server/server.py. Neither owns any memory logic, so `upsert_preference`
over MCP and `POST /memories` over HTTP cannot drift apart.
"""

from __future__ import annotations

import logging
from typing import Self

import asyncpg

from .cache import QueryCache
from .db import apply_schema, create_pool
from .embeddings import Embedder, build_embedder
from .memory import (
    RollingSummaryStore,
    SessionBufferStore,
    VectorMemoryStore,
    count_tokens,
)
from .models import (
    MemoryHit,
    SessionBuffer,
    SummaryCard,
    SummaryCardCreate,
    TemporalQueryRequest,
    TemporalQueryResult,
    Turn,
    TurnCreate,
    UpsertPreferenceRequest,
    UpsertPreferenceResult,
)
from .settings import Settings, get_settings

logger = logging.getLogger(__name__)

_QUERY_NAMESPACE = "temporal_query"

# Above the dedup threshold an incoming fact is the same fact. Between these two
# it is close enough to be about the same subject but different enough to be a
# change of mind — the case `supersedes_conflicting` handles.
_CONFLICT_THRESHOLD = 0.75


class MemoryService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        embedder: Embedder,
        cache: QueryCache,
        settings: Settings,
    ) -> None:
        self.settings = settings
        self.embedder = embedder
        self.cache = cache
        self._pool = pool
        self.turns = SessionBufferStore(pool, settings.session_buffer_window)
        self.summaries = RollingSummaryStore(pool)
        self.vectors = VectorMemoryStore(
            pool,
            decay_rate_per_day=settings.decay_rate_per_day,
            dedup_threshold=settings.dedup_threshold,
            superseded_penalty=settings.superseded_penalty,
        )

    # --- lifecycle --------------------------------------------------------

    @classmethod
    async def start(cls, settings: Settings | None = None) -> Self:
        settings = settings or get_settings()
        if settings.embedding_provider == "hashing":
            logger.warning(
                "embedding provider is 'hashing': lexical overlap only, not "
                "semantic. Set MINDBRIDGE_EMBEDDING_PROVIDER=openai|gemini for "
                "real retrieval quality."
            )
        pool = await create_pool(settings)
        await apply_schema(pool, settings)
        embedder = build_embedder(settings)
        cache = await QueryCache.connect(settings)
        return cls(pool, embedder, cache, settings)

    async def close(self) -> None:
        await self.cache.close()
        await self._pool.close()

    async def health(self) -> dict[str, object]:
        await self._pool.fetchval("SELECT 1")
        return {
            "status": "ok",
            "postgres": "ok",
            "cache": "ok" if self.cache.enabled else "disabled",
            "embedder": self.embedder.name,
            "memories": await self.vectors.count(),
            "settings": self.settings.masked(),
        }

    # --- T1 ---------------------------------------------------------------

    async def add_turn(self, session_id: str, turn: TurnCreate) -> Turn:
        return await self.turns.append(session_id, turn)

    async def read_buffer(self, session_id: str) -> SessionBuffer:
        return await self.turns.read(session_id)

    # --- T2 ---------------------------------------------------------------

    async def write_summary(self, card: SummaryCardCreate) -> SummaryCard:
        return await self.summaries.upsert(card)

    async def list_summaries(
        self, session_id: str | None = None, limit: int = 30
    ) -> list[SummaryCard]:
        return await self.summaries.list_cards(session_id, limit)

    # --- T3 ---------------------------------------------------------------

    async def upsert_preference(
        self, request: UpsertPreferenceRequest
    ) -> UpsertPreferenceResult:
        """Dedup-then-write.

        1. Embed the incoming fact.
        2. Find the nearest still-open record in the same category.
        3. At or above dedup_threshold: refresh it, insert nothing.
        4. Otherwise insert, and optionally close a conflicting neighbour.
        """
        [embedding] = await self.embedder.embed([request.content])
        match = await self.vectors.nearest_open(embedding, request.category)
        similarity = match.similarity if match else None

        if match is not None and similarity is not None:
            if similarity >= self.settings.dedup_threshold:
                record = await self.vectors.refresh(match.record.id)
                await self.cache.invalidate_namespace(_QUERY_NAMESPACE)
                return UpsertPreferenceResult(
                    action="refreshed",
                    record=record,
                    matched_id=match.record.id,
                    matched_similarity=similarity,
                    reason=(
                        f"cosine {similarity:.3f} >= threshold "
                        f"{self.settings.dedup_threshold:.2f}: same fact, "
                        "refreshed instead of duplicated"
                    ),
                )

        record = await self.vectors.insert(
            request.content,
            request.category,
            embedding,
            request.decay_factor,
        )

        action = "inserted"
        reason = (
            f"nearest open record cosine {similarity:.3f} < threshold "
            f"{self.settings.dedup_threshold:.2f}: new fact"
            if similarity is not None
            else "no existing record in this category: new fact"
        )
        if (
            request.supersedes_conflicting
            and match is not None
            and similarity is not None
            and similarity >= _CONFLICT_THRESHOLD
        ):
            await self.vectors.supersede(match.record.id, record.id)
            action = "superseded"
            reason = (
                f"cosine {similarity:.3f} is in the conflict band "
                f"[{_CONFLICT_THRESHOLD:.2f}, {self.settings.dedup_threshold:.2f}): "
                f"closed record {match.record.id} and replaced it"
            )

        await self.cache.invalidate_namespace(_QUERY_NAMESPACE)
        return UpsertPreferenceResult(
            action=action,  # type: ignore[arg-type]
            record=record,
            matched_id=match.record.id if match else None,
            matched_similarity=similarity,
            reason=reason,
        )

    async def temporal_query(
        self, request: TemporalQueryRequest
    ) -> TemporalQueryResult:
        cache_key = QueryCache.key(
            _QUERY_NAMESPACE,
            {
                "q": request.query_string.strip().lower(),
                "k": request.top_k,
                "w": request.time_window_days,
                "c": sorted(request.categories) if request.categories else None,
                "s": request.include_superseded,
            },
        )
        cached = await self.cache.get(cache_key)
        if cached is not None:
            return TemporalQueryResult.model_validate({**cached, "cache_hit": True})

        [embedding] = await self.embedder.embed([request.query_string])
        hits = await self.vectors.search(
            embedding,
            top_k=request.top_k,
            time_window_days=request.time_window_days,
            categories=request.categories,
            include_superseded=request.include_superseded,
        )
        result = TemporalQueryResult(
            query=request.query_string,
            hits=hits,
            decay_rate_per_day=self.settings.decay_rate_per_day,
            cache_hit=False,
            context_block=format_context(hits),
        )
        await self.cache.set(cache_key, result.model_dump(mode="json"))
        return result


def format_context(hits: list[MemoryHit]) -> str:
    """Render hits as the compact block a model is meant to receive.

    Every line carries provenance — id, date, score — so the model can cite what
    it used and a reader can tell a fresh preference from a decayed one.
    """
    if not hits:
        return "No stored memory matched this query."
    lines = ["Known preferences (most relevant first):"]
    for hit in hits:
        state = (
            "open"
            if hit.valid_at is None
            else f"superseded {hit.valid_at.date().isoformat()}"
        )
        lines.append(
            f"- [{hit.id}] {hit.content} "
            f"(category={hit.category}, learned={hit.created_at.date().isoformat()}, "
            f"{state}, cosine={hit.cosine_similarity:.3f}, score={hit.score:.3f})"
        )
    return "\n".join(lines)


def context_token_count(hits: list[MemoryHit]) -> int:
    return count_tokens(format_context(hits))
