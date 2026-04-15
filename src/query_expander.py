"""Embedding-based query expansion for improved recall."""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_LEGAL_SUPPORT_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "chronology",
        (
            "chronology",
            "timeline",
            "sequence",
            "before",
            "after",
            "date",
            "dated",
            "calendar",
            "meeting",
            "attendance",
            "timesheet",
            "time record",
            "note",
        ),
        ("timeline", "chronology", "meeting note", "calendar record", "time record"),
    ),
    (
        "comparator",
        (
            "comparator",
            "compare",
            "comparison",
            "unequal treatment",
            "similarly situated",
            "other employee",
            "peer",
        ),
        ("comparator", "peer treatment", "similarly situated", "unequal treatment"),
    ),
    (
        "participation",
        (
            "sbv",
            "personalrat",
            "betriebsrat",
            "lpvg",
            "participation",
            "consultation",
            "consult",
            "bem",
            "schwerbehindertenvertretung",
        ),
        ("sbv", "personalrat", "betriebsrat", "participation record", "consultation"),
    ),
    (
        "contradiction",
        (
            "contradiction",
            "contradict",
            "discrepancy",
            "inconsistent",
            "mismatch",
            "promise",
            "omission",
            "summary",
            "conflict",
        ),
        ("contradiction", "inconsistent summary", "promise", "omission", "discrepancy"),
    ),
    (
        "document_request",
        (
            "missing proof",
            "missing exhibit",
            "missing record",
            "document request",
            "preservation",
            "custodian",
        ),
        ("missing record", "document request", "preservation", "custodian"),
    ),
)


def legal_support_query_profile(query: str | None) -> dict[str, Any]:
    """Return deterministic legal-support intent flags for one query."""
    text = " ".join(str(query or "").lower().split())
    intents: list[str] = []
    suggested_terms: list[str] = []
    for intent_id, triggers, additions in _LEGAL_SUPPORT_RULES:
        if any(trigger in text for trigger in triggers):
            intents.append(intent_id)
            for term in additions:
                if term not in suggested_terms and not re.search(r"\b" + re.escape(term) + r"\b", text):
                    suggested_terms.append(term)
    return {
        "is_legal_support": bool(intents),
        "intents": intents,
        "suggested_terms": suggested_terms,
    }


class QueryExpander:
    """Expand queries with semantically related terms using the embedding model.

    Reuses the already-loaded SentenceTransformer model and keyword vocabulary
    from the keyword extractor to find related terms at near-zero cost.
    """

    def __init__(self, model: Any = None, vocabulary: list[str] | None = None):
        """Initialize query expander.

        Args:
            model: SentenceTransformer model instance (reuses existing).
            vocabulary: List of corpus keywords to match against.
        """
        self._model = model
        self._vocabulary = vocabulary or []
        self._vocab_embeddings = None

    def set_vocabulary(self, vocabulary: list[str]) -> None:
        """Set or update the keyword vocabulary.

        Args:
            vocabulary: List of keywords/phrases from the corpus.
        """
        self._vocabulary = vocabulary
        self._vocab_embeddings = None  # Reset cached embeddings

    def _compute_similarities(self, query: str):
        """Return ``(similarities, top_indices)`` for *query* against the vocabulary.

        Lazily computes and caches vocabulary embeddings.  Returns ``None`` if
        numpy/model are unavailable or if the vocabulary is empty.
        """
        import numpy as np

        if not self._vocabulary:
            return None

        if self._vocab_embeddings is None:
            self._vocab_embeddings = np.array(self._model.encode_dense(self._vocabulary))
        query_embedding = np.array(self._model.encode_dense([query]))
        similarities = np.dot(self._vocab_embeddings, query_embedding.T).flatten()
        return similarities, similarities.argsort()[::-1]

    def expand(self, query: str, n_terms: int = 3) -> str:
        """Expand a query with semantically related terms.

        Embeds the query and finds the closest keywords from the corpus
        vocabulary, appending them to the original query.

        Args:
            query: Original search query.
            n_terms: Number of related terms to add.

        Returns:
            Expanded query string.
        """
        if not query or not query.strip():
            return query

        if n_terms <= 0:
            return query

        if not self._vocabulary or not self._model:
            return query

        try:
            query_lower = query.lower()
            added: list[str] = []
            profile = legal_support_query_profile(query)
            for term in profile["suggested_terms"]:
                if len(added) >= n_terms:
                    break
                if re.search(r"\b" + re.escape(term.lower()) + r"\b", query_lower):
                    continue
                added.append(term)

            sim_result = self._compute_similarities(query)
            if sim_result is None:
                return f"{query} {' '.join(added)}".strip() if added else query
            _similarities, top_indices = sim_result
            for idx in top_indices:
                if len(added) >= n_terms:
                    break
                term = self._vocabulary[idx]
                if re.search(r"\b" + re.escape(term.lower()) + r"\b", query_lower):
                    continue
                if len(term) < 3:
                    continue
                if term in added:
                    continue
                added.append(term)

            if not added:
                return query

            expanded = f"{query} {' '.join(added)}"
            logger.debug("Query expanded: '%s' → '%s'", query, expanded)
            return expanded

        except Exception:
            logger.debug("Query expansion failed", exc_info=True)
            return query

    def get_related_terms(self, query: str, n_terms: int = 5) -> list[tuple[str, float]]:
        """Get related terms with their similarity scores.

        Args:
            query: Search query.
            n_terms: Number of terms to return.

        Returns:
            List of (term, similarity_score) tuples.
        """
        if not query or not self._vocabulary or not self._model or n_terms <= 0:
            return []

        try:
            sim_result = self._compute_similarities(query)
            if sim_result is None:
                return []
            similarities, top_indices = sim_result
            query_lower = query.lower()
            results: list[tuple[str, float]] = []
            for idx in top_indices:
                if len(results) >= n_terms:
                    break
                term = self._vocabulary[idx]
                if re.search(r"\b" + re.escape(term.lower()) + r"\b", query_lower) or len(term) < 3:
                    continue
                results.append((term, round(float(similarities[idx]), 4)))

            return results

        except Exception:
            logger.debug("get_related_terms failed", exc_info=True)
            return []
