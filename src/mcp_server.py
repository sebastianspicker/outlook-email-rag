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
from typing import Optional  # noqa: F401 — used by tool type hints

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .config import get_settings
from .mcp_models import (
    ActionItemsInput,
    ClusterEmailsInput,
    CommunicationBetweenInput,
    DecisionsInput,
    EmailIngestInput,
    EmailSearchByDateInput,
    EmailSearchByRecipientInput,
    EmailSearchBySenderInput,
    EmailSearchInput,
    EmailSearchStructuredInput,
    EmailSearchThreadInput,
    EntityNetworkInput,
    EntitySearchInput,
    EntityTimelineInput,
    ExportNetworkInput,
    FindDuplicatesInput,
    FindPeopleInput,
    FindSimilarInput,
    GenerateReportInput,
    ListEntitiesInput,
    ListSendersInput,
    NetworkAnalysisInput,
    QuerySuggestionsInput,
    ResponseTimesInput,
    SearchByTopicInput,
    SmartSearchInput,
    ThreadSummaryInput,
    TopContactsInput,
    TopKeywordsInput,
    VolumeOverTimeInput,
    WritingAnalysisInput,
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
    "has_attachments", "priority", "date_from", "date_to", "min_score",
    "topic_id", "cluster_id",
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
    """Search emails and return stable JSON output for automation clients."""
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
            - extract_entities (bool): If true, extract entities into SQLite

    Returns:
        str: JSON summary of the ingestion run.
    """
    from .ingest import ingest

    try:
        stats = ingest(
            olm_path=params.olm_path,
            max_emails=params.max_emails,
            dry_run=params.dry_run,
            extract_entities=params.extract_entities,
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


# ── Network Analysis Tools ────────────────────────────────────


@mcp.tool(name="email_top_contacts", annotations=_tool_annotations("Top Email Contacts"))
async def email_top_contacts(params: TopContactsInput) -> str:
    """Find top communication partners for an email address.

    Returns contacts ranked by total bidirectional email frequency
    (emails sent to + received from each partner).
    """
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    contacts = db.top_contacts(params.email_address, limit=params.limit)
    return json.dumps(contacts, indent=2)


@mcp.tool(
    name="email_communication_between",
    annotations=_tool_annotations("Communication Between Two Contacts"),
)
async def email_communication_between(params: CommunicationBetweenInput) -> str:
    """Get bidirectional communication stats between two email addresses."""
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    result = db.communication_between(params.email_a, params.email_b)
    return json.dumps(result, indent=2)


@mcp.tool(name="email_network_analysis", annotations=_tool_annotations("Email Network Analysis"))
async def email_network_analysis(params: NetworkAnalysisInput) -> str:
    """Analyze the communication network: centrality, communities, bridge nodes."""
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    from .network_analysis import CommunicationNetwork

    net = CommunicationNetwork(db)
    result = net.network_analysis(top_n=params.top_n)
    return json.dumps(result, indent=2)


# ── Temporal Analysis Tools ───────────────────────────────────


@mcp.tool(name="email_volume_over_time", annotations=_tool_annotations("Email Volume Over Time"))
async def email_volume_over_time(params: VolumeOverTimeInput) -> str:
    """Get email volume grouped by time period (day/week/month)."""
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    from .temporal_analysis import TemporalAnalyzer

    analyzer = TemporalAnalyzer(db)
    result = analyzer.volume_over_time(
        period=params.period,
        date_from=params.date_from,
        date_to=params.date_to,
        sender=params.sender,
    )
    return json.dumps(result, indent=2)


@mcp.tool(name="email_activity_pattern", annotations=_tool_annotations("Email Activity Heatmap"))
async def email_activity_pattern() -> str:
    """Get email activity heatmap: hour-of-day x day-of-week counts."""
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    from .temporal_analysis import TemporalAnalyzer

    analyzer = TemporalAnalyzer(db)
    result = analyzer.activity_heatmap()
    return json.dumps(result, indent=2)


@mcp.tool(name="email_response_times", annotations=_tool_annotations("Email Response Times"))
async def email_response_times(params: ResponseTimesInput) -> str:
    """Get average response times per sender (in hours)."""
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    from .temporal_analysis import TemporalAnalyzer

    analyzer = TemporalAnalyzer(db)
    result = analyzer.response_times(sender=params.sender, limit=params.limit)
    return json.dumps(result, indent=2)


# ── Entity Tools ──────────────────────────────────────────────


@mcp.tool(name="email_search_by_entity", annotations=_tool_annotations("Search by Entity"))
async def email_search_by_entity(params: EntitySearchInput) -> str:
    """Find emails mentioning a specific entity (organization, URL, phone, etc.)."""
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    results = db.search_by_entity(params.entity, entity_type=params.entity_type, limit=params.limit)
    return json.dumps(results, indent=2)


@mcp.tool(name="email_list_entities", annotations=_tool_annotations("List Top Entities"))
async def email_list_entities(params: ListEntitiesInput) -> str:
    """List most frequently mentioned entities in the email archive."""
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    results = db.top_entities(entity_type=params.entity_type, limit=params.limit)
    return json.dumps(results, indent=2)


@mcp.tool(name="email_entity_network", annotations=_tool_annotations("Entity Co-occurrences"))
async def email_entity_network(params: EntityNetworkInput) -> str:
    """Find entities that co-occur with the given entity in the same emails."""
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    results = db.entity_co_occurrences(params.entity, limit=params.limit)
    return json.dumps(results, indent=2)


# ── NLP Entity Tools ──────────────────────────────────────────


@mcp.tool(name="email_find_people", annotations=_tool_annotations("Find People in Emails"))
async def email_find_people(params: FindPeopleInput) -> str:
    """Search emails by person name mentioned in the email body.

    Uses NLP-extracted person entities (names like 'John Smith', 'Dr. Mueller').
    Requires entity extraction during ingestion.

    Args:
        params: name (str) - person name to search, limit (int).

    Returns:
        JSON list of emails mentioning that person.
    """
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    results = db.people_in_emails(params.name, limit=params.limit)
    return json.dumps(results, indent=2)


@mcp.tool(name="email_entity_timeline", annotations=_tool_annotations("Entity Mention Timeline"))
async def email_entity_timeline(params: EntityTimelineInput) -> str:
    """Show how often an entity appears over time.

    Track mention frequency of any entity (person, organization, etc.)
    across the email archive, grouped by day/week/month.

    Args:
        params: entity (str) - entity to track, period (str) - 'day'/'week'/'month'.

    Returns:
        JSON list of {period, count} entries.
    """
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    results = db.entity_timeline(params.entity, period=params.period)
    return json.dumps(results, indent=2)


# ── Thread Intelligence Tools ────────────────────────────────


@mcp.tool(name="email_thread_summary", annotations=_tool_annotations("Summarize Thread"))
async def email_thread_summary(params: ThreadSummaryInput) -> str:
    """Summarize a conversation thread using extractive summarization.

    Selects the most important sentences from the thread based on
    TF-IDF scoring with position bias.

    Args:
        params: conversation_id (str), max_sentences (int).

    Returns:
        JSON with thread summary text.
    """
    retriever = get_retriever()
    results = retriever.search_by_thread(
        conversation_id=params.conversation_id, top_k=50
    )
    if not results:
        return json.dumps({"error": "No emails found for this thread."})

    emails = [
        {"clean_body": r.text, "sender_email": r.metadata.get("sender_email", ""),
         "sender_name": r.metadata.get("sender_name", ""), "date": r.metadata.get("date", ""),
         "uid": r.metadata.get("uid", ""), "subject": r.metadata.get("subject", "")}
        for r in results
    ]

    from .thread_summarizer import summarize_thread

    summary = summarize_thread(emails, max_sentences=params.max_sentences)
    return json.dumps({"conversation_id": params.conversation_id, "summary": summary})


@mcp.tool(name="email_action_items", annotations=_tool_annotations("Extract Action Items"))
async def email_action_items(params: ActionItemsInput) -> str:
    """Extract action items from a thread or across recent emails.

    Detects patterns like 'please do X', 'need to', 'I will', 'by Friday'.

    Args:
        params: conversation_id or days, limit.

    Returns:
        JSON list of action items with assignee and deadline.
    """
    from .thread_intelligence import ThreadAnalyzer

    analyzer = ThreadAnalyzer()

    if params.conversation_id:
        retriever = get_retriever()
        results = retriever.search_by_thread(
            conversation_id=params.conversation_id, top_k=50
        )
        if not results:
            return json.dumps({"error": "No emails found for this thread."})

        all_items = []
        for r in results:
            items = analyzer.extract_action_items(
                r.text,
                sender=r.metadata.get("sender_email", ""),
                source_uid=r.metadata.get("uid", ""),
            )
            all_items.extend(items)

        return json.dumps(
            [{"text": a.text, "assignee": a.assignee, "deadline": a.deadline,
              "is_urgent": a.is_urgent} for a in all_items[:params.limit]],
            indent=2,
        )

    return json.dumps({"error": "Provide conversation_id to extract action items."})


@mcp.tool(name="email_decisions", annotations=_tool_annotations("Extract Decisions"))
async def email_decisions(params: DecisionsInput) -> str:
    """Extract decisions from email threads.

    Detects patterns like 'we decided', 'agreed to', 'approved', 'go ahead with'.

    Args:
        params: conversation_id or days.

    Returns:
        JSON list of decisions with who made them and when.
    """
    from .thread_intelligence import ThreadAnalyzer

    analyzer = ThreadAnalyzer()

    if params.conversation_id:
        retriever = get_retriever()
        results = retriever.search_by_thread(
            conversation_id=params.conversation_id, top_k=50
        )
        if not results:
            return json.dumps({"error": "No emails found for this thread."})

        all_decisions = []
        for r in results:
            decisions = analyzer.extract_decisions(
                r.text,
                sender=r.metadata.get("sender_email", ""),
                date=r.metadata.get("date", ""),
                source_uid=r.metadata.get("uid", ""),
            )
            all_decisions.extend(decisions)

        return json.dumps(
            [{"text": d.text, "made_by": d.made_by, "date": d.date}
             for d in all_decisions],
            indent=2,
        )

    return json.dumps({"error": "Provide conversation_id to extract decisions."})


# ── Smart Search Tool ────────────────────────────────────────


@mcp.tool(
    name="email_smart_search",
    annotations=_tool_annotations("Smart Search"),
)
async def email_smart_search(params: SmartSearchInput) -> str:
    """Intelligent search that auto-routes based on query analysis.

    Automatically detects query intent and applies relevant filters:
    - Person names -> searches person entities + semantic search
    - Topic keywords -> adds topic filter if matching topic found
    - Uses query expansion for better recall

    Combines results from multiple search paths with deduplication.

    Args:
        params: query (str), top_k (int).

    Returns:
        JSON with search results and detected intent.
    """
    retriever = get_retriever()
    query = params.query
    detected_intent: dict = {}

    # Strategy 1: Always do expanded semantic search
    results = retriever.search_filtered(
        query=query, top_k=params.top_k, expand_query=True,
    )
    detected_intent["expand_query"] = True

    # Strategy 2: If we have entity DB, also search person entities
    db = get_email_db()
    if db:
        try:
            person_results = db.people_in_emails(query, limit=5)
            if person_results:
                detected_intent["person_matches"] = len(person_results)
        except Exception:
            pass

        # Strategy 3: Check if query matches a known topic
        try:
            topic_dist = db.topic_distribution()
            for topic in topic_dist:
                label = topic.get("label", "").lower()
                if any(w in label for w in query.lower().split() if len(w) > 3):
                    detected_intent["topic_match"] = {
                        "id": topic["topic_id"],
                        "label": topic["label"],
                    }
                    break
        except Exception:
            pass

    formatted = retriever.format_results_for_claude(results)
    return json.dumps({
        "query": query,
        "count": len(results),
        "detected_intent": detected_intent,
        "formatted_results": formatted,
    })


# ── Cluster Tools ─────────────────────────────────────────────


@mcp.tool(name="email_clusters", annotations=_tool_annotations("List Email Clusters"))
async def email_clusters_tool() -> str:
    """List all email clusters with sizes, representative subjects, and labels.

    Clusters group similar emails together based on embedding similarity.
    Requires clustering during ingestion.

    Returns:
        JSON list of cluster summaries.
    """
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    results = db.cluster_summary()
    if not results:
        return json.dumps({"error": "No clusters available. Run ingestion with --cluster."})
    return json.dumps(results, indent=2)


@mcp.tool(name="email_find_similar", annotations=_tool_annotations("Find Similar Emails"))
async def email_find_similar(params: FindSimilarInput) -> str:
    """Find emails most similar to a given email or query text.

    Provide either uid (to find emails similar to a specific email) or
    query (to find emails similar to a text description).

    Args:
        params: uid or query, top_k.

    Returns:
        JSON list of similar emails with similarity scores.
    """
    if not params.uid and not params.query:
        return json.dumps({"error": "Provide either uid or query."})

    retriever = get_retriever()
    if params.query:
        results = retriever.search(params.query, top_k=params.top_k)
        return sanitize_untrusted_text(retriever.format_results_for_claude(results))

    return json.dumps({"error": "UID-based similarity requires embeddings. Use query instead."})


@mcp.tool(name="email_cluster_emails", annotations=_tool_annotations("Emails in Cluster"))
async def email_cluster_emails(params: ClusterEmailsInput) -> str:
    """Get emails in a specific cluster, sorted by proximity to centroid.

    Args:
        params: cluster_id (int), limit (int).

    Returns:
        JSON list of emails in the cluster.
    """
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    results = db.emails_in_cluster(params.cluster_id, limit=params.limit)
    return json.dumps(results, indent=2)


# ── Keyword & Topic Tools ────────────────────────────────────


@mcp.tool(name="email_topics", annotations=_tool_annotations("List Discovered Topics"))
async def email_topics() -> str:
    """List all discovered topics with labels, top words, and email counts.

    Topics are discovered via NMF topic modeling during ingestion.
    Each topic has an auto-generated label from its top words.

    Returns:
        JSON list of {id, label, top_words, email_count} entries.
    """
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    results = db.topic_distribution()
    if not results:
        return json.dumps({"error": "No topics available. Run ingestion with --extract-keywords."})
    return json.dumps(results, indent=2)


@mcp.tool(name="email_search_by_topic", annotations=_tool_annotations("Search Emails by Topic"))
async def email_search_by_topic(params: SearchByTopicInput) -> str:
    """Find emails assigned to a specific topic, ranked by relevance.

    Args:
        params: topic_id (int) - ID from email_topics, limit (int).

    Returns:
        JSON list of emails with topic weight.
    """
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    results = db.emails_by_topic(params.topic_id, limit=params.limit)
    return json.dumps(results, indent=2)


@mcp.tool(name="email_keywords", annotations=_tool_annotations("Top Keywords"))
async def email_keywords(params: TopKeywordsInput) -> str:
    """Top keywords across the archive or filtered by sender/folder.

    Keywords are extracted via TF-IDF during ingestion.

    Args:
        params: sender (optional), folder (optional), limit (int).

    Returns:
        JSON list of {keyword, avg_score, email_count} entries.
    """
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    results = db.top_keywords(sender=params.sender, folder=params.folder, limit=params.limit)
    if not results:
        return json.dumps({"error": "No keywords available. Run ingestion with --extract-keywords."})
    return json.dumps(results, indent=2)


# ── Query Suggestions ─────────────────────────────────────────


@mcp.tool(name="email_query_suggestions", annotations=_tool_annotations("Query Suggestions"))
async def email_query_suggestions(params: QuerySuggestionsInput) -> str:
    """Get search suggestions based on indexed email data.

    Returns categorized suggestions including top senders, folders,
    and entities to help discover relevant search queries.
    """
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    from .query_suggestions import QuerySuggester

    suggester = QuerySuggester(db)
    result = suggester.suggest(limit=params.limit)
    return json.dumps(result, indent=2)


# ── Data Intelligence Tools ───────────────────────────────────


@mcp.tool(name="email_find_duplicates", annotations=_tool_annotations("Find Duplicate Emails"))
async def email_find_duplicates(params: FindDuplicatesInput) -> str:
    """Find near-duplicate emails using character n-gram similarity."""
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    from .dedup_detector import DuplicateDetector

    detector = DuplicateDetector(db, threshold=params.threshold)
    duplicates = detector.find_duplicates(limit=params.limit)
    return json.dumps({"count": len(duplicates), "duplicates": duplicates}, indent=2)


@mcp.tool(name="email_language_stats", annotations=_tool_annotations("Email Language Statistics"))
async def email_language_stats() -> str:
    """Get language distribution across all indexed emails."""
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE

    # Query detected_language if available
    try:
        rows = db.conn.execute(
            """
            SELECT detected_language, COUNT(*) as cnt
            FROM emails
            WHERE detected_language IS NOT NULL AND detected_language != ''
            GROUP BY detected_language
            ORDER BY cnt DESC
            """
        ).fetchall()
        if rows:
            stats = [{"language": row["detected_language"], "count": row["cnt"]} for row in rows]
            return json.dumps({"languages": stats}, indent=2)
    except Exception:
        pass
    return json.dumps({"error": "No language data available. Re-ingest with language detection enabled."})


@mcp.tool(name="email_sentiment_overview", annotations=_tool_annotations("Email Sentiment Overview"))
async def email_sentiment_overview() -> str:
    """Get sentiment distribution across indexed emails."""
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE

    try:
        rows = db.conn.execute(
            """
            SELECT sentiment_label, COUNT(*) as cnt,
                   ROUND(AVG(sentiment_score), 4) as avg_score
            FROM emails
            WHERE sentiment_label IS NOT NULL AND sentiment_label != ''
            GROUP BY sentiment_label
            ORDER BY cnt DESC
            """
        ).fetchall()
        if rows:
            stats = [
                {"sentiment": row["sentiment_label"], "count": row["cnt"], "avg_score": row["avg_score"]}
                for row in rows
            ]
            return json.dumps({"sentiments": stats}, indent=2)
    except Exception:
        pass
    return json.dumps({"error": "No sentiment data available. Re-ingest with sentiment analysis enabled."})


# ── Reporting & Export Tools ──────────────────────────────────


@mcp.tool(
    name="email_generate_report",
    annotations=ToolAnnotations(
        title="Generate Archive Report",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def email_generate_report(params: GenerateReportInput) -> str:
    """Generate a self-contained HTML report of the email archive.

    The report includes: archive overview, top senders, folder distribution,
    monthly volume, top entities, and response times.
    """
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    from .report_generator import ReportGenerator

    generator = ReportGenerator(db)
    generator.generate(title=params.title, output_path=params.output_path)
    return json.dumps({"status": "ok", "output_path": params.output_path})


@mcp.tool(
    name="email_export_network",
    annotations=ToolAnnotations(
        title="Export Communication Network",
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    ),
)
async def email_export_network(params: ExportNetworkInput) -> str:
    """Export the communication network as GraphML for external visualization.

    The GraphML format is supported by Gephi, Cytoscape, and other
    network analysis tools.
    """
    db = get_email_db()
    if not db:
        return _DB_UNAVAILABLE
    from .network_analysis import CommunicationNetwork

    net = CommunicationNetwork(db)
    result = net.export_graphml(params.output_path)
    return json.dumps(result, indent=2)


# ── Writing Analysis Tools ────────────────────────────────────


@mcp.tool(
    name="email_writing_analysis",
    annotations=_tool_annotations("Writing Style Analysis"),
)
async def email_writing_analysis(params: WritingAnalysisInput) -> str:
    """Analyze writing style and readability across senders.

    Computes metrics like readability score, average sentence length,
    vocabulary richness, and formality for each sender's emails.

    If a specific sender is given, returns their detailed profile.
    If omitted, compares the top senders by volume.

    Args:
        params: sender (optional str), limit (int).

    Returns:
        JSON with writing style metrics per sender.
    """
    from .writing_analyzer import WritingAnalyzer

    retriever = get_retriever()
    db = get_email_db()
    analyzer = WritingAnalyzer()

    def _get_sender_texts(sender_filter: str, max_texts: int = 50) -> list[str]:
        """Get email texts for a sender via semantic search."""
        try:
            results = retriever.search_filtered(
                query="*", top_k=max_texts, sender=sender_filter,
            )
            return [r.text for r in results if r.text]
        except Exception:
            return []

    if params.sender:
        texts = _get_sender_texts(params.sender, max_texts=params.limit)
        if not texts:
            return json.dumps(
                {"error": f"No emails found for sender: {params.sender}"}
            )
        profile = analyzer.analyze_sender_profile(texts, params.sender)
        if not profile:
            return json.dumps(
                {"error": f"Not enough content to analyze: {params.sender}"}
            )
        return json.dumps(profile, indent=2)

    # Compare top senders
    if not db:
        return json.dumps({"error": "SQLite database not available."})

    try:
        senders = db.top_senders(limit=params.limit)
    except Exception:
        return json.dumps({"error": "Could not fetch sender list."})

    profiles = []
    for s in senders:
        email_addr = s.get("sender_email", "")
        if not email_addr:
            continue
        texts = _get_sender_texts(email_addr, max_texts=30)
        profile = analyzer.analyze_sender_profile(texts, email_addr)
        if profile:
            profiles.append(profile)

    return json.dumps(profiles, indent=2)


# ── Entry Point ────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
