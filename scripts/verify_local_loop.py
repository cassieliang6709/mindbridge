"""Assertions behind ``scripts/verify-local-loop.sh``.

This deliberately re-extracts one real day into T2, then treats T3 as
read-only. A verification run must not leave a synthetic preference in the
user's durable memory just to prove that writes work.

中文说明：本模块实现本地验证闭环。它会对一个真实日期重新做 T2 抽取,但把 T3
视为只读;验证不能为了证明写入路径可用而向用户长期记忆塞入一条合成偏好。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _require(condition: bool, message: str) -> None:
    """Raise a clear verification error when a required condition is false.

    中文：当必需的校验条件为假时,抛出清晰的校验错误。

    Args:
        condition: Condition that must hold. 必须成立的条件。
        message: Failure message. 失败时显示的信息。

    Raises:
        RuntimeError: If ``condition`` is false. 条件为假时抛出。
    """
    if not condition:
        raise RuntimeError(message)


async def _mcp_query(query: str) -> str:
    """Query the MCP server's temporal-search tool in a subprocess.

    中文：以子进程方式调用 MCP server 的 temporal search 工具。

    Args:
        query: Search text to send to MCP. 发送给 MCP 的检索文本。

    Returns:
        Concatenated text returned by the tool. 工具返回的拼接文本。
    """
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        env=os.environ.copy(),
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {tool.name for tool in tools.tools}
            _require(
                {
                    "get_daily_card",
                    "review_long_term_memory",
                    "upsert_preference",
                    "temporal_query",
                }
                <= names,
                f"MCP tools missing: {sorted(names)}",
            )
            result = await session.call_tool(
                "temporal_query",
                {
                    "query_string": query,
                    "top_k": 3,
                    "time_window": "all",
                    "include_superseded": False,
                },
            )
    return "\n".join(
        block.text for block in result.content if hasattr(block, "text")
    )


async def verify(api_url: str, web_url: str, requested_date: str | None) -> None:
    """Verify health, safe T2 re-extraction, and T3 recall through all routes.

    中文：验证服务健康状态、安全的 T2 重抽取,以及各访问路径对 T3 的召回。

    Args:
        api_url: API base URL. API 服务基础 URL。
        web_url: Web app base URL. Web 应用基础 URL。
        requested_date: Day card to re-extract, or None to choose one.
            指定重抽取日卡;None 时自动选择。

    Raises:
        RuntimeError: If any verification assertion fails. 任一断言失败时抛出。
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        health = (await client.get(f"{api_url}/healthz")).raise_for_status().json()
        _require(health.get("postgres") == "ok", "Postgres is not healthy")
        _require(health.get("cache") == "ok", "Redis is not healthy")
        print("PASS  data layer: Postgres + Redis healthy")

        response = await client.get(
            f"{api_url}/summaries", params={"limit": 365, "scope": "day"}
        )
        cards = response.raise_for_status().json()
        _require(bool(cards), "no T2 day cards; run ingest first")

        if requested_date:
            target = next(
                (card for card in cards if card["period"] == requested_date), None
            )
            _require(target is not None, f"no T2 day card for {requested_date}")
        else:
            target = next(
                (
                    card
                    for card in cards
                    if str(card.get("generated_by", "")).startswith("mlx:")
                ),
                cards[0],
            )
        target_date = target["period"]

    print(f"RUN   local extraction for {target_date} (T3 writes disabled)")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "extract.runner",
        "--date",
        target_date,
        "--provider",
        "mlx",
        "--force",
        "--no-preferences",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        detail = (stderr or stdout).decode("utf-8", errors="replace")[-1200:]
        raise RuntimeError(f"local MLX extraction failed:\n{detail}")

    async with httpx.AsyncClient(timeout=30.0) as client:
        cards = (
            (
                await client.get(
                    f"{api_url}/summaries",
                    params={"limit": 365, "scope": "day"},
                )
            )
            .raise_for_status()
            .json()
        )
        card = next(card for card in cards if card["period"] == target_date)
        _require(bool(card.get("narrative")), "MLX extraction wrote no T2 narrative")
        _require(
            str(card.get("generated_by", "")).startswith("mlx:"),
            f"unexpected T2 generator: {card.get('generated_by')!r}",
        )
        print(f"PASS  transcript -> MLX schema -> T2 ({target_date})")

        memories = (
            (
                await client.get(
                    f"{api_url}/memories",
                    params={"limit": 40, "include_superseded": "false"},
                )
            )
            .raise_for_status()
            .json()
        )
        _require(bool(memories), "T3 is empty; write one real preference first")
        probe = memories[0]
        query = probe["content"]
        rest = (
            (
                await client.post(
                    f"{api_url}/memories/query",
                    json={
                        "query_string": query,
                        "top_k": 3,
                        "include_superseded": False,
                    },
                )
            )
            .raise_for_status()
            .json()
        )
        rest_ids = {hit["id"] for hit in rest["hits"]}
        _require(probe["id"] in rest_ids, "REST recall missed the exact T3 probe")
        print(f"PASS  REST temporal query -> T3 memory [{probe['id']}]")

        diary = (
            (
                await client.get(
                    f"{web_url}/api/diary", params={"date": target_date}
                )
            )
            .raise_for_status()
            .json()
        )
        _require(diary.get("source") == "live", "Diary route used offline sample data")
        diary_card = next(
            card for card in diary["cards"] if card["period"] == target_date
        )
        _require(
            str(diary_card.get("generated_by", "")).startswith("mlx:"),
            "Diary route did not expose the MLX-written card",
        )
        print("PASS  Diary route -> live MLX-written T2 card")

    mcp_text = await _mcp_query(query)
    _require(
        f"[{probe['id']}]" in mcp_text,
        "MCP recall did not return the same T3 record as REST",
    )
    print(f"PASS  MCP temporal_query -> same T3 memory [{probe['id']}]")
    print("\nVERIFIED  transcript -> local 3B -> schema -> T2/T3 -> REST/Diary/MCP")


def main() -> None:
    """Parse flags and run the verification loop, exiting nonzero on failure.

    中文：解析参数并运行验证闭环;失败时以非零退出码结束。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None, help="T2 day card to re-extract")
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--web-url", default="http://127.0.0.1:3000")
    args = parser.parse_args()
    try:
        asyncio.run(verify(args.api_url.rstrip("/"), args.web_url.rstrip("/"), args.date))
    except Exception as exc:
        print(f"FAIL  {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
