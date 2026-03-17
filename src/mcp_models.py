"""Pydantic input models for MCP tools.

All MCP tool parameter models live here. Two base classes eliminate
repeated ``model_config`` declarations:

- ``StrictInput``: strips whitespace from strings, forbids extra fields.
- ``PlainInput``: forbids extra fields only (no string stripping).

``DateRangeInput`` is a mixin providing ISO-date validation for
``date_from`` / ``date_to`` pairs.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .validation import parse_iso_date
from .validation import validate_date_window as ensure_valid_date_window


def _validate_output_path(v: str | None) -> str | None:
    """Reject null bytes and path-traversal components in output file paths."""
    if v is None:
        return v
    if "\x00" in v:
        raise ValueError("output_path must not contain null bytes")
    if ".." in Path(v).parts:
        raise ValueError("output_path must not traverse parent directories with '..'")
    return v


# ── Base Classes ─────────────────────────────────────────────


class StrictInput(BaseModel):
    """Base for MCP inputs with whitespace stripping."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class PlainInput(BaseModel):
    """Base for MCP inputs without whitespace stripping."""

    model_config = ConfigDict(extra="forbid")


class DateRangeInput(BaseModel):
    """Reusable date-range validation mixin for MCP inputs."""

    date_from: str | None = Field(
        default=None,
        description="Start date in YYYY-MM-DD format (inclusive). E.g., '2023-01-01'.",
    )
    date_to: str | None = Field(
        default=None,
        description="End date in YYYY-MM-DD format (inclusive). E.g., '2023-12-31'.",
    )

    @field_validator("date_from", "date_to")
    @classmethod
    def validate_iso_date(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return parse_iso_date(value)

    @model_validator(mode="after")
    def validate_date_window(self):
        ensure_valid_date_window(self.date_from, self.date_to)
        return self


# ── Core Search Inputs ───────────────────────────────────────


class EmailSearchStructuredInput(DateRangeInput, StrictInput):
    """Structured JSON search input for automation clients."""

    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(default=10, ge=1, le=30)
    sender: str | None = Field(default=None)
    subject: str | None = Field(default=None)
    folder: str | None = Field(default=None)
    cc: str | None = Field(default=None, description="CC recipient filter (partial match).")
    to: str | None = Field(default=None, description="To recipient filter (partial match).")
    bcc: str | None = Field(default=None, description="BCC recipient filter (partial match).")
    has_attachments: bool | None = Field(default=None, description="Filter by attachment presence.")
    priority: int | None = Field(
        default=None, ge=0, description="Minimum priority level (emails with priority >= this value)."
    )
    email_type: str | None = Field(
        default=None,
        description="Filter by email type: 'reply', 'forward', or 'original'.",
    )
    min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    rerank: bool = Field(
        default=False,
        description="Re-rank results with cross-encoder for better precision (slower).",
    )
    hybrid: bool = Field(
        default=False,
        description="Use hybrid semantic + BM25 keyword search for better recall.",
    )
    topic_id: int | None = Field(
        default=None, ge=0,
        description="Filter to emails assigned to this topic (from email_topics tool).",
    )
    cluster_id: int | None = Field(
        default=None, ge=0,
        description="Filter to emails in this cluster (from email_clusters tool).",
    )
    expand_query: bool = Field(
        default=False,
        description="Expand query with semantically related terms for better recall.",
    )
    category: str | None = Field(
        default=None,
        description="Filter by Outlook category name (partial match). E.g., 'Meeting'.",
    )
    is_calendar: bool | None = Field(
        default=None,
        description="Filter by calendar/meeting messages. True = only calendar, False = exclude.",
    )
    attachment_name: str | None = Field(
        default=None,
        description="Filter by attachment filename (partial match). E.g., 'report' or 'budget.xlsx'.",
    )
    attachment_type: str | None = Field(
        default=None,
        description="Filter by attachment file extension. E.g., 'pdf', 'docx', 'xlsx'.",
    )
    scan_id: str | None = Field(
        default=None, min_length=1, max_length=100,
        description="Scan session ID for progressive search. Excludes previously seen emails and tracks new ones.",
    )


class FindSimilarInput(StrictInput):
    """Input for finding similar emails."""

    uid: str | None = Field(default=None, description="Email UID to find similar emails for.")
    query: str | None = Field(default=None, description="Query text to find similar emails for.")
    top_k: int = Field(default=10, ge=1, le=50)
    scan_id: str | None = Field(
        default=None, min_length=1, max_length=100,
        description="Scan session ID for progressive search. Excludes previously seen emails and tracks new ones.",
    )


class EmailTriageInput(DateRangeInput, StrictInput):
    """Input for fast-triage email scanning with ultra-compact results."""

    query: str = Field(
        ..., min_length=1, max_length=500,
        description="Natural language query for triage scanning.",
    )
    top_k: int = Field(
        default=50, ge=1, le=100,
        description="Results to return (1-100). Use high values to avoid missing evidence.",
    )
    preview_chars: int = Field(
        default=200, ge=0, le=500,
        description="Body preview per result. 0=metadata only, 200=decide relevance, 500=extract quotes.",
    )
    sender: str | None = Field(default=None, description="Filter by sender (partial match).")
    folder: str | None = Field(default=None, description="Filter by folder name.")
    has_attachments: bool | None = Field(default=None)
    hybrid: bool = Field(
        default=False,
        description="Use hybrid semantic + keyword search for better recall.",
    )
    scan_id: str | None = Field(
        default=None, min_length=1, max_length=100,
        description="Scan session ID for progressive search. Excludes previously seen emails and tracks new ones.",
    )


# ── Archive Info Inputs ──────────────────────────────────────


class ListSendersInput(PlainInput):
    """Input for listing senders."""

    limit: int = Field(
        default=30,
        description="Max number of senders to return, sorted by email count.",
        ge=1,
        le=200,
    )


# ── Ingestion Input ──────────────────────────────────────────


class EmailIngestInput(StrictInput):
    """Input for ingesting an OLM email archive."""

    olm_path: str = Field(
        ...,
        description="Absolute path to the .olm file to ingest.",
        min_length=1,
    )
    max_emails: int | None = Field(
        default=None,
        description="Optional cap on number of emails to parse.",
        ge=1,
    )
    dry_run: bool = Field(
        default=False,
        description="If true, parse and chunk without writing to the database.",
    )
    extract_attachments: bool = Field(
        default=False,
        description="If true, extract and index text content from attachments (PDF, DOCX, XLSX, text).",
    )
    extract_entities: bool = Field(
        default=False,
        description="If true, extract named entities (people, orgs, locations) during ingestion.",
    )
    embed_images: bool = Field(
        default=False,
        description=(
            "If true, embed image attachments (JPG, PNG, etc.) using Visualized-BGE-M3 "
            "for cross-modal search. Automatically enables extract_attachments."
        ),
    )


# ── Network Analysis Inputs ──────────────────────────────────


class NetworkAnalysisInput(PlainInput):
    """Input for network analysis."""

    top_n: int = Field(default=20, ge=1, le=100, description="Number of top nodes to return.")


# ── Entity Inputs ────────────────────────────────────────────


class EntitySearchInput(StrictInput):
    """Input for entity search."""

    entity: str = Field(..., description="Entity text to search for (partial match).", min_length=1)
    entity_type: str | None = Field(
        default=None,
        description="Filter by entity type: 'organization', 'url', 'phone', 'email', 'person', 'event'.",
    )
    limit: int = Field(default=20, ge=1, le=100)


class ListEntitiesInput(PlainInput):
    """Input for listing top entities."""

    entity_type: str | None = Field(
        default=None, description="Filter by type: 'organization', 'url', 'phone', 'email', 'person', 'event'."
    )
    limit: int = Field(default=20, ge=1, le=100)


class EntityNetworkInput(StrictInput):
    """Input for entity co-occurrence network."""

    entity: str = Field(..., description="Entity to find co-occurrences for.", min_length=1)
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

    conversation_id: str | None = Field(
        default=None, description="Thread ID. If omitted, scans recent emails."
    )
    days: int | None = Field(default=None, ge=1, le=365, description="Scan emails from last N days.")
    limit: int = Field(default=20, ge=1, le=100)


class DecisionsInput(StrictInput):
    """Input for decision extraction."""

    conversation_id: str | None = Field(
        default=None, description="Thread ID to extract decisions from."
    )
    days: int | None = Field(default=None, ge=1, le=365)
    limit: int = Field(default=30, ge=1, le=100)


# ── Export & Browse Inputs ──────────────────────────────────


class EmailExportInput(StrictInput):
    """Input for exporting a single email or conversation thread as HTML/PDF.

    Provide exactly one of ``uid`` (single email) or ``conversation_id`` (thread).
    """

    uid: str | None = Field(
        default=None,
        description="Email UID to export a single email.",
        min_length=1,
    )
    conversation_id: str | None = Field(
        default=None,
        description="Conversation thread ID to export a full thread.",
        min_length=1,
    )
    output_path: str | None = Field(
        default=None,
        description="File path to save export. If omitted, returns HTML string.",
    )
    format: str = Field(
        default="html",
        description="Output format: 'html' or 'pdf' (pdf requires weasyprint).",
    )

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, v: str | None) -> str | None:
        return _validate_output_path(v)

    @model_validator(mode="after")
    def exactly_one_id(self):
        if self.uid and self.conversation_id:
            raise ValueError("Provide exactly one of uid or conversation_id, not both.")
        if not self.uid and not self.conversation_id:
            raise ValueError("Provide exactly one of uid or conversation_id.")
        return self


class BrowseInput(DateRangeInput, PlainInput):
    """Input for paginated email browsing, category listing, and calendar browsing."""

    offset: int = Field(default=0, ge=0, description="Starting position.")
    limit: int = Field(default=10, ge=1, le=50, description="Emails per page.")
    folder: str | None = Field(default=None, description="Filter by folder (exact match).")
    sender: str | None = Field(default=None, description="Filter by sender (partial match).")
    category: str | None = Field(default=None, description="Filter by category (exact match).")
    sort_order: str = Field(
        default="desc",
        description="Sort order: 'asc' (oldest first) or 'desc' (newest first).",
    )
    include_body: bool = Field(
        default=False,
        description="Include body text in results (default off to save context).",
    )
    is_calendar: bool | None = Field(
        default=None,
        description="Filter by calendar/meeting messages. True = only calendar emails.",
    )
    list_categories: bool = Field(
        default=False,
        description="If true, return category list with counts instead of emails.",
    )


class EmailDeepContextInput(StrictInput):
    """Input for deep single-email analysis combining body, thread, evidence, and sender."""

    uid: str = Field(..., min_length=1, description="Email UID to analyze in depth.")
    include_thread: bool = Field(default=True, description="Include thread summary and timeline.")
    include_evidence: bool = Field(default=True, description="Include existing evidence from this email.")
    include_sender_stats: bool = Field(default=True, description="Include sender communication profile.")
    max_body_chars: int = Field(default=10000, ge=0, description="Max body text chars (0=unlimited).")


# ── Evidence Management Inputs ──────────────────────────────


class EvidenceAddInput(StrictInput):
    """Input for adding an evidence item."""

    email_uid: str = Field(
        ..., description="UID of the source email (from search results).", min_length=1
    )
    category: str = Field(
        ...,
        description=(
            "Evidence category: bossing, harassment, discrimination, retaliation, "
            "hostile_environment, micromanagement, exclusion, gaslighting, "
            "workload, or general."
        ),
        min_length=1,
    )
    key_quote: str = Field(
        ...,
        description=(
            "Exact quote from the email body that constitutes evidence. "
            "Must appear verbatim in the email text."
        ),
        min_length=1,
    )
    summary: str = Field(
        ...,
        description="Brief description of why this is evidence (1-2 sentences).",
        min_length=1,
    )
    relevance: int = Field(
        ...,
        ge=1, le=5,
        description="Relevance rating: 1=tangential, 2=minor, 3=moderate, 4=significant, 5=critical.",
    )
    notes: str = Field(
        default="",
        description="Optional notes for the lawyer or analyst.",
    )


class EvidenceGetInput(PlainInput):
    """Input for getting a single evidence item."""

    evidence_id: int = Field(..., ge=1, description="ID of the evidence item.")


class EvidenceUpdateInput(StrictInput):
    """Input for updating an evidence item."""

    evidence_id: int = Field(..., ge=1, description="ID of the evidence item to update.")
    category: str | None = Field(default=None)
    key_quote: str | None = Field(default=None)
    summary: str | None = Field(default=None)
    relevance: int | None = Field(default=None, ge=1, le=5)
    notes: str | None = Field(default=None)


class EvidenceRemoveInput(PlainInput):
    """Input for removing an evidence item."""

    evidence_id: int = Field(..., ge=1, description="ID of the evidence item to remove.")


class EvidenceExportInput(StrictInput):
    """Input for exporting the evidence report."""

    output_path: str = Field(
        default="evidence_report.html",
        description="Output file path for the evidence report.",
    )
    format: str = Field(
        default="html",
        description="Output format: 'html', 'csv', or 'pdf' (pdf requires weasyprint).",
    )
    min_relevance: int | None = Field(
        default=None, ge=1, le=5,
        description="Only include items with this minimum relevance.",
    )
    category: str | None = Field(
        default=None, description="Only include items in this category."
    )

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, v: str) -> str:
        return _validate_output_path(v)  # type: ignore[return-value]


class EvidenceAddBatchInput(PlainInput):
    """Input for batch-adding evidence items."""

    items: list[EvidenceAddInput] = Field(
        ...,
        description="List of evidence items to add (1-20 per batch).",
        min_length=1,
        max_length=20,
    )


# ── Chain of Custody Inputs ─────────────────────────────────


class RelationshipPathsInput(StrictInput):
    """Input for finding communication paths between two people."""

    source: str = Field(
        ...,
        description="Source email address.",
        min_length=1,
    )
    target: str = Field(
        ...,
        description="Target email address.",
        min_length=1,
    )
    max_hops: int = Field(
        default=3, ge=1, le=6,
        description="Maximum number of intermediate hops (1-6).",
    )
    top_k: int = Field(
        default=5, ge=1, le=20,
        description="Maximum paths to return.",
    )


class SharedRecipientsInput(PlainInput):
    """Input for finding recipients common to multiple senders."""

    email_addresses: list[str] = Field(
        ...,
        description="List of sender email addresses (2 or more).",
        min_length=2,
    )
    min_shared: int = Field(
        default=2, ge=2,
        description="Minimum number of senders who must share a recipient.",
    )
    limit: int = Field(
        default=30, ge=1, le=200,
        description="Maximum number of shared recipients to return.",
    )


class CoordinatedTimingInput(PlainInput):
    """Input for detecting synchronized communication patterns."""

    email_addresses: list[str] = Field(
        ...,
        description="List of sender email addresses (2 or more).",
        min_length=2,
    )
    window_hours: int = Field(
        default=24, ge=1, le=168,
        description="Time window size in hours for co-activity detection.",
    )
    min_events: int = Field(
        default=3, ge=2,
        description="Minimum emails within a window to count as coordinated.",
    )
    limit: int = Field(
        default=20, ge=1, le=100,
        description="Maximum number of coordinated windows to return.",
    )


class RelationshipSummaryInput(StrictInput):
    """Input for a comprehensive person profile."""

    email_address: str = Field(
        ...,
        description="Email address to profile.",
        min_length=1,
    )
    limit: int = Field(
        default=20, ge=1, le=100,
        description="Max contacts to return in profile.",
    )


# ── Dossier Generation Inputs ───────────────────────────────


class CustodyChainInput(PlainInput):
    """Input for viewing chain-of-custody audit trail."""

    target_type: str | None = Field(
        default=None,
        description="Filter by target type: 'email', 'evidence', 'ingestion_run', 'export'.",
    )
    target_id: str | None = Field(
        default=None,
        description="Filter by target ID (e.g., email UID, evidence ID).",
    )
    action: str | None = Field(
        default=None,
        description=(
            "Filter by action type: 'ingest_start', 'evidence_add', "
            "'evidence_update', 'evidence_remove', 'export', 'verify'."
        ),
    )
    limit: int = Field(default=50, ge=1, le=200, description="Max events to return.")
    compact: bool = Field(
        default=True,
        description="Compact mode: omit details JSON and content_hash. Set false for full audit detail.",
    )


class EmailProvenanceInput(StrictInput):
    """Input for full email provenance lookup."""

    email_uid: str = Field(
        ...,
        description="Email UID to trace provenance for.",
        min_length=1,
    )


class EvidenceProvenanceInput(PlainInput):
    """Input for full evidence provenance lookup."""

    evidence_id: int = Field(
        ...,
        ge=1,
        description="Evidence item ID to trace provenance for.",
    )


# ── Merged Models (Phase 3) ──────────────────────────────────


class EmailClustersInput(PlainInput):
    """Input for listing clusters or emails in a cluster.

    Omit cluster_id to list all clusters; set it to list emails in that cluster.
    """

    cluster_id: int | None = Field(
        default=None, ge=0,
        description="Cluster ID. Omit to list all clusters; set to list emails in that cluster.",
    )
    limit: int = Field(default=30, ge=1, le=100)


class EmailTopicsInput(PlainInput):
    """Input for listing topics or emails assigned to a topic.

    Omit topic_id to list all topics; set it to list emails for that topic.
    """

    topic_id: int | None = Field(
        default=None, ge=0,
        description="Topic ID. Omit to list all topics; set to list emails for that topic.",
    )
    limit: int = Field(default=20, ge=1, le=100)


class EmailThreadLookupInput(StrictInput):
    """Input for looking up a thread by conversation_id or thread_topic.

    Provide exactly one of conversation_id or thread_topic.
    """

    conversation_id: str | None = Field(
        default=None,
        description="Conversation thread ID from search results.",
        min_length=1,
    )
    thread_topic: str | None = Field(
        default=None,
        description="Thread topic string (exact match from OLM metadata).",
        min_length=1,
    )
    limit: int = Field(default=50, ge=1, le=200)

    @model_validator(mode="after")
    def exactly_one_key(self):
        if self.conversation_id and self.thread_topic:
            raise ValueError("Provide exactly one of conversation_id or thread_topic, not both.")
        if not self.conversation_id and not self.thread_topic:
            raise ValueError("Provide exactly one of conversation_id or thread_topic.")
        return self


class EmailContactsInput(StrictInput):
    """Input for contact analysis.

    Omit compare_with for top contacts; set it for bidirectional communication stats.
    """

    email_address: str = Field(
        ..., description="Email address to analyze.", min_length=1,
    )
    compare_with: str | None = Field(
        default=None,
        description="Second email address for bidirectional stats. Omit for top contacts.",
        min_length=1,
    )
    limit: int = Field(default=20, ge=1, le=100)


class EvidenceQueryInput(PlainInput):
    """Input for querying evidence items (list, search, or timeline).

    Omit query to list; set query to search. Use sort='date' for timeline view.
    """

    query: str | None = Field(
        default=None,
        description="Text to search for across key_quote, summary, and notes. Omit to list all.",
        max_length=500,
    )
    sort: str = Field(
        default="relevance",
        description="Sort order: 'relevance' (default) or 'date' (chronological timeline).",
    )
    category: str | None = Field(default=None, description="Filter by category.")
    min_relevance: int | None = Field(
        default=None, ge=1, le=5, description="Minimum relevance score.",
    )
    email_uid: str | None = Field(
        default=None, description="Filter to evidence from a specific email.",
    )
    limit: int = Field(default=25, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    include_quotes: bool = Field(
        default=False,
        description="Include full key_quote text. Default: compact mode with 80-char preview.",
    )


class EvidenceOverviewInput(PlainInput):
    """Input for evidence overview (stats + categories combined)."""

    category: str | None = Field(default=None, description="Filter stats by category.")
    min_relevance: int | None = Field(
        default=None, ge=1, le=5, description="Filter stats by minimum relevance.",
    )


class EmailDiscoveryInput(StrictInput):
    """Input for keyword/suggestion discovery.

    mode='keywords': top keywords (filterable by sender/folder).
    mode='suggestions': categorized search suggestions.
    """

    mode: str = Field(
        ...,
        description="Discovery mode: 'keywords' or 'suggestions'.",
    )
    sender: str | None = Field(default=None, description="Filter keywords by sender email.")
    folder: str | None = Field(default=None, description="Filter keywords by folder name.")
    limit: int = Field(default=30, ge=1, le=200)


class EmailDossierInput(StrictInput):
    """Input for generating or previewing a proof dossier.

    Set preview_only=True to check scope before full generation.
    """

    preview_only: bool = Field(
        default=False,
        description="If true, return counts and scope only (no file generated).",
    )
    output_path: str = Field(
        default="dossier.html",
        description="File path for the generated dossier.",
    )
    format: str = Field(
        default="html",
        description="Output format: 'html' or 'pdf' (pdf requires weasyprint).",
    )
    title: str = Field(
        default="Proof Dossier",
        description="Title for the dossier cover page.",
    )
    case_reference: str = Field(default="", description="Case reference number.")
    custodian: str = Field(default="", description="Name of the evidence custodian.")
    prepared_by: str = Field(default="", description="Name/role of preparer.")
    min_relevance: int | None = Field(
        default=None, ge=1, le=5,
        description="Only include evidence with this minimum relevance.",
    )
    category: str | None = Field(default=None, description="Only include this category.")
    include_relationships: bool = Field(default=True, description="Include relationship analysis.")
    include_custody: bool = Field(default=True, description="Include chain-of-custody log.")
    persons_of_interest: list[str] | None = Field(
        default=None, description="Email addresses to focus relationship analysis on.",
    )

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, v: str) -> str:
        return _validate_output_path(v)  # type: ignore[return-value]


# ── Merged Models (Phase 4) ──────────────────────────────────


class EmailTemporalInput(DateRangeInput, StrictInput):
    """Input for temporal analysis.

    analysis='volume': email volume over time (day/week/month).
    analysis='activity': hour-of-day x day-of-week heatmap.
    analysis='response_times': average response times per sender.
    """

    analysis: str = Field(
        ...,
        description="Analysis type: 'volume', 'activity', or 'response_times'.",
    )
    period: str = Field(
        default="day",
        description="Aggregation period for volume: 'day', 'week', or 'month'.",
    )
    sender: str | None = Field(default=None, description="Filter by sender email.")
    limit: int = Field(default=20, ge=1, le=100, description="Max results for response_times.")


class EmailQualityInput(PlainInput):
    """Input for data quality checks.

    check='duplicates': find near-duplicate emails.
    check='languages': language distribution.
    check='sentiment': sentiment distribution.
    """

    check: str = Field(
        ...,
        description="Quality check: 'duplicates', 'languages', or 'sentiment'.",
    )
    limit: int = Field(
        default=50, ge=1, le=200,
        description="Max duplicate pairs to return (only for check='duplicates').",
    )
    threshold: float = Field(
        default=0.85, ge=0.5, le=1.0,
        description="Similarity threshold (only for check='duplicates').",
    )


class EmailReportInput(StrictInput):
    """Input for report generation.

    type='archive': HTML archive overview report.
    type='network': GraphML network export.
    type='writing': writing style analysis per sender.
    """

    type: str = Field(
        ...,
        description="Report type: 'archive', 'network', or 'writing'.",
    )
    output_path: str = Field(
        default="report.html",
        description="Output file path (for archive/network types).",
    )
    title: str = Field(
        default="Email Archive Report",
        description="Report title (for archive type).",
    )
    sender: str | None = Field(
        default=None,
        description="Sender to analyze (for writing type). Omit to compare top senders.",
    )
    limit: int = Field(default=10, ge=1, le=50, description="Max results (for writing type).")

    @field_validator("output_path")
    @classmethod
    def validate_output_path(cls, v: str) -> str:
        return _validate_output_path(v)  # type: ignore[return-value]


class EmailAttachmentsInput(StrictInput):
    """Input for attachment discovery.

    mode='list': browse all attachments with filters and pagination.
    mode='search': find emails with matching attachments.
    mode='stats': aggregate attachment statistics.
    """

    mode: str = Field(
        ...,
        description="Attachment mode: 'list', 'search', or 'stats'.",
    )
    filename: str | None = Field(default=None, description="Filter by filename (partial match).")
    extension: str | None = Field(default=None, description="Filter by file extension, e.g. 'pdf'.")
    mime_type: str | None = Field(default=None, description="Filter by MIME type (partial match).")
    sender: str | None = Field(
        default=None,
        description="Filter by sender (partial match, list mode only).",
    )
    limit: int = Field(default=50, ge=1, le=200, description="Max results to return.")
    offset: int = Field(default=0, ge=0, description="Pagination offset (list mode only).")


class EmailAdminInput(StrictInput):
    """Input for admin/diagnostic operations.

    action='diagnostics': show system info (no extra params needed).
    action='reingest_bodies': re-parse OLM bodies (requires olm_path).
    action='reembed': re-embed all chunks (optional batch_size).
    action='reingest_metadata': backfill v7 metadata (requires olm_path).
    action='reingest_analytics': backfill language/sentiment (no extra params).
    """

    action: str = Field(
        ...,
        description=(
            "Admin action: 'diagnostics', 'reingest_bodies', 'reembed', "
            "'reingest_metadata', or 'reingest_analytics'."
        ),
    )
    olm_path: str | None = Field(
        default=None,
        description="Path to .olm file (required for reingest_bodies and reingest_metadata).",
        min_length=1,
    )
    force: bool = Field(
        default=False,
        description="Force re-parse all emails (for reingest_bodies only).",
    )
    batch_size: int = Field(
        default=100, ge=1,
        description="Chunks per embedding batch (for reembed only).",
    )


# ── Scan Session Inputs ───────────────────────────────────────


class EmailScanInput(StrictInput):
    """Input for scan session management.

    action='status': session stats (seen count, candidate counts).
    action='flag': flag UIDs as candidates with a label.
    action='candidates': return flagged candidates, optionally filtered.
    action='reset': clear a session (or all if scan_id='__all__').
    """

    action: str = Field(
        ...,
        description="Scan action: 'status', 'flag', 'candidates', or 'reset'.",
    )
    scan_id: str = Field(
        ...,
        description="Scan session identifier (e.g., 'investigation_2026', 'harassment_case').",
        min_length=1,
        max_length=100,
    )
    uids: list[str] | None = Field(
        default=None,
        description="Email UIDs to flag (for action='flag'). Max 50 per call.",
        max_length=50,
    )
    label: str | None = Field(
        default=None,
        description="Label for flagging or filtering candidates (e.g., 'bossing', 'relevant', 'maybe').",
    )
    phase: int | None = Field(
        default=None, ge=1, le=3,
        description="Phase marker: 1=scan, 2=refine, 3=deep.",
    )
    score: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="Relevance score to record with flagged candidates.",
    )
