"""Pydantic input models for MCP tools.

All MCP tool parameter models live here. Two base classes eliminate
repeated ``model_config`` declarations:

- ``StrictInput``: strips whitespace from strings, forbids extra fields.
- ``PlainInput``: forbids extra fields only (no string stripping).

``DateRangeInput`` is a mixin providing ISO-date validation for
``date_from`` / ``date_to`` pairs.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .validation import parse_iso_date
from .validation import validate_date_window as ensure_valid_date_window

# ── Base Classes ─────────────────────────────────────────────


class StrictInput(BaseModel):
    """Base for MCP inputs with whitespace stripping."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class PlainInput(BaseModel):
    """Base for MCP inputs without whitespace stripping."""

    model_config = ConfigDict(extra="forbid")


class DateRangeInput(BaseModel):
    """Reusable date-range validation mixin for MCP inputs."""

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


# ── Core Search Inputs ───────────────────────────────────────


class EmailSearchInput(StrictInput):
    """Input for semantic email search."""

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


class EmailSearchBySenderInput(StrictInput):
    """Input for sender-filtered email search."""

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


class EmailSearchByDateInput(DateRangeInput, StrictInput):
    """Input for date-filtered email search."""

    query: str = Field(
        ...,
        description="Natural language search query.",
        min_length=1,
        max_length=500,
    )
    top_k: int = Field(default=10, ge=1, le=30)


class EmailSearchByRecipientInput(StrictInput):
    """Input for recipient-filtered email search."""

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


class EmailSearchStructuredInput(DateRangeInput, StrictInput):
    """Structured JSON search input for automation clients."""

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
    rerank: bool = Field(
        default=False,
        description="Re-rank results with cross-encoder for better precision (slower).",
    )
    hybrid: bool = Field(
        default=False,
        description="Use hybrid semantic + BM25 keyword search for better recall.",
    )
    topic_id: Optional[int] = Field(
        default=None, ge=0,
        description="Filter to emails assigned to this topic (from email_topics tool).",
    )
    cluster_id: Optional[int] = Field(
        default=None, ge=0,
        description="Filter to emails in this cluster (from email_clusters tool).",
    )
    expand_query: bool = Field(
        default=False,
        description="Expand query with semantically related terms for better recall.",
    )


class EmailSearchThreadInput(StrictInput):
    """Input for thread search."""

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


class SmartSearchInput(StrictInput):
    """Input for intelligent auto-routing search."""

    query: str = Field(
        ..., min_length=1, max_length=500,
        description="Natural language search query.",
    )
    top_k: int = Field(default=10, ge=1, le=30)


class FindSimilarInput(StrictInput):
    """Input for finding similar emails."""

    uid: Optional[str] = Field(default=None, description="Email UID to find similar emails for.")
    query: Optional[str] = Field(default=None, description="Query text to find similar emails for.")
    top_k: int = Field(default=10, ge=1, le=50)


# ── Archive Info Inputs ──────────────────────────────────────


class ListSendersInput(PlainInput):
    """Input for listing senders."""

    limit: int = Field(
        default=30,
        description="Max number of senders to return, sorted by email count.",
        ge=1,
        le=200,
    )


class QuerySuggestionsInput(PlainInput):
    """Input for search suggestions."""

    limit: int = Field(default=10, ge=1, le=30, description="Number of suggestions per category.")


# ── Ingestion Input ──────────────────────────────────────────


class EmailIngestInput(StrictInput):
    """Input for ingesting an OLM email archive."""

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
    extract_entities: bool = Field(
        default=False,
        description="If true, extract named entities (people, orgs, locations) during ingestion.",
    )


# ── Network Analysis Inputs ──────────────────────────────────


class TopContactsInput(StrictInput):
    """Input for finding top contacts."""

    email_address: str = Field(
        ..., description="Email address to find top contacts for.", min_length=1
    )
    limit: int = Field(default=20, ge=1, le=100)


class CommunicationBetweenInput(StrictInput):
    """Input for bidirectional communication stats."""

    email_a: str = Field(..., description="First email address.", min_length=1)
    email_b: str = Field(..., description="Second email address.", min_length=1)


class NetworkAnalysisInput(PlainInput):
    """Input for network analysis."""

    top_n: int = Field(default=20, ge=1, le=100, description="Number of top nodes to return.")


# ── Temporal Analysis Inputs ─────────────────────────────────


class VolumeOverTimeInput(DateRangeInput, StrictInput):
    """Input for email volume over time."""

    period: str = Field(
        default="day",
        description="Aggregation period: 'day', 'week', or 'month'.",
    )
    sender: Optional[str] = Field(default=None, description="Filter by sender email.")


class ResponseTimesInput(StrictInput):
    """Input for response time stats."""

    sender: Optional[str] = Field(default=None, description="Filter by replier email.")
    limit: int = Field(default=20, ge=1, le=100)


# ── Entity Inputs ────────────────────────────────────────────


class EntitySearchInput(StrictInput):
    """Input for entity search."""

    entity: str = Field(..., description="Entity text to search for (partial match).", min_length=1)
    entity_type: Optional[str] = Field(
        default=None,
        description="Filter by entity type: 'organization', 'url', 'phone', 'mention', 'email'.",
    )
    limit: int = Field(default=20, ge=1, le=100)


class ListEntitiesInput(PlainInput):
    """Input for listing top entities."""

    entity_type: Optional[str] = Field(
        default=None, description="Filter by type: 'organization', 'url', 'phone', 'mention', 'email'."
    )
    limit: int = Field(default=20, ge=1, le=100)


class EntityNetworkInput(StrictInput):
    """Input for entity co-occurrence network."""

    entity: str = Field(..., description="Entity to find co-occurrences for.", min_length=1)
    limit: int = Field(default=20, ge=1, le=100)


class FindPeopleInput(StrictInput):
    """Input for finding people by name."""

    name: str = Field(
        ...,
        description="Person name to search for (partial match). E.g., 'john' or 'smith'.",
        min_length=1,
    )
    limit: int = Field(default=20, ge=1, le=100)


class EntityTimelineInput(StrictInput):
    """Input for entity mention timeline."""

    entity: str = Field(
        ...,
        description="Entity text to track over time (partial match).",
        min_length=1,
    )
    period: str = Field(
        default="month",
        description="Aggregation period: 'day', 'week', or 'month'.",
    )


# ── Thread Intelligence Inputs ───────────────────────────────


class ThreadSummaryInput(StrictInput):
    """Input for thread summarization."""

    conversation_id: str = Field(
        ..., description="Conversation thread ID from a previous search result.", min_length=1
    )
    max_sentences: int = Field(default=5, ge=1, le=20)


class ActionItemsInput(StrictInput):
    """Input for action item extraction."""

    conversation_id: Optional[str] = Field(
        default=None, description="Thread ID. If omitted, scans recent emails."
    )
    days: Optional[int] = Field(default=None, ge=1, le=365, description="Scan emails from last N days.")
    limit: int = Field(default=20, ge=1, le=100)


class DecisionsInput(StrictInput):
    """Input for decision extraction."""

    conversation_id: Optional[str] = Field(
        default=None, description="Thread ID to extract decisions from."
    )
    days: Optional[int] = Field(default=None, ge=1, le=365)


# ── Topics & Clusters Inputs ────────────────────────────────


class SearchByTopicInput(PlainInput):
    """Input for searching by topic."""

    topic_id: int = Field(..., description="Topic ID from email_topics results.", ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class TopKeywordsInput(StrictInput):
    """Input for top keywords."""

    sender: Optional[str] = Field(default=None, description="Filter by sender email.")
    folder: Optional[str] = Field(default=None, description="Filter by folder name.")
    limit: int = Field(default=30, ge=1, le=200)


class ClusterEmailsInput(PlainInput):
    """Input for listing emails in a cluster."""

    cluster_id: int = Field(..., description="Cluster ID to get emails for.", ge=0)
    limit: int = Field(default=30, ge=1, le=100)


# ── Data Quality Inputs ─────────────────────────────────────


class FindDuplicatesInput(PlainInput):
    """Input for duplicate detection."""

    limit: int = Field(default=50, ge=1, le=200, description="Max duplicate pairs to return.")
    threshold: float = Field(
        default=0.85, ge=0.5, le=1.0, description="Minimum Jaccard similarity threshold."
    )


# ── Reporting Inputs ─────────────────────────────────────────


class GenerateReportInput(StrictInput):
    """Input for HTML report generation."""

    output_path: str = Field(
        default="report.html",
        description="File path for the generated HTML report.",
    )
    title: str = Field(
        default="Email Archive Report",
        description="Title for the report header.",
    )


class ExportNetworkInput(StrictInput):
    """Input for GraphML network export."""

    output_path: str = Field(
        default="network.graphml",
        description="File path for the GraphML export.",
    )


class WritingAnalysisInput(StrictInput):
    """Input for writing style analysis."""

    sender: Optional[str] = Field(
        default=None,
        description="Sender email to analyze. If omitted, compares top senders.",
    )
    limit: int = Field(default=10, ge=1, le=50)


class ReingestBodiesInput(StrictInput):
    """Input for body text re-ingestion."""

    olm_path: str = Field(
        ...,
        description="Absolute path to the .olm file to re-read bodies from.",
        min_length=1,
    )


# ── Export & Browse Inputs ──────────────────────────────────


class ExportThreadInput(StrictInput):
    """Input for exporting a conversation thread as HTML/PDF."""

    conversation_id: str = Field(
        ..., description="Conversation thread ID from search results.", min_length=1
    )
    output_path: Optional[str] = Field(
        default=None,
        description="File path to save export. If omitted, returns HTML string.",
    )
    format: str = Field(
        default="html",
        description="Output format: 'html' or 'pdf' (pdf requires weasyprint).",
    )


class ExportSingleInput(StrictInput):
    """Input for exporting a single email as HTML/PDF."""

    uid: str = Field(
        ..., description="Email UID from search results.", min_length=1
    )
    output_path: Optional[str] = Field(
        default=None,
        description="File path to save export. If omitted, returns HTML string.",
    )
    format: str = Field(
        default="html",
        description="Output format: 'html' or 'pdf' (pdf requires weasyprint).",
    )


class BrowseInput(PlainInput):
    """Input for paginated email browsing."""

    offset: int = Field(default=0, ge=0, description="Starting position.")
    limit: int = Field(default=20, ge=1, le=50, description="Emails per page.")
    folder: Optional[str] = Field(default=None, description="Filter by folder (exact match).")
    sender: Optional[str] = Field(default=None, description="Filter by sender (partial match).")
    sort_order: str = Field(
        default="desc",
        description="Sort order: 'asc' (oldest first) or 'desc' (newest first).",
    )
    include_body: bool = Field(
        default=True,
        description="Include full body text in results.",
    )


class GetFullEmailInput(StrictInput):
    """Input for getting a single email with full body."""

    uid: str = Field(
        ..., description="Email UID from search results.", min_length=1
    )
