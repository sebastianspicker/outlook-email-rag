import threading

from src.chunker import _split_text, chunk_attachment, chunk_email, strip_quoted_content, strip_signature


def test_long_header_does_not_break_chunking():
    email = {
        "uid": "123",
        "message_id": "m",
        "subject": "Subject " + ("x" * 2000),
        "sender_name": "Sender",
        "sender_email": "sender@example.com",
        "to": ["to@example.com"],
        "cc": [],
        "date": "2023-01-01",
        "body": "Body paragraph. " * 200,
        "folder": "Inbox",
        "has_attachments": False,
    }

    chunks = chunk_email(email)
    assert len(chunks) >= 1
    assert all(chunk.text for chunk in chunks)


def test_splitter_makes_progress_for_boundary_breaks():
    # Boundary at half-window can cause start pointer stagnation if progress isn't guarded.
    text = ("x" * 150) + "\n\n" + ("y" * 600)
    result = {"chunks": None}

    def _run() -> None:
        result["chunks"] = _split_text(text, max_len=300, overlap=200)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(0.5)

    assert not worker.is_alive()
    assert result["chunks"] is not None
    assert len(result["chunks"]) >= 2


def test_short_email_stays_single_chunk():
    email = {
        "uid": "1",
        "message_id": "m1",
        "subject": "Short",
        "sender_name": "S",
        "sender_email": "s@example.com",
        "to": [],
        "cc": [],
        "date": "2023-01-01",
        "body": "tiny body",
        "folder": "Inbox",
        "has_attachments": False,
    }

    chunks = chunk_email(email)
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "1__0"


# ── Phase 2: Quoted-text stripping ──────────────────────────────


def test_strip_quoted_original_message_separator():
    body = "My reply here.\n\n----- Original Message -----\nFrom: Alice\nSubject: Test\n\nOriginal body text."
    original, count = strip_quoted_content(body, "reply")
    assert original == "My reply here."
    assert count > 0


def test_strip_quoted_urspruengliche_nachricht():
    body = "Meine Antwort.\n\n--- Ursprüngliche Nachricht ---\nVon: Alice\nBetreff: Test\n\nOriginal text."
    original, count = strip_quoted_content(body, "reply")
    assert original == "Meine Antwort."
    assert count > 0


def test_strip_quoted_on_wrote_pattern():
    body = "I agree.\n\nOn Mon, Jan 1, 2025 at 10:00 AM Alice wrote:\n> Original message text\n> More text"
    original, count = strip_quoted_content(body, "reply")
    assert original == "I agree."
    assert count > 0


def test_strip_quoted_am_schrieb_pattern():
    body = "Ja, gerne.\n\nAm 01.01.2025 um 10:00 schrieb Alice:\n> Original text"
    original, count = strip_quoted_content(body, "reply")
    assert original == "Ja, gerne."
    assert count > 0


def test_strip_quoted_angle_bracket_blocks():
    body = "My reply.\n\n> Line 1\n> Line 2\n> Line 3\n> Line 4"
    original, count = strip_quoted_content(body, "reply")
    assert original == "My reply."
    assert count >= 3


def test_strip_quoted_skipped_for_originals():
    body = "Some text\n\n----- Original Message -----\nQuoted"
    original, count = strip_quoted_content(body, "original")
    assert original == body
    assert count == 0


def test_strip_quoted_empty_body():
    original, count = strip_quoted_content("", "reply")
    assert original == ""
    assert count == 0


# ── Phase 2: Header-once chunking ──────────────────────────────


def test_multi_chunk_header_once():
    """Only chunk 0 gets the full header; continuation chunks get minimal reference."""
    email = {
        "uid": "hdr1",
        "message_id": "m1",
        "subject": "Important Discussion",
        "sender_name": "Alice",
        "sender_email": "alice@example.com",
        "to": ["bob@example.com"],
        "cc": [],
        "date": "2025-01-01",
        "body": "Paragraph text. " * 300,  # Long enough to split
        "folder": "Inbox",
        "has_attachments": False,
        "email_type": "original",
    }

    chunks = chunk_email(email)
    assert len(chunks) >= 2

    # Chunk 0 should have full header (From, Date, etc.)
    assert "From:" in chunks[0].text
    assert "Date:" in chunks[0].text
    assert "Subject:" in chunks[0].text

    # Chunk 1+ should have a compact context header, not the full multi-line header
    assert "Date:" not in chunks[1].text or "Date: 2025-01-01" in chunks[1].text
    assert "[Important Discussion - Part 2/" in chunks[1].text
    # Context header is a single bracketed line, not the full "From: ... \nDate: ..." block
    assert "From: Alice <alice@example.com> | Date:" in chunks[1].text


# ── Phase 2: New metadata fields ────────────────────────────────


def test_chunk_metadata_includes_new_fields():
    email = {
        "uid": "meta1",
        "message_id": "m1",
        "subject": "RE: Test",
        "sender_name": "Alice",
        "sender_email": "alice@example.com",
        "to": ["bob@example.com"],
        "cc": ["carol@example.com"],
        "date": "2025-01-01",
        "body": "short body",
        "folder": "Inbox",
        "has_attachments": True,
        "attachment_names": ["report.pdf"],
        "conversation_id": "conv-123",
        "in_reply_to": "msg-456",
        "email_type": "reply",
        "base_subject": "Test",
        "priority": 2,
    }

    chunks = chunk_email(email)
    meta = chunks[0].metadata
    assert meta["conversation_id"] == "conv-123"
    assert meta["in_reply_to"] == "msg-456"
    assert meta["email_type"] == "reply"
    assert meta["base_subject"] == "Test"
    assert meta["priority"] == "2"
    assert meta["attachment_names"] == "report.pdf"


# ── Phase 2: CC in embedding header ────────────────────────────


def test_cc_in_embedding_header():
    """CC recipients appear in the embedding text for semantic searchability."""
    email = {
        "uid": "cc1",
        "message_id": "m1",
        "subject": "CC Test",
        "sender_name": "Alice",
        "sender_email": "alice@example.com",
        "to": ["bob@example.com"],
        "cc": ["carol@example.com", "dave@example.com"],
        "date": "2025-01-01",
        "body": "Check this out",
        "folder": "Inbox",
        "has_attachments": False,
    }

    chunks = chunk_email(email)
    assert "CC: carol@example.com, dave@example.com" in chunks[0].text


# ── Phase 2: Quoted text stripped in chunks ─────────────────────


def test_chunk_email_strips_quoted_in_reply():
    email = {
        "uid": "q1",
        "message_id": "m1",
        "subject": "RE: Question",
        "sender_name": "Bob",
        "sender_email": "bob@example.com",
        "to": ["alice@example.com"],
        "cc": [],
        "date": "2025-01-01",
        "body": "Yes, I agree.\n\n----- Original Message -----\nFrom: Alice\n\nDo you agree?",
        "folder": "Inbox",
        "has_attachments": False,
        "email_type": "reply",
    }

    chunks = chunk_email(email)
    assert len(chunks) == 1
    assert "Yes, I agree" in chunks[0].text
    assert "Do you agree" not in chunks[0].text
    assert "[Quoted:" in chunks[0].text


# ── Signature detection ──────────────────────────────────────


def test_strip_signature_rfc_separator():
    body = "Hello, world!\n\n-- \nJohn Doe\njohn@example.com"
    stripped, had_sig = strip_signature(body)
    assert had_sig is True
    assert "Hello, world!" in stripped
    assert "John Doe" not in stripped


def test_strip_signature_double_dash():
    body = "Hello!\n\n--\nJohn Doe\nCompany Inc."
    stripped, had_sig = strip_signature(body)
    assert had_sig is True
    assert stripped == "Hello!"


def test_strip_signature_sent_from():
    body = "See you tomorrow.\n\nSent from my iPhone"
    stripped, had_sig = strip_signature(body)
    assert had_sig is True
    assert "See you tomorrow" in stripped
    assert "iPhone" not in stripped


def test_strip_signature_sent_from_outlook():
    body = "Done.\n\nSent from my Outlook for Android"
    stripped, had_sig = strip_signature(body)
    assert had_sig is True
    assert stripped == "Done."


def test_strip_signature_closing_phrase():
    body = "I'll handle it.\n\nBest regards,\nAlice Smith\nManager"
    stripped, had_sig = strip_signature(body)
    assert had_sig is True
    assert "I'll handle it" in stripped
    assert "Alice Smith" not in stripped


def test_strip_signature_closing_german():
    body = "Alles erledigt.\n\nMit freundlichen Grüßen,\nHans Müller"
    stripped, had_sig = strip_signature(body)
    assert had_sig is True
    assert "Alles erledigt" in stripped


def test_strip_signature_no_signature():
    body = "Just a regular email without a signature."
    stripped, had_sig = strip_signature(body)
    assert had_sig is False
    assert stripped == body


def test_strip_signature_empty():
    stripped, had_sig = strip_signature("")
    assert had_sig is False
    assert stripped == ""


def test_chunk_email_strips_signature():
    email = {
        "uid": "sig1",
        "message_id": "m1",
        "subject": "Test",
        "sender_name": "Bob",
        "sender_email": "bob@example.com",
        "to": ["alice@example.com"],
        "cc": [],
        "date": "2025-01-01",
        "body": "Important info here.\n\n-- \nBob\nbob@example.com",
        "folder": "Inbox",
        "has_attachments": False,
        "email_type": "original",
    }
    chunks = chunk_email(email)
    assert "[Signature stripped]" in chunks[0].text
    # Signature "Bob\nbob@example.com" stripped from body, but "From: Bob <bob@example.com>" stays in header
    assert "Important info here" in chunks[0].text
    assert chunks[0].metadata["has_signature"] == "True"


def test_continuation_chunks_have_context_header():
    """Continuation chunks (2nd, 3rd, etc.) should include sender/date/subject context."""
    email = {
        "uid": "ctx1",
        "message_id": "m1",
        "subject": "Budget Review",
        "sender_name": "Alice",
        "sender_email": "alice@example.com",
        "to": ["bob@example.com"],
        "cc": [],
        "date": "2025-03-01",
        "body": "Paragraph text. " * 300,  # Long enough to split
        "folder": "Inbox",
        "has_attachments": False,
        "email_type": "original",
    }

    chunks = chunk_email(email)
    assert len(chunks) >= 2

    # Continuation chunk should have context header
    text = chunks[1].text
    assert "From: Alice <alice@example.com>" in text
    assert "Date: 2025-03-01" in text
    assert "Subject: Budget Review" in text
    assert "[Budget Review - Part 2/" in text


def test_continuation_chunks_context_header_partial_fields():
    """Context header gracefully handles missing fields."""
    email = {
        "uid": "ctx2",
        "message_id": "m2",
        "subject": "Test",
        "sender_name": "",
        "sender_email": "anon@example.com",
        "to": [],
        "cc": [],
        "date": "",
        "body": "Word " * 500,
        "folder": "Inbox",
        "has_attachments": False,
    }

    chunks = chunk_email(email)
    assert len(chunks) >= 2

    text = chunks[1].text
    # Should have sender email without name
    assert "From: anon@example.com" in text
    # No date since empty
    assert "Date:" not in text


def test_chunk_email_no_signature_flag():
    email = {
        "uid": "nosig1",
        "message_id": "m2",
        "subject": "Test",
        "sender_name": "Alice",
        "sender_email": "alice@example.com",
        "to": ["bob@example.com"],
        "cc": [],
        "date": "2025-01-01",
        "body": "Just normal text without a sig.",
        "folder": "Inbox",
        "has_attachments": False,
        "email_type": "original",
    }
    chunks = chunk_email(email)
    assert "[Signature stripped]" not in chunks[0].text
    assert chunks[0].metadata["has_signature"] == "False"


# ── Attachment chunking ───────────────────────────────────────


def test_chunk_attachment_short():
    parent_meta = {"uid": "e1", "subject": "Report", "date": "2025-01-01"}
    chunks = chunk_attachment("e1", "report.pdf", "Short content.", parent_meta)
    assert len(chunks) == 1
    assert "[Attachment: report.pdf" in chunks[0].text
    assert chunks[0].metadata["is_attachment"] == "True"
    assert chunks[0].metadata["attachment_filename"] == "report.pdf"
    assert chunks[0].metadata["parent_uid"] == "e1"
    assert "att_" in chunks[0].chunk_id


def test_chunk_attachment_long():
    parent_meta = {"uid": "e2", "subject": "Big doc", "date": "2025-06-01"}
    long_text = "Word " * 500  # Well over MAX_CHUNK_CHARS
    chunks = chunk_attachment("e2", "big.txt", long_text, parent_meta)
    assert len(chunks) >= 2
    assert "[Attachment: big.txt" in chunks[0].text
    assert "Part 2/" in chunks[1].text


def test_chunk_attachment_empty():
    chunks = chunk_attachment("e3", "empty.txt", "", {})
    assert chunks == []
    chunks = chunk_attachment("e3", "spaces.txt", "   ", {})
    assert chunks == []
