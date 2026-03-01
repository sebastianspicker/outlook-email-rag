import pytest

from src.embedder import EmailEmbedder


def test_add_chunks_rejects_non_positive_batch_size():
    embedder = EmailEmbedder.__new__(EmailEmbedder)

    with pytest.raises(ValueError):
        embedder.add_chunks([], batch_size=0)
