"""Local Streamlit UI for browsing and searching indexed emails."""

from __future__ import annotations

import csv
import io
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

        att_col, _ = st.columns([1, 4])
        with att_col:
            has_attachments = st.checkbox("Has attachments")

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
                has_att_value = True if has_attachments else None
                filters = {
                    "sender": sender or None,
                    "to": to_filter or None,
                    "subject": subject or None,
                    "folder": folder or None,
                    "cc": cc or None,
                    "has_attachments": has_att_value,
                    "date_from": valid_date_from,
                    "date_to": valid_date_to,
                    "min_score": min_score_value,
                }

                results = retriever.search_filtered(
                    query=query,
                    top_k=int(top_k),
                    sender=filters["sender"],
                    to=filters["to"],
                    subject=filters["subject"],
                    folder=filters["folder"],
                    cc=filters["cc"],
                    has_attachments=filters["has_attachments"],
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
    to_filter_val = _as_optional_str(filters.get("to"))
    subject_filter = _as_optional_str(filters.get("subject"))
    folder_filter = _as_optional_str(filters.get("folder"))
    cc_filter = _as_optional_str(filters.get("cc"))
    has_att_filter = filters.get("has_attachments")
    date_from_filter = _as_optional_str(filters.get("date_from"))
    date_to_filter = _as_optional_str(filters.get("date_to"))
    min_score_filter = _as_optional_float(filters.get("min_score"))
    active_filter_labels = build_active_filter_labels(
        sender=sender_filter,
        to=to_filter_val,
        subject=subject_filter,
        folder=folder_filter,
        cc=cc_filter,
        has_attachments=has_att_filter if isinstance(has_att_filter, bool) else None,
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
