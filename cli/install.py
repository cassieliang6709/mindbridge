"""Register MindBridge as a local stdio MCP server with a client.

One implementation for what used to be two 41-line scripts differing only in the
name of the CLI they call. This changes the client's MCP registration and
nothing else: it does not ingest a transcript, write a memory, install a
scheduler or touch a Docker volume.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Client:
    name: str
    # Claude Code scopes a registration to the user, the project or a local
    # session; user scope is the one that survives changing directory. Codex has
    # no equivalent flag, so the list differs rather than the code branching.
    add_flags: tuple[str, ...]
    label: str


CLIENTS = {
    "claude": Client("claude", ("--scope", "user"), "user-scoped Claude Code"),
    "codex": Client("codex", (), "local Codex"),
}


def _registered(client: Client) -> bool:
    return (
        subprocess.run(
            [client.name, "mcp", "get", "mindbridge"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        == 0
    )


def run(client_name: str, root: Path) -> int:
    client = CLIENTS[client_name]

    # docker and ollama are checked here, not later: a registration that
    # succeeds against a machine with no data layer produces a server that
    # starts and then fails on the first tool call, which reads as MindBridge
    # being broken rather than as a missing dependency.
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
