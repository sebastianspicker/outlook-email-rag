# ruff: noqa: F401, I001
import queue
import threading
import time

import pytest

from src.ingest import _SENTINEL, _EmbedPipeline, main, parse_args

from .helpers.ingest_fixtures import _MockEmbedder, _make_mock_email


def test_ingest_inserts_exchange_entities(monkeypatch, tmp_path):
    """Exchange-extracted entities should be inserted into entities table during ingest."""
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase
    from src.parse_olm import Email

    email = Email(
        message_id="<msg1@test.com>",
        subject="With Exchange Data",
        sender_name="Sender",
        sender_email="sender@test.com",
        to=["r@test.com"],
        cc=[],
        bcc=[],
        date="2024-01-01T10:00:00",
        body_text="Hello world",
        body_html="",
        folder="Inbox",
        has_attachments=False,
        exchange_extracted_links=[{"url": "https://intranet.company.com"}],
        exchange_extracted_emails=["contact@vendor.com"],
    )

    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [email])
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda e: [{"chunk_id": f"{e.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")
    ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file)

    db = EmailDatabase(sqlite_file)
    entities = db.conn.execute("SELECT entity_text, entity_type FROM entities ORDER BY entity_type").fetchall()
    entity_types = {row["entity_type"] for row in entities}
    assert "url" in entity_types
    assert "email" in entity_types
    db.close()


def test_reingest_analytics_backfills_missing(tmp_path):
    """reingest_analytics() should populate language/sentiment for emails missing them."""
    from src.email_db import EmailDatabase
    from src.ingest import reingest_analytics

    sqlite_file = str(tmp_path / "test.db")
    db = EmailDatabase(sqlite_file)
    from src.parse_olm import Email

    email = Email(
        message_id="<analytics1@test.com>",
        subject="Test",
        sender_name="Alice",
        sender_email="alice@test.com",
        to=["bob@test.com"],
        cc=[],
        bcc=[],
        date="2024-01-01T10:00:00",
        body_text="Thank you very much for the wonderful and excellent presentation. "
        "I really appreciate the great work done by the team.",
        body_html="",
        folder="Inbox",
        has_attachments=False,
    )
    db.insert_email(email)

    # Confirm analytics columns are NULL initially
    row = db.conn.execute("SELECT detected_language, sentiment_label FROM emails").fetchone()
    assert row["detected_language"] is None
    assert row["sentiment_label"] is None
    db.close()

    result = reingest_analytics(sqlite_path=sqlite_file)
    assert result["updated"] == 1

    db2 = EmailDatabase(sqlite_file)
    row = db2.conn.execute("SELECT detected_language, sentiment_label, sentiment_score FROM emails").fetchone()
    assert row["detected_language"] == "en"
    assert row["sentiment_label"] is not None
    db2.close()


def test_incremental_skips_existing_emails(monkeypatch, tmp_path):
    """incremental=True should skip emails already in SQLite and not re-extract entities."""
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase

    emails = [_make_mock_email(i) for i in range(1, 4)]
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: emails)
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email: [{"chunk_id": f"{email.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")

    # First ingest: all 3 emails inserted
    stats1 = ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file)
    assert stats1["sqlite_inserted"] == 3
    assert stats1["skipped_incremental"] == 0

    # Second ingest with incremental: all 3 skipped at parse-loop level
    stats2 = ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file, incremental=True)
    assert stats2["skipped_incremental"] == 3
    assert stats2["sqlite_inserted"] == 0
    assert stats2["chunks_added"] == 0

    db = EmailDatabase(sqlite_file)
    assert db.email_count() == 3
    states = db.conn.execute(
        "SELECT vector_status, attachment_status FROM email_ingest_state ORDER BY email_uid",
    ).fetchall()
    assert {row["vector_status"] for row in states} == {"completed"}
    assert {row["attachment_status"] for row in states} == {"not_requested"}
    db.close()


def test_incremental_processes_new_emails(monkeypatch, tmp_path):
    """incremental=True should process only new emails, skipping existing ones."""
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase

    first_batch = [_make_mock_email(i) for i in range(1, 3)]
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: first_batch)
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda email: [{"chunk_id": f"{email.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")

    # First ingest: 2 emails
    stats1 = ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file)
    assert stats1["sqlite_inserted"] == 2

    # Second ingest with 3 emails (2 old + 1 new), incremental mode
    mixed_batch = [_make_mock_email(i) for i in range(1, 4)]
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: mixed_batch)

    stats2 = ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file, incremental=True)
    assert stats2["skipped_incremental"] == 2
    assert stats2["sqlite_inserted"] == 1

    db = EmailDatabase(sqlite_file)
    assert db.email_count() == 3
    db.close()


def test_reingest_metadata_backfills_v7_fields(monkeypatch, tmp_path):
    """reingest_metadata should update categories, thread_topic, etc. for existing emails."""
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase
    from src.parse_olm import Email

    # First ingest: basic email without v7 metadata
    basic_email = Email(
        message_id="<msg1@test.com>",
        subject="Test",
        sender_name="Sender",
        sender_email="sender@test.com",
        to=["r@test.com"],
        cc=[],
        bcc=[],
        date="2024-01-01T10:00:00",
        body_text="Hello",
        body_html="",
        folder="Inbox",
        has_attachments=False,
    )
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [basic_email])
    monkeypatch.setattr(
        ingest_mod,
        "chunk_email",
        lambda e: [{"chunk_id": f"{e.get('uid', 'x')}-a"}],
    )
    monkeypatch.setattr(embedder_mod, "EmailEmbedder", _MockEmbedder)

    sqlite_file = str(tmp_path / "test.db")
    ingest_mod.ingest("mock.olm", dry_run=False, sqlite_path=sqlite_file)

    # Now simulate re-parse with v7 metadata
    enriched_email = Email(
        message_id="<msg1@test.com>",
        subject="Test",
        sender_name="Sender",
        sender_email="sender@test.com",
        to=["r@test.com"],
        cc=[],
        bcc=[],
        date="2024-01-01T10:00:00",
        body_text="Hello",
        body_html="",
        folder="Inbox",
        has_attachments=False,
        categories=["Important", "Project X"],
        thread_topic="Test Thread",
        is_calendar_message=True,
        exchange_extracted_emails=["vendor@example.com"],
    )
    monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [enriched_email])

    result = ingest_mod.reingest_metadata("mock.olm", sqlite_path=sqlite_file)
    assert result["updated"] == 1
    assert result["exchange_entities_inserted"] == 1

    db = EmailDatabase(sqlite_file)
    row = db.conn.execute("SELECT categories, thread_topic, is_calendar_message FROM emails").fetchone()
    assert "Important" in row["categories"]
    assert row["thread_topic"] == "Test Thread"
    assert row["is_calendar_message"] == 1

    cats = db.conn.execute("SELECT category FROM email_categories").fetchall()
    assert {r["category"] for r in cats} == {"Important", "Project X"}
    db.close()


def test_pipeline_consumer_error_does_not_deadlock():
    """When consumer thread raises, producer should not block on full queue."""
    pipeline = _EmbedPipeline.__new__(_EmbedPipeline)
    pipeline._queue = queue.Queue(maxsize=2)
    pipeline._embedder = None
    pipeline._email_db = None
    pipeline._ingestion_run_id = None
    pipeline._batch_size = 64
    pipeline._error = None
    pipeline._thread = None
    pipeline.chunks_added = 0
    pipeline.sqlite_inserted = 0
    pipeline.batches_written = 0
    pipeline.embed_seconds = 0.0
    pipeline.write_seconds = 0.0
    pipeline.sqlite_seconds = 0.0
    pipeline.entity_seconds = 0.0
    pipeline._detailed_timing = False
    pipeline.analytics_seconds = 0.0

    # Override _process_batch to always raise
    def _failing_batch(chunks, emails):
        raise RuntimeError("test error")

    pipeline._process_batch = _failing_batch
    pipeline._thread = threading.Thread(target=pipeline._run, daemon=True)
    pipeline._thread.start()

    # Submit work — consumer will crash on first batch
    pipeline._queue.put((["chunk1"], []))
    time.sleep(0.2)

    # The consumer should have drained the queue, so this put should not block
    # even though the consumer has died
    pipeline._queue.put(([" chunk2"], []))
    pipeline._queue.put(_SENTINEL)
    pipeline._thread.join(timeout=2)
    assert not pipeline._thread.is_alive(), "Consumer thread should have exited"
    assert pipeline._error is not None
