"""Local Streamlit UI for browsing and searching indexed emails."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, cast

import streamlit as st

# Streamlit doesn't support `python -m` module execution, so `streamlit run
# src/web_app.py` uses the file directly.  Relative imports work when imported
# as part of the `src` package, but fail when Streamlit runs the file as
# __main__.  The ImportError fallback handles that case.
try:
    from .retriever import EmailRetriever
    from .validation import validate_date_window
    from .web_ui import build_active_filter_labels, build_export_payload, build_filter_chip_html, sort_search_results
except ImportError:  # pragma: no cover - allows `streamlit run src/web_app.py`
    from src.retriever import EmailRetriever
    from src.validation import validate_date_window
    from src.web_ui import build_active_filter_labels, build_export_payload, build_filter_chip_html, sort_search_results

st.set_page_config(page_title="Email RAG", page_icon="📬", layout="wide", initial_sidebar_state="expanded")

SORT_OPTIONS = {
    "Relevance": "relevance",
    "Newest first": "date_desc",
    "Oldest first": "date_asc",
    "Sender A-Z": "sender_asc",
}

PAGE_SIZE = 20


@st.cache_resource
def get_retriever(chromadb_path: str | None):
    return EmailRetriever(chromadb_path=chromadb_path)


def render_sidebar(retriever: EmailRetriever) -> None:
    st.sidebar.markdown("## Archive Overview")

    stats = retriever.stats()
    st.sidebar.metric("Emails", stats.get("total_emails", 0))
    st.sidebar.metric("Chunks", stats.get("total_chunks", 0))
    st.sidebar.metric("Unique Senders", stats.get("unique_senders", 0))

    date_range = stats.get("date_range", {})
    st.sidebar.caption(
        f"Date range: {date_range.get('earliest', '?')} -> {date_range.get('latest', '?')}"
    )

    folders = stats.get("folders", {})
    if folders:
        with st.sidebar.expander("Folders", expanded=False):
            for folder_name, count in sorted(folders.items(), key=lambda x: x[1], reverse=True):
                st.text(f"{count:>5}  {folder_name}")

    with st.sidebar.expander("Top Senders", expanded=False):
        senders = retriever.list_senders(limit=20)
        if not senders:
            st.caption("No senders indexed yet.")
        else:
            max_count = max(sender["count"] for sender in senders)
            for sender in senders:
                display_name = sender["name"] or "(unknown)"
                st.text(f"{display_name} <{sender['email']}>")
                st.progress(sender["count"] / max_count if max_count else 0.0)
                st.caption(f"{sender['count']} emails")


def render_results(results: list[Any], preview_chars: int, retriever: EmailRetriever | None = None) -> None:
    st.subheader("Matching Emails")

    for index, result in enumerate(results, 1):
        metadata = result.metadata
        title = metadata.get("subject", "(no subject)")
        sender = metadata.get("sender_name") or metadata.get("sender_email", "?")
        date_value = str(metadata.get("date", "?"))[:10]
        folder = metadata.get("folder", "Unknown")
        body = result.text or ""
        preview = body if len(body) <= preview_chars else f"{body[:preview_chars]}..."

        # Build type/attachment badges for the expander title
        email_type = metadata.get("email_type", "original")
        type_badge = f" [{email_type.upper()}]" if email_type and email_type != "original" else ""
        att_count = metadata.get("attachment_count", "0")
        att_badge = f" [{att_count} att.]" if att_count and att_count != "0" else ""

        with st.expander(
            f"{index}. {title} | {sender} | {date_value} | {result.score:.0%}{type_badge}{att_badge}",
            expanded=index == 1,
        ):
            info_col1, info_col2, info_col3 = st.columns(3)
            info_col1.caption(f"Folder: {folder}")
            to_value = metadata.get("to", "")
            if to_value:
                # Truncate to first 3 recipients for display
                to_list = [t.strip() for t in str(to_value).split(",") if t.strip()]
                to_display = ", ".join(to_list[:3])
                if len(to_list) > 3:
                    to_display += f" (+{len(to_list) - 3} more)"
                info_col2.caption(f"To: {to_display}")
            info_col3.caption(f"Chunk ID: {result.chunk_id}")

            # Attachment names
            att_names = metadata.get("attachment_names", "")
            if att_names and str(att_names).strip():
                st.caption(f"Attachments: {att_names}")

            # Priority
            priority = metadata.get("priority", "0")
            if priority and str(priority) not in ("0", ""):
                st.caption(f"Priority: {priority}")

            st.progress(max(0.0, min(1.0, float(result.score))))
            st.caption("Preview")
            st.text(preview)
            if len(body) > preview_chars:
                with st.expander("Show full chunk", expanded=False):
                    st.text(body)

            # Thread view button
            conv_id = str(metadata.get("conversation_id", "") or "").strip()
            if conv_id and retriever is not None:
                if st.button("View Thread", key=f"thread_{result.chunk_id}"):
                    st.session_state["web_thread_id"] = conv_id
                    st.rerun()


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg-soft: #f7f2ea;
            --card-soft: #fffaf3;
            --ink: #1f1c16;
            --accent: #1e6f5c;
            --accent-2: #b46f34;
        }
        .stApp {
            background:
                radial-gradient(circle at 12% -5%, #efe2cf 0%, transparent 40%),
                radial-gradient(circle at 92% 8%, #e8f1eb 0%, transparent 42%),
                var(--bg-soft);
        }
        .hero-title {
            font-family: "Palatino Linotype", "Book Antiqua", Palatino, serif;
            color: var(--ink);
            font-weight: 700;
            letter-spacing: 0.02em;
            margin-bottom: 0.2rem;
        }
        .hero-subtitle {
            font-family: "Trebuchet MS", "Lucida Grande", "Segoe UI", sans-serif;
            color: #544a3f;
            margin-bottom: 1rem;
        }
        .filter-chip {
            display: inline-block;
            margin: 0 0.4rem 0.4rem 0;
            padding: 0.28rem 0.6rem;
            border-radius: 999px;
            background: var(--card-soft);
            border: 1px solid #dbcdb9;
            color: var(--ink);
            font-size: 0.82rem;
            font-family: "Trebuchet MS", "Lucida Grande", "Segoe UI", sans-serif;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_results_summary(results: list[Any], active_filters: list[str], sort_label: str) -> None:
    scores = [float(result.score) for result in results]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    max_score = max(scores) if scores else 0.0

    metric_col1, metric_col2, metric_col3 = st.columns(3)
    metric_col1.metric("Results", len(results))
    metric_col2.metric("Average Relevance", f"{avg_score:.0%}")
    metric_col3.metric("Best Match", f"{max_score:.0%}")
    st.caption(f"Sorted by: {sort_label}")

    if active_filters:
        chips = build_filter_chip_html(active_filters)
        st.markdown(chips, unsafe_allow_html=True)


def _get_email_db_safe():
    """Try to get EmailDatabase instance, return None if unavailable."""
    import os

    try:
        from .config import get_settings
        from .email_db import EmailDatabase
    except ImportError:
        from src.config import get_settings
        from src.email_db import EmailDatabase

    settings = get_settings()
    if settings.sqlite_path and os.path.exists(settings.sqlite_path):
        return EmailDatabase(settings.sqlite_path)
    return None


def render_dashboard_page() -> None:
    """Render the analytics dashboard page."""
    st.markdown("## Analytics Dashboard")

    db = _get_email_db_safe()
    if db is None:
        st.warning("SQLite database not available. Run ingestion first to enable analytics.")
        return

    try:
        from .dashboard_charts import prepare_heatmap_data, prepare_response_times_data, prepare_volume_chart_data
        from .temporal_analysis import TemporalAnalyzer
    except ImportError:
        from src.dashboard_charts import prepare_heatmap_data, prepare_response_times_data, prepare_volume_chart_data
        from src.temporal_analysis import TemporalAnalyzer

    import pandas as pd

    analyzer = TemporalAnalyzer(db)

    # Volume timeline
    st.subheader("Email Volume Over Time")
    period = st.selectbox("Period", ["day", "week", "month"], index=2)
    volume_data = prepare_volume_chart_data(analyzer, period=period)
    if volume_data:
        df = pd.DataFrame(volume_data)
        st.line_chart(df, x="period", y="count")
    else:
        st.info("No volume data available.")

    # Activity heatmap
    st.subheader("Activity Heatmap (hour × day-of-week)")
    heatmap_grid = prepare_heatmap_data(analyzer)
    if any(any(row) for row in heatmap_grid):
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        df_heat = pd.DataFrame(heatmap_grid, index=days, columns=[f"{h:02d}" for h in range(24)])
        st.dataframe(df_heat, use_container_width=True)
    else:
        st.info("No activity data available.")

    # Top contacts
    st.subheader("Top Contacts")
    email_input = st.text_input("Your email address", placeholder="you@example.com")
    if email_input:
        contacts = db.top_contacts(email_input, limit=15)
        if contacts:
            df_contacts = pd.DataFrame(contacts)
            st.bar_chart(df_contacts, x="partner", y="total_count")
        else:
            st.info(f"No contacts found for {email_input}")

    # Response times
    st.subheader("Response Times")
    resp_data = prepare_response_times_data(analyzer, limit=15)
    if resp_data:
        df_resp = pd.DataFrame(resp_data)
        st.dataframe(df_resp, use_container_width=True)
    else:
        st.info("No response time data available.")


def render_entity_page() -> None:
    """Render the entity browser page."""
    st.markdown("## Entity Browser")

    db = _get_email_db_safe()
    if db is None:
        st.warning("SQLite database not available. Run ingestion with `--extract-entities` first.")
        return

    import pandas as pd

    entity_types = ["All", "organization", "url", "phone", "mention", "email"]
    selected_type = st.selectbox("Entity Type", entity_types, index=0)
    entity_type = None if selected_type == "All" else selected_type

    entities = db.top_entities(entity_type=entity_type, limit=30)
    if entities:
        df = pd.DataFrame(entities)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No entities found. Run ingestion with `--extract-entities` to populate.")

    # Co-occurrence search
    st.subheader("Entity Co-occurrences")
    entity_query = st.text_input("Find co-occurring entities for:", placeholder="Acme Corp")
    if entity_query:
        co_entities = db.entity_co_occurrences(entity_query, limit=20)
        if co_entities:
            df_co = pd.DataFrame(co_entities)
            st.dataframe(df_co, use_container_width=True)
        else:
            st.info(f"No co-occurrences found for '{entity_query}'")


def render_network_page() -> None:
    """Render the network analysis page."""
    st.markdown("## Communication Network")

    db = _get_email_db_safe()
    if db is None:
        st.warning("SQLite database not available. Run ingestion first.")
        return

    try:
        from .dashboard_charts import prepare_network_summary
    except ImportError:
        from src.dashboard_charts import prepare_network_summary

    net_data = prepare_network_summary(db, top_n=20)

    if "error" in net_data:
        st.warning(net_data["error"])
        return

    # Metrics
    met_col1, met_col2 = st.columns(2)
    met_col1.metric("Total Nodes", net_data.get("total_nodes", 0))
    met_col2.metric("Total Edges", net_data.get("total_edges", 0))

    # Most connected
    most_connected = net_data.get("most_connected", [])
    if most_connected:
        import pandas as pd

        st.subheader("Most Connected")
        df_mc = pd.DataFrame(most_connected)
        st.dataframe(df_mc, use_container_width=True)

    # Communities
    communities = net_data.get("communities", [])
    if communities:
        st.subheader(f"Communities ({len(communities)})")
        for idx, community in enumerate(communities[:10]):
            members = community.get("members", [])
            with st.expander(f"Community {idx + 1} ({len(members)} members)"):
                for member in members[:20]:
                    st.text(member)


def render_evidence_page() -> None:
    """Render the evidence management page."""
    st.markdown("## Evidence Collection")

    db = _get_email_db_safe()
    if db is None:
        st.warning("SQLite database not available. Run ingestion first to enable evidence management.")
        return

    import pandas as pd

    # ── Overview metrics ──────────────────────────────────────
    stats = db.evidence_stats()
    met_col1, met_col2, met_col3 = st.columns(3)
    met_col1.metric("Total Items", stats["total"])
    met_col2.metric("Verified", stats["verified"])
    met_col3.metric("Unverified", stats["unverified"])

    # Category breakdown chart
    categories = db.evidence_categories()
    cats_with_items = [c for c in categories if c["count"] > 0]
    if cats_with_items:
        st.subheader("Items by Category")
        df_cats = pd.DataFrame(cats_with_items)
        st.bar_chart(df_cats, x="category", y="count")

    st.divider()

    # ── Browse / Search ───────────────────────────────────────
    st.subheader("Browse Evidence")
    browse_col1, browse_col2, browse_col3 = st.columns(3)

    with browse_col1:
        all_categories = ["All"] + [c["category"] for c in categories]
        selected_cat = st.selectbox("Category", all_categories, index=0)

    with browse_col2:
        min_rel = st.slider("Min Relevance", min_value=1, max_value=5, value=1)

    with browse_col3:
        text_filter = st.text_input("Text search", placeholder="Search quotes, summaries, notes...")

    cat_filter = None if selected_cat == "All" else selected_cat
    rel_filter = min_rel if min_rel > 1 else None

    if text_filter.strip():
        result = db.search_evidence(
            query=text_filter.strip(),
            category=cat_filter,
            min_relevance=rel_filter,
            limit=100,
        )
        items = result["items"]
        total = result["total"]
    else:
        result = db.list_evidence(
            category=cat_filter,
            min_relevance=rel_filter,
            limit=100,
        )
        items = result["items"]
        total = result["total"]

    st.caption(f"Showing {len(items)} of {total} items")

    if not items:
        st.info("No evidence items found. Use the `evidence_add` MCP tool from Claude Code to start collecting evidence.")
    else:
        for item in items:
            relevance_stars = "★" * item["relevance"] + "☆" * (5 - item["relevance"])
            verified_icon = "✓" if item.get("verified") else "✗"
            date_short = str(item.get("date", ""))[:10]

            with st.expander(
                f"{item['category'].upper()} | {relevance_stars} | {verified_icon} verified | "
                f"{item.get('sender_name', '')} | {date_short} — {item.get('subject', '')}",
                expanded=False,
            ):
                st.markdown(f"**Quote:** {item.get('key_quote', '')}")
                st.markdown(f"**Summary:** {item.get('summary', '')}")
                if item.get("notes"):
                    st.markdown(f"**Notes:** {item['notes']}")
                st.caption(
                    f"ID: {item['id']} | Sender: {item.get('sender_email', '')} | "
                    f"Recipients: {item.get('recipients', '')} | "
                    f"Email UID: {item.get('email_uid', '')}"
                )

    st.divider()

    # ── Export ────────────────────────────────────────────────
    st.subheader("Export Evidence")
    export_col1, export_col2 = st.columns(2)

    with export_col1:
        export_format = st.selectbox("Format", ["html", "csv"], index=0)

    with export_col2:
        export_min_rel = st.selectbox("Min Relevance for Export", [1, 2, 3, 4, 5], index=0)

    if st.button("Generate Export"):
        try:
            from .evidence_exporter import EvidenceExporter
        except ImportError:
            from src.evidence_exporter import EvidenceExporter

        exporter = EvidenceExporter(db)
        export_result = exporter.export(
            fmt=export_format,
            min_relevance=export_min_rel if export_min_rel > 1 else None,
            category=cat_filter,
        )

        if export_format == "html" and "html" in export_result:
            st.download_button(
                label="Download HTML Report",
                data=export_result["html"],
                file_name="evidence_report.html",
                mime="text/html",
            )
        elif export_format == "csv" and "csv" in export_result:
            st.download_button(
                label="Download CSV",
                data=export_result["csv"],
                file_name="evidence_report.csv",
                mime="text/csv",
            )


def main() -> None:
    inject_styles()
    st.markdown("<h1 class='hero-title'>Email RAG</h1>", unsafe_allow_html=True)
    st.markdown(
        "<p class='hero-subtitle'>Search Outlook exports indexed in local ChromaDB with precise filters.</p>",
        unsafe_allow_html=True,
    )

    # Navigation
    page = st.sidebar.radio(
        "Navigate",
        ["🔍 Search", "📊 Dashboard", "🏷️ Entities", "🌐 Network", "📋 Evidence"],
        index=0,
    )

    chromadb_path = st.sidebar.text_input("ChromaDB Path", value="") or None
    retriever = get_retriever(chromadb_path)
    render_sidebar(retriever)

    # Route to non-search pages (don't need ChromaDB data)
    if page == "📊 Dashboard":
        render_dashboard_page()
        return
    if page == "🏷️ Entities":
        render_entity_page()
        return
    if page == "🌐 Network":
        render_network_page()
        return
    if page == "📋 Evidence":
        render_evidence_page()
        return

    # Search page
    if retriever.collection.count() == 0:
        st.warning("No emails indexed yet.")
        st.info(
            "To index your Outlook archive, run the ingestion script:\n\n"
            "```\npython -m src.ingest path/to/export.olm\n```\n\n"
            "Or use the **`email_ingest`** MCP tool directly from Claude Code."
        )
        return

    st.session_state.setdefault("web_results", [])
    st.session_state.setdefault("web_query", "")
    st.session_state.setdefault("web_filters", {})
    st.session_state.setdefault("web_sort", "relevance")
    st.session_state.setdefault("web_page", 0)
    st.session_state.setdefault("web_thread_id", None)

    with st.form("search_form", clear_on_submit=False):
        query = st.text_input("Query", placeholder="Find contract renewal emails from legal")
        control_col1, control_col2, control_col3 = st.columns(3)
        with control_col1:
            top_k = st.number_input("Top K", min_value=1, max_value=50, value=10)
        with control_col2:
            sort_label = st.selectbox("Sort Results", list(SORT_OPTIONS.keys()), index=0)
        with control_col3:
            min_score = st.slider("Min Relevance", min_value=0.0, max_value=1.0, value=0.0, step=0.05)

        filter_col1, filter_col2, filter_col3, filter_col4, filter_col5 = st.columns(5)
        with filter_col1:
            sender = st.text_input("Sender (optional)")
        with filter_col2:
            to_filter = st.text_input("To (optional)")
        with filter_col3:
            subject = st.text_input("Subject (optional)")
        with filter_col4:
            folder = st.text_input("Folder (optional)")
        with filter_col5:
            cc = st.text_input("CC (optional)")

        filter2_col1, filter2_col2, filter2_col3, filter2_col4 = st.columns(4)
        with filter2_col1:
            bcc = st.text_input("BCC (optional)")
        with filter2_col2:
            priority = st.number_input("Min Priority", min_value=0, max_value=5, value=0, step=1)
        with filter2_col3:
            email_type_options = ["Any", "reply", "forward", "original"]
            email_type_label = st.selectbox("Email Type", email_type_options, index=0)
        with filter2_col4:
            has_attachments = st.checkbox("Has attachments")

        date_col1, date_col2 = st.columns(2)
        with date_col1:
            date_from_val = st.date_input("Date From", value=None)
        with date_col2:
            date_to_val = st.date_input("Date To", value=None)

        with st.expander("Advanced Search Options"):
            adv_col1, adv_col2, adv_col3 = st.columns(3)
            with adv_col1:
                use_hybrid = st.checkbox(
                    "Hybrid search (semantic + keyword)",
                    help="Combines dense vector search with sparse keyword matching for better recall.",
                )
            with adv_col2:
                use_rerank = st.checkbox(
                    "Re-rank results (better precision)",
                    help="Re-ranks results using ColBERT or cross-encoder for improved precision.",
                )
            with adv_col3:
                use_expand = st.checkbox(
                    "Expand query (better recall)",
                    help="Expands query with semantically related terms for broader results.",
                )

        search_clicked = st.form_submit_button("Search", type="primary")

    if search_clicked:
        if not query.strip():
            st.warning("Please enter a query.")
        else:
            valid_date_from = str(date_from_val) if date_from_val else None
            valid_date_to = str(date_to_val) if date_to_val else None
            try:
                validate_date_window(valid_date_from, valid_date_to)
            except ValueError:
                st.error("Date From cannot be later than Date To.")
            else:
                min_score_value = round(float(min_score), 2) if min_score > 0.0 else None
                has_att_value = True if has_attachments else None
                priority_value = int(priority) if priority and priority > 0 else None
                email_type_value = email_type_label if email_type_label != "Any" else None
                filters = {
                    "sender": sender or None,
                    "to": to_filter or None,
                    "subject": subject or None,
                    "folder": folder or None,
                    "cc": cc or None,
                    "bcc": bcc or None,
                    "has_attachments": has_att_value,
                    "priority": priority_value,
                    "email_type": email_type_value,
                    "date_from": valid_date_from,
                    "date_to": valid_date_to,
                    "min_score": min_score_value,
                    "hybrid": use_hybrid,
                    "rerank": use_rerank,
                    "expand_query": use_expand,
                }

                results = retriever.search_filtered(
                    query=query,
                    top_k=int(top_k),
                    sender=filters["sender"],
                    to=filters["to"],
                    subject=filters["subject"],
                    folder=filters["folder"],
                    cc=filters["cc"],
                    bcc=filters["bcc"],
                    has_attachments=filters["has_attachments"],
                    priority=filters["priority"],
                    email_type=filters["email_type"],
                    date_from=filters["date_from"],
                    date_to=filters["date_to"],
                    min_score=filters["min_score"],
                    hybrid=filters["hybrid"],
                    rerank=filters["rerank"],
                    expand_query=filters["expand_query"],
                )
                sort_value = SORT_OPTIONS[sort_label]
                sorted_results = sort_search_results(results, sort_value)

                st.session_state["web_results"] = sorted_results
                st.session_state["web_query"] = query
                st.session_state["web_filters"] = filters
                st.session_state["web_sort"] = sort_value
                st.session_state["web_page"] = 0

    results = st.session_state.get("web_results", [])
    if not results:
        st.info("Run a search to browse indexed emails with advanced filters.")
        return

    sort_value = st.session_state.get("web_sort", "relevance")
    sort_label = next((label for label, value in SORT_OPTIONS.items() if value == sort_value), "Relevance")
    filters = cast(dict[str, Any], st.session_state.get("web_filters", {}))
    sender_filter = _as_optional_str(filters.get("sender"))
    to_filter_val = _as_optional_str(filters.get("to"))
    subject_filter = _as_optional_str(filters.get("subject"))
    folder_filter = _as_optional_str(filters.get("folder"))
    cc_filter = _as_optional_str(filters.get("cc"))
    bcc_filter = _as_optional_str(filters.get("bcc"))
    has_att_filter = filters.get("has_attachments")
    priority_filter = filters.get("priority")
    email_type_filter = _as_optional_str(filters.get("email_type"))
    date_from_filter = _as_optional_str(filters.get("date_from"))
    date_to_filter = _as_optional_str(filters.get("date_to"))
    min_score_filter = _as_optional_float(filters.get("min_score"))
    active_filter_labels = build_active_filter_labels(
        sender=sender_filter,
        to=to_filter_val,
        subject=subject_filter,
        folder=folder_filter,
        cc=cc_filter,
        bcc=bcc_filter,
        has_attachments=has_att_filter if isinstance(has_att_filter, bool) else None,
        priority=int(priority_filter) if isinstance(priority_filter, (int, float)) else None,
        email_type=email_type_filter,
        date_from=date_from_filter,
        date_to=date_to_filter,
        min_score=min_score_filter,
    )
    render_results_summary(results, active_filter_labels, sort_label)

    total_pages = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(st.session_state.get("web_page", 0), total_pages - 1))
    page_results = results[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    # Thread view
    thread_id = st.session_state.get("web_thread_id")
    if thread_id:
        st.subheader("Conversation Thread")
        thread_results = retriever.search_by_thread(thread_id)
        if thread_results:
            for idx, tr in enumerate(thread_results, 1):
                tm = tr.metadata
                st.markdown(
                    f"**{idx}. {tm.get('subject', '?')}** | "
                    f"{tm.get('sender_name') or tm.get('sender_email', '?')} | "
                    f"{str(tm.get('date', '?'))[:10]}"
                )
                st.text(tr.text[:800] if len(tr.text) > 800 else tr.text)
                st.divider()
        else:
            st.info("No emails found for this thread.")
        if st.button("Close Thread View"):
            del st.session_state["web_thread_id"]
            st.rerun()
        st.divider()

    preview_chars = st.slider("Preview Length", min_value=200, max_value=4000, value=1200, step=100)
    render_results(page_results, preview_chars=preview_chars, retriever=retriever)

    if total_pages > 1:
        nav_col1, nav_col2, nav_col3 = st.columns([1, 2, 1])
        with nav_col1:
            if st.button("◀ Prev", disabled=page == 0):
                st.session_state["web_page"] = page - 1
                st.rerun()
        with nav_col2:
            st.caption(f"Page {page + 1} of {total_pages} ({len(results)} results total)")
        with nav_col3:
            if st.button("Next ▶", disabled=page >= total_pages - 1):
                st.session_state["web_page"] = page + 1
                st.rerun()

    export_col1, export_col2 = st.columns(2)
    payload = build_export_payload(
        query=st.session_state.get("web_query", ""),
        results=results,
        filters=filters,
        sort_by=sort_value,
    )
    with export_col1:
        st.download_button(
            label="Download JSON",
            data=json.dumps(payload, indent=2),
            file_name="email-search-results.json",
            mime="application/json",
        )
    with export_col2:
        csv_data = _build_csv_export(results)
        st.download_button(
            label="Download CSV",
            data=csv_data,
            file_name="email-search-results.csv",
            mime="text/csv",
        )


def _build_csv_export(results: list[Any]) -> str:
    """Build CSV string from search results."""
    output = io.StringIO()
    fieldnames = ["date", "sender", "subject", "folder", "score", "text_preview"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for result in results:
        meta = result.metadata
        text = result.text or ""
        writer.writerow({
            "date": str(meta.get("date", ""))[:10],
            "sender": meta.get("sender_name") or meta.get("sender_email", ""),
            "subject": meta.get("subject", ""),
            "folder": meta.get("folder", ""),
            "score": f"{result.score:.2f}",
            "text_preview": text[:300],
        })
    return output.getvalue()


def _as_optional_str(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _as_optional_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


if __name__ == "__main__":
    main()
