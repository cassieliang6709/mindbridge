"""MindBridge MCP server.

Exposes read tools for T2 daily cards and T3 long-term memory, plus the existing
preference write and semantic recall tools. Every tool delegates to the same
MemoryService the REST API uses, so behaviour cannot diverge between transports.

Run over stdio (how Claude Desktop, Claude Code and Cursor launch it):

    python -m mcp_server.server

Register it with Claude Desktop in
~/Library/Application Support/Claude/claude_desktop_config.json:

    {
      "mcpServers": {
        "mindbridge": {
          "command": "python",
          "args": ["-m", "mcp_server.server"],
          "cwd": "/absolute/path/to/mindbridge",
          "env": {
            "MINDBRIDGE_DATABASE_URL":
              "postgresql://mindbridge:mindbridge@localhost:5433/mindbridge"
          }
        }
      }
    }

Claude Code takes the same block via `claude mcp add`. Cursor and VS Code use
their own settings file with an identical shape.
"""

# NOTE: deliberately no `from __future__ import annotations` here. FastMCP
# introspects these signatures to build each tool's JSON schema; with postponed
# evaluation the annotations arrive as strings and the Literal enums collapse to
# bare strings, which loses the allowed values a client uses to call correctly.

import asyncio
import logging
import sys
from typing import Literal

from mcp.server.fastmcp import FastMCP

from api.models import (
    MemoryCategory,
    MemoryNamespace,
    MemoryWithDecay,
    MemoryMutationRequest,
    PatternCandidate,
    PatternCandidateCreate,
    PatternDecisionRequest,
    PatternEvidence,
    SummaryCard,
    TemporalQueryRequest,
    UpsertPreferenceRequest,
)
from api.service import MemoryService
from api.settings import get_settings

# stdio is the transport, so anything on stdout corrupts the protocol frame.
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("mindbridge.mcp")


# The service is a lazily-initialised module singleton rather than a FastMCP
# lifespan value. RequestContext in mcp 1.2.0 carries only request_id, meta and
# session — there is no lifespan_context to read it back out of, so a tool
# cannot reach anything the lifespan yielded. This works on every SDK version:
# the first tool call opens the pool, later calls reuse it, and the lock stops
# two concurrent calls racing to build two pools.
_service: MemoryService | None = None
_service_lock = asyncio.Lock()


async def _get_service() -> MemoryService:
    global _service
    if _service is None:
        async with _service_lock:
            if _service is None:
                _service = await MemoryService.start(get_settings())
                logger.info(
                    "mindbridge memory ready (embedder=%s, decay=%.4f/day, "
                    "dedup=%.2f)",
                    _service.embedder.name,
                    _service.settings.decay_rate_per_day,
                    _service.settings.dedup_threshold,
                )
    return _service


mcp = FastMCP(
    "mindbridge",
    instructions=(
        "Temporal memory for this user. T2 day cards describe what happened; "
        "T3 has operational memory (how to work with the user) and reflective "
        "memory (user-confirmed patterns, values and identity hypotheses). Call "
        "get_daily_card when the user asks what they did on a day, and "
        "review_long_term_memory when they ask what is remembered about them. "
        "Call temporal_query before answering anything that depends on their "
        "preferences, habits or past decisions. Call upsert_preference only for "
        "a durable preference, not a one-off task instruction. Reflective memory "
        "must remain a candidate until the user confirms its wording. Show "
        "supporting evidence and counterevidence, and use propose_pattern only "
        "after finding at least three observations across two dates. Use "
        "resolve_pattern only after the user explicitly confirms, edits or "
        "rejects the wording. Never present an identity "
        "hypothesis as diagnosis or fact. Cite bracketed ids when relying on memory."
    ),
)


def _format_daily_card(card: SummaryCard) -> str:
    """Human-readable, source-labelled T2 context for an MCP client."""
    lines = [
        f"T2 day card [{card.id}] · {card.period}",
        f"generated_by={card.generated_by}",
        f"summary={card.summary}",
    ]
    if card.narrative:
        lines.append(f"narrative={card.narrative}")
    if card.developer_behavior_facts:
        lines.append("observed facts:")
        lines.extend(f"- {fact}" for fact in card.developer_behavior_facts)
    if card.open_threads:
        lines.append("open threads:")
        lines.extend(f"- {thread}" for thread in card.open_threads)
    lines.append(
        "Boundary: this is a T2 record of observed work, not a durable trait or diagnosis."
    )
    return "\n".join(lines)


def _format_memory(record: MemoryWithDecay) -> str:
    status = (
        f"superseded at {record.valid_at.date().isoformat()}"
        if record.valid_at is not None
        else "current"
    )
    replacement = (
        f" · superseded_by=[{record.superseded_by}]"
        if record.superseded_by is not None
        else ""
    )
    return (
        f"[{record.id}] {record.content}\n"
        f"  namespace={record.namespace} · category={record.category} "
        f"· learned={record.created_at.date().isoformat()} "
        f"· status={status}{replacement}"
    )


def _mutation_summary(action: str, target_id: int, replacement_id: int) -> str:
    return (
        f"memory_garden action={action} · target={target_id} · replacement={replacement_id}"
    )


def _format_pattern(candidate: PatternCandidate) -> str:
    lines = [
        f"Pattern Candidate [{candidate.id}] · status={candidate.status}",
        f"hypothesis={candidate.description}",
        f"confidence={candidate.confidence:.2f}",
        f"contexts={', '.join(candidate.contexts)}",
        "supporting evidence:",
    ]
    lines.extend(
        f"- {item.source_date.isoformat()} · {item.summary}"
        f"{f' · source=[{item.source_id}]' if item.source_id else ''}"
        for item in candidate.supporting_evidence
    )
    lines.append("counter-evidence:")
    if candidate.counter_evidence:
        lines.extend(
            f"- {item.source_date.isoformat()} · {item.summary}"
            f"{f' · source=[{item.source_id}]' if item.source_id else ''}"
            for item in candidate.counter_evidence
        )
    else:
        lines.append("- none recorded; absence is not proof")
    lines.append(
        "Boundary: this is an inference awaiting the user's wording, not a trait or diagnosis."
    )
    return "\n".join(lines)


@mcp.tool()
async def get_daily_card(period: str = "latest") -> str:
    """Read one T2 day card so the user can review what they did.

    Args:
        period: ISO date such as 2026-08-19, or "latest" for the newest card.

    Returns:
        The card id, date, reproducible summary, observed facts, open threads and
        optional model narrative. This is read-only and does not change T3.
    """
    service = await _get_service()
    if period.strip().lower() == "latest":
        cards = await service.list_summaries(limit=1, scope="day")
        card = cards[0] if cards else None
    else:
        card = await service.get_summary(period.strip())
    if card is None:
        return f"No T2 day card found for {period!r}."
    return _format_daily_card(card)


@mcp.tool()
async def review_long_term_memory(
    limit: int = 20,
    include_superseded: bool = False,
    namespace: Literal["operational", "reflective", "all"] = "all",
) -> str:
    """List T3 memory for an explicit user review, without semantic ranking.

    Args:
        limit: Number of newest records to show, from 1 to 100.
        include_superseded: Include old records that are no longer current.
        namespace: Review operational memory, reflective memory, or both.

    Returns:
        A newest-first audit list with memory ids, categories, learned dates and
        validity state. Listing is read-only and does not bump access counts.
    """
    safe_limit = max(1, min(limit, 100))
    service = await _get_service()
    namespaces = None if namespace == "all" else [namespace]
    records = await service.list_memories(
        safe_limit, include_superseded, namespaces
    )
    if not records:
        return "T3 contains no matching long-term memories."
    header = (
        f"T3 review · {len(records)} record(s) · "
        f"namespace={namespace} · "
        f"superseded={'included' if include_superseded else 'excluded'}"
    )
    return "\n\n".join([header, *(_format_memory(record) for record in records)])


@mcp.tool()
async def get_memory_record(memory_id: int) -> str:
    """Read one T3 row by id for a precise audit reference."""
    service = await _get_service()
    try:
        return _format_memory(await service.get_memory(memory_id))
    except KeyError:
        return f"memory [{memory_id}] not found."


@mcp.tool()
async def archive_memory(memory_id: int) -> str:
    """Close one open memory row without deleting history."""
    service = await _get_service()
    try:
        result = await service.archive_memory(memory_id)
    except KeyError:
        return f"memory [{memory_id}] not found or already closed."
    return _mutation_summary("archive", result.target_id, result.replacement_id)


@mcp.tool()
async def edit_memory(memory_id: int, content: str, decay_factor: float | None = None) -> str:
    """Replace one open memory with user-confirmed edited wording."""
    service = await _get_service()
    try:
        result = await service.edit_memory(
            memory_id,
            MemoryMutationRequest(
                action="edit",
                content=content,
                decay_factor=decay_factor,
            ),
        )
    except KeyError:
        return f"memory [{memory_id}] not found."
    except ValueError as exc:
        return str(exc)
    return _mutation_summary("edit", result.target_id, result.replacement_id)


@mcp.tool()
async def propose_pattern(
    description: str,
    supporting_evidence: list[PatternEvidence],
    contexts: list[str],
    confidence: float,
    counter_evidence: list[PatternEvidence] | None = None,
) -> str:
    """Create a Pattern Candidate outside T3 for the user to review.

    Requires at least three concrete observations across at least two dates.
    Repetition alone is not confirmation: include counter-evidence when it
    exists, and do not use diagnostic or negative personality labels.
    """
    service = await _get_service()
    candidate = await service.propose_pattern(
        PatternCandidateCreate(
            description=description,
            supporting_evidence=supporting_evidence,
            counter_evidence=counter_evidence or [],
            contexts=contexts,
            confidence=confidence,
        )
    )
    return _format_pattern(candidate)


@mcp.tool()
async def review_pattern_candidates(
    status: Literal["pending", "confirmed", "edited", "rejected", "all"] = "pending",
    limit: int = 20,
) -> str:
    """List reflective hypotheses and their evidence without changing T3."""
    service = await _get_service()
    safe_limit = max(1, min(limit, 100))
    records = await service.list_patterns(
        status=None if status == "all" else status,
        limit=safe_limit,
    )
    if not records:
        return f"No pattern candidates found for status={status}."
    return "\n\n".join(_format_pattern(record) for record in records)


@mcp.tool()
async def resolve_pattern(
    candidate_id: int,
    decision: Literal["confirm", "edit", "reject"],
    confirmed_content: str | None = None,
    resolution_note: str | None = None,
) -> str:
    """Apply the user's explicit decision to one Pattern Candidate.

    Confirm/edit creates a user-confirmed reflective T3 memory; reject keeps the
    receipt but writes no memory. Never call this from model inference alone.
    """
    service = await _get_service()
    candidate = await service.resolve_pattern(
        candidate_id,
        PatternDecisionRequest(
            decision=decision,
            confirmed_content=confirmed_content,
            resolution_note=resolution_note,
        ),
    )
    memory = (
        f"reflective_memory=[{candidate.confirmed_memory_id}]"
        if candidate.confirmed_memory_id is not None
        else "reflective_memory=none"
    )
    return f"pattern=[{candidate.id}] · status={candidate.status} · {memory}"


@mcp.tool()
async def get_daily_review(period: str = "latest") -> str:
    """Review one day across T2, both T3 lanes and pending Pattern Candidates."""
    service = await _get_service()
    review = await service.daily_review(period)
    lines = [f"# MindBridge Daily Review · {review.period}"]
    if review.card is None:
        lines.extend(["", "## T2 · What happened", "No day card found."])
    else:
        lines.extend(["", "## T2 · What happened", _format_daily_card(review.card)])
    lines.extend(["", "## Operational T3 · How AI should work with you"])
    lines.extend(
        _format_memory(memory) for memory in review.operational_memories
    )
    if not review.operational_memories:
        lines.append("No new operational memory for this day.")
    lines.extend(["", "## Reflective T3 · What you confirmed about yourself"])
    lines.extend(_format_memory(memory) for memory in review.reflective_memories)
    if not review.reflective_memories:
        lines.append("No new reflective memory for this day.")
    lines.extend(["", "## Pattern Candidates · Waiting for you"])
    lines.extend(_format_pattern(candidate) for candidate in review.pending_patterns)
    if not review.pending_patterns:
        lines.append("No pending pattern candidates.")
    return "\n\n".join(lines)


@mcp.tool()
async def upsert_preference(
    content: str,
    category: MemoryCategory = "other",
    namespace: MemoryNamespace = "operational",
    confirmed_by_user: bool = False,
    supersedes_conflicting: bool = False,
) -> str:
    """Store a durable fact about the user, deduplicating against what is known.

    Args:
        content: The fact, as one self-contained sentence. Write "prefers uv for
            Python projects", not "yes, use that" — it has to make sense months
            later with no surrounding conversation.
        category: coding_style, tool_preference, behavioral_fact, schedule, other.
            Reflective categories are confirmed_pattern, value, recurring_trigger,
            helpful_strategy and identity_hypothesis.
        namespace: operational for working preferences; reflective for a pattern,
            value or identity hypothesis the user has explicitly reviewed.
        confirmed_by_user: Must be true for reflective memory, and only after the
            user confirms the proposed wording.
        supersedes_conflicting: Set true when this contradicts something the user
            said before, e.g. they changed their mind. The old record is closed
            and kept for history rather than overwritten.

    Returns:
        Which action was taken (inserted / refreshed / superseded) and why.
    """
    service = await _get_service()
    result = await service.upsert_preference(
        UpsertPreferenceRequest(
            content=content,
            namespace=namespace,
            category=category,
            confirmed_by_user=confirmed_by_user,
            supersedes_conflicting=supersedes_conflicting,
        )
    )
    lines = [
        f"{result.action}: [{result.record.id}] {result.record.content}",
        f"namespace={result.record.namespace} · category={result.record.category}",
        f"reason={result.reason}",
    ]
    if result.matched_id is not None and result.matched_similarity is not None:
        lines.append(
            f"nearest existing=[{result.matched_id}] "
            f"cosine={result.matched_similarity:.3f}"
        )
    return "\n".join(lines)


@mcp.tool()
async def temporal_query(
    query_string: str,
    top_k: int = 5,
    time_window: Literal["7d", "30d", "90d", "1y", "all"] = "all",
    include_superseded: bool = False,
    namespace: Literal["operational", "reflective", "all"] = "all",
    project: str | None = None,
) -> str:
    """Recall stored preferences relevant to a query, newest-weighted.

    Ranking is cosine similarity discounted by age
    (score = cosine * exp(-rate * days)), so a preference the user has not
    repeated in a year loses to a recent one, and superseded records are
    excluded unless asked for.

    Args:
        query_string: What you need to know, in natural language.
        top_k: How many memories to return (1-50).
        time_window: Only consider memories learned within this window.
        include_superseded: Include closed records, scored down. Useful when the
            user asks what they used to prefer.
        namespace: Search operational memory, reflective memory, or both.
        project: Current project name. Other project-specific preferences rank
            lower; global preferences remain unaffected.

    Returns:
        A formatted context block, one memory per line with id, date and score.
    """
    windows: dict[str, int | None] = {
        "7d": 7,
        "30d": 30,
        "90d": 90,
        "1y": 365,
        "all": None,
    }
    service = await _get_service()
    result = await service.temporal_query(
        TemporalQueryRequest(
            query_string=query_string,
            top_k=top_k,
            time_window_days=windows[time_window],
            include_superseded=include_superseded,
            namespaces=None if namespace == "all" else [namespace],
            project=project,
        )
    )
    header = (
        f"{len(result.hits)} memories · decay={result.decay_rate_per_day}/day"
        f"{' · cached' if result.cache_hit else ''}"
    )
    return f"{header}\n{result.context_block}"


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
