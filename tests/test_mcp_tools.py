import json

import pytest
from pydantic import ValidationError


def _make_result(chunk_id="x", text="hello", distance=0.25):
    from src.retriever import SearchResult

    return SearchResult(
        chunk_id=chunk_id,
        text=text,
        metadata={"subject": "Hi", "sender_email": "a@example.com"},
        distance=distance,
    )


class _BasicRetriever:
    """Minimal dummy retriever sufficient for most tool tests."""

    def search_filtered(self, query, top_k=10, **kwargs):
        return [_make_result()]

    def serialize_results(self, query, results):
        return {
            "query": query,
            "count": len(results),
            "results": [r.to_dict() for r in results],
        }

    def format_results_for_claude(self, results):
        return "formatted results"


@pytest.mark.asyncio
async def test_email_search_structured_tool_returns_json(monkeypatch):
    from src import mcp_server

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: _BasicRetriever())

    params = mcp_server.EmailSearchStructuredInput(query="hello", top_k=5)
    payload = await mcp_server.email_search_structured(params)
    data = json.loads(payload)

    assert data["query"] == "hello"
    assert data["count"] == 1
    assert data["results"][0]["chunk_id"] == "x"


@pytest.mark.asyncio
async def test_email_search_structured_forwards_new_filters(monkeypatch):
    from src import mcp_server

    captured = {}

    class DummyRetriever(_BasicRetriever):
        def search_filtered(self, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())

    params = mcp_server.EmailSearchStructuredInput(
        query="hello",
        top_k=5,
        subject="approval",
        folder="inbox",
        cc="finance",
        min_score=0.8,
    )
    payload = await mcp_server.email_search_structured(params)
    data = json.loads(payload)

    assert captured["subject"] == "approval"
    assert captured["folder"] == "inbox"
    assert captured["cc"] == "finance"
    assert captured["min_score"] == 0.8
    assert data["filters"]["subject"] == "approval"
    assert data["filters"]["folder"] == "inbox"
    assert data["filters"]["cc"] == "finance"
    assert data["filters"]["min_score"] == 0.8


@pytest.mark.asyncio
async def test_email_search_structured_emits_strict_json(monkeypatch):
    from src import mcp_server

    class DummyRetriever(_BasicRetriever):
        def search_filtered(self, query, top_k=10, **kwargs):
            return [_make_result(distance=float("nan"))]

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())

    params = mcp_server.EmailSearchStructuredInput(query="hello", top_k=5)
    payload = await mcp_server.email_search_structured(params)

    assert "NaN" not in payload
    assert "Infinity" not in payload


@pytest.mark.asyncio
async def test_email_list_senders_sanitizes_control_sequences(monkeypatch):
    from src import mcp_server

    class DummyRetriever:
        def list_senders(self, limit=30):
            return [
                {
                    "name": "Mallory\x1b]8;;https://evil.test\x07",
                    "email": "m\x07allory@example.com",
                    "count": 3,
                }
            ]

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())
    output = await mcp_server.email_list_senders(mcp_server.ListSendersInput(limit=10))

    assert "evil.test" not in output
    assert "\x1b" not in output
    assert "\x07" not in output


@pytest.mark.asyncio
async def test_email_search_sanitizes_control_sequences(monkeypatch):
    from src import mcp_server

    class DummyRetriever:
        def search(self, query, top_k=10):
            return []

        def format_results_for_claude(self, results):
            return "Unsafe \x1b]8;;https://evil.test\x07 link \x1b[31mred\x1b[0m"

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())
    output = await mcp_server.email_search(mcp_server.EmailSearchInput(query="security"))

    assert "evil.test" not in output
    assert "\x1b" not in output
    assert "\x07" not in output


@pytest.mark.asyncio
async def test_email_list_folders_returns_formatted_list(monkeypatch):
    from src import mcp_server

    class DummyRetriever:
        def list_folders(self):
            return [
                {"folder": "Inbox", "count": 42},
                {"folder": "Archive", "count": 7},
            ]

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())
    output = await mcp_server.email_list_folders()

    assert "Inbox" in output
    assert "42" in output
    assert "Archive" in output
    assert "7" in output


@pytest.mark.asyncio
async def test_email_list_folders_empty_archive(monkeypatch):
    from src import mcp_server

    class DummyRetriever:
        def list_folders(self):
            return []

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())
    output = await mcp_server.email_list_folders()

    assert "No folders" in output


@pytest.mark.asyncio
async def test_email_ingest_returns_stats_json(monkeypatch):
    from src import mcp_server

    fake_stats = {
        "emails_parsed": 10,
        "chunks_created": 15,
        "chunks_added": 15,
        "chunks_skipped": 0,
        "batches_written": 1,
        "total_in_db": 15,
        "dry_run": False,
        "elapsed_seconds": 1.2,
    }

    monkeypatch.setattr("src.ingest.ingest", lambda **kwargs: fake_stats)

    params = mcp_server.EmailIngestInput(olm_path="/tmp/test.olm")
    output = await mcp_server.email_ingest(params)
    data = json.loads(output)

    assert data["emails_parsed"] == 10
    assert data["chunks_added"] == 15


@pytest.mark.asyncio
async def test_email_ingest_handles_file_not_found(monkeypatch):
    from src import mcp_server

    def _raise(**kwargs):
        raise FileNotFoundError("not found")

    monkeypatch.setattr("src.ingest.ingest", _raise)

    params = mcp_server.EmailIngestInput(olm_path="/nonexistent.olm")
    output = await mcp_server.email_ingest(params)
    data = json.loads(output)

    assert "error" in data


def test_structured_input_rejects_invalid_dates():
    from src import mcp_server

    with pytest.raises(ValidationError):
        mcp_server.EmailSearchStructuredInput(
            query="hello",
            date_from="2024/01/01",
        )


def test_by_date_input_rejects_invalid_dates():
    from src import mcp_server

    with pytest.raises(ValidationError):
        mcp_server.EmailSearchByDateInput(
            query="hello",
            date_from="2024/01/01",
        )


def test_structured_input_rejects_invalid_date_order():
    from src import mcp_server

    with pytest.raises(ValidationError):
        mcp_server.EmailSearchStructuredInput(
            query="hello",
            date_from="2024-05-01",
            date_to="2024-01-01",
        )


def test_by_date_input_rejects_invalid_date_order():
    from src import mcp_server

    with pytest.raises(ValidationError):
        mcp_server.EmailSearchByDateInput(
            query="hello",
            date_from="2024-05-01",
            date_to="2024-01-01",
        )


def test_structured_input_rejects_invalid_min_score():
    from src import mcp_server

    with pytest.raises(ValidationError):
        mcp_server.EmailSearchStructuredInput(
            query="hello",
            min_score=1.2,
        )
