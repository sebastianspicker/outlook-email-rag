# ruff: noqa: F401
import json
import sqlite3

import pytest
from pydantic import ValidationError

from src.config import get_settings

from .helpers.mcp_tool_fakes import _BasicRetriever, _make_result, _patch_search_deps


@pytest.mark.asyncio
async def test_email_search_structured_tool_returns_json(monkeypatch):
    from src.mcp_models import EmailSearchStructuredInput
    from src.tools.search import email_search_structured

    _patch_search_deps(monkeypatch, _BasicRetriever())

    params = EmailSearchStructuredInput(query="hello", top_k=5)
    payload = await email_search_structured(params)
    data = json.loads(payload)

    assert data["query"] == "hello"
    assert data["count"] == 1
    assert data["results"][0]["chunk_id"] == "x"


@pytest.mark.asyncio
async def test_email_answer_context_returns_ranked_evidence_bundle(monkeypatch):
    import src.tools.search as search_mod
    from src.mcp_models import EmailAnswerContextInput

    class DummyRetriever(_BasicRetriever):
        def search_filtered(self, query, top_k=10, **kwargs):
            assert query == "Who asked for the updated budget?"
            assert top_k == 3
            assert kwargs["sender"] == "alice@example.com"
            return [
                _make_result(
                    uid="uid-ctx-1",
                    chunk_id="chunk-1",
                    text="Please send the updated budget by Friday.",
                    distance=0.15,
                )
            ]

    class DummyDB:
        def get_emails_full_batch(self, uids):
            return {
                "uid-ctx-1": {
                    "uid": "uid-ctx-1",
                    "body_text": "Intro. Please send the updated budget by Friday. Thanks.",
                    "normalized_body_source": "body_text_html",
                    "forensic_body_text": "",
                    "forensic_body_source": "",
                }
            }

        conn = None

    class DummyDeps:
        @staticmethod
        def get_retriever():
            return DummyRetriever()

        @staticmethod
        def get_email_db():
            return DummyDB()

        @staticmethod
        async def offload(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        DB_UNAVAILABLE = json.dumps({"error": "SQLite database not available."})

        @staticmethod
        def sanitize(text: str) -> str:
            return text

        @staticmethod
        def tool_annotations(title: str):
            return {"title": title}

        @staticmethod
        def write_tool_annotations(title: str):
            return {"title": title}

        @staticmethod
        def idempotent_write_annotations(title: str):
            return {"title": title}

    monkeypatch.setattr(search_mod, "_deps", DummyDeps)

    params = EmailAnswerContextInput(
        question="Who asked for the updated budget?",
        max_results=3,
        sender="alice@example.com",
    )
    payload = await search_mod.email_answer_context(params)
    data = json.loads(payload)

    assert data["question"] == "Who asked for the updated budget?"
    assert data["count"] == 1
    assert data["candidates"][0]["rank"] == 1
    assert data["candidates"][0]["score"] == pytest.approx(0.85)
    assert data["candidates"][0]["snippet"] == "Please send the updated budget by Friday."
    assert data["candidates"][0]["follow_up"]["tool"] == "email_deep_context"
    assert data["search"]["sender"] == "alice@example.com"
    assert data["candidates"][0]["body_render_mode"] == "retrieval"
    assert data["candidates"][0]["body_render_source"] == "body_text_html"
    assert data["candidates"][0]["provenance"]["uid"] == "uid-ctx-1"
    assert data["candidates"][0]["provenance"]["snippet_start"] == 7
    assert data["candidates"][0]["provenance"]["snippet_end"] == 48
    assert data["candidates"][0]["provenance"]["segment_ordinal"] is None
    assert data["candidates"][0]["provenance"]["evidence_handle"].startswith("email:uid-ctx-1:retrieval:body_text_html:7:48")


@pytest.mark.asyncio
async def test_email_answer_context_forensic_mode_uses_forensic_body(monkeypatch):
    import src.tools.search as search_mod
    from src.mcp_models import EmailAnswerContextInput

    class DummyRetriever(_BasicRetriever):
        def search_filtered(self, query, top_k=10, **kwargs):
            return [
                _make_result(
                    uid="uid-forensic-1",
                    chunk_id="chunk-1",
                    text="Please send the updated budget by Friday.",
                    distance=0.15,
                )
            ]

    class DummyDB:
        def get_emails_full_batch(self, uids):
            return {
                "uid-forensic-1": {
                    "uid": "uid-forensic-1",
                    "body_text": "Intro. Please send the updated budget by Friday. Thanks.",
                    "normalized_body_source": "body_text_html",
                    "forensic_body_text": "Quoted header\nPlease send the updated budget by Friday.\nRegards",
                    "forensic_body_source": "raw_body_html",
                }
            }

        conn = None

    class DummyDeps:
        @staticmethod
        def get_retriever():
            return DummyRetriever()

        @staticmethod
        def get_email_db():
            return DummyDB()

        @staticmethod
        async def offload(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        DB_UNAVAILABLE = json.dumps({"error": "SQLite database not available."})

        @staticmethod
        def sanitize(text: str) -> str:
            return text

        @staticmethod
        def tool_annotations(title: str):
            return {"title": title}

        @staticmethod
        def write_tool_annotations(title: str):
            return {"title": title}

        @staticmethod
        def idempotent_write_annotations(title: str):
            return {"title": title}

    monkeypatch.setattr(search_mod, "_deps", DummyDeps)

    payload = await search_mod.email_answer_context(
        EmailAnswerContextInput(
            question="Who asked for the updated budget?",
            evidence_mode="forensic",
        )
    )
    data = json.loads(payload)

    assert data["evidence_mode"]["requested"] == "forensic"
    assert data["candidates"][0]["body_render_mode"] == "forensic"
    assert data["candidates"][0]["body_render_source"] == "raw_body_html"
    assert data["candidates"][0]["snippet"] == "Please send the updated budget by Friday."
    assert data["candidates"][0]["verification_status"] == "forensic_exact"
    assert data["candidates"][0]["provenance"]["evidence_handle"].startswith("email:uid-forensic-1:forensic:raw_body_html:")


@pytest.mark.asyncio
async def test_email_answer_context_hybrid_mode_falls_back_explicitly_when_forensic_missing(monkeypatch):
    import src.tools.search as search_mod
    from src.mcp_models import EmailAnswerContextInput

    class DummyRetriever(_BasicRetriever):
        def search_filtered(self, query, top_k=10, **kwargs):
            return [
                _make_result(
                    uid="uid-hybrid-1",
                    chunk_id="chunk-1",
                    text="Please send the updated budget by Friday.",
                    distance=0.15,
                )
            ]

    class DummyDB:
        def get_emails_full_batch(self, uids):
            return {
                "uid-hybrid-1": {
                    "uid": "uid-hybrid-1",
                    "body_text": "Intro. Please send the updated budget by Friday. Thanks.",
                    "normalized_body_source": "body_text_html",
                    "forensic_body_text": "",
                    "forensic_body_source": "",
                }
            }

        conn = None

    class DummyDeps:
        @staticmethod
        def get_retriever():
            return DummyRetriever()

        @staticmethod
        def get_email_db():
            return DummyDB()

        @staticmethod
        async def offload(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        DB_UNAVAILABLE = json.dumps({"error": "SQLite database not available."})

        @staticmethod
        def sanitize(text: str) -> str:
            return text

        @staticmethod
        def tool_annotations(title: str):
            return {"title": title}

        @staticmethod
        def write_tool_annotations(title: str):
            return {"title": title}

        @staticmethod
        def idempotent_write_annotations(title: str):
            return {"title": title}

    monkeypatch.setattr(search_mod, "_deps", DummyDeps)

    payload = await search_mod.email_answer_context(
        EmailAnswerContextInput(
            question="Who asked for the updated budget?",
            evidence_mode="hybrid",
        )
    )
    data = json.loads(payload)

    assert data["evidence_mode"]["requested"] == "hybrid"
    assert data["candidates"][0]["body_render_mode"] == "retrieval"
    assert data["candidates"][0]["verification_status"] == "hybrid_fallback_retrieval"
    assert data["candidates"][0]["provenance"]["evidence_handle"].startswith("email:uid-hybrid-1:retrieval:body_text_html:")


@pytest.mark.asyncio
async def test_email_answer_context_handles_no_results(monkeypatch):
    import src.tools.search as search_mod
    from src.mcp_models import EmailAnswerContextInput

    class DummyRetriever(_BasicRetriever):
        def search_filtered(self, query, top_k=10, **kwargs):
            return []

    class DummyDeps:
        @staticmethod
        def get_retriever():
            return DummyRetriever()

        @staticmethod
        def get_email_db():
            return None

        @staticmethod
        async def offload(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        DB_UNAVAILABLE = json.dumps({"error": "SQLite database not available."})

        @staticmethod
        def sanitize(text: str) -> str:
            return text

        @staticmethod
        def tool_annotations(title: str):
            return {"title": title}

        @staticmethod
        def write_tool_annotations(title: str):
            return {"title": title}

        @staticmethod
        def idempotent_write_annotations(title: str):
            return {"title": title}

    monkeypatch.setattr(search_mod, "_deps", DummyDeps)

    params = EmailAnswerContextInput(question="Was there any update on the rack move?")
    payload = await search_mod.email_answer_context(params)
    data = json.loads(payload)

    assert data["question"] == "Was there any update on the rack move?"
    assert data["count"] == 0
    assert data["candidates"] == []
    assert data["attachment_candidates"] == []
    assert "message" in data
