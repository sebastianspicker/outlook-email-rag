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

import json
import os
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, ConfigDict

load_dotenv()

# Initialize MCP server
mcp = FastMCP("email_mcp")

# Lazy-load retriever (initialized on first tool call)
_retriever = None


def get_retriever():
    global _retriever
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
        description="Natural language search query. Be specific — e.g., 'budget approval from finance team in Q3 2023' works better than 'budget'.",
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
        description="Sender name or email to filter by (partial match supported). E.g., 'john' matches 'john.doe@company.com' and 'John Smith'.",
        min_length=1,
    )
    top_k: int = Field(default=10, ge=1, le=30)


class EmailSearchByDateInput(BaseModel):
    """Input for date-filtered email search."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Natural language search query.",
        min_length=1,
        max_length=500,
    )
    date_from: Optional[str] = Field(
        default=None,
        description="Start date in YYYY-MM-DD format (inclusive). E.g., '2023-01-01'.",
    )
    date_to: Optional[str] = Field(
        default=None,
        description="End date in YYYY-MM-DD format (inclusive). E.g., '2023-12-31'.",
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


# ── Tools ──────────────────────────────────────────────────────


@mcp.tool(
    name="email_search",
    annotations={
        "title": "Search Emails",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
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
    return retriever.format_results_for_claude(results)


@mcp.tool(
    name="email_search_by_sender",
    annotations={
        "title": "Search Emails by Sender",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
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
    results = retriever.search_by_sender(params.query, params.sender, top_k=params.top_k)
    return retriever.format_results_for_claude(results)


@mcp.tool(
    name="email_search_by_date",
    annotations={
        "title": "Search Emails by Date Range",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
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
    results = retriever.search_by_date(
        params.query,
        date_from=params.date_from,
        date_to=params.date_to,
        top_k=params.top_k,
    )
    return retriever.format_results_for_claude(results)


@mcp.tool(
    name="email_list_senders",
    annotations={
        "title": "List Email Senders",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
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
    for s in senders:
        name_part = f"{s['name']} " if s["name"] else ""
        lines.append(f"  {s['count']:>4} emails — {name_part}<{s['email']}>")

    return "\n".join(lines)


@mcp.tool(
    name="email_stats",
    annotations={
        "title": "Email Archive Statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
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
    stats = retriever.stats()
    return json.dumps(stats, indent=2)


# ── Entry Point ────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
