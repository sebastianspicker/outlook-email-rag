# ruff: noqa: F401, I001
import queue
import threading
import time

import pytest

from src.ingest import _SENTINEL, _EmbedPipeline, main, parse_args

from .helpers.ingest_fixtures import _MockEmbedder, _make_mock_email


def test_exchange_entities_from_email_extracts_all_types():
    """_exchange_entities_from_email should extract URLs, emails, contacts, meetings."""
    from src.ingest import _exchange_entities_from_email
    from src.parse_olm import Email

    email = Email(
        message_id="<msg@test.com>",
        subject="Test",
        sender_name="Sender",
        sender_email="sender@test.com",
        to=["r@test.com"],
        cc=[],
        bcc=[],
        date="2024-01-01T10:00:00",
        body_text="Body",
        body_html="",
        folder="Inbox",
        has_attachments=False,
        exchange_extracted_links=[{"url": "https://example.com", "text": "Example"}],
        exchange_extracted_emails=["alice@company.com"],
        exchange_extracted_contacts=["Bob Smith"],
        exchange_extracted_meetings=[{"subject": "Team Standup", "start": "2024-01-02"}],
    )

    entities = _exchange_entities_from_email(email)
    assert len(entities) == 4

    types = {e[1] for e in entities}
    assert types == {"url", "email", "person", "event"}

    # Check normalized forms are lowercased
    for text, _etype, norm in entities:
        assert norm == text.lower()


def test_exchange_entities_from_email_empty():
    """_exchange_entities_from_email returns empty list when no Exchange data."""
    from src.ingest import _exchange_entities_from_email

    email = _make_mock_email(1)
    entities = _exchange_entities_from_email(email)
    assert entities == []


def test_ingest_computes_language_and_sentiment(monkeypatch, tmp_path):
    """Ingest should auto-populate detected_language and sentiment columns."""
    import src.embedder as embedder_mod
    import src.ingest as ingest_mod
    from src.email_db import EmailDatabase
    from src.parse_olm import Email

    email = Email(
        message_id="<lang1@test.com>",
        subject="Thank you",
        sender_name="Alice",
        sender_email="alice@test.com",
        to=["bob@test.com"],
        cc=[],
        bcc=[],
        date="2024-01-01T10:00:00",
        body_text="Thank you so much for the excellent work on this project. "
        "I really appreciate your help and the team has been great.",
        body_html="",
        folder="Inbox",
        has_attachments=False,
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
    row = db.conn.execute("SELECT detected_language, sentiment_label, sentiment_score FROM emails").fetchone()
    assert row["detected_language"] == "en"
    assert row["sentiment_label"] == "positive"
    assert row["sentiment_score"] > 0
    db.close()


def test_exchange_entities_dedup_with_regex(tmp_path):
    """Exchange and regex entities with the same normalized_form and type should deduplicate."""
    from src.email_db import EmailDatabase

    db = EmailDatabase(":memory:")
    email = _make_mock_email(1)
    db.insert_email(email)

    # Simulate regex extractor inserting a URL entity
    db.insert_entities_batch(
        email.uid,
        [
            ("https://example.com", "url", "https://example.com"),
        ],
    )

    # Now simulate Exchange extractor inserting the same URL (canonical type)
    from src.ingest import _exchange_entities_from_email
    from src.parse_olm import Email

    exchange_email = Email(
        message_id="<msg1@test.com>",
        subject="Test",
        sender_name="Sender",
        sender_email="sender@test.com",
        to=["r@test.com"],
        cc=[],
        bcc=[],
        date="2024-01-01T10:00:00",
        body_text="Body",
        body_html="",
        folder="Inbox",
        has_attachments=False,
        exchange_extracted_links=[{"url": "https://example.com"}],
    )
    exchange_entities = _exchange_entities_from_email(exchange_email)
    assert exchange_entities[0][1] == "url"  # canonical type, not exchange_url

    # Insert exchange entities — ON CONFLICT should deduplicate
    db.insert_entities_batch(email.uid, exchange_entities)

    # Should be only ONE entity row for this URL
    rows = db.conn.execute("SELECT * FROM entities WHERE normalized_form = 'https://example.com'").fetchall()
    assert len(rows) == 1
    db.close()


def test_reextract_entities_backfills_provenance_for_existing_archive(tmp_path):
    from src.email_db import EmailDatabase
    from src.entity_extractor import ExtractedEntity
    from src.ingest_reingest import reextract_entities_impl

    sqlite_file = str(tmp_path / "entities.db")
    db = EmailDatabase(sqlite_file)
    email = _make_mock_email(1)
    db.insert_email(email)
    db.insert_entities_batch(
        email.uid,
        [("https://example.com", "url", "https://example.com")],
    )
    db.close()

    result = reextract_entities_impl(
        sqlite_path=sqlite_file,
        entity_extractor_fn=lambda body, sender: [ExtractedEntity("https://example.com", "url", "https://example.com")],
        extractor_key="spacy_regex",
        extraction_version="1",
        force=False,
    )

    assert result["updated"] == 1
    db = EmailDatabase(sqlite_file)
    row = db.conn.execute(
        "SELECT extractor_key, extraction_version FROM entity_mentions"
    ).fetchone()
    assert row["extractor_key"] == "spacy_regex"
    assert row["extraction_version"] == "1"
    db.close()
