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
    from src.mcp_models import EmailSearchStructuredInput
    from src.tools.search import email_search_structured

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: _BasicRetriever())

    params = EmailSearchStructuredInput(query="hello", top_k=5)
    payload = await email_search_structured(params)
    data = json.loads(payload)

    assert data["query"] == "hello"
    assert data["count"] == 1
    assert data["results"][0]["chunk_id"] == "x"


@pytest.mark.asyncio
async def test_email_search_structured_forwards_new_filters(monkeypatch):
    from src import mcp_server
    from src.mcp_models import EmailSearchStructuredInput
    from src.tools.search import email_search_structured

    captured = {}

    class DummyRetriever(_BasicRetriever):
        def search_filtered(self, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())

    params = EmailSearchStructuredInput(
        query="hello",
        top_k=5,
        subject="approval",
        folder="inbox",
        cc="finance",
        min_score=0.8,
    )
    payload = await email_search_structured(params)
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
    from src.mcp_models import EmailSearchStructuredInput
    from src.tools.search import email_search_structured

    class DummyRetriever(_BasicRetriever):
        def search_filtered(self, query, top_k=10, **kwargs):
            return [_make_result(distance=float("nan"))]

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())

    params = EmailSearchStructuredInput(query="hello", top_k=5)
    payload = await email_search_structured(params)

    assert "NaN" not in payload
    assert "Infinity" not in payload


@pytest.mark.asyncio
async def test_email_list_senders_returns_json(monkeypatch):
    from src import mcp_server
    from src.mcp_models import ListSendersInput
    from src.tools.search import email_list_senders

    class DummyRetriever:
        def list_senders(self, limit=30):
            return [
                {
                    "name": "Alice",
                    "email": "alice@example.com",
                    "count": 3,
                }
            ]

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())
    output = await email_list_senders(ListSendersInput(limit=10))
    data = json.loads(output)

    assert data["count"] == 1
    assert data["senders"][0]["name"] == "Alice"
    assert data["senders"][0]["count"] == 3


@pytest.mark.asyncio
async def test_email_list_folders_returns_json(monkeypatch):
    from src import mcp_server
    from src.tools.search import email_list_folders

    class DummyRetriever:
        def list_folders(self):
            return [
                {"folder": "Inbox", "count": 42},
                {"folder": "Archive", "count": 7},
            ]

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())
    output = await email_list_folders()
    data = json.loads(output)

    assert data["count"] == 2
    assert data["folders"][0]["folder"] == "Inbox"
    assert data["folders"][0]["count"] == 42
    assert data["folders"][1]["folder"] == "Archive"


@pytest.mark.asyncio
async def test_email_list_folders_empty_archive(monkeypatch):
    from src import mcp_server
    from src.tools.search import email_list_folders

    class DummyRetriever:
        def list_folders(self):
            return []

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())
    output = await email_list_folders()
    data = json.loads(output)

    assert data["count"] == 0
    assert data["folders"] == []


@pytest.mark.asyncio
async def test_email_ingest_returns_stats_json(monkeypatch):
    from src.mcp_models import EmailIngestInput
    from src.tools.search import email_ingest

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

    params = EmailIngestInput(olm_path="/tmp/test.olm")
    output = await email_ingest(params)
    data = json.loads(output)

    assert data["emails_parsed"] == 10
    assert data["chunks_added"] == 15


@pytest.mark.asyncio
async def test_email_ingest_handles_file_not_found(monkeypatch):
    from src.mcp_models import EmailIngestInput
    from src.tools.search import email_ingest

    def _raise(**kwargs):
        raise FileNotFoundError("not found")

    monkeypatch.setattr("src.ingest.ingest", _raise)

    params = EmailIngestInput(olm_path="/nonexistent.olm")
    output = await email_ingest(params)
    data = json.loads(output)

    assert "error" in data


def test_structured_input_rejects_invalid_dates():
    from src.mcp_models import EmailSearchStructuredInput

    with pytest.raises(ValidationError):
        EmailSearchStructuredInput(
            query="hello",
            date_from="2024/01/01",
        )


def test_structured_input_rejects_invalid_date_order():
    from src.mcp_models import EmailSearchStructuredInput

    with pytest.raises(ValidationError):
        EmailSearchStructuredInput(
            query="hello",
            date_from="2024-05-01",
            date_to="2024-01-01",
        )


def test_structured_input_rejects_invalid_min_score():
    from src.mcp_models import EmailSearchStructuredInput

    with pytest.raises(ValidationError):
        EmailSearchStructuredInput(
            query="hello",
            min_score=1.2,
        )


@pytest.mark.asyncio
async def test_email_search_structured_forwards_email_type(monkeypatch):
    from src import mcp_server
    from src.mcp_models import EmailSearchStructuredInput
    from src.tools.search import email_search_structured

    captured = {}

    class DummyRetriever(_BasicRetriever):
        def search_filtered(self, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())

    params = EmailSearchStructuredInput(
        query="hello",
        email_type="reply",
    )
    payload = await email_search_structured(params)
    data = json.loads(payload)

    assert captured["email_type"] == "reply"
    assert data["filters"]["email_type"] == "reply"


def test_structured_input_accepts_email_type():
    from src.mcp_models import EmailSearchStructuredInput

    params = EmailSearchStructuredInput(
        query="hello",
        email_type="forward",
    )
    assert params.email_type == "forward"


def test_ingest_input_accepts_extract_attachments_and_embed_images():
    from src.mcp_models import EmailIngestInput

    params = EmailIngestInput(
        olm_path="/tmp/test.olm",
        extract_attachments=True,
        embed_images=True,
    )
    assert params.extract_attachments is True
    assert params.embed_images is True


@pytest.mark.asyncio
async def test_email_diagnostics_returns_json(monkeypatch):
    from src.mcp_server import _offload
    from src.tools import diagnostics

    class DummyEmbedder:
        device = "cpu"
        _model = type("Model", (), {"__name__": "StubModel"})()
        has_sparse = False
        has_colbert = False

    class DummyRetriever:
        embedder = DummyEmbedder()
        _sparse_index = None

    class MockDeps:
        get_retriever = staticmethod(lambda: DummyRetriever())
        get_email_db = staticmethod(lambda: None)
        offload = staticmethod(_offload)

    monkeypatch.setattr(diagnostics, "_deps", MockDeps)

    output = await diagnostics.email_diagnostics(MockDeps)
    data = json.loads(output)

    assert "embedding_model" in data
    assert "device" in data
    assert "sparse_enabled" in data
    assert "colbert_rerank_enabled" in data
    assert "sparse_vector_count" in data
    assert "sparse_index_built" in data


def test_all_tool_modules_importable():
    """Smoke test: every tool module under src/tools/ imports cleanly."""
    from src.tools import (
        attachments,
        browse,
        data_quality,
        diagnostics,
        entities,
        evidence,
        network,
        reporting,
        temporal,
        threads,
        topics,
    )

    for module in [
        attachments,
        browse,
        data_quality,
        diagnostics,
        entities,
        evidence,
        network,
        reporting,
        temporal,
        threads,
        topics,
    ]:
        assert callable(module.register), f"{module.__name__} missing register()"


@pytest.mark.asyncio
async def test_offload_runs_sync_in_thread():
    """_offload should run a sync function without blocking the event loop."""
    from src.mcp_server import _offload

    result = await _offload(lambda: 42)
    assert result == 42


@pytest.mark.asyncio
async def test_email_search_structured_forwards_attachment_filters(monkeypatch):
    from src import mcp_server
    from src.mcp_models import EmailSearchStructuredInput
    from src.tools.search import email_search_structured

    captured = {}

    class DummyRetriever(_BasicRetriever):
        def search_filtered(self, **kwargs):
            captured.update(kwargs)
            return []

    monkeypatch.setattr(mcp_server, "get_retriever", lambda: DummyRetriever())

    params = EmailSearchStructuredInput(
        query="hello",
        attachment_name="report",
        attachment_type="pdf",
    )
    payload = await email_search_structured(params)
    data = json.loads(payload)

    assert captured["attachment_name"] == "report"
    assert captured["attachment_type"] == "pdf"
    assert data["filters"]["attachment_name"] == "report"
    assert data["filters"]["attachment_type"] == "pdf"


class TestAttachmentFilters:
    def test_matches_attachment_name_in_names(self):
        from src.result_filters import _matches_attachment_name
        from src.retriever import SearchResult

        r = SearchResult(chunk_id="x", text="", metadata={"attachment_names": "report.pdf, budget.xlsx"}, distance=0.1)
        assert _matches_attachment_name(r, "report") is True
        assert _matches_attachment_name(r, "slides") is False
        assert _matches_attachment_name(r, None) is True

    def test_matches_attachment_name_in_filename(self):
        from src.result_filters import _matches_attachment_name
        from src.retriever import SearchResult

        r = SearchResult(chunk_id="x", text="", metadata={"attachment_filename": "report.pdf"}, distance=0.1)
        assert _matches_attachment_name(r, "report") is True

    def test_matches_attachment_name_list_metadata(self):
        from src.result_filters import _matches_attachment_name
        from src.retriever import SearchResult

        r = SearchResult(chunk_id="x", text="", metadata={"attachment_names": ["report.pdf", "budget.xlsx"]}, distance=0.1)
        assert _matches_attachment_name(r, "budget") is True

    def test_matches_attachment_type(self):
        from src.result_filters import _matches_attachment_type
        from src.retriever import SearchResult

        r = SearchResult(chunk_id="x", text="", metadata={"attachment_names": "report.pdf, budget.xlsx"}, distance=0.1)
        assert _matches_attachment_type(r, "pdf") is True
        assert _matches_attachment_type(r, "xlsx") is True
        assert _matches_attachment_type(r, "docx") is False
        assert _matches_attachment_type(r, None) is True

    def test_matches_attachment_type_with_dot(self):
        from src.result_filters import _matches_attachment_type
        from src.retriever import SearchResult

        r = SearchResult(chunk_id="x", text="", metadata={"attachment_filename": "slides.pptx"}, distance=0.1)
        assert _matches_attachment_type(r, ".pptx") is True
        assert _matches_attachment_type(r, "pptx") is True

    def test_matches_attachment_type_no_substring_false_positive(self):
        """Filtering for .doc should NOT match .docx files."""
        from src.result_filters import _matches_attachment_type
        from src.retriever import SearchResult

        r = SearchResult(chunk_id="x", text="", metadata={"attachment_names": "report.docx"}, distance=0.1)
        assert _matches_attachment_type(r, "doc") is False
        assert _matches_attachment_type(r, "docx") is True

    def test_matches_category_no_substring_false_positive(self):
        """Filtering for 'urgent' should NOT match 'Non-Urgent'."""
        from src.result_filters import _matches_category
        from src.retriever import SearchResult

        r = SearchResult(chunk_id="x", text="", metadata={"categories": "Non-Urgent, Important"}, distance=0.1)
        assert _matches_category(r, "urgent") is False
        assert _matches_category(r, "Non-Urgent") is True
        assert _matches_category(r, "Important") is True
        assert _matches_category(r, "import") is False


@pytest.mark.asyncio
async def test_offload_with_args():
    """_offload passes positional and keyword arguments through."""
    from src.mcp_server import _offload

    result = await _offload(lambda x, y=0: x + y, 10, y=5)
    assert result == 15
