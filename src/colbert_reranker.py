"""ColBERT MaxSim reranking using BGE-M3 token-level embeddings.

Uses the same BGE-M3 model as the primary embedder — no additional model
load required.  ColBERT scores are computed on-the-fly for top-N candidates
(typically 30-50), trading ~25MB temporary memory for high-precision reranking.

Particularly effective for:
- German compound words (token-level matching handles subwords)
- Legal evidence queries (exact phrase matching)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from .multi_vector_embedder import MultiVectorEmbedder
    from .retriever import SearchResult

logger = logging.getLogger(__name__)


class ColBERTReranker:
    """Re-score search results using ColBERT MaxSim token-level matching.

    Unlike cross-encoders, ColBERT reranking uses the same BGE-M3 model
    that produced the initial embeddings, so there is no extra model load.
    """

    def __init__(self, embedder: MultiVectorEmbedder) -> None:
        self._embedder = embedder

    def rerank(
        self,
        query: str,
        results: list[SearchResult],
        top_k: int | None = None,
    ) -> list[SearchResult]:
        """Re-rank results by ColBERT MaxSim score.

        Encodes the query and all candidate documents at the token level,
        then computes MaxSim scores for reranking.

        Args:
            query: The original search query.
            results: Candidate results from initial retrieval.
            top_k: Number of top results to return (default: all).

        Returns:
            Re-ranked list of SearchResult objects sorted by descending
            ColBERT score. The ``distance`` field is updated.
        """
        if not results:
            return []

        if not self._embedder.has_colbert:
            logger.debug("ColBERT not available, returning results unchanged")
            return results[:top_k] if top_k else results

        # Encode query tokens
        query_vecs = self._embedder.encode_colbert([query])
        if not query_vecs or query_vecs[0] is None:
            return results[:top_k] if top_k else results

        q_vecs = query_vecs[0]  # (num_query_tokens, dim)

        # Encode document tokens
        doc_texts = [r.text for r in results]
        doc_vecs_list = self._embedder.encode_colbert(doc_texts)
        if not doc_vecs_list:
            return results[:top_k] if top_k else results

        # Score each document
        scored: list[tuple[SearchResult, float]] = []
        for result, d_vecs in zip(results, doc_vecs_list):
            if d_vecs is None or len(d_vecs) == 0:
                scored.append((result, 0.0))
                continue
            score = maxsim(q_vecs, d_vecs)
            scored.append((result, score))

        # Sort by score descending (higher = more relevant)
        scored.sort(key=lambda x: x[1], reverse=True)

        limit = top_k if top_k else len(scored)

        from .retriever import SearchResult as SR

        reranked = []
        for result, score in scored[:limit]:
            # Convert score to distance (lower = more relevant)
            distance = max(0.0, 1.0 - score)
            reranked.append(
                SR(
                    chunk_id=result.chunk_id,
                    text=result.text,
                    metadata=result.metadata,
                    distance=distance,
                )
            )

        return reranked


def maxsim(query_vecs: np.ndarray, doc_vecs: np.ndarray) -> float:
    """Compute ColBERT MaxSim score between query and document token vectors.

    For each query token, find the maximum cosine similarity with any document
    token, then average across all query tokens.

    Args:
        query_vecs: (num_query_tokens, dim) array.
        doc_vecs: (num_doc_tokens, dim) array.

    Returns:
        MaxSim score (higher = more similar).
    """
    if query_vecs.size == 0 or doc_vecs.size == 0:
        return 0.0

    # Normalize vectors for cosine similarity
    q_norms = np.linalg.norm(query_vecs, axis=1, keepdims=True)
    d_norms = np.linalg.norm(doc_vecs, axis=1, keepdims=True)

    # Avoid division by zero
    q_norms = np.maximum(q_norms, 1e-8)
    d_norms = np.maximum(d_norms, 1e-8)

    q_normed = query_vecs / q_norms
    d_normed = doc_vecs / d_norms

    # Similarity matrix: (num_query_tokens, num_doc_tokens)
    sim_matrix = q_normed @ d_normed.T

    # MaxSim: max similarity per query token, then average
    max_sims = sim_matrix.max(axis=1)
    return float(max_sims.mean())
