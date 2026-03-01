"""MCP Server for Email RAG."""

from __future__ import annotations

import json
import re
import threading
from datetime import date
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import get_settings

load_dotenv()
mcp = FastMCP("email_mcp")

_retriever = None
_retriever_lock = threading.Lock()
ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
OSC_ESCAPE_RE = re.compile(r"\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)")


def get_retriever():
    global _retriever
    with _retriever_lock:
        if _retriever is None:
            from .retriever import EmailRetriever

            _retriever = EmailRetriever()
    return _retriever


class EmailSearchInput(BaseModel):
    """Input for semantic email search."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(
        ...,
        description="Natural language search query.",
        min_length=1,
        max_length=500,
    )
    top_k: int = Field(default=10, ge=1, le=30)


class EmailSearchBySenderInput(BaseModel):
    """Input for sender-filtered search."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., min_length=1, max_length=500)
    sender: str = Field(..., min_length=1)
    top_k: int = Field(default=10, ge=1, le=30)


class EmailSearchByDateInput(BaseModel):
    """Input for date-filtered search."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., min_length=1, max_length=500)
    date_from: Optional[str] = Field(default=None)
    date_to: Optional[str] = Field(default=None)
    top_k: int = Field(default=10, ge=1, le=30)

    @field_validator("date_from", "date_to")
    @classmethod
    def validate_iso_date(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def validate_date_window(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be later than date_to")
        return self


class ListSendersInput(BaseModel):
    """Input for listing senders."""

    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=30, ge=1, le=200)


class EmailSearchStructuredInput(BaseModel):
    """Structured JSON search input for automation clients."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(default=10, ge=1, le=30)
    sender: Optional[str] = Field(default=None)
    date_from: Optional[str] = Field(default=None)
    date_to: Optional[str] = Field(default=None)

    @field_validator("date_from", "date_to")
    @classmethod
    def validate_iso_date(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        date.fromisoformat(value)
        return value

    @model_validator(mode="after")
    def validate_date_window(self):
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from cannot be later than date_to")
        return self


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
    """Search through the email archive using natural language."""
    retriever = get_retriever()
    results = retriever.search(params.query, top_k=params.top_k)
    return _sanitize_untrusted_text(retriever.format_results_for_claude(results))


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
    """Search emails filtered by sender."""
    retriever = get_retriever()
    results = retriever.search_by_sender(params.query, params.sender, top_k=params.top_k)
    return _sanitize_untrusted_text(retriever.format_results_for_claude(results))


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
    """Search emails filtered by date range."""
    retriever = get_retriever()
    results = retriever.search_by_date(
        params.query,
        date_from=params.date_from,
        date_to=params.date_to,
        top_k=params.top_k,
    )
    return _sanitize_untrusted_text(retriever.format_results_for_claude(results))


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
    """List unique senders sorted by frequency."""
    retriever = get_retriever()
    senders = retriever.list_senders(limit=params.limit)

    if not senders:
        return "No senders found in the archive."

    lines = [f"Top {len(senders)} senders in the email archive:\n"]
    for sender in senders:
        safe_name = _sanitize_untrusted_text(str(sender["name"]))
        safe_email = _sanitize_untrusted_text(str(sender["email"]))
        name_part = f"{safe_name} " if safe_name else ""
        lines.append(f"  {sender['count']:>4} emails - {name_part}<{safe_email}>")

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
    """Return JSON-formatted archive stats."""
    retriever = get_retriever()
    return json.dumps(retriever.stats(), indent=2)


@mcp.tool(
    name="email_search_structured",
    annotations={
        "title": "Search Emails (Structured JSON)",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def email_search_structured(params: EmailSearchStructuredInput) -> str:
    """Search emails and return stable JSON output for automation clients."""
    retriever = get_retriever()
    results = retriever.search_filtered(
        query=params.query,
        top_k=params.top_k,
        sender=params.sender,
        date_from=params.date_from,
        date_to=params.date_to,
    )
    payload = _serialize_results(retriever, params.query, results)
    payload["top_k"] = params.top_k
    payload["filters"] = {
        "sender": params.sender,
        "date_from": params.date_from,
        "date_to": params.date_to,
    }
    payload["model"] = get_settings().embedding_model
    return json.dumps(payload, indent=2)


def _serialize_results(retriever, query: str, results) -> dict:
    if hasattr(retriever, "serialize_results"):
        return retriever.serialize_results(query, results)

    return {
        "query": query,
        "count": len(results),
        "results": [result.to_dict() for result in results],
    }


def _sanitize_untrusted_text(value: str) -> str:
    no_osc = OSC_ESCAPE_RE.sub("", value)
    no_ansi = ANSI_ESCAPE_RE.sub("", no_osc)
    no_esc = no_ansi.replace("\x1b", "")
    return "".join(ch for ch in no_esc if ch in "\n\t" or ord(ch) >= 0x20)


if __name__ == "__main__":
    mcp.run()
