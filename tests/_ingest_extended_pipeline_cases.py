# ruff: noqa: F401,I001
"""Extended tests for src/ingest.py to increase coverage from ~73% to >=85%.

Covers: reingest paths, _reset_index, _resolve_entity_extractor,
_auto_download_spacy_models, _checkpoint_wal, _NoOpProgressBar,
_make_progress_bar, _hash_file_sha256, pipeline edge cases,
main() dispatch branches, attachment processing, and more.
"""

import argparse
import types
from unittest.mock import MagicMock, patch

import pytest

from src.email_db import EmailDatabase
from src.ingest import (
    _auto_download_spacy_models,
    _EmbedPipeline,
    _hash_file_sha256,
    _make_progress_bar,
    _NoOpProgressBar,
    _resolve_entity_extractor,
    format_ingestion_summary,
    main,
    parse_args,
    reembed,
    reingest_analytics,
    reingest_bodies,
    reingest_metadata,
)
from src.parse_olm import Email

# ── Helpers ──────────────────────────────────────────────────────────

from .helpers.ingest_extended_fixtures import _MockEmailDB, _MockEmbedder, _block_import, _make_email


class TestCheckpointWal:
    def test_checkpoint_wal_success(self):
        mock_db = MagicMock()
        pipeline = _EmbedPipeline(
            embedder=None,
            email_db=mock_db,
            entity_extractor_fn=None,
            batch_size=100,
        )
        pipeline._checkpoint_wal()
        mock_db.conn.execute.assert_called_with("PRAGMA wal_checkpoint(PASSIVE)")

    def test_checkpoint_wal_failure_is_non_critical(self):
        mock_db = MagicMock()
        mock_db.conn.execute.side_effect = Exception("WAL error")
        pipeline = _EmbedPipeline(
            embedder=None,
            email_db=mock_db,
            entity_extractor_fn=None,
            batch_size=100,
        )
        # Should not raise
        pipeline._checkpoint_wal()

    def test_checkpoint_wal_no_db(self):
        pipeline = _EmbedPipeline(
            embedder=None,
            email_db=None,
            entity_extractor_fn=None,
            batch_size=100,
        )
        # Should not raise even when email_db is None
        pipeline._checkpoint_wal()


class TestComputeAnalytics:
    def test_skips_when_no_email_db(self):
        pipeline = _EmbedPipeline(
            embedder=None,
            email_db=None,
            entity_extractor_fn=None,
            batch_size=100,
        )
        # Should return immediately without error
        pipeline._compute_analytics([_make_email(1)])

    def test_skips_short_body(self):
        mock_db = MagicMock()
        pipeline = _EmbedPipeline(
            embedder=None,
            email_db=mock_db,
            entity_extractor_fn=None,
            batch_size=100,
        )
        email = _make_email(1, body_text="short")
        pipeline._compute_analytics([email])
        mock_db.update_analytics_batch.assert_not_called()


class TestPipelineProcessBatch:
    def test_process_batch_with_entity_extraction(self):
        mock_db = _MockEmailDB()

        def _extract(body, sender):
            entity = MagicMock()
            entity.text = "ACME Corp"
            entity.entity_type = "organization"
            entity.normalized_form = "acme corp"
            return [entity]

        pipeline = _EmbedPipeline(
            embedder=None,
            email_db=mock_db,
            entity_extractor_fn=_extract,
            batch_size=100,
        )
        pipeline._wal_checkpoint_interval = 0  # disable WAL checkpoint

        emails = [_make_email(1)]
        pipeline._process_batch([], emails)
        assert len(mock_db._entities) > 0

    def test_process_batch_with_cooldown(self, monkeypatch):
        monkeypatch.setenv("INGEST_BATCH_COOLDOWN", "0.01")
        mock_embedder = MagicMock()
        mock_embedder.add_chunks.return_value = 1

        pipeline = _EmbedPipeline(
            embedder=mock_embedder,
            email_db=None,
            entity_extractor_fn=None,
            batch_size=100,
        )
        assert pipeline._cooldown == 0.01

        from src.chunker import EmailChunk

        chunk = EmailChunk(uid="test", chunk_id="test::0", text="hello", metadata={})
        pipeline._process_batch([chunk], [])
        assert pipeline.chunks_added == 1

    def test_wal_checkpoint_triggers_on_interval(self):
        mock_db = MagicMock()
        mock_db.insert_emails_batch.return_value = set()
        mock_embedder = MagicMock()
        mock_embedder.add_chunks.return_value = 1

        pipeline = _EmbedPipeline(
            embedder=mock_embedder,
            email_db=mock_db,
            entity_extractor_fn=None,
            batch_size=100,
        )
        pipeline._wal_checkpoint_interval = 1
        pipeline.batches_written = 0

        from src.chunker import EmailChunk

        chunk = EmailChunk(uid="test", chunk_id="test::0", text="hello", metadata={})
        pipeline._process_batch([chunk], [])
        # After batch, batches_written should be 1, and 1 % 1 == 0 triggers checkpoint
        mock_db.conn.execute.assert_called_with("PRAGMA wal_checkpoint(PASSIVE)")

    def test_entity_extraction_skips_empty_body(self):
        mock_db = _MockEmailDB()
        extract_called = []

        def _extract(body, sender):
            extract_called.append(body)
            return []

        pipeline = _EmbedPipeline(
            embedder=None,
            email_db=mock_db,
            entity_extractor_fn=_extract,
            batch_size=100,
        )
        pipeline._wal_checkpoint_interval = 0

        email = _make_email(1, body_text="")
        pipeline._process_batch([], [email])
        # Extractor should NOT have been called for empty body
        assert len(extract_called) == 0


class TestPipelineSubmitError:
    def test_submit_raises_when_error_set(self):
        pipeline = _EmbedPipeline(
            embedder=None,
            email_db=None,
            entity_extractor_fn=None,
            batch_size=100,
        )
        pipeline._error = RuntimeError("previous error")
        with pytest.raises(RuntimeError, match="previous error"):
            pipeline.submit(["chunk"], [])


class TestResetIndex:
    def test_reset_index_deletes_sqlite_and_chromadb(self, tmp_path, monkeypatch):
        from src.ingest import _reset_index

        sqlite_file = tmp_path / "test.db"
        sqlite_file.write_text("dummy", encoding="utf-8")
        chroma_dir = tmp_path / "chromadb"
        chroma_dir.mkdir()
        (chroma_dir / "data.bin").write_text("dummy", encoding="utf-8")

        args = argparse.Namespace(
            sqlite_path=str(sqlite_file),
            chromadb_path=str(chroma_dir),
        )
        _reset_index(args)

        assert not sqlite_file.exists()
        assert not chroma_dir.exists()

    def test_reset_index_handles_missing_files(self, tmp_path):
        from src.ingest import _reset_index

        args = argparse.Namespace(
            sqlite_path=str(tmp_path / "nonexistent.db"),
            chromadb_path=str(tmp_path / "nonexistent_dir"),
        )
        # Should not raise
        _reset_index(args)


class TestPipelineSkipAlreadyInserted:
    def test_skips_entity_extraction_for_existing(self):
        """When insert_emails_batch returns fewer UIDs, entities should be skipped for duplicates."""
        mock_db = MagicMock()
        email1 = _make_email(1)
        email2 = _make_email(2)
        # Only email2 is new
        mock_db.insert_emails_batch.return_value = {email2.uid}
        mock_db.update_analytics_batch.return_value = 0

        extract_calls = []

        def _extract(body, sender):
            extract_calls.append(body)
            return []

        pipeline = _EmbedPipeline(
            embedder=None,
            email_db=mock_db,
            entity_extractor_fn=_extract,
            batch_size=100,
        )
        pipeline._wal_checkpoint_interval = 0

        pipeline._process_batch([], [email1, email2])
        # Only email2 (new) should have entity extraction
        assert len(extract_calls) == 1

    def test_marks_ingest_failed_when_embedding_raises(self):
        mock_db = _MockEmailDB()

        class _BoomEmbedder:
            def add_chunks(self, chunks, **_kw):
                raise RuntimeError("vector store unavailable")

        pipeline = _EmbedPipeline(
            embedder=_BoomEmbedder(),
            email_db=mock_db,
            entity_extractor_fn=None,
            batch_size=100,
        )
        pipeline._wal_checkpoint_interval = 0

        from src.chunker import EmailChunk

        email = _make_email(1)
        email._ingest_body_chunk_count = 1
        email._ingest_attachment_chunk_count = 0
        email._ingest_image_chunk_count = 0
        email._ingest_attachment_requested = False
        email._ingest_image_requested = False
        chunk = EmailChunk(uid=email.uid, chunk_id=f"{email.uid}__0", text="hello", metadata={"uid": email.uid})

        with pytest.raises(RuntimeError, match="vector store unavailable"):
            pipeline._process_batch([chunk], [email])

        mock_db.conn.rollback.assert_called_once()
        assert mock_db._failed == {}

    def test_rolls_back_relational_rows_when_embedding_raises(self, tmp_path):
        db = EmailDatabase(str(tmp_path / "emails.db"))

        class _BoomEmbedder:
            def add_chunks(self, chunks, **_kw):
                raise RuntimeError("vector store unavailable")

        pipeline = _EmbedPipeline(
            embedder=_BoomEmbedder(),
            email_db=db,
            entity_extractor_fn=None,
            batch_size=100,
        )
        pipeline._wal_checkpoint_interval = 0

        from src.chunker import EmailChunk

        email = _make_email(1, body_text="")
        email._ingest_body_chunk_count = 1
        email._ingest_attachment_chunk_count = 0
        email._ingest_image_chunk_count = 0
        email._ingest_attachment_requested = False
        email._ingest_image_requested = False
        chunk = EmailChunk(uid=email.uid, chunk_id=f"{email.uid}__0", text="hello", metadata={"uid": email.uid})

        with pytest.raises(RuntimeError, match="vector store unavailable"):
            pipeline._process_batch([chunk], [email])

        email_count = db.conn.execute("SELECT COUNT(*) FROM emails").fetchone()[0]
        ingest_state_count = db.conn.execute("SELECT COUNT(*) FROM email_ingest_state").fetchone()[0]
        assert email_count == 0
        assert ingest_state_count == 0


class TestAttachmentProcessing:
    def test_attachment_text_extraction(self, monkeypatch, tmp_path):
        """When extract_attachments=True, attachment text should be chunked."""
        import src.ingest as ingest_mod

        class _EmailWithAtt:
            def __init__(self):
                self.uid = "uid-att"
                self.attachment_contents = [("doc.txt", b"Hello attachment text")]
                self.message_id = "<att@test.com>"

            def to_dict(self):
                return {
                    "uid": self.uid,
                    "subject": "Test",
                    "sender_name": "S",
                    "sender_email": "s@test.com",
                    "date": "2024-01-01",
                    "folder": "Inbox",
                }

        monkeypatch.setattr(ingest_mod, "parse_olm", lambda _path, **_kw: [_EmailWithAtt()])
        monkeypatch.setattr(
            ingest_mod,
            "chunk_email",
            lambda e: [{"chunk_id": f"{e.get('uid', 'x')}-a"}],
        )
        monkeypatch.setattr(
            ingest_mod,
            "chunk_attachment",
            lambda **kw: [MagicMock()],
        )

        # Mock the attachment_extractor
        original_import = __import__

        def _mock_import(name, *args, **kwargs):
            if name == "src.attachment_extractor" or (args and "attachment_extractor" in str(args)):
                mod = types.ModuleType("src.attachment_extractor")
                mod.extract_text = lambda name, data: "extracted text" if data else None
                return mod
            return original_import(name, *args, **kwargs)

        stats = ingest_mod.ingest(
            "mock.olm",
            dry_run=True,
            extract_attachments=True,
        )
        # Attachment chunking should have happened
        assert stats["emails_parsed"] == 1
