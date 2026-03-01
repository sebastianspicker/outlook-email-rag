"""
Chunk emails for embedding.

Strategy:
- Short emails (< 500 chars): single chunk with full metadata header
- Medium emails (500-2000 chars): single chunk with metadata header
- Long emails (> 2000 chars): split into overlapping chunks, each with metadata header

Each chunk gets a metadata header so the embedding captures WHO/WHEN/WHAT context,
not just the body text. This dramatically improves retrieval quality.
"""

from dataclasses import dataclass


@dataclass
class EmailChunk:
    """A single chunk ready for embedding."""
    uid: str          # Parent email UID
    chunk_id: str     # uid__chunk_N
    text: str         # The text to embed
    metadata: dict    # Stored alongside the vector in ChromaDB


# Tuning parameters
MAX_CHUNK_CHARS = 1500
OVERLAP_CHARS = 200


def chunk_email(email_dict: dict) -> list[EmailChunk]:
    """
    Convert a parsed email dict into one or more chunks for embedding.

    Args:
        email_dict: Output of Email.to_dict()

    Returns:
        List of EmailChunk objects ready for embedding.
    """
    uid = email_dict["uid"]
    body = email_dict.get("body", "")
    header = _build_header(email_dict)

    # Metadata stored in ChromaDB (not embedded, but returned with results)
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

    # For short/medium emails, one chunk is enough
    if len(body) <= MAX_CHUNK_CHARS:
        text = f"{header}\n\n{body}" if body else header
        return [EmailChunk(
            uid=uid,
            chunk_id=f"{uid}__0",
            text=text,
            metadata={**base_metadata, "chunk_index": "0", "total_chunks": "1"},
        )]

    # For long emails, split body into overlapping chunks
    chunks = []
    body_segments = _split_text(body, MAX_CHUNK_CHARS - len(header) - 50, OVERLAP_CHARS)

    for i, segment in enumerate(body_segments):
        text = f"{header}\n\n[Part {i+1}/{len(body_segments)}]\n{segment}"
        chunks.append(EmailChunk(
            uid=uid,
            chunk_id=f"{uid}__{i}",
            text=text,
            metadata={
                **base_metadata,
                "chunk_index": str(i),
                "total_chunks": str(len(body_segments)),
            },
        ))

    return chunks


def _build_header(email_dict: dict) -> str:
    """Build a concise metadata header for embedding context."""
    parts = []
    if email_dict.get("date"):
        parts.append(f"Date: {email_dict['date']}")
    if email_dict.get("sender_name") or email_dict.get("sender_email"):
        sender = email_dict.get("sender_name", "")
        if email_dict.get("sender_email"):
            sender = f"{sender} <{email_dict['sender_email']}>" if sender else email_dict["sender_email"]
        parts.append(f"From: {sender}")
    if email_dict.get("to"):
        parts.append(f"To: {', '.join(email_dict['to'][:3])}")
    if email_dict.get("subject"):
        parts.append(f"Subject: {email_dict['subject']}")
    if email_dict.get("folder"):
        parts.append(f"Folder: {email_dict['folder']}")
    if email_dict.get("has_attachments"):
        att_names = email_dict.get("attachment_names", [])
        if att_names:
            parts.append(f"Attachments: {', '.join(att_names[:5])}")
        else:
            parts.append("Has attachments")

    return "\n".join(parts)


def _split_text(text: str, max_len: int, overlap: int) -> list[str]:
    """Split text into overlapping segments, preferring to break at paragraph/sentence boundaries."""
    if len(text) <= max_len:
        return [text]

    segments = []
    start = 0

    while start < len(text):
        end = start + max_len

        if end >= len(text):
            segments.append(text[start:])
            break

        # Try to break at paragraph boundary
        break_point = text.rfind("\n\n", start + max_len // 2, end)
        if break_point == -1:
            # Try sentence boundary
            break_point = text.rfind(". ", start + max_len // 2, end)
            if break_point != -1:
                break_point += 1  # Include the period
        if break_point == -1:
            # Try any newline
            break_point = text.rfind("\n", start + max_len // 2, end)
        if break_point == -1:
            # Hard break at max_len
            break_point = end

        segments.append(text[start:break_point])
        start = break_point - overlap

    return segments
