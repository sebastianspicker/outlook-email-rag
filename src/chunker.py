"""
Chunk emails for embedding.

Strategy:
- Short emails (< 500 chars): single chunk with full metadata header
- Medium emails (500-2000 chars): single chunk with metadata header
- Long emails (> 2000 chars): split into overlapping chunks, each with metadata header

Each chunk gets a metadata header so the embedding captures WHO/WHEN/WHAT context,
not just the body text. This dramatically improves retrieval quality.
"""

from __future__ import annotations

from dataclasses import dataclass

from .formatting import build_email_header


@dataclass
class EmailChunk:
    """A single chunk ready for embedding."""

    uid: str
    chunk_id: str
    text: str
    metadata: dict


MAX_CHUNK_CHARS = 1500
OVERLAP_CHARS = 200


def chunk_email(email_dict: dict) -> list[EmailChunk]:
    """Convert a parsed email dict into chunks ready for embedding."""
    uid = email_dict["uid"]
    body = email_dict.get("body", "")
    header = _build_header(email_dict)

    base_metadata = {
        "uid": uid,
        "message_id": email_dict.get("message_id", ""),
        "subject": email_dict.get("subject", ""),
        "sender_name": email_dict.get("sender_name", ""),
        "sender_email": email_dict.get("sender_email", ""),
        "to": ", ".join(email_dict.get("to", [])),
        "cc": ", ".join(email_dict.get("cc", [])),
        "date": email_dict.get("date", ""),
        "folder": email_dict.get("folder", ""),
        "has_attachments": str(email_dict.get("has_attachments", False)),
    }

    if len(body) <= MAX_CHUNK_CHARS:
        text = f"{header}\n\n{body}" if body else header
        return [
            EmailChunk(
                uid=uid,
                chunk_id=f"{uid}__0",
                text=text,
                metadata={**base_metadata, "chunk_index": "0", "total_chunks": "1"},
            )
        ]

    max_body_len = max(OVERLAP_CHARS + 100, MAX_CHUNK_CHARS - len(header) - 50)
    body_segments = _split_text(body, max_body_len, OVERLAP_CHARS)

    chunks: list[EmailChunk] = []
    for i, segment in enumerate(body_segments):
        text = f"{header}\n\n[Part {i + 1}/{len(body_segments)}]\n{segment}"
        chunks.append(
            EmailChunk(
                uid=uid,
                chunk_id=f"{uid}__{i}",
                text=text,
                metadata={
                    **base_metadata,
                    "chunk_index": str(i),
                    "total_chunks": str(len(body_segments)),
                },
            )
        )

    return chunks


def _build_header(email_dict: dict) -> str:
    """Wrapper for consistent header formatting across the codebase."""
    return build_email_header(email_dict)


def _split_text(text: str, max_len: int, overlap: int) -> list[str]:
    """Split text into overlapping segments while preferring natural boundaries."""
    if len(text) <= max_len:
        return [text]

    segments: list[str] = []
    start = 0

    while start < len(text):
        end = start + max_len

        if end >= len(text):
            segments.append(text[start:])
            break

        break_point = text.rfind("\n\n", start + max_len // 2, end)
        if break_point == -1:
            break_point = text.rfind(". ", start + max_len // 2, end)
            if break_point != -1:
                break_point += 1
        if break_point == -1:
            break_point = text.rfind("\n", start + max_len // 2, end)
        if break_point == -1:
            break_point = end

        # Guarantee forward progress even when boundary lands close to the overlap window.
        if break_point <= start:
            break_point = end

        segments.append(text[start:break_point])
        start = max(start + 1, break_point - overlap)

    return segments
