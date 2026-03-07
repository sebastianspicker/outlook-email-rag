import pytest

from src.retriever import EmailRetriever, SearchResult


def test_search_filtered_expands_candidate_window_when_needed():
    retriever = EmailRetriever.__new__(EmailRetriever)

    calls = {"n": 0}

    def _search(query, top_k=10, where=None):
        calls["n"] += 1
        # First call (small candidate window): no sender match
        # With dedup_multiplier=2, initial fetch = 1*8*2=16
        if top_k <= 16:
            return [
                SearchResult(
                    f"x{i}",
                    "text",
                    {"sender_email": f"user{i}@example.com", "uid": f"u{i}", "date": "2024-01-01"},
                    0.1,
                )
                for i in range(top_k)
            ]
        # Later call (expanded candidate window): includes desired sender
        return [
            SearchResult("a", "text", {"sender_email": "x@example.com", "uid": "ua", "date": "2024-01-01"}, 0.1),
            SearchResult("c", "text", {"sender_email": "target@example.com", "uid": "uc", "date": "2024-01-01"}, 0.3),
        ]

    retriever.search = _search

    results = retriever.search_filtered(query="budget", sender="target", top_k=1)

    assert len(results) == 1
    assert results[0].chunk_id == "c"
    assert calls["n"] >= 2


def test_search_filtered_rejects_excessive_top_k():
    retriever = EmailRetriever.__new__(EmailRetriever)

    with pytest.raises(ValueError):
        retriever.search_filtered(query="budget", top_k=2000)


def test_search_filtered_allows_large_top_k_with_filters():
    retriever = EmailRetriever()

    size = 250
    retriever.collection.add(
        ids=[f"id-{idx}" for idx in range(size)],
        embeddings=[[0.1, 0.2, 0.3] for _ in range(size)],
        documents=[f"email {idx}" for idx in range(size)],
        metadatas=[
            {
                "uid": f"u-{idx}",
                "sender_email": "target@example.com",
                "sender_name": "Target",
                "date": "2024-01-01T00:00:00Z",
            }
            for idx in range(size)
        ],
    )

    results = retriever.search_filtered(
        query="budget",
        sender="target@example.com",
        top_k=200,
    )

    assert len(results) == 200


def test_search_filtered_treats_blank_sender_as_no_filter():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult(
                chunk_id="r1",
                text="hello",
                metadata={"sender_email": "a@example.com", "date": "2024-01-01"},
                distance=0.2,
            )
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", sender="   ", top_k=1)

    assert len(results) == 1
    assert results[0].chunk_id == "r1"


def test_search_filtered_applies_subject_folder_and_min_score():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult(
                chunk_id="match",
                text="hello",
                metadata={
                    "sender_email": "a@example.com",
                    "subject": "Budget Approval Request",
                    "folder": "Inbox/Finance",
                    "date": "2024-01-01",
                },
                distance=0.15,  # score 0.85
            ),
            SearchResult(
                chunk_id="low-score",
                text="hello",
                metadata={
                    "sender_email": "a@example.com",
                    "subject": "Budget Approval Request",
                    "folder": "Inbox/Finance",
                    "date": "2024-01-01",
                },
                distance=0.30,  # score 0.70
            ),
            SearchResult(
                chunk_id="wrong-folder",
                text="hello",
                metadata={
                    "sender_email": "a@example.com",
                    "subject": "Budget Approval Request",
                    "folder": "Archive",
                    "date": "2024-01-01",
                },
                distance=0.10,  # score 0.90
            ),
            SearchResult(
                chunk_id="wrong-subject",
                text="hello",
                metadata={
                    "sender_email": "a@example.com",
                    "subject": "Travel Itinerary",
                    "folder": "Inbox/Finance",
                    "date": "2024-01-01",
                },
                distance=0.10,  # score 0.90
            ),
        ]

    retriever.search = _search

    results = retriever.search_filtered(
        query="budget",
        subject="approval",
        folder="finance",
        min_score=0.8,
        top_k=5,
    )

    assert len(results) == 1
    assert results[0].chunk_id == "match"


def test_search_filtered_rejects_invalid_min_score():
    retriever = EmailRetriever.__new__(EmailRetriever)

    with pytest.raises(ValueError):
        retriever.search_filtered(query="budget", min_score=1.5)


def test_search_filtered_sender_filter_handles_non_string_metadata():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult(
                chunk_id="r1",
                text="hello",
                metadata={"sender_email": None, "sender_name": 123, "date": "2024-01-01"},
                distance=0.2,
            )
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", sender="alice", top_k=1)

    assert results == []


def test_search_filtered_subject_filter_handles_none_metadata():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult(
                chunk_id="r1",
                text="hello",
                metadata={"subject": None, "folder": "Inbox", "date": "2024-01-01"},
                distance=0.2,
            )
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", subject="none", top_k=1)

    assert results == []


def test_search_filtered_applies_cc_filter():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult(
                chunk_id="match",
                text="hello",
                metadata={"cc": "finance-team@example.com, legal@example.com", "date": "2024-01-01"},
                distance=0.1,
            ),
            SearchResult(
                chunk_id="no-cc",
                text="hello",
                metadata={"cc": "", "date": "2024-01-01"},
                distance=0.2,
            ),
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", cc="finance-team", top_k=5)

    assert len(results) == 1
    assert results[0].chunk_id == "match"


def test_search_filtered_folder_filter_handles_none_metadata():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult(
                chunk_id="r1",
                text="hello",
                metadata={"subject": "Budget", "folder": None, "date": "2024-01-01"},
                distance=0.2,
            )
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", folder="none", top_k=1)

    assert results == []


# ── Phase 2: New filter tests ────────────────────────────────────


def test_search_filtered_applies_to_filter():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult(
                chunk_id="match",
                text="hello",
                metadata={"to": "alice@example.com, bob@example.com", "date": "2024-01-01"},
                distance=0.1,
            ),
            SearchResult(
                chunk_id="no-match",
                text="hello",
                metadata={"to": "carol@example.com", "date": "2024-01-01"},
                distance=0.2,
            ),
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", to="alice", top_k=5)

    assert len(results) == 1
    assert results[0].chunk_id == "match"


def test_search_filtered_to_filter_blank_is_no_filter():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult("r1", "text", {"to": "a@example.com", "date": "2024-01-01"}, 0.2),
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", to="   ", top_k=1)

    assert len(results) == 1


def test_search_filtered_applies_bcc_filter():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult(
                chunk_id="match",
                text="hello",
                metadata={"bcc": "secret@example.com", "date": "2024-01-01"},
                distance=0.1,
            ),
            SearchResult(
                chunk_id="no-match",
                text="hello",
                metadata={"bcc": "", "date": "2024-01-01"},
                distance=0.2,
            ),
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", bcc="secret", top_k=5)

    assert len(results) == 1
    assert results[0].chunk_id == "match"


def test_search_filtered_applies_has_attachments_true():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult(
                chunk_id="with-att",
                text="hello",
                metadata={"has_attachments": "True", "date": "2024-01-01"},
                distance=0.1,
            ),
            SearchResult(
                chunk_id="no-att",
                text="hello",
                metadata={"has_attachments": "False", "date": "2024-01-01"},
                distance=0.2,
            ),
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", has_attachments=True, top_k=5)

    assert len(results) == 1
    assert results[0].chunk_id == "with-att"


def test_search_filtered_applies_has_attachments_false():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult(
                chunk_id="with-att",
                text="hello",
                metadata={"has_attachments": "True", "date": "2024-01-01"},
                distance=0.1,
            ),
            SearchResult(
                chunk_id="no-att",
                text="hello",
                metadata={"has_attachments": "False", "date": "2024-01-01"},
                distance=0.2,
            ),
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", has_attachments=False, top_k=5)

    assert len(results) == 1
    assert results[0].chunk_id == "no-att"


def test_search_filtered_applies_priority_filter():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult(
                chunk_id="high-pri",
                text="urgent",
                metadata={"priority": "2", "date": "2024-01-01"},
                distance=0.1,
            ),
            SearchResult(
                chunk_id="low-pri",
                text="normal",
                metadata={"priority": "0", "date": "2024-01-01"},
                distance=0.2,
            ),
            SearchResult(
                chunk_id="no-pri",
                text="normal",
                metadata={"date": "2024-01-01"},
                distance=0.3,
            ),
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", priority=1, top_k=5)

    assert len(results) == 1
    assert results[0].chunk_id == "high-pri"


def test_search_filtered_priority_none_is_no_filter():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult("r1", "text", {"priority": "0", "date": "2024-01-01"}, 0.2),
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", priority=None, top_k=1)

    assert len(results) == 1


def test_search_filtered_has_attachments_none_is_no_filter():
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult("r1", "text", {"has_attachments": "True", "date": "2024-01-01"}, 0.2),
        ]

    retriever.search = _search
    results = retriever.search_filtered(query="budget", has_attachments=None, top_k=1)

    assert len(results) == 1


def test_search_filtered_combined_new_filters():
    """Multiple new filters applied together."""
    retriever = EmailRetriever.__new__(EmailRetriever)

    def _search(query, top_k=10, where=None):
        return [
            SearchResult(
                chunk_id="perfect",
                text="hello",
                metadata={
                    "to": "alice@example.com",
                    "has_attachments": "True",
                    "priority": "2",
                    "date": "2024-01-01",
                },
                distance=0.1,
            ),
            SearchResult(
                chunk_id="wrong-to",
                text="hello",
                metadata={
                    "to": "bob@example.com",
                    "has_attachments": "True",
                    "priority": "2",
                    "date": "2024-01-01",
                },
                distance=0.1,
            ),
            SearchResult(
                chunk_id="no-att",
                text="hello",
                metadata={
                    "to": "alice@example.com",
                    "has_attachments": "False",
                    "priority": "2",
                    "date": "2024-01-01",
                },
                distance=0.1,
            ),
        ]

    retriever.search = _search
    results = retriever.search_filtered(
        query="budget", to="alice", has_attachments=True, priority=1, top_k=5
    )

    assert len(results) == 1
    assert results[0].chunk_id == "perfect"
