"""One-card extraction primitive used by the durable background worker."""

from __future__ import annotations

from collections.abc import Mapping

from api.memory_candidates import MemoryCandidateCreate
from api.models import NarrativeUpdate
from api.service import MemoryService

from .pipeline import extract_day
from .prompts import build_day_input
from .providers import build_provider
from .runner import _load_target


class ExtractionRejected(RuntimeError):
    """The input is valid but cannot safely produce a durable result."""


async def extract_card(
    service: MemoryService, payload: Mapping[str, object]
) -> dict[str, object]:
    """Generate T2 prose and stage T3 suggestions without promoting them."""
    period = str(payload.get("period") or "").strip()
    if not period:
        raise ExtractionRejected("period is required")
    session_value = payload.get("session_id")
    session_id = str(session_value) if session_value is not None else None
    timezone_name = str(payload.get("timezone") or "America/New_York")
    provider_name = str(payload.get("provider") or "mlx")
    if provider_name != "mlx":
        raise ExtractionRejected(
            "background extraction is local-only; hosted providers require an interactive consent"
        )

    card, facts, turns, project = await _load_target(
        service, period, session_id, timezone_name
    )
    if card is None:
        raise ExtractionRejected(f"no T2 card for {period}; run ingestion first")
    if card.narrative and not bool(payload.get("force", False)):
        return {
            "period": period,
            "session_id": session_id,
            "summary_id": card.id,
            "skipped": "narrative already exists",
            "candidate_ids": [],
        }

    settings = service.settings
    max_input_tokens = int(payload.get("max_input_tokens") or 4_000)
    day = build_day_input(
        period,
        facts,
        turns,
        max_input_tokens=max_input_tokens,
        project=project,
    )
    provider = build_provider(
        "mlx",
        None,
        str(payload.get("model") or settings.mlx_model),
        base_url=str(payload.get("base_url") or settings.mlx_url),
        timeout=settings.mlx_timeout_seconds,
        wire_model=(
            str(payload["wire_model"]) if payload.get("wire_model") else None
        ),
    )
    result = await extract_day(
        provider,
        day,
        max_attempts=int(payload.get("schema_attempts") or 3),
        session_id=session_id,
    )
    if not result.ok or result.draft is None:
        raise ExtractionRejected(
            f"model output failed schema after {len(result.attempts)} attempt(s)"
        )

    draft = result.draft
    await service.summaries.set_narrative(
        NarrativeUpdate(
            period=period,
            session_id=session_id,
            narrative=draft.narrative,
            highlights=draft.highlights,
            open_threads=draft.open_threads,
            generated_by=f"{result.provider}:{result.model}",
            model=result.model,
        )
    )

    min_confidence = float(payload.get("min_confidence") or 0.7)
    candidates = []
    receipt = {
        "summary_id": card.id,
        "provider": result.provider,
        "model": result.model,
        "prompt_version": result.prompt_version,
        "attempts": len(result.attempts),
        "first_attempt_valid": result.first_attempt_valid,
    }
    for preference in draft.preferences:
        if preference.confidence < min_confidence:
            continue
        candidate = await service.propose_memory_candidate(
            MemoryCandidateCreate(
                content=preference.content,
                namespace="operational",
                category=preference.category,
                project=preference.project,
                confidence=preference.confidence,
                evidence=preference.evidence,
                source_period=period,
                source_session_id=session_id,
                source_summary_id=card.id,
                source_receipt=receipt,
            )
        )
        candidates.append(candidate.id)

    return {
        "period": period,
        "session_id": session_id,
        "summary_id": card.id,
        "sampled_turns": day.sampled_turns,
        "total_turns": day.total_turns,
        "attempts": len(result.attempts),
        "first_attempt_valid": result.first_attempt_valid,
        "candidate_ids": candidates,
    }
