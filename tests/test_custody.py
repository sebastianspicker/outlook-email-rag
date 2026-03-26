"""Tests for chain-of-custody and provenance features (Phase 1)."""

import hashlib
import os
import tempfile

import pytest

from src.email_db import EmailDatabase


@pytest.fixture()
def db():
    """Create a temporary EmailDatabase for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        database = EmailDatabase(db_path)
        yield database
        database.close()


@pytest.fixture()
def db_with_email(db):
    """Database with a sample email inserted."""
    db.conn.execute(
        """INSERT INTO emails (uid, message_id, subject, sender_name, sender_email,
           date, folder, body_text, body_html, has_attachments, attachment_count,
           priority, is_read, body_length, content_sha256)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "test-uid-1",
            "<msg1@test.com>",
            "Test Subject",
            "Alice",
            "alice@test.com",
            "2024-01-15",
            "Inbox",
            "This is the email body with important evidence text.",
            "<p>This is the email body with important evidence text.</p>",
            0,
            0,
            0,
            1,
            50,
            hashlib.sha256(b"This is the email body with important evidence text.").hexdigest(),
        ),
    )
    db.conn.execute(
        "INSERT INTO recipients(email_uid, address, display_name, type) VALUES(?,?,?,?)",
        ("test-uid-1", "bob@test.com", "Bob", "to"),
    )
    db.conn.commit()
    return db


# ── Schema v4 migration ──────────────────────────────────────


def test_schema_v4_creates_custody_chain_table(db):
    """Schema v4 should create custody_chain table."""
    row = db.conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='custody_chain'").fetchone()
    assert row is not None


def test_schema_v4_adds_olm_sha256_column(db):
    """Schema v4 should add olm_sha256 column to ingestion_runs."""
    # Insert a run to verify column exists
    run_id = db.record_ingestion_start("test.olm", olm_sha256="abc123")
    row = db.conn.execute("SELECT olm_sha256 FROM ingestion_runs WHERE id=?", (run_id,)).fetchone()
    assert row["olm_sha256"] == "abc123"


def test_schema_v4_adds_file_size_bytes_column(db):
    """Schema v4 should add file_size_bytes column to ingestion_runs."""
    run_id = db.record_ingestion_start("test.olm", file_size_bytes=12345)
    row = db.conn.execute("SELECT file_size_bytes FROM ingestion_runs WHERE id=?", (run_id,)).fetchone()
    assert row["file_size_bytes"] == 12345


def test_schema_v4_adds_custodian_column(db):
    """Schema v4 should add custodian column to ingestion_runs."""
    run_id = db.record_ingestion_start("test.olm", custodian="admin")
    row = db.conn.execute("SELECT custodian FROM ingestion_runs WHERE id=?", (run_id,)).fetchone()
    assert row["custodian"] == "admin"


def test_schema_v4_adds_content_sha256_to_emails(db):
    """Schema v4 should add content_sha256 column to emails."""
    cols = [row[1] for row in db.conn.execute("PRAGMA table_info(emails)").fetchall()]
    assert "content_sha256" in cols


def test_schema_v4_adds_content_hash_to_evidence(db):
    """Schema v4 should add content_hash column to evidence_items."""
    cols = [row[1] for row in db.conn.execute("PRAGMA table_info(evidence_items)").fetchall()]
    assert "content_hash" in cols


# ── compute_content_hash ─────────────────────────────────────


def test_compute_content_hash_deterministic():
    """Same input should always produce the same hash."""
    h1 = EmailDatabase.compute_content_hash("hello world")
    h2 = EmailDatabase.compute_content_hash("hello world")
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest


def test_compute_content_hash_different_inputs():
    """Different inputs should produce different hashes."""
    h1 = EmailDatabase.compute_content_hash("input A")
    h2 = EmailDatabase.compute_content_hash("input B")
    assert h1 != h2


# ── log_custody_event / get_custody_chain ────────────────────


def test_log_custody_event_returns_id(db):
    """log_custody_event should return an auto-increment ID."""
    eid = db.log_custody_event("test_action")
    assert isinstance(eid, int)
    assert eid >= 1


def test_log_custody_event_stores_all_fields(db):
    """All fields should be persisted correctly."""
    eid = db.log_custody_event(
        "evidence_add",
        target_type="evidence",
        target_id="42",
        details={"category": "harassment", "relevance": 5},
        content_hash="abcdef1234567890",
        actor="admin",
    )
    events = db.get_custody_chain()
    # Find the event we just created (may have others from setup)
    event = next(e for e in events if e["id"] == eid)
    assert event["action"] == "evidence_add"
    assert event["target_type"] == "evidence"
    assert event["target_id"] == "42"
    assert event["details"]["category"] == "harassment"
    assert event["content_hash"] == "abcdef1234567890"
    assert event["actor"] == "admin"


def test_get_custody_chain_filters_by_target_type(db):
    """Should filter events by target_type."""
    db.log_custody_event("add", target_type="evidence", target_id="1")
    db.log_custody_event("add", target_type="email", target_id="uid-1")

    evidence_events = db.get_custody_chain(target_type="evidence")
    email_events = db.get_custody_chain(target_type="email")

    assert all(e["target_type"] == "evidence" for e in evidence_events)
    assert all(e["target_type"] == "email" for e in email_events)


def test_get_custody_chain_filters_by_action(db):
    """Should filter events by action."""
    db.log_custody_event("evidence_add", target_type="evidence")
    db.log_custody_event("evidence_remove", target_type="evidence")

    add_events = db.get_custody_chain(action="evidence_add")
    assert all(e["action"] == "evidence_add" for e in add_events)


def test_get_custody_chain_respects_limit(db):
    """Should limit the number of returned events."""
    for i in range(10):
        db.log_custody_event("test", target_id=str(i))

    events = db.get_custody_chain(limit=3)
    assert len(events) == 3


def test_get_custody_chain_ordered_desc(db):
    """Events should be ordered newest first."""
    db.log_custody_event("first")
    db.log_custody_event("second")
    events = db.get_custody_chain()
    # Most recent first
    assert events[0]["id"] > events[-1]["id"]


# ── record_ingestion_start with custody ──────────────────────


def test_record_ingestion_start_logs_custody_event(db):
    """record_ingestion_start should auto-log a custody event."""
    run_id = db.record_ingestion_start(
        "test.olm",
        olm_sha256="deadbeef",
        file_size_bytes=9999,
        custodian="tester",
    )

    events = db.get_custody_chain(target_type="ingestion_run", target_id=str(run_id))
    assert len(events) >= 1
    event = events[0]
    assert event["action"] == "ingest_start"
    assert event["details"]["olm_sha256"] == "deadbeef"
    assert event["details"]["file_size_bytes"] == 9999
    assert event["actor"] == "tester"
    assert event["content_hash"] == "deadbeef"


def test_record_ingestion_start_default_custodian(db):
    """Default custodian should be 'system'."""
    run_id = db.record_ingestion_start("test.olm")
    row = db.conn.execute("SELECT custodian FROM ingestion_runs WHERE id=?", (run_id,)).fetchone()
    assert row["custodian"] == "system"


# ── add_evidence with custody ────────────────────────────────


def test_add_evidence_creates_custody_event(db_with_email):
    """add_evidence should log an evidence_add custody event."""
    result = db_with_email.add_evidence(
        "test-uid-1",
        "harassment",
        "important evidence",
        "test summary",
        4,
    )
    events = db_with_email.get_custody_chain(target_type="evidence", target_id=str(result["id"]))
    assert len(events) >= 1
    event = events[0]
    assert event["action"] == "evidence_add"
    assert event["details"]["category"] == "harassment"
    assert event["details"]["relevance"] == 4


def test_add_evidence_warns_on_nonstandard_category(db_with_email, caplog):
    """add_evidence should log a warning for non-standard categories."""
    import logging

    with caplog.at_level(logging.WARNING, logger="src.db_evidence"):
        db_with_email.add_evidence(
            "test-uid-1",
            "made_up_category",
            "important evidence",
            "test",
            3,
        )
    assert "Non-standard evidence category" in caplog.text
    assert "made_up_category" in caplog.text


def test_add_evidence_no_warning_for_standard_category(db_with_email, caplog):
    """add_evidence should not warn for standard categories."""
    import logging

    with caplog.at_level(logging.WARNING, logger="src.db_evidence"):
        db_with_email.add_evidence(
            "test-uid-1",
            "harassment",
            "evidence text",
            "test",
            3,
        )
    assert "Non-standard evidence category" not in caplog.text


def test_evidence_categories_match_claude_md():
    """EVIDENCE_CATEGORIES should match the canonical list from CLAUDE.md."""
    expected = {
        "bossing",
        "harassment",
        "discrimination",
        "retaliation",
        "hostile_environment",
        "micromanagement",
        "exclusion",
        "gaslighting",
        "workload",
        "general",
    }
    assert set(EmailDatabase.EVIDENCE_CATEGORIES) == expected


def test_add_evidence_computes_content_hash(db_with_email):
    """add_evidence should compute and store a content_hash."""
    result = db_with_email.add_evidence(
        "test-uid-1",
        "discrimination",
        "evidence text",
        "summary",
        3,
    )
    assert "content_hash" in result
    assert len(result["content_hash"]) == 64

    # Verify stored in DB
    row = db_with_email.conn.execute("SELECT content_hash FROM evidence_items WHERE id=?", (result["id"],)).fetchone()
    assert row["content_hash"] == result["content_hash"]


def test_add_evidence_content_hash_is_deterministic(db_with_email):
    """Same email_uid|category|key_quote should produce same hash."""
    expected = EmailDatabase.compute_content_hash("test-uid-1|harassment|evidence text")
    result = db_with_email.add_evidence(
        "test-uid-1",
        "harassment",
        "evidence text",
        "summary",
        3,
    )
    assert result["content_hash"] == expected


# ── update_evidence with custody ─────────────────────────────


def test_update_evidence_logs_custody_event(db_with_email):
    """update_evidence should log an evidence_update custody event with old values."""
    result = db_with_email.add_evidence(
        "test-uid-1",
        "harassment",
        "important evidence",
        "old summary",
        3,
    )
    eid = result["id"]

    db_with_email.update_evidence(eid, summary="new summary", relevance=5)

    events = db_with_email.get_custody_chain(target_type="evidence", target_id=str(eid), action="evidence_update")
    assert len(events) >= 1
    event = events[0]
    assert event["details"]["old_values"]["summary"] == "old summary"
    assert event["details"]["old_values"]["relevance"] == 3
    assert event["details"]["new_values"]["summary"] == "new summary"
    assert event["details"]["new_values"]["relevance"] == 5


def test_update_evidence_recomputes_content_hash(db_with_email):
    """Updating category or key_quote should recompute content_hash."""
    result = db_with_email.add_evidence(
        "test-uid-1",
        "harassment",
        "old quote",
        "summary",
        3,
    )
    old_hash = result["content_hash"]

    db_with_email.update_evidence(result["id"], key_quote="new quote")

    row = db_with_email.conn.execute("SELECT content_hash FROM evidence_items WHERE id=?", (result["id"],)).fetchone()
    assert row["content_hash"] != old_hash
    assert row["content_hash"] == EmailDatabase.compute_content_hash("test-uid-1|harassment|new quote")


# ── remove_evidence with custody ─────────────────────────────


def test_remove_evidence_logs_custody_event(db_with_email):
    """remove_evidence should log an evidence_remove custody event with snapshot."""
    result = db_with_email.add_evidence(
        "test-uid-1",
        "discrimination",
        "the evidence quote",
        "summary",
        4,
    )
    eid = result["id"]

    db_with_email.remove_evidence(eid)

    events = db_with_email.get_custody_chain(target_type="evidence", target_id=str(eid), action="evidence_remove")
    assert len(events) >= 1
    event = events[0]
    assert event["details"]["email_uid"] == "test-uid-1"
    assert event["details"]["category"] == "discrimination"
    assert event["details"]["relevance"] == 4


def test_remove_nonexistent_evidence_no_custody_event(db_with_email):
    """Removing a nonexistent evidence ID should not create a custody event."""
    initial_events = db_with_email.get_custody_chain(action="evidence_remove")
    db_with_email.remove_evidence(99999)
    after_events = db_with_email.get_custody_chain(action="evidence_remove")
    assert len(after_events) == len(initial_events)


# ── email_provenance ─────────────────────────────────────────


def test_email_provenance_returns_email_data(db_with_email):
    """email_provenance should return the email record."""
    prov = db_with_email.email_provenance("test-uid-1")
    assert "email" in prov
    assert prov["email"]["uid"] == "test-uid-1"
    assert prov["email"]["sender_email"] == "alice@test.com"


def test_email_provenance_includes_ingestion_run(db_with_email):
    """email_provenance should include the ingestion run."""
    run_id = db_with_email.record_ingestion_start("test.olm", olm_sha256="abcdef", file_size_bytes=1234)
    db_with_email.record_ingestion_complete(run_id, {"emails_parsed": 1, "emails_inserted": 1})

    prov = db_with_email.email_provenance("test-uid-1")
    assert prov["ingestion_run"] is not None
    assert prov["ingestion_run"]["olm_sha256"] == "abcdef"


def test_email_provenance_uses_ingestion_run_id(db):
    """email_provenance should return the correct run via ingestion_run_id."""
    from dataclasses import dataclass
    from dataclasses import field as dataclass_field

    @dataclass
    class FakeEmail:
        uid: str = "prov-uid-1"
        message_id: str = "<prov@test.com>"
        subject: str = "Provenance Test"
        sender_name: str = "Alice"
        sender_email: str = "alice@test.com"
        date: str = "2024-01-15"
        folder: str = "Inbox"
        email_type: str = "original"
        has_attachments: bool = False
        attachment_names: list = dataclass_field(default_factory=list)
        priority: int = 0
        is_read: bool = True
        conversation_id: str = ""
        in_reply_to: str = ""
        base_subject: str = "Provenance Test"
        clean_body: str = "Test body"
        body_html: str = ""
        to: list = dataclass_field(default_factory=list)
        cc: list = dataclass_field(default_factory=list)
        bcc: list = dataclass_field(default_factory=list)
        attachments: list = dataclass_field(default_factory=list)

    # Create two ingestion runs
    run1 = db.record_ingestion_start("first.olm", olm_sha256="aaa")
    db.record_ingestion_complete(run1, {"emails_parsed": 1, "emails_inserted": 1})

    run2 = db.record_ingestion_start("second.olm", olm_sha256="bbb")
    db.record_ingestion_complete(run2, {"emails_parsed": 1, "emails_inserted": 1})

    # Insert email with run1's ID
    db.insert_emails_batch([FakeEmail()], ingestion_run_id=run1)

    prov = db.email_provenance("prov-uid-1")
    assert prov["ingestion_run"] is not None
    # Should point to run1, not the latest (run2)
    assert prov["ingestion_run"]["id"] == run1
    assert prov["ingestion_run"]["olm_sha256"] == "aaa"


def test_email_provenance_not_found(db):
    """email_provenance should return error for missing email."""
    prov = db.email_provenance("nonexistent-uid")
    assert "error" in prov


# ── evidence_provenance ──────────────────────────────────────


def test_evidence_provenance_returns_full_chain(db_with_email):
    """evidence_provenance should return evidence + source email + custody events."""
    result = db_with_email.add_evidence(
        "test-uid-1",
        "harassment",
        "important evidence",
        "summary",
        5,
    )
    prov = db_with_email.evidence_provenance(result["id"])

    assert "evidence" in prov
    assert prov["evidence"]["category"] == "harassment"
    assert "source_email" in prov
    assert prov["source_email"]["email"]["uid"] == "test-uid-1"
    assert "custody_events" in prov
    assert len(prov["custody_events"]) >= 1


def test_evidence_provenance_not_found(db):
    """evidence_provenance should return error for missing evidence."""
    prov = db.evidence_provenance(99999)
    assert "error" in prov


# ── content_sha256 on email insert ───────────────────────────


def test_insert_emails_batch_computes_content_sha256(db):
    """insert_emails_batch should compute content_sha256 per email."""
    from dataclasses import dataclass
    from dataclasses import field as dataclass_field

    @dataclass
    class FakeEmail:
        uid: str = "fake-uid"
        message_id: str = "<fake@test.com>"
        subject: str = "Fake Subject"
        sender_name: str = "Sender"
        sender_email: str = "sender@test.com"
        date: str = "2024-01-01"
        folder: str = "Inbox"
        email_type: str = "original"
        has_attachments: bool = False
        attachment_names: list = dataclass_field(default_factory=list)
        priority: int = 0
        is_read: bool = True
        conversation_id: str = "conv-1"
        in_reply_to: str = ""
        base_subject: str = "Fake Subject"
        clean_body: str = "Hello world email content"
        body_html: str = "<p>Hello world email content</p>"
        to: list = dataclass_field(default_factory=list)
        cc: list = dataclass_field(default_factory=list)
        bcc: list = dataclass_field(default_factory=list)
        attachments: list = dataclass_field(default_factory=list)

    inserted = db.insert_emails_batch([FakeEmail()])
    assert len(inserted) == 1

    row = db.conn.execute("SELECT content_sha256 FROM emails WHERE uid='fake-uid'").fetchone()
    assert row["content_sha256"] is not None
    assert row["content_sha256"] == hashlib.sha256(b"Hello world email content").hexdigest()


# ── _hash_file_sha256 ────────────────────────────────────────


def test_hash_file_sha256():
    """_hash_file_sha256 should compute correct SHA-256 of a file."""
    from src.ingest import _hash_file_sha256

    with tempfile.NamedTemporaryFile(delete=False, suffix=".olm") as f:
        f.write(b"test file content for hashing")
        f.flush()
        path = f.name

    try:
        result = _hash_file_sha256(path)
        expected = hashlib.sha256(b"test file content for hashing").hexdigest()
        assert result == expected
    finally:
        os.unlink(path)


def test_hash_file_sha256_large_file():
    """_hash_file_sha256 should handle files larger than one read chunk."""
    from src.ingest import _hash_file_sha256

    data = b"x" * 20000  # Larger than 8192 chunk size
    with tempfile.NamedTemporaryFile(delete=False, suffix=".olm") as f:
        f.write(data)
        f.flush()
        path = f.name

    try:
        result = _hash_file_sha256(path)
        expected = hashlib.sha256(data).hexdigest()
        assert result == expected
    finally:
        os.unlink(path)
