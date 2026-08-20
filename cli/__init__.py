"""Command line entry points for MindBridge.

The CLI is the operator's surface: ingest, pattern discovery, health checks and
launching the MCP server. It deliberately does not mirror the eleven MCP memory
tools — reading and revising memory happens in a conversation with a client that
has the context, not in a terminal.
"""
