"""Extended tests for src/embedder.py beyond the basic guards."""

from __future__ import annotations

import pytest

from src.chunker import EmailChunk
from src.embedder import EmailEmbedder


def _make_chunk(uid: str = "uid1", index: int = 0, text: str = "Hello world") -> EmailChunk:
    return EmailChunk(
        uid=uid,
        chunk_id=f"{uid}__{index}",
        text=text,
        metadata={"uid": uid, "chunk_index": str(index)},
    )


# ── add_chunks ───────────────────────────────────────────────────────


def test_add_chunks_empty_list(tmp_path):
    embedder = EmailEmbedder(chromadb_path=str(tmp_path / "db"))
    result = embedder.add_chunks([])
    assert result == 0
    assert embedder.count() == 0


def test_add_chunks_single_chunk(tmp_path):
    embedder = EmailEmbedder(chromadb_path=str(tmp_path / "db"))
    chunks = [_make_chunk()]
    added = embedder.add_chunks(chunks, batch_size=100)
    assert added == 1
    assert embedder.count() == 1


def test_add_chunks_skips_existing(tmp_path):
    embedder = EmailEmbedder(chromadb_path=str(tmp_path / "db"))
    chunk = _make_chunk()
    embedder.add_chunks([chunk], batch_size=100)
    assert embedder.count() == 1

    # Adding the same chunk again should skip it
    added = embedder.add_chunks([chunk], batch_size=100)
    assert added == 0
    assert embedder.count() == 1


def test_add_chunks_multiple_batches(tmp_path):
    embedder = EmailEmbedder(chromadb_path=str(tmp_path / "db"))
    chunks = [_make_chunk(uid=f"uid{i}", index=0) for i in range(5)]

    # batch_size=2 means 3 batches: [2, 2, 1]
    added = embedder.add_chunks(chunks, batch_size=2)
    assert added == 5
    assert embedder.count() == 5


def test_add_chunks_rejects_non_positive_batch_size():
    embedder = EmailEmbedder.__new__(EmailEmbedder)
    with pytest.raises(ValueError, match="batch_size must be a positive integer"):
        embedder.add_chunks([], batch_size=0)
    with pytest.raises(ValueError, match="batch_size must be a positive integer"):
        embedder.add_chunks([], batch_size=-1)


# ── get_existing_ids ─────────────────────────────────────────────────


def test_get_existing_ids_empty(tmp_path):
    embedder = EmailEmbedder(chromadb_path=str(tmp_path / "db"))
    ids = embedder.get_existing_ids()
    assert ids == set()


def test_get_existing_ids_after_insert(tmp_path):
    embedder = EmailEmbedder(chromadb_path=str(tmp_path / "db"))
    embedder.add_chunks([_make_chunk(uid="a"), _make_chunk(uid="b")], batch_size=100)

    ids = embedder.get_existing_ids(refresh=True)
    assert "a__0" in ids
    assert "b__0" in ids
    assert len(ids) == 2


def test_get_existing_ids_cached(tmp_path):
    embedder = EmailEmbedder(chromadb_path=str(tmp_path / "db"))
    ids1 = embedder.get_existing_ids()
    ids2 = embedder.get_existing_ids()
    # Should return the same set object (cached)
    assert ids1 is ids2


def test_get_existing_ids_refresh(tmp_path):
    embedder = EmailEmbedder(chromadb_path=str(tmp_path / "db"))
    ids1 = embedder.get_existing_ids()
    ids2 = embedder.get_existing_ids(refresh=True)
    # refresh=True creates a new set
    assert ids1 is not ids2


# ── count ────────────────────────────────────────────────────────────


def test_count_empty(tmp_path):
    embedder = EmailEmbedder(chromadb_path=str(tmp_path / "db"))
    assert embedder.count() == 0


def test_count_after_inserts(tmp_path):
    embedder = EmailEmbedder(chromadb_path=str(tmp_path / "db"))
    embedder.add_chunks([_make_chunk(uid="x", index=0), _make_chunk(uid="y", index=0)], batch_size=100)
    assert embedder.count() == 2
