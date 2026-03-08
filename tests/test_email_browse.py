"""Tests for email browse/paginate and full-body retrieval in EmailDatabase."""

from __future__ import annotations

from src.email_db import EmailDatabase
from src.parse_olm import Email


def _make_email(**overrides) -> Email:
    defaults = {
        "message_id": "<msg1@example.com>",
        "subject": "Hello",
        "sender_name": "Alice",
        "sender_email": "alice@example.com",
        "to": ["Bob <bob@example.com>"],
        "cc": [],
        "bcc": [],
        "date": "2024-01-15T10:30:00",
        "body_text": "Test body content",
        "body_html": "<p>Test body content</p>",
        "folder": "Inbox",
        "has_attachments": False,
    }
    defaults.update(overrides)
    return Email(**defaults)


# ── Body text storage ────────────────────────────────────────────────


def test_body_text_stored_on_insert():
    db = EmailDatabase(":memory:")
    email = _make_email(body_text="Full email body here", body_html="<p>Full email body</p>")
    db.insert_email(email)

    row = db.conn.execute("SELECT body_text, body_html FROM emails WHERE uid = ?", (email.uid,)).fetchone()
    assert row["body_text"] == "Full email body here"
    assert row["body_html"] == "<p>Full email body</p>"
    db.close()


def test_body_text_stored_on_batch_insert():
    db = EmailDatabase(":memory:")
    emails = [
        _make_email(message_id="<m1@ex.com>", body_text="Body one"),
        _make_email(message_id="<m2@ex.com>", body_text="Body two"),
    ]
    db.insert_emails_batch(emails)

    rows = db.conn.execute("SELECT body_text FROM emails ORDER BY uid").fetchall()
    texts = sorted(r["body_text"] for r in rows)
    assert "Body one" in texts
    assert "Body two" in texts
    db.close()


# ── get_email_full ───────────────────────────────────────────────────


def test_get_email_full_returns_body():
    db = EmailDatabase(":memory:")
    email = _make_email(body_text="Complete body text")
    db.insert_email(email)

    full = db.get_email_full(email.uid)
    assert full is not None
    assert full["body_text"] == "Complete body text"
    assert full["subject"] == "Hello"
    assert full["sender_email"] == "alice@example.com"
    db.close()


def test_get_email_full_includes_recipients():
    db = EmailDatabase(":memory:")
    email = _make_email(
        to=["Bob <bob@example.com>"],
        cc=["Carol <carol@example.com>"],
        bcc=["Dave <dave@example.com>"],
    )
    db.insert_email(email)

    full = db.get_email_full(email.uid)
    assert full is not None
    assert len(full["to"]) == 1
    assert "bob@example.com" in full["to"][0]
    assert len(full["cc"]) == 1
    assert "carol@example.com" in full["cc"][0]
    assert len(full["bcc"]) == 1
    assert "dave@example.com" in full["bcc"][0]
    db.close()


def test_get_email_full_missing_uid():
    db = EmailDatabase(":memory:")
    assert db.get_email_full("nonexistent") is None
    db.close()


# ── get_thread_emails ────────────────────────────────────────────────


def test_get_thread_emails_ordered_by_date():
    db = EmailDatabase(":memory:")
    db.insert_email(_make_email(
        message_id="<m1@ex.com>",
        date="2024-01-10T10:00:00",
        conversation_id="conv_A",
        body_text="First",
    ))
    db.insert_email(_make_email(
        message_id="<m2@ex.com>",
        date="2024-01-12T10:00:00",
        conversation_id="conv_A",
        body_text="Third",
    ))
    db.insert_email(_make_email(
        message_id="<m3@ex.com>",
        date="2024-01-11T10:00:00",
        conversation_id="conv_A",
        body_text="Second",
    ))

    thread = db.get_thread_emails("conv_A")
    assert len(thread) == 3
    # Sorted by date ASC
    assert thread[0]["body_text"] == "First"
    assert thread[1]["body_text"] == "Second"
    assert thread[2]["body_text"] == "Third"
    db.close()


def test_get_thread_emails_includes_recipients():
    db = EmailDatabase(":memory:")
    db.insert_email(_make_email(
        conversation_id="conv_B",
        to=["Bob <bob@example.com>"],
        cc=["Carol <carol@example.com>"],
    ))
    thread = db.get_thread_emails("conv_B")
    assert len(thread) == 1
    assert len(thread[0]["to"]) == 1
    assert len(thread[0]["cc"]) == 1
    db.close()


def test_get_thread_emails_empty_conversation_id():
    db = EmailDatabase(":memory:")
    assert db.get_thread_emails("") == []
    assert db.get_thread_emails("nonexistent_conv") == []
    db.close()


def test_get_thread_emails_only_matching_conversation():
    db = EmailDatabase(":memory:")
    db.insert_email(_make_email(message_id="<m1@ex.com>", conversation_id="conv_A"))
    db.insert_email(_make_email(message_id="<m2@ex.com>", conversation_id="conv_B"))

    thread_a = db.get_thread_emails("conv_A")
    assert len(thread_a) == 1
    db.close()


# ── list_emails_paginated ───────────────────────────────────────────


def _seed_db(db: EmailDatabase, n: int = 5) -> None:
    """Insert n emails with sequential dates and folders."""
    for i in range(n):
        db.insert_email(_make_email(
            message_id=f"<m{i}@ex.com>",
            date=f"2024-01-{10 + i:02d}T10:00:00",
            folder="Inbox" if i % 2 == 0 else "Sent",
            sender_email=f"sender{i}@example.com",
            sender_name=f"Sender {i}",
            subject=f"Email {i}",
        ))


def test_paginated_returns_correct_structure():
    db = EmailDatabase(":memory:")
    _seed_db(db, 5)

    page = db.list_emails_paginated(offset=0, limit=20)
    assert "emails" in page
    assert "total" in page
    assert "offset" in page
    assert "limit" in page
    assert page["total"] == 5
    assert len(page["emails"]) == 5
    db.close()


def test_paginated_respects_limit():
    db = EmailDatabase(":memory:")
    _seed_db(db, 10)

    page = db.list_emails_paginated(offset=0, limit=3)
    assert len(page["emails"]) == 3
    assert page["total"] == 10
    db.close()


def test_paginated_respects_offset():
    db = EmailDatabase(":memory:")
    _seed_db(db, 5)

    page1 = db.list_emails_paginated(offset=0, limit=2, sort_order="ASC")
    page2 = db.list_emails_paginated(offset=2, limit=2, sort_order="ASC")

    # Pages should not overlap
    uids1 = {e["uid"] for e in page1["emails"]}
    uids2 = {e["uid"] for e in page2["emails"]}
    assert uids1.isdisjoint(uids2)
    db.close()


def test_paginated_sort_order_desc():
    db = EmailDatabase(":memory:")
    _seed_db(db, 5)

    page = db.list_emails_paginated(sort_order="DESC")
    dates = [e["date"] for e in page["emails"]]
    assert dates == sorted(dates, reverse=True)
    db.close()


def test_paginated_sort_order_asc():
    db = EmailDatabase(":memory:")
    _seed_db(db, 5)

    page = db.list_emails_paginated(sort_order="ASC")
    dates = [e["date"] for e in page["emails"]]
    assert dates == sorted(dates)
    db.close()


def test_paginated_filter_by_folder():
    db = EmailDatabase(":memory:")
    _seed_db(db, 6)  # indices 0,2,4 → Inbox; 1,3,5 → Sent

    page = db.list_emails_paginated(folder="Inbox")
    assert page["total"] == 3
    for e in page["emails"]:
        assert e["folder"] == "Inbox"
    db.close()


def test_paginated_filter_by_sender():
    db = EmailDatabase(":memory:")
    _seed_db(db, 5)

    page = db.list_emails_paginated(sender="sender2@example.com")
    assert page["total"] == 1
    assert page["emails"][0]["sender_email"] == "sender2@example.com"
    db.close()


def test_paginated_empty_result():
    db = EmailDatabase(":memory:")
    page = db.list_emails_paginated()
    assert page["total"] == 0
    assert page["emails"] == []
    db.close()


def test_paginated_invalid_sort_by_defaults_to_date():
    db = EmailDatabase(":memory:")
    _seed_db(db, 3)

    # Invalid sort_by should silently default to "date"
    page = db.list_emails_paginated(sort_by="nonexistent_column")
    assert page["total"] == 3  # Should not crash
    db.close()


def test_paginated_includes_conversation_id():
    db = EmailDatabase(":memory:")
    db.insert_email(_make_email(conversation_id="conv_XYZ"))

    page = db.list_emails_paginated()
    assert page["emails"][0]["conversation_id"] == "conv_XYZ"
    db.close()


# ── update_body_text ────────────────────────────────────────────────


def test_update_body_text_success():
    db = EmailDatabase(":memory:")
    email = _make_email(body_text="")
    db.insert_email(email)

    ok = db.update_body_text(email.uid, "New body", "<p>New body</p>")
    assert ok is True

    full = db.get_email_full(email.uid)
    assert full["body_text"] == "New body"
    assert full["body_html"] == "<p>New body</p>"
    db.close()


def test_update_body_text_nonexistent_uid():
    db = EmailDatabase(":memory:")
    ok = db.update_body_text("no_such_uid", "body", "html")
    assert ok is False
    db.close()


# ── uids_missing_body ──────────────────────────────────────────────


def test_uids_missing_body():
    db = EmailDatabase(":memory:")
    # Insert an email with body_text set to NULL by using a direct SQL insert
    # (since _make_email always has body_text)
    db.insert_email(_make_email(message_id="<m1@ex.com>", body_text="has body"))

    # Insert one without body via direct SQL
    db.conn.execute(
        "INSERT INTO emails(uid, message_id, subject, sender_email, date, folder) "
        "VALUES('uid_no_body', '<m2@ex.com>', 'No body', 'x@x.com', '2024-01-01', 'Inbox')"
    )
    db.conn.commit()

    missing = db.uids_missing_body()
    assert "uid_no_body" in missing
    assert len(missing) == 1
    db.close()


# ── Schema migration ────────────────────────────────────────────────


def test_schema_has_body_columns():
    db = EmailDatabase(":memory:")
    cols = {
        row[1]
        for row in db.conn.execute("PRAGMA table_info(emails)").fetchall()
    }
    assert "body_text" in cols
    assert "body_html" in cols
    db.close()
