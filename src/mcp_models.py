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
    email_type: Optional[str] = Field(
        default=None,
        description="Filter by email type: 'reply', 'forward', or 'original'.",
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
    category: Optional[str] = Field(
        default=None,
        description="Filter by Outlook category name (partial match). E.g., 'Meeting'.",
    )
    is_calendar: Optional[bool] = Field(
        default=None,
        description="Filter by calendar/meeting messages. True = only calendar, False = exclude.",
    )
    attachment_name: Optional[str] = Field(
        default=None,
        description="Filter by attachment filename (partial match). E.g., 'report' or 'budget.xlsx'.",
    )
    attachment_type: Optional[str] = Field(
        default=None,
        description="Filter by attachment file extension. E.g., 'pdf', 'docx', 'xlsx'.",
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


class ThreadTopicSearchInput(StrictInput):
    """Input for searching emails by thread topic."""

    thread_topic: str = Field(
        ...,
        description="Thread topic string to search for (exact match).",
        min_length=1,
    )
    limit: int = Field(default=50, ge=1, le=200)


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
    force: bool = Field(
        default=False,
        description="Force re-parse ALL emails, overwriting existing body text. "
        "Use after fixing parser bugs to update previously truncated/dirty bodies.",
    )


class ReingestMetadataInput(StrictInput):
    """Input for backfilling v7 metadata from OLM."""

    olm_path: str = Field(
        ...,
        description="Absolute path to the .olm file to re-read metadata from.",
        min_length=1,
    )


class ReembedInput(StrictInput):
    """Input for re-embedding all emails from corrected SQLite body text."""

    batch_size: int = Field(
        default=100,
        description="Number of chunks to embed per batch.",
        ge=1,
    )


# ── Category & Calendar Inputs ──────────────────────────────


class ListCategoriesInput(PlainInput):
    """Input for listing categories."""

    limit: int = Field(default=50, ge=1, le=200, description="Max categories to return.")


class BrowseCalendarInput(DateRangeInput, StrictInput):
    """Input for browsing calendar/meeting emails."""

    limit: int = Field(default=30, ge=1, le=100, description="Max emails to return.")


# ── Attachment Inputs ──────────────────────────────────────


class ListAttachmentsInput(StrictInput):
    """Input for browsing attachments."""

    filename: Optional[str] = Field(default=None, description="Filter by filename (partial match).")
    extension: Optional[str] = Field(default=None, description="Filter by file extension, e.g. 'pdf'.")
    mime_type: Optional[str] = Field(default=None, description="Filter by MIME type (partial match).")
    sender: Optional[str] = Field(default=None, description="Filter by sender name or email (partial match).")
    limit: int = Field(default=50, ge=1, le=200, description="Max attachments to return.")
    offset: int = Field(default=0, ge=0, description="Pagination offset.")


class SearchByAttachmentInput(StrictInput):
    """Input for finding emails by attachment."""

    filename: Optional[str] = Field(default=None, description="Match attachment filename (partial).")
    extension: Optional[str] = Field(default=None, description="Match file extension, e.g. 'xlsx'.")
    mime_type: Optional[str] = Field(default=None, description="Match MIME type (partial).")
    limit: int = Field(default=50, ge=1, le=200, description="Max emails to return.")


# ── Export & Browse Inputs ──────────────────────────────────


class EmailExportInput(StrictInput):
    """Input for exporting a single email or conversation thread as HTML/PDF.

    Provide exactly one of ``uid`` (single email) or ``conversation_id`` (thread).
    """

    uid: Optional[str] = Field(
        default=None,
        description="Email UID to export a single email.",
        min_length=1,
    )
    conversation_id: Optional[str] = Field(
        default=None,
        description="Conversation thread ID to export a full thread.",
        min_length=1,
    )
    output_path: Optional[str] = Field(
        default=None,
        description="File path to save export. If omitted, returns HTML string.",
    )
    format: str = Field(
        default="html",
        description="Output format: 'html' or 'pdf' (pdf requires weasyprint).",
    )

    @model_validator(mode="after")
    def exactly_one_id(self):
        if self.uid and self.conversation_id:
            raise ValueError("Provide exactly one of uid or conversation_id, not both.")
        if not self.uid and not self.conversation_id:
            raise ValueError("Provide exactly one of uid or conversation_id.")
        return self


class BrowseInput(PlainInput):
    """Input for paginated email browsing."""

    offset: int = Field(default=0, ge=0, description="Starting position.")
    limit: int = Field(default=20, ge=1, le=50, description="Emails per page.")
    folder: Optional[str] = Field(default=None, description="Filter by folder (exact match).")
    sender: Optional[str] = Field(default=None, description="Filter by sender (partial match).")
    category: Optional[str] = Field(default=None, description="Filter by category (exact match).")
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


# ── Evidence Management Inputs ──────────────────────────────


class EvidenceAddInput(StrictInput):
    """Input for adding an evidence item."""

    email_uid: str = Field(
        ..., description="UID of the source email (from search results).", min_length=1
    )
    category: str = Field(
        ...,
        description=(
            "Evidence category: discrimination, harassment, sexual_harassment, "
            "insult, bossing, retaliation, exclusion, microaggression, "
            "hostile_environment, or other."
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


class EvidenceListInput(PlainInput):
    """Input for listing evidence items."""

    category: Optional[str] = Field(default=None, description="Filter by category.")
    min_relevance: Optional[int] = Field(
        default=None, ge=1, le=5, description="Minimum relevance score."
    )
    email_uid: Optional[str] = Field(
        default=None, description="Filter to evidence from a specific email."
    )
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class EvidenceGetInput(PlainInput):
    """Input for getting a single evidence item."""

    evidence_id: int = Field(..., ge=1, description="ID of the evidence item.")


class EvidenceUpdateInput(StrictInput):
    """Input for updating an evidence item."""

    evidence_id: int = Field(..., ge=1, description="ID of the evidence item to update.")
    category: Optional[str] = Field(default=None)
    key_quote: Optional[str] = Field(default=None)
    summary: Optional[str] = Field(default=None)
    relevance: Optional[int] = Field(default=None, ge=1, le=5)
    notes: Optional[str] = Field(default=None)


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
    min_relevance: Optional[int] = Field(
        default=None, ge=1, le=5,
        description="Only include items with this minimum relevance.",
    )
    category: Optional[str] = Field(
        default=None, description="Only include items in this category."
    )


class EvidenceAddBatchInput(PlainInput):
    """Input for batch-adding evidence items."""

    items: list[EvidenceAddInput] = Field(
        ...,
        description="List of evidence items to add (1-20 per batch).",
        min_length=1,
        max_length=20,
    )


class EvidenceSearchInput(StrictInput):
    """Input for searching within evidence items."""

    query: str = Field(
        ...,
        description="Text to search for across key_quote, summary, and notes.",
        min_length=1,
        max_length=500,
    )
    category: Optional[str] = Field(default=None, description="Filter by category.")
    min_relevance: Optional[int] = Field(
        default=None, ge=1, le=5, description="Minimum relevance score."
    )
    limit: int = Field(default=50, ge=1, le=200)


class EvidenceTimelineInput(PlainInput):
    """Input for chronological evidence view."""

    category: Optional[str] = Field(default=None, description="Filter by category.")
    min_relevance: Optional[int] = Field(
        default=None, ge=1, le=5, description="Minimum relevance score."
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


class DossierGenerateInput(StrictInput):
    """Input for generating a proof dossier."""

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
    case_reference: str = Field(
        default="",
        description="Case reference number or identifier.",
    )
    custodian: str = Field(
        default="",
        description="Name of the evidence custodian.",
    )
    prepared_by: str = Field(
        default="",
        description="Name and role of person who prepared this dossier (e.g. 'Jane Smith, Paralegal').",
    )
    min_relevance: Optional[int] = Field(
        default=None, ge=1, le=5,
        description="Only include evidence with this minimum relevance.",
    )
    category: Optional[str] = Field(
        default=None,
        description="Only include evidence in this category.",
    )
    include_relationships: bool = Field(
        default=True,
        description="Include relationship analysis section.",
    )
    include_custody: bool = Field(
        default=True,
        description="Include chain-of-custody log section.",
    )
    persons_of_interest: Optional[list[str]] = Field(
        default=None,
        description="Email addresses to focus relationship analysis on.",
    )


class DossierPreviewInput(PlainInput):
    """Input for previewing dossier contents without generating it."""

    min_relevance: Optional[int] = Field(
        default=None, ge=1, le=5,
        description="Minimum relevance score filter.",
    )
    category: Optional[str] = Field(
        default=None,
        description="Category filter.",
    )


class CustodyChainInput(PlainInput):
    """Input for viewing chain-of-custody audit trail."""

    target_type: Optional[str] = Field(
        default=None,
        description="Filter by target type: 'email', 'evidence', 'ingestion_run', 'export'.",
    )
    target_id: Optional[str] = Field(
        default=None,
        description="Filter by target ID (e.g., email UID, evidence ID).",
    )
    action: Optional[str] = Field(
        default=None,
        description=(
            "Filter by action type: 'ingest_start', 'evidence_add', "
            "'evidence_update', 'evidence_remove', 'export', 'verify'."
        ),
    )
    limit: int = Field(default=50, ge=1, le=500, description="Max events to return.")


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
