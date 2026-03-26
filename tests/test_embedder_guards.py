import pytest

from src.chunker import EmailChunk
from src.embedder import EmailEmbedder


def test_add_chunks_rejects_non_positive_batch_size():
    embedder = EmailEmbedder.__new__(EmailEmbedder)

    with pytest.raises(ValueError):
        embedder.add_chunks([], batch_size=0)


def test_add_chunks_deduplicates_within_batch(tmp_path):
    """Duplicate chunk IDs in the same batch must not raise DuplicateIDError."""
    embedder = EmailEmbedder(chromadb_path=str(tmp_path / "db"))

    dup_id = "aaaa__0"
    chunks = [
        EmailChunk(uid="aaaa", chunk_id=dup_id, text="Hello", metadata={"uid": "aaaa", "chunk_index": "0"}),
        EmailChunk(uid="aaaa", chunk_id=dup_id, text="World", metadata={"uid": "aaaa", "chunk_index": "0"}),
        EmailChunk(uid="bbbb", chunk_id="bbbb__0", text="Other", metadata={"uid": "bbbb", "chunk_index": "0"}),
    ]

    added = embedder.add_chunks(chunks, batch_size=100)

    # Only 2 unique chunk IDs, so only 2 should be stored
    assert added == 2
    assert embedder.count() == 2
