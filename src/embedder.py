"""Embedding and ChromaDB storage."""

from __future__ import annotations

import logging
from typing import Iterable

from sentence_transformers import SentenceTransformer

from .chunker import EmailChunk
from .config import Settings, get_settings
from .storage import get_chroma_client, get_collection, iter_collection_ids

logger = logging.getLogger(__name__)


class EmailEmbedder:
    """Manages embedding and storage of email chunks."""

    def __init__(
        self,
        chromadb_path: str | None = None,
        model_name: str | None = None,
        collection_name: str | None = None,
    ):
        settings = get_settings()
        self.settings = Settings(
            chromadb_path=chromadb_path or settings.chromadb_path,
            embedding_model=model_name or settings.embedding_model,
            collection_name=collection_name or settings.collection_name,
            top_k=settings.top_k,
            claude_model=settings.claude_model,
            log_level=settings.log_level,
        )

        self.chromadb_path = self.settings.chromadb_path
        self.model_name = self.settings.embedding_model
        self.collection_name = self.settings.collection_name

        self._model: SentenceTransformer | None = None
        self._existing_ids_cache: set[str] | None = None

        self.client = get_chroma_client(self.chromadb_path)
        self.collection = get_collection(self.client, self.collection_name)

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            logger.info("Loading embedding model: %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def get_existing_ids(self, refresh: bool = False) -> set[str]:
        """Get all known chunk IDs, cached for current embedder lifecycle."""
        if self._existing_ids_cache is None or refresh:
            self._existing_ids_cache = set(iter_collection_ids(self.collection))
        return self._existing_ids_cache

    def add_chunks(
        self,
        chunks: list[EmailChunk],
        show_progress: bool = True,
        batch_size: int = 100,
    ) -> int:
        """Embed and store chunks in ChromaDB and return number of inserted chunks."""
        if batch_size <= 0:
            raise ValueError("batch_size must be a positive integer.")

        if not chunks:
            return 0

        existing = self.get_existing_ids(refresh=False)
        new_chunks = [chunk for chunk in chunks if chunk.chunk_id not in existing]

        if not new_chunks:
            if show_progress:
                logger.info("All %s chunks already in database, skipping.", len(chunks))
            return 0

        if show_progress:
            logger.info(
                "Embedding %s new chunks (%s already stored).",
                len(new_chunks),
                len(chunks) - len(new_chunks),
            )

        added = 0
        for batch in _iter_batches(new_chunks, batch_size):
            texts = [chunk.text for chunk in batch]
            ids = [chunk.chunk_id for chunk in batch]
            metadatas = [chunk.metadata for chunk in batch]

            embeddings = _to_list(self.model.encode(texts, show_progress_bar=False))

            self.collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas,
            )

            existing.update(ids)
            added += len(batch)
            if show_progress:
                logger.info("Stored %s/%s chunks...", added, len(new_chunks))

        return added

    def count(self) -> int:
        """Total number of chunks in the database."""
        return self.collection.count()


def _iter_batches(items: list[EmailChunk], batch_size: int) -> Iterable[list[EmailChunk]]:
    for idx in range(0, len(items), batch_size):
        yield items[idx : idx + batch_size]


def _to_list(value):
    if hasattr(value, "tolist"):
        return value.tolist()
    return value
