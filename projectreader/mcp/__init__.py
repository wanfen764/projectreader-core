"""Public MCP integration for ProjectReader Core."""

from .server import (
    CORE_MCP_TOOLS,
    MCP_PROTOCOL_VERSION,
    ProjectReaderMcpAdapter,
    ProjectReaderMcpServer,
    create_server,
    serve_repository,
)

__all__ = [
    "CORE_MCP_TOOLS",
    "MCP_PROTOCOL_VERSION",
    "ProjectReaderMcpAdapter",
    "ProjectReaderMcpServer",
    "create_server",
    "serve_repository",
]
