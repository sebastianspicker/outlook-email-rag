"""
Embedding and ChromaDB storage.

Uses sentence-transformers for local embedding and ChromaDB for persistent vector storage.
"""

import os
import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer

from .chunker import EmailChunk

# Defaults
DEFAULT_CHROMADB_PATH = "data/chromadb"
DEFAULT_MODEL = "all-MiniLM-L6-v2"
COLLECTION_NAME = "emails"
BATCH_SIZE = 100


class EmailEmbedder:
    """Manages embedding and storage of email chunks."""

    def __init__(
        self,
        chromadb_path: str | None = None,
        model_name: str | None = None,
    ):
        self.chromadb_path = chromadb_path or os.getenv("CHROMADB_PATH", DEFAULT_CHROMADB_PATH)
        self.model_name = model_name or os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL)

        # Lazy-load model (downloads on first use)
        self._model: SentenceTransformer | None = None

        # Initialize ChromaDB
        os.makedirs(self.chromadb_path, exist_ok=True)
        self.client = chromadb.PersistentClient(
            path=self.chromadb_path,
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            print(f"Loading embedding model: {self.model_name}...")
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def get_existing_ids(self) -> set[str]:
        """Get all chunk IDs already in the database (for deduplication)."""
        try:
            result = self.collection.get(include=[])
            return set(result["ids"]) if result["ids"] else set()
        except Exception:
            return set()

    def add_chunks(self, chunks: list[EmailChunk], show_progress: bool = True) -> int:
        """
        Embed and store chunks in ChromaDB.

        Args:
            chunks: List of EmailChunk objects to embed.
            show_progress: Whether to print progress.

        Returns:
            Number of new chunks added.
        """
        if not chunks:
            return 0

        # Filter out already-stored chunks
        existing = self.get_existing_ids()
        new_chunks = [c for c in chunks if c.chunk_id not in existing]

        if not new_chunks:
            if show_progress:
                print(f"  All {len(chunks)} chunks already in database, skipping.")
            return 0

        if show_progress:
            print(f"  Embedding {len(new_chunks)} new chunks ({len(chunks) - len(new_chunks)} already stored)...")

        # Process in batches
        added = 0
        for i in range(0, len(new_chunks), BATCH_SIZE):
            batch = new_chunks[i : i + BATCH_SIZE]

            texts = [c.text for c in batch]
            ids = [c.chunk_id for c in batch]
            metadatas = [c.metadata for c in batch]

            # Embed
            embeddings = self.model.encode(texts, show_progress_bar=False).tolist()

            # Store
            self.collection.add(
                ids=ids,
                embeddings=embeddings,
                documents=texts,
                metadatas=metadatas,
            )

            added += len(batch)
            if show_progress:
                print(f"  Stored {added}/{len(new_chunks)} chunks...")

        return added

    def count(self) -> int:
        """Total number of chunks in the database."""
        return self.collection.count()
