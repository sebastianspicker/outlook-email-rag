"""Core search MCP tools (moved from mcp_server.py)."""

from __future__ import annotations

from ..mcp_models import (
    EmailIngestInput,
    EmailSearchInput,
    EmailSearchStructuredInput,
    EmailSearchThreadInput,
    ListSendersInput,
)
from .utils import json_error, json_response, run_with_retriever

_deps = None

_FILTER_FIELDS = [
    "sender", "subject", "folder", "cc", "to", "bcc",
    "has_attachments", "priority", "email_type",
    "date_from", "date_to", "min_score",
    "topic_id", "cluster_id",
    "category", "is_calendar",
    "attachment_name", "attachment_type",
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


async def email_search(params: EmailSearchInput) -> str:
    """Search the email archive using natural language.

    Use for quick natural language queries without metadata filters.
    For filtered searches (by sender, date, folder, etc.), use email_search_structured.
    For auto-intent detection, use email_smart_search.
    """
    def _run():
        r = _deps.get_retriever()
        return _deps.sanitize(r.format_results_for_claude(r.search(params.query, top_k=params.top_k)))
    return await _deps.offload(_run)


async def email_list_senders(params: ListSendersInput) -> str:
    """List all unique senders in the email archive, sorted by frequency.

    Useful for discovering who is in the archive before searching for
    specific conversations. Returns sender name, email, and message count.
    """
    def _run():
        r = _deps.get_retriever()
        senders = r.list_senders(limit=params.limit)
        if not senders:
            return "No senders found in the archive."
        lines = [f"Top {len(senders)} senders in the archive:\n"]
        for entry in senders:
            label = entry.get("name") or entry.get("email") or "unknown"
            lines.append(f"  {entry['count']:>5} emails — {label}")
        return _deps.sanitize("\n".join(lines))
    return await _deps.offload(_run)


async def email_stats() -> str:
    """Get statistics about the email archive.

    Returns total email count, date range, number of unique senders,
    and folder distribution. Useful for understanding the scope of the
    archive before searching.
    """
    return await run_with_retriever(_deps, lambda r: json_response(r.stats()))


async def email_search_structured(params: EmailSearchStructuredInput) -> str:
    """The most powerful search tool — combines semantic query with metadata filters.

    Supports filters: sender, date range, folder, to, cc, bcc, attachments,
    priority, topic, cluster. Returns structured JSON. Also supports reranking,
    hybrid BM25 search, and query expansion. For simple unfiltered queries,
    email_search is faster.
    """
    def _run():
        from ..config import get_settings

        r = _deps.get_retriever()
        search_kwargs = _build_search_kwargs(params)
        results = r.search_filtered(**search_kwargs)
        payload = r.serialize_results(params.query, results)
        payload["top_k"] = params.top_k
        payload["filters"] = {
            "sender": params.sender, "subject": params.subject,
            "folder": params.folder, "cc": params.cc, "to": params.to,
            "bcc": params.bcc, "has_attachments": params.has_attachments,
            "priority": params.priority, "email_type": params.email_type,
            "date_from": params.date_from, "date_to": params.date_to,
            "min_score": params.min_score, "rerank": params.rerank,
            "hybrid": params.hybrid, "topic_id": params.topic_id,
            "cluster_id": params.cluster_id, "expand_query": params.expand_query,
            "attachment_name": params.attachment_name, "attachment_type": params.attachment_type,
            "category": params.category, "is_calendar": params.is_calendar,
        }
        payload["model"] = get_settings().embedding_model
        return json_response(payload)
    return await _deps.offload(_run)


async def email_list_folders() -> str:
    """List all folders in the email archive with email counts.

    Returns a sorted list of folder names and the number of emails in each.
    Useful for understanding archive structure before scoping a search.
    """
    def _run():
        r = _deps.get_retriever()
        folders = r.list_folders()
        if not folders:
            return "No folders found in the archive."
        lines = [f"Folders in the email archive ({len(folders)} total):\n"]
        for entry in folders:
            lines.append(f"  {entry['count']:>5} emails - {entry['folder']}")
        return _deps.sanitize("\n".join(lines))
    return await _deps.offload(_run)


async def email_ingest(params: EmailIngestInput) -> str:
    """Ingest an Outlook .olm export into the email vector database.

    Parses the archive, chunks each email, embeds the chunks, and stores
    them in ChromaDB. Already-indexed emails are skipped automatically.
    """
    def _run():
        from ..ingest import ingest

        try:
            stats = ingest(
                olm_path=params.olm_path, max_emails=params.max_emails,
                dry_run=params.dry_run, extract_attachments=params.extract_attachments,
                extract_entities=params.extract_entities, embed_images=params.embed_images,
            )
        except FileNotFoundError as exc:
            return json_error(str(exc))
        except Exception as exc:  # noqa: BLE001
            return json_error(str(exc))
        return json_response(stats)
    return await _deps.offload(_run)


async def email_search_thread(params: EmailSearchThreadInput) -> str:
    """Retrieve all emails in a conversation thread.

    Given a ``conversation_id`` (from a previous search result), returns all
    emails in that thread sorted by date.
    """
    def _run():
        r = _deps.get_retriever()
        results = r.search_by_thread(conversation_id=params.conversation_id, top_k=params.top_k)
        if not results:
            return "No emails found for this conversation thread."
        return _deps.sanitize(r.format_results_for_claude(results))
    return await _deps.offload(_run)


def register(mcp_instance, deps) -> None:
    """Register core search tools."""
    global _deps
    _deps = deps

    ann = deps.tool_annotations
    mcp_instance.tool(name="email_search", annotations=ann("Search Emails"))(email_search)
    mcp_instance.tool(name="email_list_senders", annotations=ann("List Email Senders"))(email_list_senders)
    mcp_instance.tool(name="email_stats", annotations=ann("Email Archive Stats"))(email_stats)
    mcp_instance.tool(name="email_search_structured", annotations=ann("Search Emails (Structured JSON)"))(email_search_structured)
    mcp_instance.tool(name="email_list_folders", annotations=ann("List Email Folders"))(email_list_folders)
    mcp_instance.tool(name="email_ingest", annotations=deps.idempotent_write_annotations("Ingest Email Archive"))(email_ingest)
    mcp_instance.tool(name="email_search_thread", annotations=ann("Search Email Thread"))(email_search_thread)
