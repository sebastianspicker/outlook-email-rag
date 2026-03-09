"""Embedding and ChromaDB storage."""

from __future__ import annotations

import logging
from typing import Iterable

from .chunker import EmailChunk
from .config import resolve_runtime_settings
from .multi_vector_embedder import MultiVectorEmbedder, MultiVectorResult
from .storage import get_chroma_client, get_collection, iter_collection_ids, to_builtin_list

logger = logging.getLogger(__name__)


class EmailEmbedder:
    """Manages embedding and storage of email chunks."""

    def __init__(
        self,
        chromadb_path: str | None = None,
        model_name: str | None = None,
        collection_name: str | None = None,
    ):
        self.settings = resolve_runtime_settings(
            chromadb_path=chromadb_path,
            embedding_model=model_name,
            collection_name=collection_name,
        )

        self.chromadb_path = self.settings.chromadb_path
        self.model_name = self.settings.embedding_model
        self.collection_name = self.settings.collection_name

        self._embedder: MultiVectorEmbedder | None = None
        self._existing_ids_cache: set[str] | None = None

        self.client = get_chroma_client(self.chromadb_path)
        self.collection = get_collection(self.client, self.collection_name)

    @property
    def embedder(self) -> MultiVectorEmbedder:
        """Lazy-loaded multi-vector embedder."""
        if self._embedder is None:
            batch_size = self.settings.embedding_batch_size
            self._embedder = MultiVectorEmbedder(
                model_name=self.model_name,
                device=self.settings.device,
                sparse_enabled=self.settings.sparse_enabled,
                colbert_enabled=self.settings.colbert_rerank_enabled,
                batch_size=batch_size,
            )
        return self._embedder

    @property
    def model(self) -> MultiVectorEmbedder:
        """Backward-compatible alias for ``embedder``."""
        return self.embedder

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

        # Deduplicate: skip chunks already in DB *and* duplicates within this batch
        seen: set[str] = set()
        new_chunks: list[EmailChunk] = []
        for chunk in chunks:
            if chunk.chunk_id not in existing and chunk.chunk_id not in seen:
                seen.add(chunk.chunk_id)
                new_chunks.append(chunk)

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

            result: MultiVectorResult = self.embedder.encode_all(texts)
            embeddings = to_builtin_list(result.dense)

            self.collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas,
            )

            # Sparse vectors stored via callback if available (Phase 2 hook)
            if result.sparse is not None:
                self._store_sparse(ids, result.sparse)

            existing.update(ids)
            added += len(batch)
            if show_progress:
                logger.info("Stored %s/%s chunks...", added, len(new_chunks))

        return added

    def _store_sparse(self, ids: list[str], sparse_vectors: list[dict[int, float]]) -> None:
        """Persist sparse vectors to SQLite alongside dense in ChromaDB."""
        try:
            import os

            sqlite_path = self.settings.sqlite_path
            if not sqlite_path or not os.path.exists(sqlite_path):
                logger.debug("Sparse vectors available but no SQLite DB found, skipping storage.")
                return

            from .email_db import EmailDatabase

            db = EmailDatabase(sqlite_path)
            inserted = db.insert_sparse_batch(ids, sparse_vectors)
            logger.debug("Stored %d sparse vectors in SQLite.", inserted)
        except Exception:
            logger.debug("Failed to store sparse vectors", exc_info=True)

    def delete_chunks_by_uid(self, uid: str) -> int:
        """Delete all chunks for an email UID from ChromaDB. Returns count deleted."""
        existing = self.get_existing_ids(refresh=False)
        chunk_ids = [cid for cid in existing if cid.startswith(f"{uid}__")]
        if not chunk_ids:
            return 0
        self.collection.delete(ids=chunk_ids)
        existing.difference_update(chunk_ids)
        return len(chunk_ids)

    def upsert_chunks(
        self,
        chunks: list[EmailChunk],
        batch_size: int = 100,
    ) -> int:
        """Re-embed and upsert chunks in ChromaDB (overwrites existing). Returns count."""
        if not chunks:
            return 0

        added = 0
        for batch in _iter_batches(chunks, batch_size):
            texts = [chunk.text for chunk in batch]
            ids = [chunk.chunk_id for chunk in batch]
            metadatas = [chunk.metadata for chunk in batch]

            result: MultiVectorResult = self.embedder.encode_all(texts)
            embeddings = to_builtin_list(result.dense)

            self.collection.upsert(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas,
            )

            if result.sparse is not None:
                self._store_sparse(ids, result.sparse)

            existing = self.get_existing_ids(refresh=False)
            existing.update(ids)
            added += len(batch)

        return added

    def count(self) -> int:
        """Total number of chunks in the database."""
        return self.collection.count()


def _iter_batches(items: list[EmailChunk], batch_size: int) -> Iterable[list[EmailChunk]]:
    for idx in range(0, len(items), batch_size):
        yield items[idx : idx + batch_size]
