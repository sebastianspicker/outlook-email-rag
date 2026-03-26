"""Local Streamlit UI for browsing and searching indexed emails."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, cast

import streamlit as st

# Streamlit doesn't support `python -m` module execution, so `streamlit run
# src/web_app.py` uses the file directly.  Relative imports work when imported
# as part of the `src` package, but fail when Streamlit runs the file as
# __main__.  The ImportError fallback handles that case.
try:
    from .formatting import format_date
    from .retriever import EmailRetriever
    from .validation import validate_date_window
    from .web_ui import build_active_filter_labels, build_export_payload, build_filter_chip_html, sort_search_results
except ImportError:  # pragma: no cover - allows `streamlit run src/web_app.py`
    from src.formatting import format_date
    from src.retriever import EmailRetriever
    from src.validation import validate_date_window
    from src.web_ui import build_active_filter_labels, build_export_payload, build_filter_chip_html, sort_search_results

st.set_page_config(
    page_title="Email RAG - Email Discovery",
    page_icon="\U0001f50d",
    layout="wide",
    initial_sidebar_state="expanded",
)

SORT_OPTIONS = {
    "Relevance": "relevance",
    "Newest first": "date_desc",
    "Oldest first": "date_asc",
    "Sender A-Z": "sender_asc",
}

PAGE_SIZE = 20


@st.cache_resource
def get_retriever(chromadb_path: str | None, _cache_version: int = 0):
    return EmailRetriever(chromadb_path=chromadb_path)


def invalidate_retriever_cache() -> None:
    """Invalidate the cached retriever so the next access creates a fresh one."""
    get_retriever.clear()


def render_sidebar(retriever: EmailRetriever) -> None:
    from html import escape as html_escape

    st.sidebar.markdown("#### Archive Overview")

    stats = retriever.stats()
    sidebar_col1, sidebar_col2, sidebar_col3 = st.sidebar.columns(3)
    sidebar_col1.metric("Emails", f"{stats.get('total_emails', 0):,}")
    sidebar_col2.metric("Chunks", f"{stats.get('total_chunks', 0):,}")
    sidebar_col3.metric("Senders", f"{stats.get('unique_senders', 0):,}")

    date_range = stats.get("date_range", {})
    earliest = date_range.get("earliest", "?")
    latest = date_range.get("latest", "?")
    st.sidebar.caption(f"{earliest}  to  {latest}")

    folders = stats.get("folders", {})
    if folders:
        with st.sidebar.expander("Folders", expanded=False):
            sorted_folders = sorted(folders.items(), key=lambda x: x[1], reverse=True)
            for folder_name, count in sorted_folders:
                st.sidebar.markdown(
                    f"<div style='display:flex;justify-content:space-between;font-size:0.82rem;padding:0.1rem 0;'>"
                    f"<span>{html_escape(folder_name)}</span><span style='color:#64748b;font-weight:600;'>{count:,}</span></div>",
                    unsafe_allow_html=True,
                )

    with st.sidebar.expander("Top Senders", expanded=False):
        senders = retriever.list_senders(limit=15)
        if not senders:
            st.caption("No senders indexed yet.")
        else:
            max_count = max(sender["count"] for sender in senders)
            for sender in senders:
                display_name = sender["name"] or sender["email"]
                pct = sender["count"] / max_count if max_count else 0.0
                st.sidebar.markdown(
                    f"<div style='font-size:0.8rem;margin-bottom:0.15rem;'>"
                    f"<span style='font-weight:500;'>{html_escape(display_name)}</span> "
                    f"<span style='color:#64748b;'>({sender['count']:,})</span></div>",
                    unsafe_allow_html=True,
                )
                st.sidebar.progress(pct)


def _score_css_class(score: float) -> str:
    if score >= 0.75:
        return "score-high"
    if score >= 0.45:
        return "score-mid"
    return "score-low"


def _type_badge_html(email_type: str | None) -> str:
    if not email_type or email_type == "original":
        return ""
    css_class = f"type-{email_type}" if email_type in ("reply", "forward") else "type-original"
    return f" <span class='type-badge {css_class}'>{email_type}</span>"


def _attachment_badge_html(att_count: str | int) -> str:
    count = str(att_count)
    if count in ("0", "", "None"):
        return ""
    return f" <span class='type-badge type-attachment'>{count} att.</span>"


def render_results(results: list[Any], preview_chars: int, retriever: EmailRetriever | None = None) -> None:
    st.markdown("### Matching Emails")

    from html import escape as html_escape

    for index, result in enumerate(results, 1):
        metadata = result.metadata
        title = html_escape(metadata.get("subject", "(no subject)"))
        sender_name = html_escape(metadata.get("sender_name", ""))
        sender_email_val = html_escape(metadata.get("sender_email", ""))
        sender_display = sender_name or sender_email_val or "?"
        date_value = str(metadata.get("date", "?"))[:10]
        folder = html_escape(metadata.get("folder", "Unknown"))
        body = result.text or ""
        preview = body if len(body) <= preview_chars else f"{body[:preview_chars]}..."
        score = float(result.score)

        email_type = metadata.get("email_type", "original")
        att_count = metadata.get("attachment_count", "0")

        # Build a clear expander label
        score_pct = f"{score:.0%}"
        expander_label = f"{index}. {title}  --  {sender_display}  |  {date_value}  |  {score_pct}"

        with st.expander(expander_label, expanded=index == 1):
            # Score + type badges as HTML
            score_class = _score_css_class(score)
            badges_html = f"<span class='score-badge {score_class}'>{score_pct}</span>"
            badges_html += _type_badge_html(email_type)
            badges_html += _attachment_badge_html(att_count)
            st.markdown(badges_html, unsafe_allow_html=True)

            # Metadata fields in columns
            meta_col1, meta_col2, meta_col3, meta_col4 = st.columns(4)
            with meta_col1:
                st.markdown(f"<div class='email-field'><strong>From:</strong> {sender_display}</div>", unsafe_allow_html=True)
            with meta_col2:
                to_value = metadata.get("to", "")
                if to_value:
                    to_list = [t.strip() for t in str(to_value).split(",") if t.strip()]
                    to_display = html_escape(", ".join(to_list[:3]))
                    if len(to_list) > 3:
                        to_display += f" (+{len(to_list) - 3})"
                    st.markdown(f"<div class='email-field'><strong>To:</strong> {to_display}</div>", unsafe_allow_html=True)
            with meta_col3:
                st.markdown(f"<div class='email-field'><strong>Folder:</strong> {folder}</div>", unsafe_allow_html=True)
            with meta_col4:
                formatted_date = format_date(str(metadata.get("date", "")))
                st.markdown(
                    f"<div class='email-field'><strong>Date:</strong> {formatted_date or date_value}</div>",
                    unsafe_allow_html=True,
                )

            # Attachment names
            att_names = metadata.get("attachment_names", "")
            if att_names and str(att_names).strip():
                st.markdown(
                    f"<div class='email-field'><strong>Attachments:</strong> {html_escape(str(att_names))}</div>",
                    unsafe_allow_html=True,
                )

            # Priority indicator
            priority = metadata.get("priority", "0")
            if priority and str(priority) not in ("0", ""):
                st.markdown(
                    f"<div class='email-field'><strong>Priority:</strong> {html_escape(str(priority))}</div>",
                    unsafe_allow_html=True,
                )

            # Email body preview - rendered as styled HTML instead of monospace st.text
            st.markdown(
                f"<div class='email-body-preview'>{html_escape(preview)}</div>",
                unsafe_allow_html=True,
            )

            if len(body) > preview_chars:
                with st.expander("Show full text", expanded=False):
                    st.markdown(
                        f"<div class='email-body-full'>{html_escape(body)}</div>",
                        unsafe_allow_html=True,
                    )

            # Action buttons row
            btn_col1, btn_col2 = st.columns([1, 5])
            conv_id = str(metadata.get("conversation_id", "") or "").strip()
            with btn_col1:
                if conv_id and retriever is not None:
                    if st.button("View Thread", key=f"thread_{result.chunk_id}", type="secondary"):
                        st.session_state["web_thread_id"] = conv_id
                        st.rerun()
            with btn_col2:
                uid = metadata.get("uid", "")
                uid_short = uid[:12] + "..." if len(uid) > 12 else uid
                st.caption(f"UID: {uid_short} | Chunk: {result.chunk_id}")


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        /* ── Professional email discovery palette ─────────────── */
        :root {
            --bg-primary: #f8fafc;
            --bg-surface: #ffffff;
            --bg-muted: #f1f5f9;
            --ink-primary: #0f172a;
            --ink-secondary: #475569;
            --ink-muted: #94a3b8;
            --accent-blue: #2563eb;
            --accent-blue-soft: #dbeafe;
            --accent-green: #059669;
            --accent-green-soft: #d1fae5;
            --accent-amber: #d97706;
            --accent-amber-soft: #fef3c7;
            --accent-red: #dc2626;
            --accent-red-soft: #fee2e2;
            --border-light: #e2e8f0;
            --border-medium: #cbd5e1;
            --radius-sm: 6px;
            --radius-md: 8px;
            --radius-lg: 12px;
        }

        .hero-title {
            font-family: "Inter", "Segoe UI", -apple-system, sans-serif;
            color: var(--ink-primary);
            font-weight: 700;
            font-size: 1.8rem;
            letter-spacing: -0.02em;
            margin-bottom: 0;
        }
        .hero-subtitle {
            font-family: "Inter", "Segoe UI", -apple-system, sans-serif;
            color: var(--ink-secondary);
            font-size: 0.95rem;
            margin-bottom: 1.2rem;
        }

        /* ── Filter chips ────────────────────────────────────── */
        .filter-chip {
            display: inline-block;
            margin: 0 0.35rem 0.35rem 0;
            padding: 0.2rem 0.55rem;
            border-radius: 999px;
            background: var(--accent-blue-soft);
            border: 1px solid #bfdbfe;
            color: #1e40af;
            font-size: 0.78rem;
            font-weight: 500;
            font-family: "Inter", "Segoe UI", -apple-system, sans-serif;
        }

        /* ── Email card styling ──────────────────────────────── */
        .email-header {
            display: flex;
            flex-wrap: wrap;
            gap: 0.5rem;
            align-items: center;
            margin-bottom: 0.5rem;
        }
        .email-field {
            font-size: 0.82rem;
            color: var(--ink-secondary);
            font-family: "Inter", "Segoe UI", -apple-system, sans-serif;
        }
        .email-field strong {
            color: var(--ink-primary);
            font-weight: 600;
        }
        .email-body-preview {
            font-family: "Inter", "Segoe UI", -apple-system, sans-serif;
            font-size: 0.88rem;
            line-height: 1.55;
            color: var(--ink-primary);
            padding: 0.75rem 1rem;
            background: var(--bg-muted);
            border-radius: var(--radius-md);
            border-left: 3px solid var(--border-medium);
            white-space: pre-wrap;
            word-wrap: break-word;
            max-height: 400px;
            overflow-y: auto;
        }
        .email-body-full {
            font-family: "Inter", "Segoe UI", -apple-system, sans-serif;
            font-size: 0.85rem;
            line-height: 1.6;
            color: var(--ink-primary);
            padding: 1rem;
            background: var(--bg-muted);
            border-radius: var(--radius-md);
            border: 1px solid var(--border-light);
            white-space: pre-wrap;
            word-wrap: break-word;
            max-height: 600px;
            overflow-y: auto;
        }

        /* ── Score badge ─────────────────────────────────────── */
        .score-badge {
            display: inline-block;
            padding: 0.15rem 0.5rem;
            border-radius: var(--radius-sm);
            font-size: 0.78rem;
            font-weight: 600;
            font-family: "SF Mono", "Fira Code", "JetBrains Mono", monospace;
        }
        .score-high { background: var(--accent-green-soft); color: #065f46; }
        .score-mid { background: var(--accent-amber-soft); color: #92400e; }
        .score-low { background: var(--accent-red-soft); color: #991b1b; }

        /* ── Type badge ──────────────────────────────────────── */
        .type-badge {
            display: inline-block;
            padding: 0.12rem 0.45rem;
            border-radius: var(--radius-sm);
            font-size: 0.72rem;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }
        .type-reply { background: #ede9fe; color: #5b21b6; }
        .type-forward { background: #fce7f3; color: #9d174d; }
        .type-original { background: var(--accent-blue-soft); color: #1e40af; }
        .type-attachment { background: #fef3c7; color: #92400e; }

        /* ── Thread view ─────────────────────────────────────── */
        .thread-email {
            padding: 0.75rem 1rem;
            margin-bottom: 0.5rem;
            background: var(--bg-surface);
            border: 1px solid var(--border-light);
            border-radius: var(--radius-md);
            border-left: 3px solid var(--accent-blue);
        }
        .thread-email-header {
            font-size: 0.82rem;
            color: var(--ink-secondary);
            margin-bottom: 0.4rem;
        }
        .thread-email-body {
            font-size: 0.85rem;
            line-height: 1.5;
            color: var(--ink-primary);
            white-space: pre-wrap;
            word-wrap: break-word;
        }

        /* ── Sidebar polish ──────────────────────────────────── */
        [data-testid="stSidebar"] .stMetric label {
            font-size: 0.78rem;
            color: var(--ink-muted);
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        /* ── Search type indicator ───────────────────────────── */
        .search-mode-indicator {
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            padding: 0.25rem 0.6rem;
            border-radius: var(--radius-sm);
            font-size: 0.78rem;
            font-weight: 500;
            margin-right: 0.4rem;
        }
        .mode-semantic { background: #dbeafe; color: #1d4ed8; }
        .mode-hybrid { background: #ede9fe; color: #6d28d9; }
        .mode-reranked { background: #d1fae5; color: #047857; }

        /* ── Evidence quote styling ──────────────────────────── */
        .evidence-quote {
            font-family: "Inter", "Segoe UI", -apple-system, sans-serif;
            font-size: 0.88rem;
            line-height: 1.55;
            padding: 0.75rem 1rem;
            background: #fefce8;
            border-radius: var(--radius-md);
            border-left: 4px solid #eab308;
            color: #713f12;
            font-style: italic;
        }

        /* ── Pagination ─────────────────────────────────────── */
        .pagination-info {
            text-align: center;
            font-size: 0.82rem;
            color: var(--ink-muted);
            padding: 0.5rem 0;
        }

        /* ── Empty state ────────────────────────────────────── */
        .empty-state {
            text-align: center;
            padding: 2rem 1rem;
            color: var(--ink-muted);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_results_summary(
    results: list[Any],
    active_filters: list[str],
    sort_label: str,
    search_modes: list[str] | None = None,
) -> None:
    scores = [float(result.score) for result in results]
    avg_score = sum(scores) / len(scores) if scores else 0.0
    max_score = max(scores) if scores else 0.0
    min_score = min(scores) if scores else 0.0

    metric_col1, metric_col2, metric_col3, metric_col4 = st.columns(4)
    metric_col1.metric("Results", len(results))
    metric_col2.metric("Best Match", f"{max_score:.0%}")
    metric_col3.metric("Avg Relevance", f"{avg_score:.0%}")
    metric_col4.metric("Lowest Score", f"{min_score:.0%}")

    # Search mode badges
    mode_html = ""
    if search_modes:
        for mode in search_modes:
            css = "mode-semantic"
            if mode == "hybrid":
                css = "mode-hybrid"
            elif mode == "reranked":
                css = "mode-reranked"
            mode_html += f"<span class='search-mode-indicator {css}'>{mode}</span>"
    mode_html += f"<span style='font-size:0.82rem;color:#64748b;'>Sorted by: {sort_label}</span>"
    st.markdown(mode_html, unsafe_allow_html=True)

    if active_filters:
        chips = build_filter_chip_html(active_filters)
        st.markdown(chips, unsafe_allow_html=True)


@st.cache_resource
def _get_email_db_safe():
    """Try to get EmailDatabase instance, return None if unavailable.

    Cached via st.cache_resource so only one connection is created
    per Streamlit server process (avoids leaking SQLite connections).
    """
    try:
        from .config import get_settings
        from .email_db import EmailDatabase
    except ImportError:
        from src.config import get_settings
        from src.email_db import EmailDatabase

    settings = get_settings()
    if settings.sqlite_path and Path(settings.sqlite_path).exists():
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
            st.bar_chart(df_contacts, x="partner", y="total")
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

    entity_types = ["All", "organization", "person", "url", "phone", "email", "event"]
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


def _relevance_badge_html(relevance: int) -> str:
    """Render a relevance score as a styled badge."""
    _REL_COLORS = {
        5: ("#065f46", "#d1fae5"),  # green
        4: ("#065f46", "#d1fae5"),
        3: ("#92400e", "#fef3c7"),  # amber
        2: ("#78716c", "#f5f5f4"),  # gray
        1: ("#78716c", "#f5f5f4"),
    }
    _REL_LABELS = {5: "DIRECT PROOF", 4: "STRONG", 3: "SUPPORTING", 2: "BACKGROUND", 1: "TANGENTIAL"}
    color, bg = _REL_COLORS.get(relevance, ("#78716c", "#f5f5f4"))
    label = _REL_LABELS.get(relevance, str(relevance))
    stars = "\u2605" * relevance + "\u2606" * (5 - relevance)
    return (
        f"<span style='display:inline-block;padding:0.15rem 0.5rem;border-radius:6px;"
        f"background:{bg};color:{color};font-size:0.75rem;font-weight:600;"
        f"font-family:\"SF Mono\",\"Fira Code\",monospace;'>"
        f"{stars} {label}</span>"
    )


def _verified_badge_html(verified: bool) -> str:
    """Render a verification status badge."""
    if verified:
        return (
            "<span style='display:inline-block;padding:0.12rem 0.45rem;border-radius:6px;"
            "background:#d1fae5;color:#065f46;font-size:0.72rem;font-weight:600;"
            "letter-spacing:0.04em;'>VERIFIED</span>"
        )
    return (
        "<span style='display:inline-block;padding:0.12rem 0.45rem;border-radius:6px;"
        "background:#fef3c7;color:#92400e;font-size:0.72rem;font-weight:600;"
        "letter-spacing:0.04em;'>PENDING</span>"
    )


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
    met_col1, met_col2, met_col3, met_col4 = st.columns(4)
    met_col1.metric("Total Items", stats["total"])
    met_col2.metric("Verified", stats["verified"])
    met_col3.metric("Unverified", stats["unverified"])
    verified_pct = f"{stats['verified'] / stats['total']:.0%}" if stats["total"] > 0 else "N/A"
    met_col4.metric("Verification Rate", verified_pct)

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
        from html import escape as _html_escape

        for item in items:
            relevance = item.get("relevance", 0)
            verified = bool(item.get("verified"))
            date_short = str(item.get("date", ""))[:10]
            category = item.get("category", "general")
            sender_name = item.get("sender_name", "")
            subject = item.get("subject", "(no subject)")

            with st.expander(
                f"{category.upper()} | "
                + "\u2605" * relevance + "\u2606" * (5 - relevance)
                + f" | {'VERIFIED' if verified else 'PENDING'} | "
                f"{sender_name} | {date_short} -- {subject}",
                expanded=False,
            ):
                # Status badges row
                badges = _relevance_badge_html(relevance)
                badges += " " + _verified_badge_html(verified)
                badges += " " + _type_badge_html(None)  # category badge
                badges += (
                    f" <span style='display:inline-block;padding:0.12rem 0.45rem;border-radius:6px;"
                    f"background:#ede9fe;color:#5b21b6;font-size:0.72rem;font-weight:600;"
                    f"text-transform:uppercase;letter-spacing:0.04em;'>{_html_escape(category)}</span>"
                )
                st.markdown(badges, unsafe_allow_html=True)

                # Metadata fields
                ev_col1, ev_col2, ev_col3 = st.columns(3)
                with ev_col1:
                    sender_display_ev = _html_escape(sender_name or item.get("sender_email", ""))
                    st.markdown(
                        f"<div class='email-field'><strong>From:</strong> {sender_display_ev}</div>",
                        unsafe_allow_html=True,
                    )
                with ev_col2:
                    st.markdown(
                        f"<div class='email-field'><strong>Date:</strong> {_html_escape(date_short)}</div>",
                        unsafe_allow_html=True,
                    )
                with ev_col3:
                    st.markdown(
                        f"<div class='email-field'><strong>Subject:</strong> {_html_escape(str(subject))}</div>",
                        unsafe_allow_html=True,
                    )

                # Key quote with distinctive styling
                quote = item.get("key_quote", "")
                if quote:
                    st.markdown(
                        f"<div style='font-family:\"Inter\",sans-serif;font-size:0.88rem;line-height:1.55;"
                        f"padding:0.75rem 1rem;background:#fefce8;border-radius:8px;"
                        f"border-left:4px solid #eab308;color:#713f12;'>"
                        f"<strong style='font-style:normal;color:#92400e;'>Quote:</strong> "
                        f"<em>\"{_html_escape(quote)}\"</em></div>",
                        unsafe_allow_html=True,
                    )

                # Summary
                summary = item.get("summary", "")
                if summary:
                    st.markdown(f"**Summary:** {_html_escape(summary)}")

                # Notes
                if item.get("notes"):
                    st.markdown(f"**Notes:** {_html_escape(item['notes'])}")

                # Metadata footer
                st.caption(
                    f"Evidence ID: {item['id']} | "
                    f"Email UID: {item.get('email_uid', '')} | "
                    f"Sender: {item.get('sender_email', '')} | "
                    f"Recipients: {item.get('recipients', '')}"
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
        export_min_rel_val: int | None = export_min_rel if export_min_rel > 1 else None
        if export_format == "csv":
            export_result = exporter.export_csv(min_relevance=export_min_rel_val, category=cat_filter)
        else:
            export_result = exporter.export_html(min_relevance=export_min_rel_val, category=cat_filter)

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
        "<p class='hero-subtitle'>Search and investigate your Outlook email archive"
        " with semantic search, filters, and analytics.</p>",
        unsafe_allow_html=True,
    )

    # Navigation
    page = st.sidebar.radio(
        "Navigate",
        ["Search", "Dashboard", "Entities", "Network", "Evidence"],
        index=0,
        captions=[
            "Semantic & filtered search",
            "Volume & activity",
            "People & orgs",
            "Communication graph",
            "Collected evidence",
        ],
    )

    chromadb_path = st.sidebar.text_input("ChromaDB Path", value="") or None
    retriever = get_retriever(chromadb_path)
    render_sidebar(retriever)

    # Route to non-search pages (don't need ChromaDB data)
    if page == "Dashboard":
        render_dashboard_page()
        return
    if page == "Entities":
        render_entity_page()
        return
    if page == "Network":
        render_network_page()
        return
    if page == "Evidence":
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
        query = st.text_input(
            "Search Query",
            placeholder="e.g. contract renewal emails from legal department",
            help="Natural language query. The system uses semantic search to find relevant emails.",
        )

        # Search controls
        ctrl_col1, ctrl_col2, ctrl_col3, ctrl_col4 = st.columns([2, 2, 2, 2])
        with ctrl_col1:
            top_k = st.number_input("Max Results", min_value=1, max_value=50, value=10)
        with ctrl_col2:
            sort_label = st.selectbox("Sort By", list(SORT_OPTIONS.keys()), index=0)
        with ctrl_col3:
            min_score = st.slider("Min Relevance", min_value=0.0, max_value=1.0, value=0.0, step=0.05)
        with ctrl_col4:
            email_type_options = ["Any", "reply", "forward", "original"]
            email_type_label = st.selectbox("Email Type", email_type_options, index=0)

        # Metadata filters
        with st.expander("Metadata Filters", expanded=False):
            filt_col1, filt_col2, filt_col3 = st.columns(3)
            with filt_col1:
                sender = st.text_input("Sender", placeholder="name or email")
                to_filter = st.text_input("To", placeholder="recipient")
            with filt_col2:
                subject = st.text_input("Subject", placeholder="keyword in subject")
                folder = st.text_input("Folder", placeholder="Inbox, Sent, etc.")
            with filt_col3:
                cc = st.text_input("CC", placeholder="cc recipient")
                bcc = st.text_input("BCC", placeholder="bcc recipient")

            extra_col1, extra_col2, extra_col3 = st.columns(3)
            with extra_col1:
                date_from_val = st.date_input("Date From", value=None)
            with extra_col2:
                date_to_val = st.date_input("Date To", value=None)
            with extra_col3:
                priority = st.number_input("Min Priority", min_value=0, max_value=5, value=0, step=1)
                has_attachments = st.checkbox("Has attachments")

        # Search mode options
        with st.expander("Search Mode", expanded=False):
            mode_col1, mode_col2, mode_col3 = st.columns(3)
            with mode_col1:
                use_hybrid = st.checkbox(
                    "Hybrid search",
                    help="Combines semantic vectors with BM25 keyword matching for better recall.",
                )
            with mode_col2:
                use_rerank = st.checkbox(
                    "Re-rank results",
                    help="Re-ranks using ColBERT/cross-encoder for better precision. Slower but more accurate.",
                )
            with mode_col3:
                use_expand = st.checkbox(
                    "Expand query",
                    help="Adds semantically related terms for broader coverage.",
                )

        search_clicked = st.form_submit_button("Search", type="primary", use_container_width=True)

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
        last_query = st.session_state.get("web_query", "")
        if last_query:
            st.warning(
                f"No results found for \"{last_query}\". "
                "Try broadening your search terms, removing filters, "
                "or enabling hybrid search mode for better keyword coverage."
            )
        else:
            st.info("Enter a search query above and click Search to browse indexed emails with advanced filters.")
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
    # Build search mode labels
    search_modes: list[str] = []
    if filters.get("hybrid"):
        search_modes.append("hybrid")
    elif not filters.get("hybrid"):
        search_modes.append("semantic")
    if filters.get("rerank"):
        search_modes.append("reranked")
    if filters.get("expand_query"):
        search_modes.append("expanded")

    render_results_summary(results, active_filter_labels, sort_label, search_modes=search_modes)

    total_pages = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
    page: int = max(0, min(int(st.session_state.get("web_page", 0)), total_pages - 1))
    page_results = results[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    # Thread view
    thread_id = st.session_state.get("web_thread_id")
    if thread_id:
        from html import escape as html_escape

        st.markdown("### Conversation Thread")
        thread_results = retriever.search_by_thread(thread_id)
        if thread_results:
            # Thread summary bar
            participants = list(dict.fromkeys(
                (tr.metadata.get("sender_name") or tr.metadata.get("sender_email", "?"))
                for tr in thread_results
            ))
            dates = [str(tr.metadata.get("date", ""))[:10] for tr in thread_results if tr.metadata.get("date")]
            thread_summary = (
                f"<div style='padding:0.6rem 1rem;background:#eef2f7;border-radius:8px;"
                f"font-size:0.82rem;color:#475569;margin-bottom:0.75rem;'>"
                f"<strong>{len(thread_results)} messages</strong> &middot; "
                f"<strong>{len(participants)} participants</strong>"
            )
            if dates:
                thread_summary += f" &middot; {min(dates)} to {max(dates)}"
            thread_summary += (
                f"<br/><span style='color:#94a3b8;'>Participants: "
                f"{html_escape(', '.join(participants[:5]))}"
                + (f' (+{len(participants) - 5})' if len(participants) > 5 else '')
                + "</span></div>"
            )
            st.markdown(thread_summary, unsafe_allow_html=True)

            for idx, tr in enumerate(thread_results, 1):
                tm = tr.metadata
                sender_val = tm.get("sender_name") or tm.get("sender_email", "?")
                date_val = str(tm.get("date", "?"))[:10]
                subj_val = tm.get("subject", "?")
                email_type = tm.get("email_type", "original")
                type_indicator = ""
                _type_style = "font-size:0.72rem;font-weight:600;margin-left:0.4rem;"
                if email_type == "reply":
                    type_indicator = f"<span style='color:#5b21b6;{_type_style}'>REPLY</span>"
                elif email_type == "forward":
                    type_indicator = f"<span style='color:#9d174d;{_type_style}'>FWD</span>"
                body_text = tr.text[:800] if len(tr.text) > 800 else tr.text
                # Alternate border color for different senders
                border_color = "#2563eb" if idx % 2 == 1 else "#7c3aed"
                st.markdown(
                    f"<div class='thread-email' style='border-left-color:{border_color};'>"
                    f"<div class='thread-email-header'>"
                    f"<strong>{idx}. {html_escape(str(sender_val))}</strong>{type_indicator}"
                    f" &middot; {html_escape(str(date_val))}"
                    f"<br/><span style='color:#64748b;font-size:0.78rem;'>{html_escape(str(subj_val))}</span>"
                    f"</div>"
                    f"<div class='thread-email-body'>{html_escape(body_text)}</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("No emails found for this thread.")
        if st.button("Close Thread View", type="secondary"):
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

    st.divider()
    st.markdown("#### Export Results")
    export_col1, export_col2, export_col3 = st.columns([2, 2, 4])
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
            use_container_width=True,
        )
    with export_col2:
        csv_data = _build_csv_export(results)
        st.download_button(
            label="Download CSV",
            data=csv_data,
            file_name="email-search-results.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with export_col3:
        query_text = st.session_state.get("web_query", "")
        st.caption(f'Exporting {len(results)} results for query: "{query_text}"')


_CSV_FORMULA_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe_cell(value: str) -> str:
    """Prefix cells starting with formula characters to prevent CSV injection."""
    if value and value[0] in _CSV_FORMULA_CHARS:
        return f"'{value}"
    return value


def _build_csv_export(results: list[Any]) -> str:
    """Build CSV string from search results."""
    output = io.StringIO()
    fieldnames = ["date", "sender", "subject", "folder", "score", "text_preview"]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for result in results:
        meta = result.metadata
        text = result.text or ""
        writer.writerow(
            {
                "date": str(meta.get("date", ""))[:10],
                "sender": _csv_safe_cell(meta.get("sender_name") or meta.get("sender_email", "")),
                "subject": _csv_safe_cell(meta.get("subject", "")),
                "folder": _csv_safe_cell(meta.get("folder", "")),
                "score": f"{result.score:.2f}",
                "text_preview": _csv_safe_cell(text[:300]),
            }
        )
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
