"""TF-IDF keyword extraction from email text."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Default English stopwords (small set, no heavy deps)
_STOP_WORDS = frozenset({
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "was", "are", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "dare",
    "it", "its", "this", "that", "these", "those", "i", "me", "my", "we",
    "our", "you", "your", "he", "him", "his", "she", "her", "they", "them",
    "their", "what", "which", "who", "whom", "when", "where", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other", "some",
    "such", "no", "not", "only", "own", "same", "so", "than", "too", "very",
    "just", "because", "as", "until", "while", "about", "between", "through",
    "during", "before", "after", "above", "below", "up", "down", "out",
    "off", "over", "under", "again", "further", "then", "once", "here",
    "there", "if", "also", "re", "ve", "ll", "am", "don", "didn", "doesn",
    "hadn", "hasn", "haven", "isn", "wasn", "weren", "won", "wouldn",
    "couldn", "shouldn", "ain", "aren", "ll", "ve", "re",
})


class KeywordExtractor:
    """Extract keywords from text using TF-IDF.

    Uses scikit-learn's TfidfVectorizer for efficient keyword extraction.
    scikit-learn is already a transitive dependency (via sentence-transformers).
    """

    def __init__(
        self,
        min_df: int = 1,
        max_df: float = 0.95,
        ngram_range: tuple[int, int] = (1, 2),
        stop_words: frozenset[str] | None = None,
    ):
        self.min_df = min_df
        self.max_df = max_df
        self.ngram_range = ngram_range
        self.stop_words = stop_words or _STOP_WORDS
        self._vectorizer = None

    def _get_vectorizer(self):
        """Create or return cached TF-IDF vectorizer."""
        if self._vectorizer is None:
            from sklearn.feature_extraction.text import TfidfVectorizer

            self._vectorizer = TfidfVectorizer(
                min_df=self.min_df,
                max_df=self.max_df,
                ngram_range=self.ngram_range,
                stop_words=list(self.stop_words),
                sublinear_tf=True,
                max_features=10000,
            )
        return self._vectorizer

    def extract_keywords(
        self, text: str, top_n: int = 10
    ) -> list[tuple[str, float]]:
        """Extract top keywords from a single document.

        Args:
            text: Document text.
            top_n: Number of top keywords to return.

        Returns:
            List of (keyword, tfidf_score) tuples, sorted by score descending.
        """
        if not text or not text.strip():
            return []

        from sklearn.feature_extraction.text import TfidfVectorizer

        # Single-document vectorizer (can't use corpus vectorizer)
        vec = TfidfVectorizer(
            ngram_range=self.ngram_range,
            stop_words=list(self.stop_words),
            sublinear_tf=True,
        )
        try:
            tfidf_matrix = vec.fit_transform([text])
        except ValueError:
            # Empty vocabulary (text has only stop words)
            return []

        feature_names = vec.get_feature_names_out()
        scores = tfidf_matrix.toarray()[0]

        # Sort by score descending
        indices = scores.argsort()[::-1][:top_n]
        return [(feature_names[i], round(float(scores[i]), 4)) for i in indices if scores[i] > 0]

    def extract_corpus_keywords(
        self, texts: list[str], top_n: int = 20
    ) -> list[tuple[str, float]]:
        """Extract top keywords from a corpus of documents.

        Uses IDF weighting across the corpus to find distinctive terms.

        Args:
            texts: List of document texts.
            top_n: Number of top keywords to return.

        Returns:
            List of (keyword, avg_tfidf_score) tuples.
        """
        if not texts:
            return []

        # Filter empty texts
        valid_texts = [t for t in texts if t and t.strip()]
        if not valid_texts:
            return []

        vectorizer = self._get_vectorizer()
        try:
            tfidf_matrix = vectorizer.fit_transform(valid_texts)
        except ValueError:
            return []

        feature_names = vectorizer.get_feature_names_out()
        # Average TF-IDF score across all documents
        avg_scores = tfidf_matrix.mean(axis=0).A1

        indices = avg_scores.argsort()[::-1][:top_n]
        return [
            (feature_names[i], round(float(avg_scores[i]), 4))
            for i in indices
            if avg_scores[i] > 0
        ]

    def extract_per_document(
        self, texts: list[str], top_n: int = 10
    ) -> list[list[tuple[str, float]]]:
        """Extract top keywords for each document in a pre-fitted corpus.

        Call extract_corpus_keywords() first to fit the vectorizer.

        Args:
            texts: List of document texts (same corpus used for fitting).
            top_n: Keywords per document.

        Returns:
            List of keyword lists, one per document.
        """
        if not texts:
            return []

        vectorizer = self._get_vectorizer()
        valid_texts = [t for t in texts if t and t.strip()]
        if not valid_texts:
            return [[] for _ in texts]

        try:
            tfidf_matrix = vectorizer.fit_transform(valid_texts)
        except ValueError:
            return [[] for _ in texts]

        feature_names = vectorizer.get_feature_names_out()
        results = []

        for i in range(tfidf_matrix.shape[0]):
            row = tfidf_matrix[i].toarray()[0]
            indices = row.argsort()[::-1][:top_n]
            keywords = [
                (feature_names[j], round(float(row[j]), 4))
                for j in indices
                if row[j] > 0
            ]
            results.append(keywords)

        return results
