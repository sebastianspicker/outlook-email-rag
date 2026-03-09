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

import json
import threading

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import get_settings
from .mcp_models import (
    EmailIngestInput,
    EmailSearchByDateInput,
    EmailSearchByRecipientInput,
    EmailSearchBySenderInput,
    EmailSearchInput,
    EmailSearchStructuredInput,
    EmailSearchThreadInput,
    ListSendersInput,
)
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


# ── Search kwargs builder ─────────────────────────────────────

_FILTER_FIELDS = [
    "sender", "subject", "folder", "cc", "to", "bcc",
    "has_attachments", "priority", "email_type",
    "date_from", "date_to", "min_score",
    "topic_id", "cluster_id",
    "category", "is_calendar",
]
_BOOL_FIELDS = ["rerank", "hybrid", "expand_query"]


def _build_search_kwargs(params: EmailSearchStructuredInput) -> dict:
    """Build search_filtered kwargs from structured input, skipping None values."""
    kwargs: dict = {"query": params.query, "top_k": params.top_k}
    for field in _FILTER_FIELDS:
        value = getattr(params, field)
        if value is not None:
            kwargs[field] = value
    for field in _BOOL_FIELDS:
        if getattr(params, field):
            kwargs[field] = True
    return kwargs


# ── Core Search Tools ─────────────────────────────────────────


@mcp.tool(
    name="email_search",
    annotations=_tool_annotations("Search Emails"),
)
async def email_search(params: EmailSearchInput) -> str:
    """Search the email archive using natural language.

    Use for quick natural language queries without metadata filters.
    For filtered searches (by sender, date, folder, etc.), use email_search_structured.
    For auto-intent detection, use email_smart_search.
    """
    retriever = get_retriever()
    results = retriever.search(params.query, top_k=params.top_k)
    return sanitize_untrusted_text(retriever.format_results_for_claude(results))


@mcp.tool(
    name="email_search_by_sender",
    annotations=_tool_annotations("Search Emails by Sender"),
)
async def email_search_by_sender(params: EmailSearchBySenderInput) -> str:
    """Search emails filtered by a specific sender.

    Combines semantic search with sender filtering. The sender filter
    supports partial matching on both name and email address.

    Args:
        params (EmailSearchBySenderInput): Search parameters containing:
            - query (str): Natural language search query
            - sender (str): Sender name or email (partial match)
            - top_k (int): Number of results to return (default: 10)

    Returns:
        str: Formatted email results from the specified sender.
    """
    retriever = get_retriever()
    results = retriever.search_filtered(query=params.query, sender=params.sender, top_k=params.top_k)
    return sanitize_untrusted_text(retriever.format_results_for_claude(results))


@mcp.tool(
    name="email_search_by_date",
    annotations=_tool_annotations("Search Emails by Date Range"),
)
async def email_search_by_date(params: EmailSearchByDateInput) -> str:
    """Search emails within a specific date range.

    Combines semantic search with date filtering. Provide one or both of
    date_from and date_to to narrow results to a time period.

    Args:
        params (EmailSearchByDateInput): Search parameters containing:
            - query (str): Natural language search query
            - date_from (str, optional): Start date YYYY-MM-DD
            - date_to (str, optional): End date YYYY-MM-DD
            - top_k (int): Number of results to return (default: 10)

    Returns:
        str: Formatted email results within the date range.
    """
    retriever = get_retriever()
    results = retriever.search_filtered(
        query=params.query,
        date_from=params.date_from,
        date_to=params.date_to,
        top_k=params.top_k,
    )
    return sanitize_untrusted_text(retriever.format_results_for_claude(results))


@mcp.tool(
    name="email_list_senders",
    annotations=_tool_annotations("List Email Senders"),
)
async def email_list_senders(params: ListSendersInput) -> str:
    """List all unique senders in the email archive, sorted by frequency.

    Useful for discovering who is in the archive before searching for
    specific conversations. Returns sender name, email, and message count.

    Args:
        params (ListSendersInput): Parameters containing:
            - limit (int): Max senders to return (default: 30)

    Returns:
        str: Formatted list of senders with message counts.
    """
    retriever = get_retriever()
    senders = retriever.list_senders(limit=params.limit)
    if not senders:
        return "No senders found in the archive."

    lines = [f"Top {len(senders)} senders in the archive:\n"]
    for entry in senders:
        label = entry.get("name") or entry.get("email") or "unknown"
        lines.append(f"  {entry['count']:>5} emails — {label}")
    return sanitize_untrusted_text("\n".join(lines))


@mcp.tool(
    name="email_stats",
    annotations=_tool_annotations("Email Archive Stats"),
)
async def email_stats() -> str:
    """Get statistics about the email archive.

    Returns total email count, date range, number of unique senders,
    and folder distribution. Useful for understanding the scope of the
    archive before searching.

    Returns:
        str: JSON-formatted statistics about the email archive.
    """
    retriever = get_retriever()
    return json.dumps(retriever.stats(), indent=2)


@mcp.tool(
    name="email_search_structured",
    annotations=_tool_annotations("Search Emails (Structured JSON)"),
)
async def email_search_structured(params: EmailSearchStructuredInput) -> str:
    """The most powerful search tool — combines semantic query with metadata filters.

    Supports filters: sender, date range, folder, to, cc, bcc, attachments,
    priority, topic, cluster. Returns structured JSON. Also supports reranking,
    hybrid BM25 search, and query expansion. For simple unfiltered queries,
    email_search is faster.
    """
    retriever = get_retriever()
    search_kwargs = _build_search_kwargs(params)

    results = retriever.search_filtered(**search_kwargs)
    payload = retriever.serialize_results(params.query, results)
    payload["top_k"] = params.top_k
    payload["filters"] = {
        "sender": params.sender,
        "subject": params.subject,
        "folder": params.folder,
        "cc": params.cc,
        "to": params.to,
        "bcc": params.bcc,
        "has_attachments": params.has_attachments,
        "priority": params.priority,
        "email_type": params.email_type,
        "date_from": params.date_from,
        "date_to": params.date_to,
        "min_score": params.min_score,
        "rerank": params.rerank,
        "hybrid": params.hybrid,
        "topic_id": params.topic_id,
        "cluster_id": params.cluster_id,
        "expand_query": params.expand_query,
    }
    payload["model"] = get_settings().embedding_model
    return json.dumps(payload, indent=2)


@mcp.tool(
    name="email_search_by_recipient",
    annotations=_tool_annotations("Search Emails by Recipient"),
)
async def email_search_by_recipient(params: EmailSearchByRecipientInput) -> str:
    """Search emails where a specific person is in the To field.

    Combines semantic search with recipient filtering. The recipient filter
    supports partial matching on the To address field.

    Args:
        params (EmailSearchByRecipientInput): Search parameters containing:
            - query (str): Natural language search query
            - recipient (str): Recipient name or email (partial match on To)
            - top_k (int): Number of results to return (default: 10)

    Returns:
        str: Formatted email results sent to the specified recipient.
    """
    retriever = get_retriever()
    results = retriever.search_filtered(query=params.query, to=params.recipient, top_k=params.top_k)
    return sanitize_untrusted_text(retriever.format_results_for_claude(results))


@mcp.tool(
    name="email_list_folders",
    annotations=_tool_annotations("List Email Folders"),
)
async def email_list_folders() -> str:
    """List all folders in the email archive with email counts.

    Returns a sorted list of folder names and the number of emails in each.
    Useful for understanding archive structure before scoping a search.

    Returns:
        str: Formatted list of folders with email counts.
    """
    retriever = get_retriever()
    folders = retriever.list_folders()

    if not folders:
        return "No folders found in the archive."

    lines = [f"Folders in the email archive ({len(folders)} total):\n"]
    for entry in folders:
        lines.append(f"  {entry['count']:>5} emails - {entry['folder']}")
    return "\n".join(lines)


@mcp.tool(
    name="email_ingest",
    annotations=ToolAnnotations(
        title="Ingest Email Archive",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def email_ingest(params: EmailIngestInput) -> str:
    """Ingest an Outlook .olm export into the email vector database.

    Parses the archive, chunks each email, embeds the chunks, and stores
    them in ChromaDB. Already-indexed emails are skipped automatically.

    Args:
        params (EmailIngestInput): Ingestion parameters containing:
            - olm_path (str): Absolute path to the .olm file
            - max_emails (int, optional): Cap on emails to parse
            - dry_run (bool): If true, parse without writing (default: False)
            - extract_attachments (bool): If true, extract text from attachments
            - extract_entities (bool): If true, extract entities into SQLite
            - embed_images (bool): If true, embed image attachments via Visualized-BGE-M3

    Returns:
        str: JSON summary of the ingestion run.
    """
    from .ingest import ingest

    try:
        stats = ingest(
            olm_path=params.olm_path,
            max_emails=params.max_emails,
            dry_run=params.dry_run,
            extract_attachments=params.extract_attachments,
            extract_entities=params.extract_entities,
            embed_images=params.embed_images,
        )
    except FileNotFoundError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})

    return json.dumps(stats, indent=2)


@mcp.tool(
    name="email_search_thread",
    annotations=_tool_annotations("Search Email Thread"),
)
async def email_search_thread(params: EmailSearchThreadInput) -> str:
    """Retrieve all emails in a conversation thread.

    Given a ``conversation_id`` (from a previous search result), returns all
    emails in that thread sorted by date.  Use this to explore the full
    context of a conversation after finding a relevant email via search.

    Args:
        params (EmailSearchThreadInput): Parameters containing:
            - conversation_id (str): Thread identifier from prior search metadata
            - top_k (int): Max results (default: 50)

    Returns:
        str: Formatted thread of emails sorted chronologically.
    """
    retriever = get_retriever()
    results = retriever.search_by_thread(
        conversation_id=params.conversation_id,
        top_k=params.top_k,
    )
    if not results:
        return "No emails found for this conversation thread."
    return sanitize_untrusted_text(retriever.format_results_for_claude(results))


# ── Write Tool Annotations ────────────────────────────────────


def _write_tool_annotations(title: str) -> ToolAnnotations:
    """Tool annotations for write operations."""
    return ToolAnnotations(
        title=title,
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )


# ── Tool Module Registration ──────────────────────────────────


class ToolDeps:
    """Dependencies injected into tool modules to avoid circular imports."""

    get_retriever = staticmethod(get_retriever)
    get_email_db = staticmethod(get_email_db)
    tool_annotations = staticmethod(_tool_annotations)
    write_tool_annotations = staticmethod(_write_tool_annotations)
    build_search_kwargs = staticmethod(_build_search_kwargs)
    DB_UNAVAILABLE = _DB_UNAVAILABLE
    sanitize = staticmethod(sanitize_untrusted_text)


from .tools import register_all  # noqa: E402

register_all(mcp, ToolDeps)


# ── Diagnostic Tools ──────────────────────────────────────────


@mcp.tool(
    name="email_model_info",
    annotations=_tool_annotations("Embedding Model Info"),
)
async def email_model_info() -> str:
    """Return information about the active embedding backend and configuration.

    Shows which embedding model is loaded, whether sparse and ColBERT features
    are available, the compute device, and batch size settings.
    """
    retriever = get_retriever()
    settings = get_settings()

    info: dict = {
        "embedding_model": settings.embedding_model,
        "device": str(getattr(retriever.embedder, "device", "unknown")),
        "backend": type(getattr(retriever.embedder, "_model", retriever.embedder)).__name__,
        "sparse_enabled": getattr(settings, "sparse_enabled", False),
        "colbert_rerank_enabled": getattr(settings, "colbert_rerank_enabled", False),
        "batch_size": getattr(settings, "embedding_batch_size", 0),
    }

    # Check multi-vector capabilities
    multi = getattr(retriever, "embedder", None)
    if multi:
        info["has_sparse"] = getattr(multi, "has_sparse", False)
        info["has_colbert"] = getattr(multi, "has_colbert", False)

    return json.dumps(info, indent=2)


@mcp.tool(
    name="email_sparse_status",
    annotations=_tool_annotations("Sparse Index Status"),
)
async def email_sparse_status() -> str:
    """Return the status of the sparse vector index.

    Shows whether sparse vectors are stored, the count, and whether
    the in-memory sparse index is built.
    """
    settings = get_settings()
    db = get_email_db()

    status: dict = {
        "sparse_enabled": getattr(settings, "sparse_enabled", False),
        "sparse_vector_count": 0,
        "index_built": False,
    }

    if db:
        count_method = getattr(db, "sparse_vector_count", None)
        if count_method:
            status["sparse_vector_count"] = count_method()

    # Check if retriever has a built sparse index
    try:
        retriever = get_retriever()
        sparse_idx = getattr(retriever, "_sparse_index", None)
        if sparse_idx:
            status["index_built"] = getattr(sparse_idx, "_built", False)
    except Exception:  # noqa: BLE001
        pass

    return json.dumps(status, indent=2)


# ── Entry Point ────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
