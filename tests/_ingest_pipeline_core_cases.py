# ruff: noqa: F401, I001
import queue
import threading
import time

import pytest

from src.ingest import _SENTINEL, _EmbedPipeline, main, parse_args

from .helpers.ingest_fixtures import _MockEmbedder, _make_mock_email


def test_ingest_dry_run_reports_qol_stats(monkeypatch):
    import src.ingest as ingest_mod

    class _Email:
        def __init__(self, idx):
            self.idx = idx

        def to_dict(self):
            return {"id": self.idx}

    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [_Email(1), _Email(2), _Email(3)])
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email: [{"chunk_id": f"{email['id']}-a"}, {"chunk_id": f"{email['id']}-b"}],
    )

    stats = ingest_mod.ingest("data/mock.olm", dry_run=True, batch_size=2)

    assert stats["emails_parsed"] == 3
    assert stats["chunks_created"] == 6
    assert stats["chunks_added"] == 0
    assert stats["chunks_skipped"] == 0
    assert stats["batches_written"] == 0


def test_ingest_populates_sqlite(monkeypatch, tmp_path):
    import src.ingest as ingest_mod

    emails = [_make_mock_email(i) for i in range(1, 4)]
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: emails)
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email: [{"chunk_id": f"{email.get('uid', 'x')}-a"}],
    )

    import src.embedder as embedder_mod

    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")
    stats = ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file)

    assert stats["sqlite_inserted"] == 3

    from src.email_db import EmailDatabase

    db = EmailDatabase(sqlite_file)
    assert db.email_count() == 3
    db.close()


def test_ingest_persists_attachment_evidence_metadata(monkeypatch, tmp_path):
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase

    email = _make_mock_email(1)
    email.has_attachments = True
    email.attachment_names = ["notes.txt"]
    email.attachments = [
        {
            "name": "notes.txt",
            "mime_type": "text/plain",
            "size": 18,
            "content_id": "",
            "is_inline": False,
        }
    ]
    email.attachment_contents = [("notes.txt", b"hello from attachment")]

    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [email])
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email_dict: [{"chunk_id": f"{email_dict.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr("src.attachment_extractor.image_ocr_available", lambda: False)
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")
    stats = ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file, extract_attachments=True)

    assert stats["sqlite_inserted"] == 1

    db = EmailDatabase(sqlite_file)
    attachments = db.attachments_for_email(email.uid)
    assert len(attachments) == 1
    assert attachments[0]["extraction_state"] == "text_extracted"
    assert attachments[0]["evidence_strength"] == "strong_text"
    assert attachments[0]["ocr_used"] == 0
    assert attachments[0]["failure_reason"] in (None, "")
    assert attachments[0]["text_preview"] == "hello from attachment"
    assert attachments[0]["extracted_text"] == "hello from attachment"
    assert attachments[0]["text_source_path"] == f"attachment://{email.uid}/0/notes.txt"
    assert attachments[0]["text_locator"] == {
        "kind": "mailbox_attachment",
        "email_uid": email.uid,
        "attachment_index": 0,
        "filename": "notes.txt",
        "extraction_state": "text_extracted",
    }
    db.close()


def test_ingest_zero_chunk_email_marks_ledger_completed(monkeypatch, tmp_path):
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase

    email = _make_mock_email(1)

    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [email])
    monkeypatch.setattr(ingest_mod, "chunk_email", lambda _email: [])
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")
    stats = ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file)

    assert stats["sqlite_inserted"] == 1

    db = EmailDatabase(sqlite_file)
    row = db.conn.execute(
        "SELECT vector_status, vector_chunk_count, attachment_status, image_status FROM email_ingest_state WHERE email_uid = ?",
        (email.uid,),
    ).fetchone()
    assert row is not None
    assert row["vector_status"] == "completed"
    assert row["vector_chunk_count"] == 0
    assert row["attachment_status"] == "not_requested"
    assert row["image_status"] == "not_requested"
    db.close()


def test_ingest_binary_only_attachment_stays_degraded_in_ledger(monkeypatch, tmp_path):
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase

    email = _make_mock_email(1)
    email.has_attachments = True
    email.attachment_names = ["photo.png"]
    email.attachments = [
        {
            "name": "photo.png",
            "mime_type": "image/png",
            "size": 128,
            "content_id": "",
            "is_inline": False,
        }
    ]
    email.attachment_contents = [("photo.png", b"fake-image")]

    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [email])
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email_dict: [{"chunk_id": f"{email_dict.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr("src.attachment_extractor.image_ocr_available", lambda: False)
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")
    stats = ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file, extract_attachments=True)

    assert stats["sqlite_inserted"] == 1

    db = EmailDatabase(sqlite_file)
    row = db.conn.execute(
        "SELECT vector_status, attachment_status FROM email_ingest_state WHERE email_uid = ?",
        (email.uid,),
    ).fetchone()
    assert row is not None
    assert row["vector_status"] == "completed"
    assert row["attachment_status"] == "degraded"
    attachment = db.attachments_for_email(email.uid)[0]
    assert attachment["extraction_state"] == "binary_only"
    assert attachment["failure_reason"] == "no_text_extracted_ocr_not_available"
    db.close()


def test_ingest_image_attachment_uses_ocr_when_available(monkeypatch, tmp_path):
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase

    email = _make_mock_email(1)
    email.has_attachments = True
    email.attachment_names = ["photo.png"]
    email.attachments = [
        {
            "name": "photo.png",
            "mime_type": "image/png",
            "size": 128,
            "content_id": "",
            "is_inline": False,
        }
    ]
    email.attachment_contents = [("photo.png", b"fake-image")]

    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [email])
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email_dict: [{"chunk_id": f"{email_dict.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr("src.attachment_extractor.extract_image_text_ocr", lambda filename, content: "Recovered screenshot text")
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")
    ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file, extract_attachments=True)

    db = EmailDatabase(sqlite_file)
    row = db.conn.execute(
        "SELECT attachment_status FROM email_ingest_state WHERE email_uid = ?",
        (email.uid,),
    ).fetchone()
    attachment = db.attachments_for_email(email.uid)[0]
    assert row["attachment_status"] == "completed"
    assert attachment["extraction_state"] == "ocr_text_extracted"
    assert attachment["evidence_strength"] == "strong_text"
    assert attachment["ocr_used"] == 1
    assert attachment["text_preview"] == "Recovered screenshot text"
    db.close()


def test_reprocess_degraded_attachments_recovers_image_text(tmp_path, monkeypatch):
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase

    def _make_image_email():
        email = _make_mock_email(1)
        email.has_attachments = True
        email.attachment_names = ["photo.png"]
        email.attachments = [
            {
                "name": "photo.png",
                "mime_type": "image/png",
                "size": 128,
                "content_id": "",
                "is_inline": False,
            }
        ]
        email.attachment_contents = [("photo.png", b"fake-image")]
        return email

    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email_dict: [{"chunk_id": f"{email_dict.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    email_uid = _make_image_email().uid
    sqlite_file = str(tmp_path / "test.db")
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [_make_image_email()])
    monkeypatch.setattr("src.attachment_extractor.image_ocr_available", lambda: False)
    monkeypatch.setattr("src.attachment_extractor.extract_image_text_ocr", lambda filename, content: None)
    ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file, extract_attachments=True)

    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [_make_image_email()])
    monkeypatch.setattr("src.attachment_extractor.image_ocr_available", lambda: True)
    monkeypatch.setattr(
        "src.attachment_extractor.extract_image_text_ocr",
        lambda filename, content: "Recovered screenshot text",
    )
    result = ingest_mod.reprocess_degraded_attachments(
        "mock.olm",
        sqlite_path=sqlite_file,
        batch_size=10,
    )

    assert result["updated"] == 1
    assert result["ocr_recovered"] == 1
    db = EmailDatabase(sqlite_file)
    row = db.conn.execute(
        "SELECT attachment_status FROM email_ingest_state WHERE email_uid = ?",
        (email_uid,),
    ).fetchone()
    attachment = db.attachments_for_email(email_uid)[0]
    assert row["attachment_status"] == "completed"
    assert attachment["extraction_state"] == "ocr_text_extracted"
    assert attachment["ocr_used"] == 1
    db.close()


def test_ingest_dry_run_skips_sqlite(monkeypatch, tmp_path):
    import src.ingest as ingest_mod

    emails = [_make_mock_email(1)]
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: emails)
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email: [{"chunk_id": "x"}],
    )

    sqlite_file = str(tmp_path / "test.db")
    stats = ingest_mod.ingest("mock.olm", dry_run=True, sqlite_path=sqlite_file)

    assert stats["sqlite_inserted"] == 0
    import os

    assert not os.path.exists(sqlite_file)


def test_reingest_is_idempotent(monkeypatch, tmp_path):
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod

    emails = [_make_mock_email(i) for i in range(1, 3)]
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: emails)
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email: [{"chunk_id": f"{email.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")
    stats1 = ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file)
    assert stats1["sqlite_inserted"] == 2

    stats2 = ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file)
    assert stats2["sqlite_inserted"] == 0

    from src.email_db import EmailDatabase

    db = EmailDatabase(sqlite_file)
    assert db.email_count() == 2
    db.close()


def test_reingest_force_updates_headers(monkeypatch, tmp_path):
    """--reingest-bodies --force should update subject, sender_name, sender_email."""
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase
    from src.parse_olm import Email

    # First ingest: store emails with MIME-encoded subject and sender name.
    encoded_emails = [
        Email(
            message_id="<msg1@test.com>",
            subject="=?iso-8859-1?Q?Caf=E9?=",
            sender_name="=?utf-8?B?TMO8ZGVy?=",
            sender_email="old@test.com",
            to=["r@test.com"],
            cc=[],
            bcc=[],
            date="2024-01-01T10:00:00",
            body_text="Old body",
            body_html="",
            folder="Inbox",
            has_attachments=False,
        )
    ]
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: encoded_emails)
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email: [{"chunk_id": f"{email.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")
    ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file)

    # Verify encoded values were stored as-is (simulating old parser without decode).
    db = EmailDatabase(sqlite_file)
    row = db.conn.execute("SELECT subject, sender_name, sender_email FROM emails").fetchone()
    assert row["subject"] == "=?iso-8859-1?Q?Caf=E9?="
    db.close()

    # Now simulate re-parse with decoded values (as the fixed parser would produce).
    decoded_emails = [
        Email(
            message_id="<msg1@test.com>",
            subject="Café",
            sender_name="Lüder",
            sender_email="new@test.com",
            to=["r@test.com"],
            cc=[],
            bcc=[],
            date="2024-01-01T10:00:00",
            body_text="New body",
            body_html="",
            folder="Inbox",
            has_attachments=False,
        )
    ]
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: decoded_emails)

    result = ingest_mod.reingest_bodies("mock.olm", sqlite_path=sqlite_file, force=True)
    assert result["updated"] == 1

    db = EmailDatabase(sqlite_file)
    row = db.conn.execute("SELECT subject, sender_name, sender_email, base_subject, email_type FROM emails").fetchone()
    assert row["subject"] == "Café"
    assert row["sender_name"] == "Lüder"
    assert row["sender_email"] == "new@test.com"
    assert row["base_subject"] == "Café"
    assert row["email_type"] == "original"
    db.close()


def test_reingest_no_force_skips_headers(monkeypatch, tmp_path):
    """Without --force, reingest should NOT update headers (only missing bodies)."""
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase
    from src.parse_olm import Email

    emails = [
        Email(
            message_id="<msg1@test.com>",
            subject="=?utf-8?Q?encoded?=",
            sender_name="Old Name",
            sender_email="old@test.com",
            to=["r@test.com"],
            cc=[],
            bcc=[],
            date="2024-01-01T10:00:00",
            body_text="Body text",
            body_html="",
            folder="Inbox",
            has_attachments=False,
        )
    ]
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: emails)
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email: [{"chunk_id": f"{email.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")
    ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file)

    # Non-force reingest: all bodies present → nothing to do, headers untouched.
    decoded_emails = [
        Email(
            message_id="<msg1@test.com>",
            subject="decoded",
            sender_name="New Name",
            sender_email="new@test.com",
            to=["r@test.com"],
            cc=[],
            bcc=[],
            date="2024-01-01T10:00:00",
            body_text="New body",
            body_html="",
            folder="Inbox",
            has_attachments=False,
        )
    ]
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: decoded_emails)

    result = ingest_mod.reingest_bodies("mock.olm", sqlite_path=sqlite_file, force=False)
    assert result["updated"] == 0  # nothing missing

    db = EmailDatabase(sqlite_file)
    row = db.conn.execute("SELECT subject, sender_name FROM emails").fetchone()
    assert row["subject"] == "=?utf-8?Q?encoded?="  # unchanged
    assert row["sender_name"] == "Old Name"  # unchanged
    db.close()
