import json

import pytest
from pydantic import ValidationError


@pytest.mark.asyncio
async def test_email_search_structured_tool_returns_json(monkeypatch):
    from src import mcp_server
    from src.retriever import SearchResult

    class DummyRetriever:
        def search_filtered(self, query, top_k=10, sender=None, date_from=None, date_to=None):
            return [
                SearchResult(
                    chunk_id="x",
                    text="hello",
                    metadata={"subject": "Hi", "sender_email": "a@example.com"},
                    distance=0.25,
                )
            ]

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())

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

    class DummyRetriever:
        def search_filtered(self, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())

    params = mcp_server.EmailSearchStructuredInput(
        query="hello",
        top_k=5,
        subject="approval",
        folder="inbox",
        min_score=0.8,
    )
    payload = await mcp_server.email_search_structured(params)
    data = json.loads(payload)

    assert captured["subject"] == "approval"
    assert captured["folder"] == "inbox"
    assert captured["min_score"] == 0.8
    assert data["filters"]["subject"] == "approval"
    assert data["filters"]["folder"] == "inbox"
    assert data["filters"]["min_score"] == 0.8


@pytest.mark.asyncio
async def test_email_search_structured_emits_strict_json(monkeypatch):
    from src import mcp_server
    from src.retriever import SearchResult

    class DummyRetriever:
        def search_filtered(self, query, top_k=10, sender=None, date_from=None, date_to=None):
            return [
                SearchResult(
                    chunk_id="x",
                    text="hello",
                    metadata={"subject": "Hi", "sender_email": "a@example.com"},
                    distance=float("nan"),
                )
            ]

        def serialize_results(self, query, results):
            return {
                "query": query,
                "count": len(results),
                "results": [result.to_dict() for result in results],
            }

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
