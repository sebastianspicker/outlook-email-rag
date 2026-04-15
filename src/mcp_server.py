"""
MCP Server for Email RAG.

Exposes email search as tools that any MCP client can call directly.
Run with: python -m src.mcp_server

Example MCP client settings:
{
    "mcpServers": {
        "email_search": {
            "command": "/absolute/path/to/.venv/bin/python",
            "args": ["-m", "src.mcp_server"],
            "cwd": "/absolute/path/to/outlook-email-rag"
        }
    }
}

IMPORTANT: Use absolute paths when your MCP client launches servers from a
different working directory.
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import logging
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

if TYPE_CHECKING:
    from .email_db import EmailDatabase
    from .retriever import EmailRetriever

try:
    from mcp.server.fastmcp import FastMCP as _FastMCP
    from mcp.types import ToolAnnotations as _MCPToolAnnotations

    _MCP_IMPORT_ERROR: ModuleNotFoundError | None = None
    FastMCP = _FastMCP
    ToolAnnotations = _MCPToolAnnotations
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in interpreter-specific entrypoint tests
    _MCP_IMPORT_ERROR = exc
    FastMCP = None

    @dataclass
    class _FallbackToolAnnotations:
        title: str
        readOnlyHint: bool
        destructiveHint: bool
        idempotentHint: bool
        openWorldHint: bool

    ToolAnnotations = _FallbackToolAnnotations

from .config import get_settings
from .sanitization import (
    apply_privacy_guardrails,
    privacy_mode_policy,
    sanitize_untrusted_text,
)

logger = logging.getLogger(__name__)

load_dotenv()
# Clear any previously cached settings so they reflect the .env values
# loaded above (the lru_cache in get_settings() would otherwise return
# a stale Settings instance built before load_dotenv ran).
get_settings.cache_clear()

# ── Instance lock ─────────────────────────────────────────────

_lock_fd = None


def _acquire_instance_lock() -> None:
    """Acquire an exclusive file lock to prevent concurrent instances.

    Uses ``fcntl.flock`` (Unix) with ``LOCK_EX | LOCK_NB``.  On Windows
    (no ``fcntl``), logs a warning and continues without locking.
    """
    global _lock_fd
    try:
        import fcntl
    except ImportError:
        logger.warning("fcntl not available (Windows?) — skipping instance lock")
        return

    _chromadb_path, sqlite_path = _resolved_runtime_paths()
    data_dir = Path(sqlite_path).parent
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / "mcp_server.lock"

    fd = open(lock_path, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        # Lock held by another process — read existing PID for diagnostics
        try:
            existing_pid = lock_path.read_text().strip()
        except Exception:
            existing_pid = "unknown"

        # Check if the locking process is still alive.  A stale lock from
        # a crashed server should not block startup.
        stale = False
        if existing_pid and existing_pid != "unknown":
            try:
                os.kill(int(existing_pid), 0)  # signal 0 = existence check
            except (OSError, ValueError):
                stale = True

        if stale:
            logger.warning(
                "Stale lock from dead process (PID %s) — reclaiming lock.",
                existing_pid,
            )
            fd.close()
            fd = open(lock_path, "w")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                logger.error("Failed to reclaim stale lock.")
                fd.close()
                raise SystemExit(1) from None
        else:
            logger.error(
                "Another MCP server instance is already running (PID %s). Only one instance can access the database at a time.",
                existing_pid,
            )
            fd.close()
            raise SystemExit(1) from None

    fd.write(str(os.getpid()))
    fd.flush()
    _lock_fd = fd
    atexit.register(_release_lock)


def _release_lock() -> None:
    global _lock_fd
    if _lock_fd is not None:
        try:
            _lock_fd.close()
        except Exception:
            logger.debug("Failed to close MCP server lock during shutdown", exc_info=True)
        _lock_fd = None


def _log_startup_info() -> None:
    """Log diagnostic info to stderr on startup."""
    chromadb_path, sqlite_path = _resolved_runtime_paths()
    settings = get_settings()
    sqlite_exists = os.path.exists(sqlite_path)
    chromadb_exists = os.path.isdir(chromadb_path)
    logger.info(
        "MCP server starting — PID=%d python=%s cwd=%s",
        os.getpid(),
        sys.executable,
        os.getcwd(),
    )
    logger.info(
        "  sqlite=%s (exists=%s) chromadb=%s (exists=%s)",
        sqlite_path,
        sqlite_exists,
        chromadb_path,
        chromadb_exists,
    )
    logger.info(
        "  profile=%s body=%d tokens=%d full=%d json=%d triage_cap=%d search_cap=%d",
        settings.mcp_model_profile,
        settings.mcp_max_body_chars,
        settings.mcp_max_response_tokens,
        settings.mcp_max_full_body_chars,
        settings.mcp_max_json_response_chars,
        settings.mcp_max_triage_results,
        settings.mcp_max_search_results,
    )


def _missing_mcp_runtime_message() -> str:
    return (
        "The active Python interpreter does not have the 'mcp' package installed. "
        "Use '.venv/bin/python -m src.mcp_server' or install this project's dependencies in the current interpreter."
    )


class _MissingFastMCP:
    """Fallback MCP runtime placeholder when the active interpreter lacks the mcp package."""

    def __init__(self, _name: str):
        self._name = _name

    def tool(self, *args: Any, **kwargs: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def _decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            return fn

        return _decorator

    def run(self) -> None:
        raise SystemExit(_missing_mcp_runtime_message())


mcp = FastMCP("email_mcp") if FastMCP is not None else _MissingFastMCP("email_mcp")

_retriever = None
_retriever_lock = threading.Lock()
_runtime_chromadb_path: str | None = None
_runtime_sqlite_path: str | None = None


def set_runtime_archive_paths(*, chromadb_path: str | None = None, sqlite_path: str | None = None) -> None:
    """Persist runtime archive overrides for later read tools in this server process."""
    global _runtime_chromadb_path, _runtime_sqlite_path
    if chromadb_path:
        _runtime_chromadb_path = chromadb_path
    if sqlite_path:
        _runtime_sqlite_path = sqlite_path


def _resolved_runtime_paths() -> tuple[str, str]:
    settings = get_settings()
    return (_runtime_chromadb_path or settings.chromadb_path, _runtime_sqlite_path or settings.sqlite_path)


def _tool_annotations(title: str) -> Any:
    """Standardized non-destructive MCP tool annotations."""
    return ToolAnnotations(
        title=title,
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


def get_retriever() -> EmailRetriever:
    """Lazy singleton for the email retriever.

    Thread-safe via double-checked locking: the fast path reads
    ``_retriever`` without the lock (safe under CPython GIL since
    pointer reads are atomic).  The slow path acquires the lock and
    rechecks to prevent duplicate initialization.  Once initialized,
    the retriever is read-only and shared across all worker threads.
    """
    global _retriever
    if _retriever is not None:
        return _retriever
    with _retriever_lock:
        if _retriever is None:
            from .retriever import EmailRetriever

            chromadb_path, _sqlite_path = _resolved_runtime_paths()
            try:
                _retriever = EmailRetriever(chromadb_path=chromadb_path)
            except TypeError as exc:
                if "chromadb_path" not in str(exc):
                    raise
                _retriever = EmailRetriever()
    if _retriever is None:
        raise RuntimeError("Retriever initialization failed")
    return _retriever


# ── EmailDatabase helper ──────────────────────────────────────

_email_db = None
_email_db_lock = threading.Lock()

_DB_UNAVAILABLE = json.dumps({"error": "SQLite database not available. Run ingestion first."})


def get_email_db() -> EmailDatabase | None:
    """Lazy singleton for the SQLite email database.

    Thread-safe via double-checked locking (same pattern as
    ``get_retriever``).  The returned ``EmailDatabase`` instance uses
    ``check_same_thread=False`` + WAL mode, which is safe for concurrent
    reads from multiple ``asyncio.to_thread`` workers.  Write operations
    (evidence_add, etc.) are serialized by SQLite's internal WAL locking.
    """
    global _email_db
    if _email_db is not None:
        return _email_db
    with _email_db_lock:
        if _email_db is None:
            from .email_db import EmailDatabase

            _chromadb_path, sqlite_path = _resolved_runtime_paths()
            if Path(sqlite_path).exists():
                _email_db = EmailDatabase(sqlite_path)
    return _email_db


async def _offload(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a synchronous function in a thread to avoid blocking the event loop.

    ``asyncio.to_thread`` dispatches *fn* to the default thread-pool
    executor.  Each MCP tool call may run concurrently in a separate
    thread, so *fn* and the singletons it touches must be thread-safe.
    """
    if args or kwargs:
        import functools

        return await asyncio.to_thread(functools.partial(fn, *args, **kwargs))
    return await asyncio.to_thread(fn)


def _write_tool_annotations(title: str) -> Any:
    """Tool annotations for write operations."""
    return ToolAnnotations(
        title=title,
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )


def _idempotent_write_annotations(title: str) -> Any:
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
    def get_retriever() -> EmailRetriever:
        return get_retriever()

    @staticmethod
    def get_email_db() -> EmailDatabase | None:
        return get_email_db()

    offload = staticmethod(_offload)
    tool_annotations = staticmethod(_tool_annotations)
    write_tool_annotations = staticmethod(_write_tool_annotations)
    idempotent_write_annotations = staticmethod(_idempotent_write_annotations)
    DB_UNAVAILABLE = _DB_UNAVAILABLE
    sanitize = staticmethod(sanitize_untrusted_text)
    apply_privacy_guardrails = staticmethod(apply_privacy_guardrails)
    privacy_mode_policy = staticmethod(privacy_mode_policy)


if FastMCP is not None:
    from .tools import register_all

    register_all(mcp, ToolDeps())


# ── Entry Point ────────────────────────────────────────────────


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the minimal MCP server CLI parser."""
    parser = argparse.ArgumentParser(
        prog="python -m src.mcp_server",
        description="Run the Email RAG MCP server over stdio.",
    )
    parser.add_argument("--version", action="version", version="0.1.0")
    parser.add_argument("--chromadb-path", default=None, help="Custom ChromaDB path for this MCP server process.")
    parser.add_argument("--sqlite-path", default=None, help="Custom SQLite metadata path for this MCP server process.")
    return parser


def main(argv: list[str] | None = None) -> None:
    """Startup routine: acquire lock, log diagnostics, then run the server."""
    args = _build_arg_parser().parse_args(argv)
    set_runtime_archive_paths(
        chromadb_path=getattr(args, "chromadb_path", None),
        sqlite_path=getattr(args, "sqlite_path", None),
    )
    if _MCP_IMPORT_ERROR is not None:
        raise SystemExit(_missing_mcp_runtime_message()) from _MCP_IMPORT_ERROR
    _acquire_instance_lock()
    _log_startup_info()
    mcp.run()


if __name__ == "__main__":
    main()
