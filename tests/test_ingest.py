import pytest

from src.ingest import main, parse_args


def test_parse_args_rejects_non_positive_batch_size():
    with pytest.raises(SystemExit):
        parse_args(["data/file.olm", "--batch-size", "0"])


def test_parse_args_rejects_non_positive_max_emails():
    with pytest.raises(SystemExit):
        parse_args(["data/file.olm", "--max-emails", "0"])


def test_main_handles_invalid_archive_path_gracefully(tmp_path, capsys):
    invalid_archive = tmp_path / "invalid.olm"
    invalid_archive.write_text("not-a-zip", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        main([str(invalid_archive), "--dry-run"])

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "Invalid OLM archive" in out


def test_main_handles_missing_archive_gracefully(capsys):
    with pytest.raises(SystemExit) as excinfo:
        main(["/tmp/does-not-exist.olm", "--dry-run"])

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "OLM file not found" in out


def test_main_handles_generic_oserror_gracefully(monkeypatch, capsys):
    import src.ingest as ingest_mod

    def _raise_oserror(*args, **kwargs):
        raise PermissionError("permission denied")

    monkeypatch.setattr(ingest_mod, "ingest", _raise_oserror)

    with pytest.raises(SystemExit) as excinfo:
        main(["data/file.olm", "--dry-run"])

    assert excinfo.value.code == 2
    out = capsys.readouterr().out
    assert "Could not read OLM archive" in out


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


def _make_mock_email(idx):
    from src.parse_olm import Email

    return Email(
        message_id=f"<msg{idx}@test.com>",
        subject=f"Subject {idx}",
        sender_name="Sender",
        sender_email="sender@test.com",
        to=["recipient@test.com"],
        cc=[],
        bcc=[],
        date=f"2024-01-0{idx}T10:00:00",
        body_text=f"Body {idx}",
        body_html="",
        folder="Inbox",
        has_attachments=False,
    )


class _MockEmbedder:
    def __init__(self, **_kw):
        self.chromadb_path = "mock"
        self.model_name = "mock"
        self._count = 0

    def count(self):
        return self._count

    def add_chunks(self, chunks, **_kw):
        self._count += len(chunks)
        return len(chunks)


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
    row = db.conn.execute(
        "SELECT subject, sender_name, sender_email, base_subject, email_type FROM emails"
    ).fetchone()
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


def test_reembed_rechunks_and_upserts(monkeypatch, tmp_path):
    """reembed() should read body text from SQLite, re-chunk, and upsert embeddings."""
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod

    # Initial ingest to populate SQLite
    emails = [_make_mock_email(i) for i in range(1, 3)]
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: emails)
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email: [{"chunk_id": f"{email.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")
    chromadb_dir = str(tmp_path / "chroma")
    ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file)

    # Track what reembed does
    upserted_chunks = []

    class _MockEmbedderForReembed:
        def __init__(self, **_kw):
            pass

        def get_existing_ids(self, refresh=False):
            return set()

        def delete_chunks_by_uid(self, uid):
            return 0

        def upsert_chunks(self, chunks, batch_size=100):
            upserted_chunks.extend(chunks)
            return len(chunks)

    # Patch EmailEmbedder used inside reembed()
    monkeypatch.setattr(
        "src.embedder.EmailEmbedder", _MockEmbedderForReembed,
    )

    result = ingest_mod.reembed(chromadb_path=chromadb_dir, sqlite_path=sqlite_file)
    assert result["reembedded"] == 2
    assert result["chunks_added"] == len(upserted_chunks)
    assert result["skipped_no_body"] == 0
    assert len(upserted_chunks) >= 2  # At least 1 chunk per email


def test_reembed_skips_emails_without_body(monkeypatch, tmp_path):
    """reembed() should skip emails with empty body text."""
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase

    emails = [_make_mock_email(1)]
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: emails)
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email: [{"chunk_id": f"{email.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")
    ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file)

    # Wipe body text to simulate missing body
    db = EmailDatabase(sqlite_file)
    db.conn.execute("UPDATE emails SET body_text = ''")
    db.conn.commit()
    db.close()

    class _MockEmbedderForReembed:
        def __init__(self, **_kw):
            pass

        def get_existing_ids(self, refresh=False):
            return set()

        def delete_chunks_by_uid(self, uid):
            return 0

        def upsert_chunks(self, chunks, batch_size=100):
            return len(chunks)

    monkeypatch.setattr("src.embedder.EmailEmbedder", _MockEmbedderForReembed)

    result = ingest_mod.reembed(sqlite_path=sqlite_file)
    assert result["reembedded"] == 0
    assert result["skipped_no_body"] == 1


def test_reembed_empty_database(monkeypatch, tmp_path):
    """reembed() should handle empty database gracefully."""
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase

    sqlite_file = str(tmp_path / "test.db")
    db = EmailDatabase(sqlite_file)
    db.close()

    result = ingest_mod.reembed(sqlite_path=sqlite_file)
    assert result["reembedded"] == 0
    assert result["total"] == 0


def test_format_ingestion_summary_includes_qol_fields():
    from src.ingest import format_ingestion_summary

    lines = format_ingestion_summary(
        {
            "emails_parsed": 10,
            "chunks_created": 20,
            "chunks_added": 18,
            "chunks_skipped": 2,
            "batches_written": 3,
            "total_in_db": 99,
            "dry_run": False,
            "elapsed_seconds": 1.5,
        }
    )

    assert "=== Ingestion Summary ===" in lines
    assert "Emails parsed: 10" in lines
    assert "Chunks created: 20" in lines
    assert "Chunks added: 18" in lines
    assert "Chunks skipped: 2" in lines
    assert "Write batches: 3" in lines
    assert "Total in DB: 99" in lines


def test_format_ingestion_summary_for_dry_run_hides_db_totals():
    from src.ingest import format_ingestion_summary

    lines = format_ingestion_summary(
        {
            "emails_parsed": 10,
            "chunks_created": 20,
            "chunks_added": 0,
            "chunks_skipped": 0,
            "batches_written": 0,
            "total_in_db": None,
            "dry_run": True,
            "elapsed_seconds": 1.5,
        }
    )

    assert "Database write disabled (dry-run)." in lines
    assert not any(line.startswith("Chunks added:") for line in lines)
    assert not any(line.startswith("Total in DB:") for line in lines)


def test_ingest_embed_images_enables_extract_attachments(monkeypatch):
    """embed_images=True should auto-enable extract_attachments."""
    import src.ingest as ingest_mod

    class _Email:
        def __init__(self, idx):
            self.idx = idx
            self.uid = f"uid-{idx}"
            self.attachment_contents = []

        def to_dict(self):
            return {"id": self.idx, "uid": self.uid}

    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **kw: [_Email(1)])
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email: [{"chunk_id": f"{email.get('uid', 'x')}-a"}],
    )

    # When embed_images=True, dry_run works without needing the embedder
    stats = ingest_mod.ingest("data/mock.olm", dry_run=True, embed_images=True)
    assert stats["extract_attachments"] is True
    assert stats["image_embeddings"] == 0


def test_ingest_embed_images_param_accepted(monkeypatch):
    """Verify embed_images param is accepted by ingest() function."""
    import src.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [])

    stats = ingest_mod.ingest("data/mock.olm", dry_run=True, embed_images=False)
    assert stats["image_embeddings"] == 0


def test_ingest_stats_include_image_embeddings(monkeypatch):
    """Verify image_embeddings key exists in ingestion stats."""
    import src.ingest as ingest_mod

    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [])

    stats = ingest_mod.ingest("data/mock.olm", dry_run=True)
    assert "image_embeddings" in stats
