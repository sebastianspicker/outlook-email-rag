"""In-memory inverted index for BGE-M3 learned sparse vectors.

Replaces BM25 with higher-quality sparse retrieval from the same BGE-M3
model that produces dense embeddings — one model, two retrieval modes.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .email_db import EmailDatabase

logger = logging.getLogger(__name__)


class SparseIndex:
    """In-memory inverted index built from SQLite-stored sparse vectors.

    Designed for < 100k chunks (~50 MB memory).  Supports dot-product
    scoring against query sparse vectors from BGE-M3.
    """

    def __init__(self) -> None:
        self._inverted: dict[int, list[tuple[str, float]]] = {}
        self._doc_norms: dict[str, float] = {}
        self._built = False
        self._doc_count = 0

    @property
    def is_built(self) -> bool:
        return self._built

    @property
    def doc_count(self) -> int:
        return self._doc_count

    def build_from_db(self, db: EmailDatabase) -> None:
        """Build the inverted index from all sparse vectors in SQLite."""
        all_vecs = db.all_sparse_vectors()
        self.build_from_vectors(all_vecs)

    def build_from_vectors(self, vectors: dict[str, dict[int, float]]) -> None:
        """Build the inverted index from a dict of {chunk_id: {token_id: weight}}.

        Args:
            vectors: Mapping from chunk ID to sparse vector.
        """
        self._inverted.clear()
        self._doc_norms.clear()

        for chunk_id, sv in vectors.items():
            norm = 0.0
            for token_id, weight in sv.items():
                if weight <= 0:
                    continue
                self._inverted.setdefault(token_id, []).append((chunk_id, weight))
                norm += weight * weight
            self._doc_norms[chunk_id] = math.sqrt(norm) if norm > 0 else 0.0

        self._doc_count = len(vectors)
        self._built = True
        logger.info(
            "Sparse index built: %d documents, %d unique tokens",
            self._doc_count,
            len(self._inverted),
        )

    def search(
        self,
        query_sparse: dict[int, float],
        top_k: int = 10,
    ) -> list[tuple[str, float]]:
        """Search using dot-product scoring against query sparse vector.

        Args:
            query_sparse: Query sparse vector {token_id: weight}.
            top_k: Number of results to return.

        Returns:
            List of (chunk_id, score) tuples, highest score first.
        """
        if not self._built or not query_sparse:
            return []

        scores: dict[str, float] = {}

        for token_id, q_weight in query_sparse.items():
            if q_weight <= 0:
                continue
            postings = self._inverted.get(token_id)
            if not postings:
                continue
            for chunk_id, d_weight in postings:
                scores[chunk_id] = scores.get(chunk_id, 0.0) + q_weight * d_weight

        if not scores:
            return []

        # Sort by score descending
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return ranked[:top_k]
