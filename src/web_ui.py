"""Pure helper utilities for Streamlit web UI behavior."""

from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any, Iterable


def build_active_filter_labels(
    sender: str | None,
    subject: str | None,
    folder: str | None,
    date_from: str | None,
    date_to: str | None,
    min_score: float | None,
    cc: str | None = None,
) -> list[str]:
    labels: list[str] = []

    sender_value = _normalize_optional_text(sender)
    subject_value = _normalize_optional_text(subject)
    folder_value = _normalize_optional_text(folder)
    cc_value = _normalize_optional_text(cc)

    if sender_value:
        labels.append(f"Sender: {sender_value}")
    if subject_value:
        labels.append(f"Subject: {subject_value}")
    if folder_value:
        labels.append(f"Folder: {folder_value}")
    if cc_value:
        labels.append(f"CC: {cc_value}")
    if date_from:
        labels.append(f"From: {date_from}")
    if date_to:
        labels.append(f"To: {date_to}")
    if min_score is not None:
        labels.append(f"Min score: {min_score:.2f}")

    return labels


def sort_search_results(results: Iterable[Any], sort_by: str) -> list[Any]:
    items = list(results)
    if sort_by == "date_desc":
        return sorted(items, key=_date_key, reverse=True)
    if sort_by == "date_asc":
        return sorted(items, key=_date_key)
    if sort_by == "sender_asc":
        return sorted(items, key=_sender_key)
    return sorted(items, key=lambda item: float(getattr(item, "score", 0.0)), reverse=True)


def build_filter_chip_html(labels: list[str]) -> str:
    """Render safe chip HTML for active filter labels."""
    return "".join(f"<span class='filter-chip'>{escape(label)}</span>" for label in labels)


def build_export_payload(
    query: str,
    results: Iterable[Any],
    filters: dict[str, Any],
    sort_by: str,
    generated_at: str | None = None,
) -> dict[str, Any]:
    serialized_results = [_serialize_result(result) for result in results]
    timestamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    return {
        "query": query,
        "count": len(serialized_results),
        "results": serialized_results,
        "filters": filters,
        "sort_by": sort_by,
        "generated_at": timestamp,
    }


def _serialize_result(result: Any) -> dict[str, Any]:
    if hasattr(result, "to_dict"):
        value = result.to_dict()
        if isinstance(value, dict):
            return value

    return {
        "chunk_id": str(getattr(result, "chunk_id", "")),
        "score": float(getattr(result, "score", 0.0)),
        "metadata": dict(getattr(result, "metadata", {})),
        "text": str(getattr(result, "text", "")),
    }


def _date_key(result: Any) -> str:
    metadata = getattr(result, "metadata", {}) or {}
    return str(metadata.get("date", ""))[:10]


def _sender_key(result: Any) -> str:
    metadata = getattr(result, "metadata", {}) or {}
    sender_name = str(metadata.get("sender_name", "")).strip()
    sender_email = str(metadata.get("sender_email", "")).strip()
    return (sender_name or sender_email).lower()


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
