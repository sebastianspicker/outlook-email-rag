"""
MCP Server for Email RAG.

Exposes email search as tools that Claude Code can call directly.
Run with: python -m src.mcp_server

Configure in Claude Code's MCP settings:
{
    "mcpServers": {
        "email_search": {
            "command": "/path/to/.venv/bin/python",
            "args": ["-m", "src.mcp_server"],
            "cwd": "/path/to/email-rag"
        }
    }
}
"""

from __future__ import annotations

import asyncio
import json
import threading

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import get_settings
from .sanitization import sanitize_untrusted_text

load_dotenv()

mcp = FastMCP("email_mcp")

_retriever = None
_retriever_lock = threading.Lock()


def _tool_annotations(title: str) -> ToolAnnotations:
    """Standardized non-destructive MCP tool annotations."""
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


def get_retriever():
    global _retriever
    with _retriever_lock:
        if _retriever is None:
            from .retriever import EmailRetriever

            _retriever = EmailRetriever()
    return _retriever


# ── EmailDatabase helper ──────────────────────────────────────

_email_db = None
_email_db_lock = threading.Lock()

_DB_UNAVAILABLE = json.dumps({"error": "SQLite database not available. Run ingestion first."})


def get_email_db():
    """Lazy singleton for the SQLite email database."""
    global _email_db
    with _email_db_lock:
        if _email_db is None:
            import os

            from .email_db import EmailDatabase

            settings = get_settings()
            if os.path.exists(settings.sqlite_path):
                _email_db = EmailDatabase(settings.sqlite_path)
    return _email_db


async def _offload(fn, *args, **kwargs):
    """Run a synchronous function in a thread to avoid blocking the event loop."""
    if args or kwargs:
        import functools
        return await asyncio.to_thread(functools.partial(fn, *args, **kwargs))
    return await asyncio.to_thread(fn)


def _write_tool_annotations(title: str) -> ToolAnnotations:
    """Tool annotations for write operations."""
    return ToolAnnotations(
        title=title,
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )


def _idempotent_write_annotations(title: str) -> ToolAnnotations:
    """Tool annotations for idempotent write operations (report/export/ingest)."""
    return ToolAnnotations(
        title=title,
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


# ── Tool Module Registration ──────────────────────────────────


class ToolDeps:
    """Dependencies injected into tool modules to avoid circular imports."""

    @staticmethod
    def get_retriever():
        return get_retriever()

    @staticmethod
    def get_email_db():
        return get_email_db()

    offload = staticmethod(_offload)
    tool_annotations = staticmethod(_tool_annotations)
    write_tool_annotations = staticmethod(_write_tool_annotations)
    idempotent_write_annotations = staticmethod(_idempotent_write_annotations)
    DB_UNAVAILABLE = _DB_UNAVAILABLE
    sanitize = staticmethod(sanitize_untrusted_text)


from .tools import register_all  # noqa: E402

register_all(mcp, ToolDeps)


# ── Re-exports for backward compatibility ─────────────────────

from .mcp_models import (  # noqa: E402, F401
    EmailIngestInput,
    EmailSearchInput,
    EmailSearchStructuredInput,
    EmailSearchThreadInput,
    ListSendersInput,
)
from .tools.search import (  # noqa: E402, F401
    _build_search_kwargs,
    email_ingest,
    email_list_folders,
    email_list_senders,
    email_search,
    email_search_structured,
    email_search_thread,
    email_stats,
)

# ── Entry Point ────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
