"""Formatting helpers shared across chunking and retrieval output."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def format_sender(name: str | None, email: str | None) -> str:
    """Format sender consistently for headers and output."""
    clean_name = (name or "").strip()
    clean_email = (email or "").strip()
    if clean_name and clean_email:
        return f"{clean_name} <{clean_email}>"
    if clean_name:
        return clean_name
    return clean_email


def build_email_header(email_dict: Mapping[str, Any]) -> str:
    """Build a concise metadata header for embedding context."""
    parts: list[str] = []

    date_value = email_dict.get("date")
    if date_value:
        parts.append(f"Date: {date_value}")

    sender = format_sender(email_dict.get("sender_name"), email_dict.get("sender_email"))
    if sender:
        parts.append(f"From: {sender}")

    to_values = _as_list(email_dict.get("to"))
    if to_values:
        parts.append(f"To: {', '.join(to_values[:3])}")

    subject = email_dict.get("subject")
    if subject:
        parts.append(f"Subject: {subject}")

    folder = email_dict.get("folder")
    if folder:
        parts.append(f"Folder: {folder}")

    if email_dict.get("has_attachments"):
        attachment_names = _as_list(email_dict.get("attachment_names"))
        if attachment_names:
            parts.append(f"Attachments: {', '.join(attachment_names[:5])}")
        else:
            parts.append("Has attachments")

    return "\n".join(parts)


def build_result_header(metadata: Mapping[str, Any]) -> str:
    """Build result header used in Claude context formatting."""
    parts: list[str] = []

    date_value = metadata.get("date")
    if date_value:
        parts.append(f"Date: {date_value}")

    sender = format_sender(metadata.get("sender_name"), metadata.get("sender_email"))
    if sender:
        parts.append(f"From: {sender}")

    to_value = metadata.get("to")
    if to_value:
        if isinstance(to_value, list):
            to_value = ", ".join(str(v) for v in to_value)
        parts.append(f"To: {to_value}")

    subject = metadata.get("subject")
    if subject:
        parts.append(f"Subject: {subject}")

    folder = metadata.get("folder")
    if folder:
        parts.append(f"Folder: {folder}")

    return "\n".join(parts)


def format_context_block(text: str, metadata: Mapping[str, Any], score: float) -> str:
    """Format a single result block for Claude context."""
    header = build_result_header(metadata)
    return f"---\n{header}\nRelevance: {score:.2f}\n---\n{text}\n"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value if v]
    if isinstance(value, tuple):
        return [str(v) for v in value if v]
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [str(value)]
