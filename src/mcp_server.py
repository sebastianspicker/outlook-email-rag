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
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import get_settings
from .sanitization import sanitize_untrusted_text
from .validation import parse_iso_date
from .validation import validate_date_window as ensure_valid_date_window

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


# ── Tool Input Models ──────────────────────────────────────────


class EmailSearchInput(BaseModel):
    """Input for semantic email search."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description=(
            "Natural language search query. Be specific — e.g., "
            "'budget approval from finance team in Q3 2023' works better than 'budget'."
        ),
        min_length=1,
        max_length=500,
    )
    top_k: int = Field(
        default=10,
        description="Number of email results to return (1-30).",
        ge=1,
        le=30,
    )


class EmailSearchBySenderInput(BaseModel):
    """Input for sender-filtered email search."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Natural language search query for the content you're looking for.",
        min_length=1,
        max_length=500,
    )
    sender: str = Field(
        ...,
        description=(
            "Sender name or email to filter by (partial match supported). "
            "E.g., 'john' matches 'john.doe@company.com' and 'John Smith'."
        ),
        min_length=1,
    )
    top_k: int = Field(default=10, ge=1, le=30)


class DateRangeInput(BaseModel):
    """Reusable date-range validation model for MCP inputs."""

    date_from: Optional[str] = Field(
        default=None,
        description="Start date in YYYY-MM-DD format (inclusive). E.g., '2023-01-01'.",
    )
    date_to: Optional[str] = Field(
        default=None,
        description="End date in YYYY-MM-DD format (inclusive). E.g., '2023-12-31'.",
    )

    @field_validator("date_from", "date_to")
    @classmethod
    def validate_iso_date(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return parse_iso_date(value)

    @model_validator(mode="after")
    def validate_date_window(self):
        ensure_valid_date_window(self.date_from, self.date_to)
        return self


class EmailSearchByDateInput(DateRangeInput):
    """Input for date-filtered email search."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Natural language search query.",
        min_length=1,
        max_length=500,
    )
    top_k: int = Field(default=10, ge=1, le=30)


class ListSendersInput(BaseModel):
    """Input for listing senders."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(
        default=30,
        description="Max number of senders to return, sorted by email count.",
        ge=1,
        le=200,
    )


class EmailSearchStructuredInput(DateRangeInput):
    """Structured JSON search input for automation clients."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., min_length=1, max_length=500)
    date_from: Optional[str] = Field(
        default=None,
        description="Start date in YYYY-MM-DD format (inclusive).",
    )
    date_to: Optional[str] = Field(
        default=None,
        description="End date in YYYY-MM-DD format (inclusive).",
    )
    top_k: int = Field(default=10, ge=1, le=30)
    sender: Optional[str] = Field(default=None)
    subject: Optional[str] = Field(default=None)
    folder: Optional[str] = Field(default=None)
    cc: Optional[str] = Field(default=None, description="CC recipient filter (partial match).")
    to: Optional[str] = Field(default=None, description="To recipient filter (partial match).")
    bcc: Optional[str] = Field(default=None, description="BCC recipient filter (partial match).")
    has_attachments: Optional[bool] = Field(default=None, description="Filter by attachment presence.")
    priority: Optional[int] = Field(
        default=None, ge=0, description="Minimum priority level (emails with priority >= this value)."
    )
    min_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)


# ── Tools ──────────────────────────────────────────────────────


@mcp.tool(
    name="email_search",
    annotations=_tool_annotations("Search Emails"),
)
async def email_search(params: EmailSearchInput) -> str:
    """Search through the email archive using natural language.

    Performs semantic search across all indexed emails and returns the most
    relevant results with full email context (sender, date, subject, body).

    Use specific, descriptive queries for best results. For example:
    - "server migration plan from IT department"
    - "invoice from Acme Corp over $10,000"
    - "meeting notes about product roadmap"

    Args:
        params (EmailSearchInput): Search parameters containing:
            - query (str): Natural language search query
            - top_k (int): Number of results to return (default: 10)

    Returns:
        str: Formatted email results with metadata and relevance scores.
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

    lines = [f"Top {len(senders)} senders in the email archive:\n"]
    for sender in senders:
        safe_name = sanitize_untrusted_text(str(sender["name"]))
        safe_email = sanitize_untrusted_text(str(sender["email"]))
        name_part = f"{safe_name} " if safe_name else ""
        lines.append(f"  {sender['count']:>4} emails - {name_part}<{safe_email}>")

    return "\n".join(lines)


@mcp.tool(
    name="email_stats",
    annotations=_tool_annotations("Email Archive Statistics"),
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
    """Search emails and return stable JSON output for automation clients."""
    retriever = get_retriever()
    search_kwargs = {
        "query": params.query,
        "top_k": params.top_k,
    }
    if params.sender is not None:
        search_kwargs["sender"] = params.sender
    if params.subject is not None:
        search_kwargs["subject"] = params.subject
    if params.folder is not None:
        search_kwargs["folder"] = params.folder
    if params.cc is not None:
        search_kwargs["cc"] = params.cc
    if params.to is not None:
        search_kwargs["to"] = params.to
    if params.bcc is not None:
        search_kwargs["bcc"] = params.bcc
    if params.has_attachments is not None:
        search_kwargs["has_attachments"] = params.has_attachments
    if params.priority is not None:
        search_kwargs["priority"] = params.priority
    if params.date_from is not None:
        search_kwargs["date_from"] = params.date_from
    if params.date_to is not None:
        search_kwargs["date_to"] = params.date_to
    if params.min_score is not None:
        search_kwargs["min_score"] = params.min_score

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
        "date_from": params.date_from,
        "date_to": params.date_to,
        "min_score": params.min_score,
    }
    payload["model"] = get_settings().embedding_model
    return json.dumps(payload, indent=2)


class EmailSearchByRecipientInput(BaseModel):
    """Input for recipient-filtered email search."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Natural language search query for the content you're looking for.",
        min_length=1,
        max_length=500,
    )
    recipient: str = Field(
        ...,
        description=(
            "Recipient name or email to filter by (partial match on To field). "
            "E.g., 'alice' matches 'alice@company.com' in the To field."
        ),
        min_length=1,
    )
    top_k: int = Field(default=10, ge=1, le=30)


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


# ── New Tools ──────────────────────────────────────────────────


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


class EmailIngestInput(BaseModel):
    """Input for ingesting an OLM email archive."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    olm_path: str = Field(
        ...,
        description="Absolute path to the .olm file to ingest.",
        min_length=1,
    )
    max_emails: Optional[int] = Field(
        default=None,
        description="Optional cap on number of emails to parse.",
        ge=1,
    )
    dry_run: bool = Field(
        default=False,
        description="If true, parse and chunk without writing to the database.",
    )


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

    Returns:
        str: JSON summary of the ingestion run.
    """
    from .ingest import ingest

    try:
        stats = ingest(
            olm_path=params.olm_path,
            max_emails=params.max_emails,
            dry_run=params.dry_run,
        )
    except FileNotFoundError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": str(exc)})

    return json.dumps(stats, indent=2)


class EmailSearchThreadInput(BaseModel):
    """Input for thread search."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    conversation_id: str = Field(
        ...,
        description=(
            "The conversation_id from a previous search result's metadata. "
            "Returns all emails in that conversation thread, sorted by date."
        ),
        min_length=1,
    )
    top_k: int = Field(
        default=50,
        description="Maximum number of emails to return from the thread.",
        ge=1,
        le=100,
    )


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


# ── Entry Point ────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
