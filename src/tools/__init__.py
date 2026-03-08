"""MCP tool modules for the email RAG server.

Each module exports a ``register(mcp, deps)`` function that binds
its tools to the shared FastMCP instance.  The *deps* argument is a
``ToolDeps`` namespace providing singletons, helpers, and constants
so that tool modules never import from ``mcp_server`` directly
(which would create a circular dependency).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

from . import (
    browse,
    data_quality,
    entities,
    evidence,
    network,
    reporting,
    temporal,
    threads,
    topics,
)

_MODULES = [
    network,
    temporal,
    entities,
    threads,
    topics,
    data_quality,
    reporting,
    browse,
    evidence,
]


def register_all(mcp: "FastMCP", deps: object) -> None:
    """Register all tool modules with the MCP server."""
    for module in _MODULES:
        module.register(mcp, deps)
