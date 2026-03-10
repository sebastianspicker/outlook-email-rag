"""Embedding and ChromaDB storage."""

from __future__ import annotations

import logging
import time
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
        self._sparse_db: object | None = None  # injected via set_sparse_db()
        self._sparse_db_fallback: object | None = None  # lazy singleton

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
                mps_float16=self.settings.mps_float16,
            )
        return self._embedder

    @property
    def model(self) -> MultiVectorEmbedder:
        """Backward-compatible alias for ``embedder``."""
        return self.embedder

    def set_sparse_db(self, db: object) -> None:
        """Inject a shared database connection for sparse vector storage."""
        self._sparse_db = db

    def close(self) -> None:
        """Close any fallback database connection created by _store_sparse."""
        if self._sparse_db_fallback is not None:
            self._sparse_db_fallback.close()
            self._sparse_db_fallback = None

    def get_existing_ids(self, refresh: bool = False) -> set[str]:
        """Get all known chunk IDs, cached for current embedder lifecycle."""
        if self._existing_ids_cache is None or refresh:
            if self.collection.count() == 0:
                self._existing_ids_cache = set()
            else:
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
        t_start = time.monotonic()
        for batch in _iter_batches(new_chunks, batch_size):
            # Separate pre-embedded chunks from those needing encoding
            needs_encoding: list[EmailChunk] = []
            pre_embedded: list[EmailChunk] = []
            for chunk in batch:
                if chunk.embedding is not None:
                    pre_embedded.append(chunk)
                else:
                    needs_encoding.append(chunk)

            all_ids: list[str] = []
            all_embeddings: list[list[float]] = []
            all_texts: list[str] = []
            all_metadatas: list[dict] = []

            # Encode chunks that need it
            if needs_encoding:
                texts = [c.text for c in needs_encoding]
                result: MultiVectorResult = self.embedder.encode_all(texts)
                encoded_embeddings = to_builtin_list(result.dense)

                for i, chunk in enumerate(needs_encoding):
                    all_ids.append(chunk.chunk_id)
                    all_embeddings.append(encoded_embeddings[i])
                    all_texts.append(chunk.text)
                    all_metadatas.append(chunk.metadata)

                # Sparse vectors stored via callback if available (Phase 2 hook)
                if result.sparse is not None:
                    self._store_sparse(
                        [c.chunk_id for c in needs_encoding], result.sparse,
                    )

            # Add pre-embedded chunks directly (no re-encoding)
            for chunk in pre_embedded:
                all_ids.append(chunk.chunk_id)
                all_embeddings.append(chunk.embedding)
                all_texts.append(chunk.text)
                all_metadatas.append(chunk.metadata)

            self.collection.add(
                ids=all_ids,
                embeddings=all_embeddings,
                documents=all_texts,
                metadatas=all_metadatas,
            )

            existing.update(all_ids)
            added += len(batch)
            if show_progress:
                elapsed = time.monotonic() - t_start
                rate = added / elapsed if elapsed > 0 else 0
                logger.info(
                    "Stored %s/%s chunks (%.1fs, %.0f chunks/s)...",
                    added, len(new_chunks), elapsed, rate,
                )

        return added

    def _store_sparse(self, ids: list[str], sparse_vectors: list[dict[int, float]]) -> None:
        """Persist sparse vectors to SQLite alongside dense in ChromaDB."""
        try:
            # Use injected DB first, then lazy-cached fallback
            db = self._sparse_db
            if db is None:
                if self._sparse_db_fallback is None:
                    import os

                    sqlite_path = self.settings.sqlite_path
                    if not sqlite_path or not os.path.exists(sqlite_path):
                        logger.debug("Sparse vectors available but no SQLite DB found, skipping storage.")
                        return

                    from .email_db import EmailDatabase

                    self._sparse_db_fallback = EmailDatabase(sqlite_path)
                db = self._sparse_db_fallback
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
