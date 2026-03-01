"""
Retrieval logic for searching the email vector database.

Supports semantic search, filtered search (by sender, date range),
and metadata queries (list senders, stats).
"""

import os
from dataclasses import dataclass

import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer

DEFAULT_CHROMADB_PATH = "data/chromadb"
DEFAULT_MODEL = "all-MiniLM-L6-v2"
COLLECTION_NAME = "emails"


@dataclass
class SearchResult:
    """A single search result."""
    chunk_id: str
    text: str
    metadata: dict
    distance: float  # Lower = more similar (cosine distance)

    @property
    def score(self) -> float:
        """Similarity score 0-1 (higher = more similar)."""
        return max(0.0, 1.0 - self.distance)

    def to_context_string(self) -> str:
        """Format for passing to Claude as context."""
        m = self.metadata
        header_parts = []
        if m.get("date"):
            header_parts.append(f"Date: {m['date']}")
        if m.get("sender_name") or m.get("sender_email"):
            sender = m.get("sender_name", "")
            if m.get("sender_email"):
                sender = f"{sender} <{m['sender_email']}>" if sender else m["sender_email"]
            header_parts.append(f"From: {sender}")
        if m.get("to"):
            header_parts.append(f"To: {m['to']}")
        if m.get("subject"):
            header_parts.append(f"Subject: {m['subject']}")
        if m.get("folder"):
            header_parts.append(f"Folder: {m['folder']}")

        header = "\n".join(header_parts)
        # Use the stored document text (which includes body)
        return f"---\n{header}\nRelevance: {self.score:.2f}\n---\n{self.text}\n"


class EmailRetriever:
    """Search interface for the email vector database."""

    def __init__(
        self,
        chromadb_path: str | None = None,
        model_name: str | None = None,
    ):
        self.chromadb_path = chromadb_path or os.getenv("CHROMADB_PATH", DEFAULT_CHROMADB_PATH)
        self.model_name = model_name or os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL)

        self._model: SentenceTransformer | None = None

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
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def search(
        self,
        query: str,
        top_k: int = 10,
        where: dict | None = None,
    ) -> list[SearchResult]:
        """
        Semantic search across all emails.

        Args:
            query: Natural language search query.
            top_k: Number of results to return.
            where: Optional ChromaDB where filter (e.g., {"sender_email": "john@example.com"}).

        Returns:
            List of SearchResult objects, sorted by relevance.
        """
        total = self.collection.count()
        if total == 0:
            return []

        query_embedding = self.model.encode([query]).tolist()

        kwargs = {
            "query_embeddings": query_embedding,
            "n_results": min(top_k, total),
            "include": ["documents", "metadatas", "distances"],
        }
        if where:
            kwargs["where"] = where

        results = self.collection.query(**kwargs)

        search_results = []
        for i in range(len(results["ids"][0])):
            search_results.append(SearchResult(
                chunk_id=results["ids"][0][i],
                text=results["documents"][0][i],
                metadata=results["metadatas"][0][i],
                distance=results["distances"][0][i],
            ))

        return search_results

    def search_by_sender(
        self,
        query: str,
        sender: str,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """Search emails filtered by sender (partial match on email or name)."""
        # ChromaDB doesn't support LIKE queries, so we do a broad search
        # and filter in Python for partial matches
        results = self.search(query, top_k=top_k * 3)  # Fetch more, then filter
        sender_lower = sender.lower()

        filtered = [
            r for r in results
            if sender_lower in r.metadata.get("sender_email", "").lower()
            or sender_lower in r.metadata.get("sender_name", "").lower()
        ]
        return filtered[:top_k]

    def search_by_date(
        self,
        query: str,
        date_from: str | None = None,
        date_to: str | None = None,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """
        Search emails within a date range.

        Dates should be ISO format strings (e.g., "2023-01-01").
        ChromaDB doesn't natively support date range queries,
        so we fetch more results and filter in Python.
        """
        results = self.search(query, top_k=top_k * 5)

        filtered = []
        for r in results:
            date_str = r.metadata.get("date", "")
            if not date_str:
                continue
            # Compare date strings (ISO format sorts correctly)
            date_prefix = date_str[:10]  # Extract YYYY-MM-DD
            if date_from and date_prefix < date_from:
                continue
            if date_to and date_prefix > date_to:
                continue
            filtered.append(r)

        return filtered[:top_k]

    def list_senders(self, limit: int = 50) -> list[dict]:
        """
        List unique senders in the archive.

        Returns list of {"name": ..., "email": ..., "count": ...} dicts.
        """
        page_size = 1000
        offset = 0
        sender_counts: dict[str, dict] = {}

        while True:
            batch = self.collection.get(include=["metadatas"], limit=page_size, offset=offset)
            metadatas = batch.get("metadatas") or []
            if not metadatas:
                break
            for meta in metadatas:
                email = meta.get("sender_email", "unknown")
                name = meta.get("sender_name", "")
                key = email.lower()
                if key not in sender_counts:
                    sender_counts[key] = {"name": name, "email": email, "count": 0}
                sender_counts[key]["count"] += 1
            offset += page_size

        if not sender_counts:
            return []

        sorted_senders = sorted(sender_counts.values(), key=lambda x: x["count"], reverse=True)
        return sorted_senders[:limit]

    def stats(self) -> dict:
        """Get stats about the email archive."""
        total_chunks = self.collection.count()
        if total_chunks == 0:
            return {"total_chunks": 0, "total_emails": 0}

        page_size = 1000
        offset = 0
        unique_uids: set[str] = set()
        unique_senders: set[str] = set()
        earliest: str | None = None
        latest: str | None = None
        folders: dict[str, int] = {}

        while True:
            batch = self.collection.get(include=["metadatas"], limit=page_size, offset=offset)
            metadatas = batch.get("metadatas") or []
            if not metadatas:
                break
            for m in metadatas:
                uid = m.get("uid", "")
                if uid:
                    unique_uids.add(uid)
                sender_email = m.get("sender_email")
                if sender_email:
                    unique_senders.add(sender_email.lower())
                raw_date = m.get("date")
                if raw_date:
                    d = raw_date[:10]
                    if earliest is None or d < earliest:
                        earliest = d
                    if latest is None or d > latest:
                        latest = d
                folder = m.get("folder", "Unknown")
                folders[folder] = folders.get(folder, 0) + 1
            offset += page_size

        return {
            "total_chunks": total_chunks,
            "total_emails": len(unique_uids),
            "unique_senders": len(unique_senders),
            "date_range": {"earliest": earliest, "latest": latest},
            "folders": dict(sorted(folders.items(), key=lambda x: x[1], reverse=True)),
        }

    def format_results_for_claude(self, results: list[SearchResult]) -> str:
        """Format search results as context for Claude."""
        if not results:
            return "No matching emails found."

        parts = [f"Found {len(results)} relevant email(s):\n"]
        for i, r in enumerate(results, 1):
            parts.append(f"=== Email Result {i} (relevance: {r.score:.2f}) ===")
            parts.append(r.to_context_string())

        return "\n".join(parts)
