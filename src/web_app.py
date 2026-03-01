"""Local Streamlit UI for browsing and searching indexed emails."""

from __future__ import annotations

import json
from datetime import date

import streamlit as st

try:
    from .retriever import EmailRetriever
except ImportError:  # pragma: no cover - allows `streamlit run src/web_app.py`
    from src.retriever import EmailRetriever

st.set_page_config(page_title="Email RAG", layout="wide")


@st.cache_resource
def get_retriever(chromadb_path: str | None):
    return EmailRetriever(chromadb_path=chromadb_path)


def render_sidebar(retriever: EmailRetriever):
    st.sidebar.header("Archive Overview")

    stats = retriever.stats()
    st.sidebar.metric("Emails", stats.get("total_emails", 0))
    st.sidebar.metric("Chunks", stats.get("total_chunks", 0))
    st.sidebar.metric("Unique Senders", stats.get("unique_senders", 0))

    date_range = stats.get("date_range", {})
    st.sidebar.caption(
        f"Date range: {date_range.get('earliest', '?')} -> {date_range.get('latest', '?')}"
    )

    with st.sidebar.expander("Top Senders", expanded=False):
        for sender in retriever.list_senders(limit=20):
            st.write(f"{sender['count']:>4}x {sender['name']} <{sender['email']}>")


def render_results(results):
    st.subheader(f"Results ({len(results)})")

    for index, result in enumerate(results, 1):
        metadata = result.metadata
        title = metadata.get("subject", "(no subject)")
        sender = metadata.get("sender_name") or metadata.get("sender_email", "?")
        date_value = str(metadata.get("date", "?"))[:10]
        folder = metadata.get("folder", "Unknown")

        with st.expander(f"{index}. {title} | {sender} | {date_value} | {result.score:.0%}"):
            st.caption(f"Folder: {folder}")
            st.text(result.text)


def main():
    st.title("Email RAG")
    st.caption("Search Outlook exports indexed in local ChromaDB.")

    chromadb_path = st.sidebar.text_input("ChromaDB Path", value="") or None
    retriever = get_retriever(chromadb_path)
    render_sidebar(retriever)

    col1, col2, col3 = st.columns([6, 2, 2])
    with col1:
        query = st.text_input("Query", placeholder="Find contract renewal emails from legal")
    with col2:
        top_k = st.number_input("Top K", min_value=1, max_value=50, value=10)
    with col3:
        sender = st.text_input("Sender (optional)")

    dcol1, dcol2 = st.columns(2)
    with dcol1:
        date_from = st.text_input("Date From (YYYY-MM-DD)")
    with dcol2:
        date_to = st.text_input("Date To (YYYY-MM-DD)")

    if st.button("Search", type="primary"):
        if not query.strip():
            st.warning("Please enter a query.")
            return

        valid_date_from = _validate_iso_date(date_from, "Date From")
        valid_date_to = _validate_iso_date(date_to, "Date To")
        if valid_date_from is None and date_from:
            return
        if valid_date_to is None and date_to:
            return
        if valid_date_from and valid_date_to and valid_date_from > valid_date_to:
            st.error("Date From cannot be later than Date To.")
            return

        results = retriever.search_filtered(
            query=query,
            top_k=int(top_k),
            sender=sender or None,
            date_from=valid_date_from,
            date_to=valid_date_to,
        )

        if not results:
            st.info("No matching emails found. Try refining filters.")
            return

        render_results(results)

        payload = retriever.serialize_results(query, results)
        payload["filters"] = {
            "sender": sender or None,
            "date_from": valid_date_from,
            "date_to": valid_date_to,
        }

        st.download_button(
            label="Download JSON",
            data=json.dumps(payload, indent=2),
            file_name="email-search-results.json",
            mime="application/json",
        )


def _validate_iso_date(value: str, label: str) -> str | None:
    clean_value = value.strip()
    if not clean_value:
        return None

    try:
        date.fromisoformat(clean_value)
    except ValueError:
        st.error(f"{label} must use YYYY-MM-DD format.")
        return None
    return clean_value


if __name__ == "__main__":
    main()
