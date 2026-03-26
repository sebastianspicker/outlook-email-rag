"""Extended tests for low-coverage MCP tool modules.

Tests cover: threads.py, reporting.py, temporal.py, data_quality.py,
browse.py, and scan.py. Each test mocks deps (retriever + email_db),
calls the async tool function, and asserts valid JSON with expected keys.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.mcp_server import _offload
from src.retriever import SearchResult
from src.sanitization import sanitize_untrusted_text

# ── Shared Test Infrastructure ───────────────────────────────


def _make_result(
    uid="uid-1",
    text="Please review the budget proposal.",
    subject="Budget Review",
    sender="alice@example.com",
    date="2025-06-01",
    conversation_id="conv-1",
    distance=0.2,
):
    return SearchResult(
        chunk_id=f"chunk_{uid}",
        text=text,
        metadata={
            "uid": uid,
            "subject": subject,
            "sender_email": sender,
            "sender_name": sender.split("@")[0].title(),
            "date": date,
            "conversation_id": conversation_id,
        },
        distance=distance,
    )


class MockRetriever:
    """Retriever stub supporting the methods used by thread/browse tools."""

    def search_by_thread(self, conversation_id=None, top_k=50):
        return [
            _make_result(uid="uid-1", text="We decided to go with vendor A."),
            _make_result(uid="uid-2", text="Please send the updated report by Friday.", sender="bob@example.com"),
        ]

    def search_filtered(self, query="", top_k=10, **kwargs):
        return [_make_result()]

    def format_results_for_claude(self, results):
        return "formatted results"

    def serialize_results(self, query, results):
        return {"query": query, "count": len(results), "results": []}

    def list_senders(self, limit=30):
        return [{"name": "Alice", "email": "alice@example.com", "count": 10}]


class MockEmailDB:
    """Minimal email database stub with an in-memory SQLite connection."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            "CREATE TABLE emails ("
            "uid TEXT PRIMARY KEY, subject TEXT, sender_email TEXT, "
            "sender_name TEXT, date TEXT, body_text TEXT, "
            "conversation_id TEXT, folder TEXT, "
            "detected_language TEXT, sentiment_label TEXT, sentiment_score REAL, "
            "ingestion_run_id TEXT)"
        )
        self.conn.execute(
            "INSERT INTO emails VALUES "
            "('uid-1', 'Budget Review', 'alice@example.com', 'Alice', "
            "'2025-06-01', 'We decided to go with vendor A.', 'conv-1', 'Inbox', "
            "'en', 'positive', 0.85, 'run-1')"
        )
        self.conn.execute(
            "INSERT INTO emails VALUES "
            "('uid-2', 'Budget Review', 'bob@example.com', 'Bob', "
            "'2025-06-02', 'Please send the updated report by Friday.', 'conv-1', 'Inbox', "
            "'en', 'neutral', 0.50, 'run-1')"
        )
        self.conn.commit()

    def get_email_full(self, uid):
        row = self.conn.execute("SELECT * FROM emails WHERE uid = ?", (uid,)).fetchone()
        if not row:
            return None
        return dict(row)

    def get_thread_emails(self, conversation_id):
        rows = self.conn.execute(
            "SELECT * FROM emails WHERE conversation_id = ? ORDER BY date",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_emails_paginated(
        self, offset=0, limit=10, folder=None, sender=None, category=None, sort_order="DESC", date_from=None, date_to=None
    ):
        return {
            "emails": [
                {"uid": "uid-1", "subject": "Budget Review", "sender_email": "alice@example.com", "date": "2025-06-01"},
            ],
            "total": 1,
            "offset": offset,
            "limit": limit,
        }

    def get_emails_full_batch(self, uids):
        result = {}
        for uid in uids:
            full = self.get_email_full(uid)
            if full:
                result[uid] = full
        return result

    def list_evidence(self, email_uid=None, limit=50):
        return {"items": []}

    def top_contacts(self, email, limit=5):
        return [{"email": "bob@example.com", "count": 5}]

    def category_counts(self):
        return [{"category": "Meeting", "count": 3}]

    def calendar_emails(self, date_from=None, date_to=None, limit=10):
        return [{"uid": "uid-1", "subject": "Calendar Invite", "date": "2025-06-01"}]

    def thread_by_topic(self, topic, limit=50):
        return [{"uid": "uid-1", "subject": "Budget Review", "date": "2025-06-01"}]

    def top_senders(self, limit=10):
        return [{"sender_email": "alice@example.com", "count": 10}]


class MockDeps:
    """Dependency injection for tool modules matching ToolDepsProto."""

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
    """Minimal MCP stub that captures tool registrations."""

    def __init__(self):
        self._tools = {}

    def tool(self, name=None, annotations=None):
        def decorator(fn):
            self._tools[name] = fn
            return fn

        return decorator


def _register_module(module):
    """Register a tool module with a FakeMCP and MockDeps, returning the FakeMCP."""
    fake_mcp = FakeMCP()
    module.register(fake_mcp, MockDeps)
    return fake_mcp


# ── threads.py tests ─────────────────────────────────────────


class TestThreadTools:
    @pytest.mark.asyncio
    async def test_thread_summary_returns_json(self):
        from src.tools import threads

        fake_mcp = _register_module(threads)
        fn = fake_mcp._tools["email_thread_summary"]

        from src.mcp_models import ThreadSummaryInput

        params = ThreadSummaryInput(conversation_id="conv-1", max_sentences=3)
        result = await fn(params)
        data = json.loads(result)

        assert "conversation_id" in data
        assert "summary" in data
        assert data["conversation_id"] == "conv-1"

    @pytest.mark.asyncio
    async def test_thread_summary_no_results(self):
        from src.tools import threads

        class EmptyRetriever(MockRetriever):
            def search_by_thread(self, **kwargs):
                return []

        fake_mcp = FakeMCP()
        old_retriever = MockDeps._retriever
        MockDeps._retriever = EmptyRetriever()
        try:
            threads.register(fake_mcp, MockDeps)
            fn = fake_mcp._tools["email_thread_summary"]

            from src.mcp_models import ThreadSummaryInput

            params = ThreadSummaryInput(conversation_id="nonexistent")
            result = await fn(params)
            data = json.loads(result)
            assert "error" in data
        finally:
            MockDeps._retriever = old_retriever

    @pytest.mark.asyncio
    async def test_action_items_by_conversation(self):
        from src.tools import threads

        fake_mcp = _register_module(threads)
        fn = fake_mcp._tools["email_action_items"]

        from src.mcp_models import ActionItemsInput

        params = ActionItemsInput(conversation_id="conv-1", limit=10)
        result = await fn(params)
        data = json.loads(result)

        assert "count" in data
        assert "items" in data
        assert isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_action_items_by_days(self):
        from src.tools import threads

        fake_mcp = _register_module(threads)
        fn = fake_mcp._tools["email_action_items"]

        from src.mcp_models import ActionItemsInput

        params = ActionItemsInput(days=30, limit=10)
        result = await fn(params)
        data = json.loads(result)

        assert "count" in data
        assert "items" in data
        assert isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_action_items_no_params_returns_error(self):
        from src.tools import threads

        fake_mcp = _register_module(threads)
        fn = fake_mcp._tools["email_action_items"]

        from src.mcp_models import ActionItemsInput

        params = ActionItemsInput(limit=10)
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_decisions_by_conversation(self):
        from src.tools import threads

        fake_mcp = _register_module(threads)
        fn = fake_mcp._tools["email_decisions"]

        from src.mcp_models import DecisionsInput

        params = DecisionsInput(conversation_id="conv-1", limit=10)
        result = await fn(params)
        data = json.loads(result)

        assert "count" in data
        assert "items" in data
        assert isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_decisions_by_days(self):
        from src.tools import threads

        fake_mcp = _register_module(threads)
        fn = fake_mcp._tools["email_decisions"]

        from src.mcp_models import DecisionsInput

        params = DecisionsInput(days=30, limit=10)
        result = await fn(params)
        data = json.loads(result)

        assert "count" in data
        assert "items" in data
        assert isinstance(data["items"], list)

    @pytest.mark.asyncio
    async def test_decisions_no_params_returns_error(self):
        from src.tools import threads

        fake_mcp = _register_module(threads)
        fn = fake_mcp._tools["email_decisions"]

        from src.mcp_models import DecisionsInput

        params = DecisionsInput(limit=10)
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_thread_lookup_by_conversation_id(self):
        from src.tools import threads

        fake_mcp = _register_module(threads)
        fn = fake_mcp._tools["email_thread_lookup"]

        from src.mcp_models import EmailThreadLookupInput

        params = EmailThreadLookupInput(conversation_id="conv-1")
        result = await fn(params)
        data = json.loads(result)
        assert "conversation_id" in data
        assert data["conversation_id"] == "conv-1"
        assert "count" in data

    @pytest.mark.asyncio
    async def test_thread_lookup_by_topic(self):
        from src.tools import threads

        fake_mcp = _register_module(threads)
        fn = fake_mcp._tools["email_thread_lookup"]

        from src.mcp_models import EmailThreadLookupInput

        params = EmailThreadLookupInput(thread_topic="Budget Review")
        result = await fn(params)
        data = json.loads(result)
        assert "thread_topic" in data
        assert "count" in data


# ── reporting.py tests ───────────────────────────────────────


class TestReportingTools:
    @pytest.mark.asyncio
    async def test_writing_analysis_single_sender(self):
        from src.tools import reporting

        fake_mcp = _register_module(reporting)
        fn = fake_mcp._tools["email_report"]

        from src.mcp_models import EmailReportInput

        params = EmailReportInput(type="writing", sender="alice@example.com", limit=10)
        result = await fn(params)
        data = json.loads(result)

        # Either returns a profile or an error (no emails long enough)
        assert isinstance(data, dict)

    @pytest.mark.asyncio
    async def test_writing_analysis_no_sender_compares_top(self):
        from src.tools import reporting

        fake_mcp = _register_module(reporting)
        fn = fake_mcp._tools["email_report"]

        from src.mcp_models import EmailReportInput

        params = EmailReportInput(type="writing", limit=5)
        result = await fn(params)
        data = json.loads(result)
        # Returns list of profiles or error
        assert isinstance(data, (list, dict))

    @pytest.mark.asyncio
    async def test_archive_report(self, tmp_path):
        from src.tools import reporting

        fake_mcp = _register_module(reporting)
        fn = fake_mcp._tools["email_report"]

        output_file = str(tmp_path / "report.html")

        from src.mcp_models import EmailReportInput

        params = EmailReportInput(type="archive", output_path=output_file)

        # Mock ReportGenerator since it requires full DB setup
        with patch("src.report_generator.ReportGenerator") as mock_gen_cls:
            mock_gen = MagicMock()
            mock_gen_cls.return_value = mock_gen
            result = await fn(params)
            data = json.loads(result)
            assert data.get("status") == "ok"
            assert data.get("output_path") == output_file

    @pytest.mark.asyncio
    async def test_invalid_report_type(self):
        from src.tools import reporting

        fake_mcp = _register_module(reporting)
        fn = fake_mcp._tools["email_report"]

        from src.mcp_models import EmailReportInput

        params = EmailReportInput(type="invalid_type")
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data


# ── temporal.py tests ────────────────────────────────────────


class TestTemporalTools:
    @pytest.mark.asyncio
    async def test_volume_analysis(self):
        from src.tools import temporal

        fake_mcp = _register_module(temporal)
        fn = fake_mcp._tools["email_temporal"]

        from src.mcp_models import EmailTemporalInput

        with patch("src.temporal_analysis.TemporalAnalyzer") as mock_cls:
            mock_analyzer = MagicMock()
            mock_analyzer.volume_over_time.return_value = [
                {"period": "2025-06-01", "count": 5},
                {"period": "2025-06-02", "count": 3},
            ]
            mock_cls.return_value = mock_analyzer

            params = EmailTemporalInput(analysis="volume", period="day")
            result = await fn(params)
            data = json.loads(result)

            assert isinstance(data, list)
            assert len(data) == 2
            assert data[0]["period"] == "2025-06-01"

    @pytest.mark.asyncio
    async def test_activity_heatmap(self):
        from src.tools import temporal

        fake_mcp = _register_module(temporal)
        fn = fake_mcp._tools["email_temporal"]

        from src.mcp_models import EmailTemporalInput

        with patch("src.temporal_analysis.TemporalAnalyzer") as mock_cls:
            mock_analyzer = MagicMock()
            mock_analyzer.activity_heatmap.return_value = {
                "Monday": {9: 5, 10: 3},
                "Tuesday": {14: 7},
            }
            mock_cls.return_value = mock_analyzer

            params = EmailTemporalInput(analysis="activity")
            result = await fn(params)
            data = json.loads(result)

            assert "Monday" in data
            assert "Tuesday" in data

    @pytest.mark.asyncio
    async def test_response_times(self):
        from src.tools import temporal

        fake_mcp = _register_module(temporal)
        fn = fake_mcp._tools["email_temporal"]

        from src.mcp_models import EmailTemporalInput

        with patch("src.temporal_analysis.TemporalAnalyzer") as mock_cls:
            mock_analyzer = MagicMock()
            mock_analyzer.response_times.return_value = [
                {"sender": "alice@example.com", "avg_hours": 2.5},
            ]
            mock_cls.return_value = mock_analyzer

            params = EmailTemporalInput(analysis="response_times", limit=10)
            result = await fn(params)
            data = json.loads(result)

            assert isinstance(data, list)
            assert data[0]["sender"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_invalid_analysis_type(self):
        from src.tools import temporal

        fake_mcp = _register_module(temporal)
        fn = fake_mcp._tools["email_temporal"]

        from src.mcp_models import EmailTemporalInput

        params = EmailTemporalInput(analysis="invalid")
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data


# ── data_quality.py tests ────────────────────────────────────


class TestDataQualityTools:
    @pytest.mark.asyncio
    async def test_language_stats(self):
        from src.tools import data_quality

        fake_mcp = _register_module(data_quality)
        fn = fake_mcp._tools["email_quality"]

        from src.mcp_models import EmailQualityInput

        params = EmailQualityInput(check="languages")
        result = await fn(params)
        data = json.loads(result)

        assert "languages" in data
        assert len(data["languages"]) > 0
        assert data["languages"][0]["language"] == "en"

    @pytest.mark.asyncio
    async def test_sentiment_overview(self):
        from src.tools import data_quality

        fake_mcp = _register_module(data_quality)
        fn = fake_mcp._tools["email_quality"]

        from src.mcp_models import EmailQualityInput

        params = EmailQualityInput(check="sentiment")
        result = await fn(params)
        data = json.loads(result)

        assert "sentiments" in data
        assert len(data["sentiments"]) > 0

    @pytest.mark.asyncio
    async def test_duplicate_detection(self):
        from src.tools import data_quality

        fake_mcp = _register_module(data_quality)
        fn = fake_mcp._tools["email_quality"]

        from src.mcp_models import EmailQualityInput

        with patch("src.dedup_detector.DuplicateDetector") as mock_cls:
            mock_detector = MagicMock()
            mock_detector.find_duplicates.return_value = [{"uid_a": "uid-1", "uid_b": "uid-2", "similarity": 0.92}]
            mock_cls.return_value = mock_detector

            params = EmailQualityInput(check="duplicates", threshold=0.85, limit=50)
            result = await fn(params)
            data = json.loads(result)

            assert "count" in data
            assert "duplicates" in data
            assert data["count"] == 1

    @pytest.mark.asyncio
    async def test_invalid_check_type(self):
        from src.tools import data_quality

        fake_mcp = _register_module(data_quality)
        fn = fake_mcp._tools["email_quality"]

        from src.mcp_models import EmailQualityInput

        params = EmailQualityInput(check="nonexistent")
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_languages_missing_column_graceful(self):
        """When the column doesn't exist, the tool returns an instructive error."""
        from src.tools import data_quality

        class NoDB:
            """Stub DB whose conn raises OperationalError on any query."""

            class conn:
                @staticmethod
                def execute(*args):
                    raise sqlite3.OperationalError("no such column: detected_language")

        old_db = MockDeps._email_db
        MockDeps._email_db = NoDB()
        try:
            fake_mcp = _register_module(data_quality)
            fn = fake_mcp._tools["email_quality"]

            from src.mcp_models import EmailQualityInput

            params = EmailQualityInput(check="languages")
            result = await fn(params)
            data = json.loads(result)
            assert "error" in data
            assert "reingest" in data["error"].lower() or "language" in data["error"].lower()
        finally:
            MockDeps._email_db = old_db


# ── browse.py tests ──────────────────────────────────────────


class TestBrowseTools:
    @pytest.mark.asyncio
    async def test_email_browse_default(self):
        from src.tools import browse

        fake_mcp = _register_module(browse)
        fn = fake_mcp._tools["email_browse"]

        from src.mcp_models import BrowseInput

        params = BrowseInput(limit=10)
        result = await fn(params)
        data = json.loads(result)

        assert "emails" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_email_browse_list_categories(self):
        from src.tools import browse

        fake_mcp = _register_module(browse)
        fn = fake_mcp._tools["email_browse"]

        from src.mcp_models import BrowseInput

        params = BrowseInput(list_categories=True)
        result = await fn(params)
        data = json.loads(result)

        assert "categories" in data

    @pytest.mark.asyncio
    async def test_email_browse_calendar(self):
        from src.tools import browse

        fake_mcp = _register_module(browse)
        fn = fake_mcp._tools["email_browse"]

        from src.mcp_models import BrowseInput

        params = BrowseInput(is_calendar=True, limit=5)
        result = await fn(params)
        data = json.loads(result)

        assert "emails" in data
        assert "count" in data

    @pytest.mark.asyncio
    async def test_email_deep_context_basic(self):
        from src.tools import browse

        fake_mcp = _register_module(browse)
        fn = fake_mcp._tools["email_deep_context"]

        from src.mcp_models import EmailDeepContextInput

        params = EmailDeepContextInput(
            uid="uid-1",
            include_thread=True,
            include_evidence=True,
            include_sender_stats=True,
        )
        result = await fn(params)
        data = json.loads(result)

        assert "email" in data
        assert data["email"]["uid"] == "uid-1"
        assert "thread" in data
        assert "evidence" in data
        assert "sender" in data

    @pytest.mark.asyncio
    async def test_email_deep_context_not_found(self):
        from src.tools import browse

        fake_mcp = _register_module(browse)
        fn = fake_mcp._tools["email_deep_context"]

        from src.mcp_models import EmailDeepContextInput

        params = EmailDeepContextInput(uid="nonexistent-uid")
        result = await fn(params)
        data = json.loads(result)

        assert "error" in data

    @pytest.mark.asyncio
    async def test_email_deep_context_no_thread(self):
        from src.tools import browse

        fake_mcp = _register_module(browse)
        fn = fake_mcp._tools["email_deep_context"]

        from src.mcp_models import EmailDeepContextInput

        params = EmailDeepContextInput(
            uid="uid-1",
            include_thread=False,
            include_evidence=False,
            include_sender_stats=False,
        )
        result = await fn(params)
        data = json.loads(result)

        assert "email" in data
        assert "thread" not in data
        assert "evidence" not in data
        assert "sender" not in data

    @pytest.mark.asyncio
    async def test_email_export_single_html(self):
        from src.tools import browse

        fake_mcp = _register_module(browse)
        fn = fake_mcp._tools["email_export"]

        from src.mcp_models import EmailExportInput

        with patch("src.email_exporter.EmailExporter") as mock_cls:
            mock_exporter = MagicMock()
            mock_exporter.export_single_html.return_value = {
                "html": "<html>email</html>",
                "uid": "uid-1",
            }
            mock_cls.return_value = mock_exporter

            params = EmailExportInput(uid="uid-1")
            result = await fn(params)
            data = json.loads(result)

            assert "uid" in data or "html" in data

    @pytest.mark.asyncio
    async def test_email_export_thread_html(self):
        from src.tools import browse

        fake_mcp = _register_module(browse)
        fn = fake_mcp._tools["email_export"]

        from src.mcp_models import EmailExportInput

        with patch("src.email_exporter.EmailExporter") as mock_cls:
            mock_exporter = MagicMock()
            mock_exporter.export_thread_html.return_value = {
                "html": "<html>thread</html>",
                "conversation_id": "conv-1",
                "email_count": 2,
            }
            mock_cls.return_value = mock_exporter

            params = EmailExportInput(conversation_id="conv-1")
            result = await fn(params)
            data = json.loads(result)

            assert "conversation_id" in data or "html" in data


# ── scan.py tests ────────────────────────────────────────────


class TestScanTools:
    def setup_method(self):
        """Reset scan sessions between tests."""
        from src import scan_session

        scan_session.reset_all_sessions()

    @pytest.mark.asyncio
    async def test_scan_flag_and_status(self):
        from src.tools import scan

        fake_mcp = FakeMCP()
        scan.register(fake_mcp, MockDeps)
        fn = fake_mcp._tools["email_scan"]

        from src.mcp_models import EmailScanInput

        # Flag some candidates
        params = EmailScanInput(
            action="flag",
            scan_id="test_case",
            uids=["uid-1", "uid-2"],
            label="relevant",
            phase=1,
        )
        result = await fn(params)
        data = json.loads(result)

        assert data["flagged"] == 2
        assert data["total_candidates"] == 2
        assert data["scan_id"] == "test_case"

        # Check status
        params = EmailScanInput(action="status", scan_id="test_case")
        result = await fn(params)
        data = json.loads(result)

        assert data["scan_id"] == "test_case"
        assert data["candidate_count"] == 2
        assert data["seen_count"] >= 2

    @pytest.mark.asyncio
    async def test_scan_candidates(self):
        from src.tools import scan

        fake_mcp = FakeMCP()
        scan.register(fake_mcp, MockDeps)
        fn = fake_mcp._tools["email_scan"]

        from src.mcp_models import EmailScanInput

        # Flag first
        await fn(
            EmailScanInput(
                action="flag",
                scan_id="case2",
                uids=["uid-a", "uid-b"],
                label="bossing",
                phase=1,
            )
        )

        # Get candidates
        params = EmailScanInput(action="candidates", scan_id="case2")
        result = await fn(params)
        data = json.loads(result)

        assert "candidates" in data
        assert data["count"] == 2
        assert all(c["label"] == "bossing" for c in data["candidates"])

    @pytest.mark.asyncio
    async def test_scan_candidates_filtered_by_label(self):
        from src.tools import scan

        fake_mcp = FakeMCP()
        scan.register(fake_mcp, MockDeps)
        fn = fake_mcp._tools["email_scan"]

        from src.mcp_models import EmailScanInput

        await fn(
            EmailScanInput(
                action="flag",
                scan_id="case3",
                uids=["uid-1"],
                label="bossing",
                phase=1,
            )
        )
        await fn(
            EmailScanInput(
                action="flag",
                scan_id="case3",
                uids=["uid-2"],
                label="harassment",
                phase=2,
            )
        )

        # Filter by label
        params = EmailScanInput(
            action="candidates",
            scan_id="case3",
            label="bossing",
        )
        result = await fn(params)
        data = json.loads(result)

        assert data["count"] == 1
        assert data["candidates"][0]["label"] == "bossing"

    @pytest.mark.asyncio
    async def test_scan_reset(self):
        from src.tools import scan

        fake_mcp = FakeMCP()
        scan.register(fake_mcp, MockDeps)
        fn = fake_mcp._tools["email_scan"]

        from src.mcp_models import EmailScanInput

        # Create a session
        await fn(
            EmailScanInput(
                action="flag",
                scan_id="reset_test",
                uids=["uid-1"],
                label="test",
                phase=1,
            )
        )

        # Reset it
        params = EmailScanInput(action="reset", scan_id="reset_test")
        result = await fn(params)
        data = json.loads(result)

        assert data["reset"] == "reset_test"
        assert data["existed"] is True

        # Status should fail now
        params = EmailScanInput(action="status", scan_id="reset_test")
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_scan_reset_all(self):
        from src.tools import scan

        fake_mcp = FakeMCP()
        scan.register(fake_mcp, MockDeps)
        fn = fake_mcp._tools["email_scan"]

        from src.mcp_models import EmailScanInput

        # Create sessions
        await fn(
            EmailScanInput(
                action="flag",
                scan_id="s1",
                uids=["uid-1"],
                label="test",
                phase=1,
            )
        )
        await fn(
            EmailScanInput(
                action="flag",
                scan_id="s2",
                uids=["uid-2"],
                label="test",
                phase=1,
            )
        )

        # Reset all
        params = EmailScanInput(action="reset", scan_id="__all__")
        result = await fn(params)
        data = json.loads(result)

        assert data["reset"] == "all"
        assert data["sessions_cleared"] >= 2

    @pytest.mark.asyncio
    async def test_scan_flag_missing_uids(self):
        from src.tools import scan

        fake_mcp = FakeMCP()
        scan.register(fake_mcp, MockDeps)
        fn = fake_mcp._tools["email_scan"]

        from src.mcp_models import EmailScanInput

        params = EmailScanInput(
            action="flag",
            scan_id="test",
            label="test",
        )
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_scan_flag_missing_label(self):
        from src.tools import scan

        fake_mcp = FakeMCP()
        scan.register(fake_mcp, MockDeps)
        fn = fake_mcp._tools["email_scan"]

        from src.mcp_models import EmailScanInput

        params = EmailScanInput(
            action="flag",
            scan_id="test",
            uids=["uid-1"],
        )
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data

    @pytest.mark.asyncio
    async def test_scan_invalid_action(self):
        from src.tools import scan

        fake_mcp = FakeMCP()
        scan.register(fake_mcp, MockDeps)
        fn = fake_mcp._tools["email_scan"]

        from src.mcp_models import EmailScanInput

        params = EmailScanInput(action="destroy", scan_id="test")
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data
