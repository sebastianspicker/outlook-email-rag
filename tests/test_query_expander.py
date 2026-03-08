"""Tests for query expansion."""

import numpy as np

from src.query_expander import QueryExpander


class _FakeModel:
    """Minimal mock of SentenceTransformer for testing."""

    def encode(self, texts, show_progress_bar=False):
        # Return deterministic embeddings based on text content
        embeddings = []
        for text in texts:
            # Simple hash-based embedding
            rng = np.random.RandomState(hash(text) % 2**31)
            embeddings.append(rng.randn(8).astype(np.float32))
        return np.array(embeddings)


class TestQueryExpander:
    def test_expand_adds_terms(self):
        vocab = ["quarterly report", "budget review", "team meeting",
                 "project deadline", "sales forecast"]
        expander = QueryExpander(model=_FakeModel(), vocabulary=vocab)
        result = expander.expand("budget", n_terms=2)
        # Should add terms from vocabulary
        assert "budget" in result
        # Original query should be preserved
        assert result.startswith("budget")
        # Should have more content than original
        assert len(result) > len("budget")

    def test_expand_skips_existing_terms(self):
        vocab = ["budget review", "quarterly report", "financial plan"]
        expander = QueryExpander(model=_FakeModel(), vocabulary=vocab)
        result = expander.expand("budget review", n_terms=2)
        # "budget review" should not be duplicated
        count = result.lower().count("budget review")
        assert count == 1

    def test_expand_empty_query(self):
        expander = QueryExpander(model=_FakeModel(), vocabulary=["a", "b"])
        assert expander.expand("") == ""
        assert expander.expand(None) is None

    def test_expand_no_vocabulary(self):
        expander = QueryExpander(model=_FakeModel(), vocabulary=[])
        assert expander.expand("test query") == "test query"

    def test_expand_no_model(self):
        expander = QueryExpander(model=None, vocabulary=["a", "b"])
        assert expander.expand("test") == "test"

    def test_set_vocabulary(self):
        expander = QueryExpander(model=_FakeModel())
        expander.set_vocabulary(["alpha", "bravo", "charlie"])
        result = expander.expand("hello world", n_terms=2)
        assert len(result) > len("hello world")

    def test_get_related_terms(self):
        vocab = ["quarterly report", "budget plan", "team sync",
                 "product launch", "customer feedback"]
        expander = QueryExpander(model=_FakeModel(), vocabulary=vocab)
        terms = expander.get_related_terms("budget", n_terms=3)
        assert len(terms) <= 3
        for term, score in terms:
            assert isinstance(term, str)
            assert isinstance(score, float)

    def test_get_related_terms_empty(self):
        expander = QueryExpander(model=_FakeModel())
        assert expander.get_related_terms("test") == []

    def test_get_related_terms_no_model(self):
        expander = QueryExpander(model=None, vocabulary=["a"])
        assert expander.get_related_terms("test") == []

    def test_n_terms_respected(self):
        vocab = [f"term_{i}" for i in range(20)]
        expander = QueryExpander(model=_FakeModel(), vocabulary=vocab)
        result = expander.expand("query", n_terms=1)
        # Original + 1 expanded term
        parts = result.split()
        assert len(parts) <= 3  # "query" + up to 1 term (which could be multi-word)

    def test_short_terms_skipped(self):
        vocab = ["ab", "xy", "project management", "deadline"]
        expander = QueryExpander(model=_FakeModel(), vocabulary=vocab)
        terms = expander.get_related_terms("planning", n_terms=5)
        # Terms shorter than 3 chars should be skipped
        for term, _ in terms:
            assert len(term) >= 3


class TestMCPSmartSearch:
    def test_smart_search_tool_importable(self):
        from src.tools import threads  # email_smart_search lives in threads module

        assert callable(threads.register)

    def test_smart_search_input(self):
        from src.mcp_models import SmartSearchInput

        inp = SmartSearchInput(query="budget discussion", top_k=5)
        assert inp.query == "budget discussion"
        assert inp.top_k == 5

    def test_structured_search_has_topic_id(self):
        from src.mcp_models import EmailSearchStructuredInput

        inp = EmailSearchStructuredInput(
            query="test", topic_id=3, cluster_id=5, expand_query=True
        )
        assert inp.topic_id == 3
        assert inp.cluster_id == 5
        assert inp.expand_query is True

    def test_structured_search_defaults(self):
        from src.mcp_models import EmailSearchStructuredInput

        inp = EmailSearchStructuredInput(query="test")
        assert inp.topic_id is None
        assert inp.cluster_id is None
        assert inp.expand_query is False


class TestCLINewFlags:
    def test_topic_flag(self):
        from src.cli import parse_args

        args = parse_args(["--query", "test", "--topic", "5"])
        assert args.topic == 5

    def test_cluster_id_flag(self):
        from src.cli import parse_args

        args = parse_args(["--query", "test", "--cluster-id", "3"])
        assert args.cluster_id == 3

    def test_expand_query_flag(self):
        from src.cli import parse_args

        args = parse_args(["--query", "test", "--expand-query"])
        assert args.expand_query is True

    def test_flags_require_query(self):
        import pytest

        from src.cli import parse_args

        with pytest.raises(SystemExit):
            parse_args(["--topic", "5"])

        with pytest.raises(SystemExit):
            parse_args(["--expand-query"])


class TestRetrieverSemanticFilters:
    def test_matches_allowed_uids(self):
        from src.retriever import EmailRetriever, SearchResult

        r = SearchResult(
            chunk_id="c1", text="test", metadata={"uid": "abc"}, distance=0.1
        )
        assert EmailRetriever._matches_allowed_uids(r, None) is True
        assert EmailRetriever._matches_allowed_uids(r, {"abc", "def"}) is True
        assert EmailRetriever._matches_allowed_uids(r, {"xyz"}) is False
        assert EmailRetriever._matches_allowed_uids(r, set()) is False
