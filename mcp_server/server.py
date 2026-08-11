"""MindBridge MCP server.

Exposes the two tools any MCP client can call: `upsert_preference` to write a
fact and `temporal_query` to recall it. Both delegate to the same MemoryService
the REST API uses, so behaviour cannot diverge between transports.

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
        "Long-term memory for this user. Call temporal_query before answering "
        "anything that depends on their preferences, habits or past decisions. "
        "Call upsert_preference when they state a durable preference — not for "
        "one-off instructions scoped to the current task. Cite the bracketed "
        "memory ids from the returned context when you rely on them."
    ),
)


@mcp.tool()
async def upsert_preference(
    content: str,
    category: MemoryCategory = "other",
    supersedes_conflicting: bool = False,
) -> str:
    """Store a durable fact about the user, deduplicating against what is known.

    Args:
        content: The fact, as one self-contained sentence. Write "prefers uv for
            Python projects", not "yes, use that" — it has to make sense months
            later with no surrounding conversation.
        category: coding_style, tool_preference, behavioral_fact, schedule, other.
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
            category=category,
            supersedes_conflicting=supersedes_conflicting,
        )
    )
    lines = [
        f"{result.action}: [{result.record.id}] {result.record.content}",
        f"category={result.record.category}",
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
