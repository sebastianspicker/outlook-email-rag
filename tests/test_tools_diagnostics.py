"""Tests for src/tools/diagnostics.py — admin and diagnostic tools.

Covers: email_admin with action='diagnostics', 'reingest_bodies',
'reembed', 'reingest_metadata', 'reingest_analytics', and invalid actions.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.mcp_server import _offload
from src.sanitization import sanitize_untrusted_text

# ── Shared Test Infrastructure ───────────────────────────────


class MockRetriever:
    """Minimal retriever stub with embedder attribute for diagnostics."""

    def __init__(self):
        self.embedder = MagicMock()
        self.embedder.device = "cpu"
        self.embedder._model = MagicMock()
        self.embedder.has_sparse = False
        self.embedder.has_colbert = False


class MockEmailDB:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def sparse_vector_count(self):
        return 42


class MockDeps:
    _retriever = MockRetriever()
    _email_db = MockEmailDB()

    @staticmethod
    def get_retriever():
        return MockDeps._retriever

    @staticmethod
    def get_email_db():
        return MockDeps._email_db

    offload = staticmethod(_offload)
    DB_UNAVAILABLE = json.dumps({"error": "SQLite database not available."})
    sanitize = staticmethod(sanitize_untrusted_text)

    @staticmethod
    def tool_annotations(title):
        return {"title": title}

    @staticmethod
    def write_tool_annotations(title):
        return {"title": title}

    @staticmethod
    def idempotent_write_annotations(title):
        return {"title": title}


class FakeMCP:
    def __init__(self):
        self._tools = {}

    def tool(self, name=None, annotations=None):
        def decorator(fn):
            self._tools[name] = fn
            return fn

        return decorator


def _register():
    from src.tools import diagnostics

    fake_mcp = FakeMCP()
    diagnostics.register(fake_mcp, MockDeps)
    return fake_mcp


# ── Tests ────────────────────────────────────────────────────


class TestDiagnostics:
    @pytest.mark.asyncio
    async def test_diagnostics_action(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        params = EmailAdminInput(action="diagnostics")
        result = await fn(params)
        data = json.loads(result)
        assert "embedding_model" in data
        assert "device" in data
        assert "mcp_profile" in data
        assert "mcp_budget" in data
        assert "sparse_vector_count" in data
        assert data["sparse_vector_count"] == 42

    @pytest.mark.asyncio
    async def test_diagnostics_without_sparse_count(self):
        """DB without sparse_vector_count method still works."""
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        old_db = MockDeps._email_db

        class MinimalDB:
            conn = sqlite3.connect(":memory:")

        MockDeps._email_db = MinimalDB()
        try:
            from src.mcp_models import EmailAdminInput

            params = EmailAdminInput(action="diagnostics")
            result = await fn(params)
            data = json.loads(result)
            assert data["sparse_vector_count"] == 0
        finally:
            MockDeps._email_db = old_db


class TestReingestBodies:
    @pytest.mark.asyncio
    async def test_reingest_bodies_happy_path(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        with patch("src.ingest.reingest_bodies") as mock_fn:
            mock_fn.return_value = {"updated": 10, "skipped": 5}
            params = EmailAdminInput(
                action="reingest_bodies",
                olm_path="/tmp/test.olm",
            )
            result = await fn(params)
            data = json.loads(result)
            assert data["updated"] == 10

    @pytest.mark.asyncio
    async def test_reingest_bodies_missing_olm_path(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        params = EmailAdminInput(action="reingest_bodies")
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data
        assert "olm_path" in data["error"]

    @pytest.mark.asyncio
    async def test_reingest_bodies_file_not_found(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        with patch("src.ingest.reingest_bodies", side_effect=FileNotFoundError):
            params = EmailAdminInput(
                action="reingest_bodies",
                olm_path="/nonexistent.olm",
            )
            result = await fn(params)
            data = json.loads(result)
            assert "error" in data

    @pytest.mark.asyncio
    async def test_reingest_bodies_generic_error(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        with patch("src.ingest.reingest_bodies", side_effect=RuntimeError("disk full")):
            params = EmailAdminInput(
                action="reingest_bodies",
                olm_path="/tmp/test.olm",
            )
            result = await fn(params)
            data = json.loads(result)
            assert "error" in data

    @pytest.mark.asyncio
    async def test_reingest_bodies_with_force(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        with patch("src.ingest.reingest_bodies") as mock_fn:
            mock_fn.return_value = {"updated": 20, "skipped": 0}
            params = EmailAdminInput(
                action="reingest_bodies",
                olm_path="/tmp/test.olm",
                force=True,
            )
            result = await fn(params)
            data = json.loads(result)
            assert data["updated"] == 20
            mock_fn.assert_called_once_with("/tmp/test.olm", force=True)


class TestReembed:
    @pytest.mark.asyncio
    async def test_reembed_happy_path(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        with patch("src.ingest.reembed") as mock_fn:
            mock_fn.return_value = {"chunks_embedded": 500}
            params = EmailAdminInput(action="reembed", batch_size=50)
            result = await fn(params)
            data = json.loads(result)
            assert data["chunks_embedded"] == 500
            mock_fn.assert_called_once_with(batch_size=50)

    @pytest.mark.asyncio
    async def test_reembed_error(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        with patch("src.ingest.reembed", side_effect=RuntimeError("OOM")):
            params = EmailAdminInput(action="reembed")
            result = await fn(params)
            data = json.loads(result)
            assert "error" in data


class TestReingestMetadata:
    @pytest.mark.asyncio
    async def test_reingest_metadata_happy_path(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        with patch("src.ingest.reingest_metadata") as mock_fn:
            mock_fn.return_value = {"updated": 15}
            params = EmailAdminInput(
                action="reingest_metadata",
                olm_path="/tmp/test.olm",
            )
            result = await fn(params)
            data = json.loads(result)
            assert data["updated"] == 15

    @pytest.mark.asyncio
    async def test_reingest_metadata_missing_olm_path(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        params = EmailAdminInput(action="reingest_metadata")
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data
        assert "olm_path" in data["error"]

    @pytest.mark.asyncio
    async def test_reingest_metadata_file_not_found(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        with patch("src.ingest.reingest_metadata", side_effect=FileNotFoundError):
            params = EmailAdminInput(
                action="reingest_metadata",
                olm_path="/nonexistent.olm",
            )
            result = await fn(params)
            data = json.loads(result)
            assert "error" in data

    @pytest.mark.asyncio
    async def test_reingest_metadata_generic_error(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        with patch("src.ingest.reingest_metadata", side_effect=RuntimeError("bad XML")):
            params = EmailAdminInput(
                action="reingest_metadata",
                olm_path="/tmp/test.olm",
            )
            result = await fn(params)
            data = json.loads(result)
            assert "error" in data


class TestReingestAnalytics:
    @pytest.mark.asyncio
    async def test_reingest_analytics_happy_path(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        with patch("src.ingest.reingest_analytics") as mock_fn:
            mock_fn.return_value = {"processed": 100}
            params = EmailAdminInput(action="reingest_analytics")
            result = await fn(params)
            data = json.loads(result)
            assert data["processed"] == 100

    @pytest.mark.asyncio
    async def test_reingest_analytics_error(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        with patch("src.ingest.reingest_analytics", side_effect=RuntimeError("model load failed")):
            params = EmailAdminInput(action="reingest_analytics")
            result = await fn(params)
            data = json.loads(result)
            assert "error" in data


class TestInvalidAction:
    @pytest.mark.asyncio
    async def test_invalid_action(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_admin"]
        from src.mcp_models import EmailAdminInput

        params = EmailAdminInput(action="destroy_everything")
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data
        assert "Invalid action" in data["error"]
