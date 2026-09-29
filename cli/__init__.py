"""Command line entry points for MindBridge.

The CLI is the operator's surface: ingest, pattern discovery, health checks and
launching the MCP server. It deliberately does not mirror the eleven MCP memory
tools — reading and revising memory happens in a conversation with a client that
has the context, not in a terminal.

中文说明：此 CLI 是操作者使用的入口，负责数据摄取、模式发现、健康检查以及启动
MCP 服务器。它有意不复刻 MCP 提供的十一个记忆工具——读取和修改记忆是在有完整
对话上下文的客户端中完成的，而不是在终端里。
"""
