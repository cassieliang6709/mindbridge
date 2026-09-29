"""Register MindBridge as a local stdio MCP server with a client.

One implementation for what used to be two 41-line scripts differing only in the
name of the CLI they call. This changes the client's MCP registration and
nothing else: it does not ingest a transcript, write a memory, install a
scheduler or touch a Docker volume.

中文说明：把 MindBridge 注册为客户端的本地 stdio MCP 服务器。这里用一份
实现替代了以前两个 41 行、仅调用的 CLI 名称不同的脚本。此命令只改动客户端
的 MCP 注册信息，别的什么都不做:不摄取对话记录、不写记忆、不安装调度器、
也不动 Docker 卷。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Client:
    """One MCP client MindBridge knows how to register with.

    中文：MindBridge 知道如何注册的某个 MCP 客户端。
    """

    name: str
    # Claude Code scopes a registration to the user, the project or a local
    # session; user scope is the one that survives changing directory. Codex has
    # no equivalent flag, so the list differs rather than the code branching.
    # 中文：Claude Code 的注册可以限定在用户、项目或本地会话三种作用域；
    # 其中用户作用域(user scope)是唯一能在切换目录后依然生效的。Codex
    # 没有对应的 flag，所以这里是靠不同的参数列表来区分，而不是靠代码分支。
    add_flags: tuple[str, ...]
    label: str


CLIENTS = {
    "claude": Client("claude", ("--scope", "user"), "user-scoped Claude Code"),
    "codex": Client("codex", (), "local Codex"),
}


def _registered(client: Client) -> bool:
    """Whether `client` already has MindBridge registered as an MCP server.

    中文：检查该客户端是否已经把 MindBridge 注册为 MCP 服务器。
    """
    return (
        subprocess.run(
            [client.name, "mcp", "get", "mindbridge"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def run(client_name: str, root: Path) -> int:
    """Register MindBridge as an MCP server with the named client.

    中文：向指定名称的客户端注册 MindBridge 作为 MCP 服务器。

    Args:
        client_name: Key into CLIENTS, either "claude" or "codex". CLIENTS
            字典的键，取值为 "claude" 或 "codex"。
        root: Repository root, used to locate `.env` and the installed
            launcher. 仓库根目录，用于定位 `.env` 和已安装的启动脚本。

    Returns:
        0 on success, 1 if a prerequisite is missing, or the underlying
        `mcp add` subprocess's exit code on failure. 成功时返回 0；缺少前置
        依赖时返回 1；否则返回底层 `mcp add` 子进程的退出码。
    """
    client = CLIENTS[client_name]

    # docker and ollama are checked here, not later: a registration that
    # succeeds against a machine with no data layer produces a server that
    # starts and then fails on the first tool call, which reads as MindBridge
    # being broken rather than as a missing dependency.
    # 中文：之所以在这里而不是稍后检查 docker 和 ollama，是因为如果在没有
    # 数据层的机器上注册成功，会得到一个能启动、但一遇到第一次工具调用就
    # 失败的服务器——这看起来像是 MindBridge 坏了，而不是缺少依赖。
    for command in (client.name, "docker", "ollama"):
        if shutil.which(command) is None:
            print(f"missing required command: {command}")
            return 1

    if not (root / ".env").is_file():
        print("missing .env — copy .env.example to .env first")
        return 1

    launcher = root / ".venv/bin/mindbridge-mcp"
    if not launcher.is_file():
        print(
            f"missing {launcher} — create .venv, install requirements.txt, "
            "then run: .venv/bin/pip install -e ."
        )
        return 1

    if _registered(client):
        print(
            f"MindBridge is already registered in {client.name}. "
            "Existing configuration was left unchanged."
        )
    else:
        result = subprocess.run(
            [client.name, "mcp", "add", *client.add_flags, "mindbridge", "--", str(launcher)]
        )
        if result.returncode != 0:
            return result.returncode
        print(f"MindBridge registered as a {client.label} MCP server.")

    print("Start the local data layer: docker compose up -d db redis")
    print("Ensure Ollama has the embedder: ollama pull nomic-embed-text")
    print(f"Then restart {client.name} or open a fresh session and run: /mcp")
    print("Check the whole loop at once with: mindbridge doctor")
    return 0
