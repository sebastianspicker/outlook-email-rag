"""Local Streamlit UI for browsing and searching indexed emails."""

from __future__ import annotations

import json
from typing import Any, cast

import streamlit as st

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


def render_results(results: list[Any], preview_chars: int) -> None:
    st.subheader("Matching Emails")

    for index, result in enumerate(results, 1):
        metadata = result.metadata
        title = metadata.get("subject", "(no subject)")
        sender = metadata.get("sender_name") or metadata.get("sender_email", "?")
        date_value = str(metadata.get("date", "?"))[:10]
        folder = metadata.get("folder", "Unknown")
        body = result.text or ""
        preview = body if len(body) <= preview_chars else f"{body[:preview_chars]}..."

        with st.expander(f"{index}. {title} | {sender} | {date_value} | {result.score:.0%}", expanded=index == 1):
            info_col1, info_col2 = st.columns(2)
            info_col1.caption(f"Folder: {folder}")
            info_col2.caption(f"Chunk ID: {result.chunk_id}")
            st.progress(max(0.0, min(1.0, float(result.score))))
            st.caption("Preview")
            st.text(preview)
            if len(body) > preview_chars:
                with st.expander("Show full chunk", expanded=False):
                    st.text(body)


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


def main() -> None:
    inject_styles()
    st.markdown("<h1 class='hero-title'>Email RAG</h1>", unsafe_allow_html=True)
    st.markdown(
        "<p class='hero-subtitle'>Search Outlook exports indexed in local ChromaDB with precise filters.</p>",
        unsafe_allow_html=True,
    )

    chromadb_path = st.sidebar.text_input("ChromaDB Path", value="") or None
    retriever = get_retriever(chromadb_path)
    render_sidebar(retriever)

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

    with st.form("search_form", clear_on_submit=False):
        query = st.text_input("Query", placeholder="Find contract renewal emails from legal")
        control_col1, control_col2, control_col3 = st.columns(3)
        with control_col1:
            top_k = st.number_input("Top K", min_value=1, max_value=50, value=10)
        with control_col2:
            sort_label = st.selectbox("Sort Results", list(SORT_OPTIONS.keys()), index=0)
        with control_col3:
            min_score = st.slider("Min Relevance", min_value=0.0, max_value=1.0, value=0.0, step=0.05)

        filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
        with filter_col1:
            sender = st.text_input("Sender (optional)")
        with filter_col2:
            subject = st.text_input("Subject (optional)")
        with filter_col3:
            folder = st.text_input("Folder (optional)")
        with filter_col4:
            cc = st.text_input("CC (optional)")

        date_col1, date_col2 = st.columns(2)
        with date_col1:
            date_from_val = st.date_input("Date From", value=None)
        with date_col2:
            date_to_val = st.date_input("Date To", value=None)

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
                filters = {
                    "sender": sender or None,
                    "subject": subject or None,
                    "folder": folder or None,
                    "cc": cc or None,
                    "date_from": valid_date_from,
                    "date_to": valid_date_to,
                    "min_score": min_score_value,
                }

                results = retriever.search_filtered(
                    query=query,
                    top_k=int(top_k),
                    sender=filters["sender"],
                    subject=filters["subject"],
                    folder=filters["folder"],
                    cc=filters["cc"],
                    date_from=filters["date_from"],
                    date_to=filters["date_to"],
                    min_score=filters["min_score"],
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
    subject_filter = _as_optional_str(filters.get("subject"))
    folder_filter = _as_optional_str(filters.get("folder"))
    cc_filter = _as_optional_str(filters.get("cc"))
    date_from_filter = _as_optional_str(filters.get("date_from"))
    date_to_filter = _as_optional_str(filters.get("date_to"))
    min_score_filter = _as_optional_float(filters.get("min_score"))
    active_filter_labels = build_active_filter_labels(
        sender=sender_filter,
        subject=subject_filter,
        folder=folder_filter,
        cc=cc_filter,
        date_from=date_from_filter,
        date_to=date_to_filter,
        min_score=min_score_filter,
    )
    render_results_summary(results, active_filter_labels, sort_label)

    total_pages = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(st.session_state.get("web_page", 0), total_pages - 1))
    page_results = results[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]

    preview_chars = st.slider("Preview Length", min_value=200, max_value=4000, value=1200, step=100)
    render_results(page_results, preview_chars=preview_chars)

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

    payload = build_export_payload(
        query=st.session_state.get("web_query", ""),
        results=results,
        filters=filters,
        sort_by=sort_value,
    )
    st.download_button(
        label="Download JSON",
        data=json.dumps(payload, indent=2),
        file_name="email-search-results.json",
        mime="application/json",
    )


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
