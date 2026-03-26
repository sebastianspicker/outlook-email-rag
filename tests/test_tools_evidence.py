"""Tests for src/tools/evidence.py — evidence management, custody, dossier tools.

Covers: evidence_add, evidence_add_batch, evidence_remove, evidence_update,
evidence_query, evidence_overview, evidence_get, evidence_verify,
evidence_export, custody_chain, email_provenance, evidence_provenance,
email_dossier.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from src.mcp_server import _offload
from src.sanitization import sanitize_untrusted_text

# ── Shared Test Infrastructure ───────────────────────────────


class MockEmailDB:
    """In-memory email database stub with evidence and custody methods."""

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
        self.conn.commit()
        self._next_evidence_id = 1
        self._evidence = {}

    def get_email_full(self, uid):
        row = self.conn.execute("SELECT * FROM emails WHERE uid = ?", (uid,)).fetchone()
        return dict(row) if row else None

    def add_evidence(self, email_uid, category, key_quote, summary, relevance, notes=""):
        eid = self._next_evidence_id
        self._next_evidence_id += 1
        item = {
            "id": eid,
            "email_uid": email_uid,
            "category": category,
            "key_quote": key_quote,
            "summary": summary,
            "relevance": relevance,
            "notes": notes,
            "verified": True,
        }
        self._evidence[eid] = item
        return item

    def get_evidence(self, evidence_id):
        return self._evidence.get(evidence_id)

    def update_evidence(self, evidence_id, **fields):
        item = self._evidence.get(evidence_id)
        if not item:
            return False
        for k, v in fields.items():
            if v is not None:
                item[k] = v
        return True

    def remove_evidence(self, evidence_id):
        return self._evidence.pop(evidence_id, None) is not None

    def list_evidence(self, category=None, min_relevance=None, email_uid=None, limit=25, offset=0):
        items = list(self._evidence.values())
        if category:
            items = [i for i in items if i["category"] == category]
        if min_relevance:
            items = [i for i in items if i["relevance"] >= min_relevance]
        if email_uid:
            items = [i for i in items if i["email_uid"] == email_uid]
        return {"items": items[offset : offset + limit], "total": len(items)}

    def search_evidence(self, query, category=None, min_relevance=None, limit=25):
        items = [i for i in self._evidence.values() if query.lower() in (i.get("summary", "") + i.get("key_quote", "")).lower()]
        return {"items": items[:limit], "total": len(items)}

    def evidence_timeline(self, category=None, min_relevance=None, limit=25, offset=0):
        items = list(self._evidence.values())
        return items[offset : offset + limit]

    def evidence_stats(self, category=None, min_relevance=None):
        return {"total": len(self._evidence), "verified": len(self._evidence)}

    def evidence_categories(self):
        cats = {}
        for item in self._evidence.values():
            cat = item["category"]
            cats[cat] = cats.get(cat, 0) + 1
        return [{"category": k, "count": v} for k, v in cats.items()]

    def verify_evidence_quotes(self):
        return {"total": len(self._evidence), "verified": len(self._evidence), "failed": 0}

    def get_custody_chain(self, target_type=None, target_id=None, action=None, limit=50):
        return [
            {
                "id": 1,
                "target_type": "evidence",
                "target_id": "1",
                "action": "evidence_add",
                "timestamp": "2025-06-01T00:00:00",
                "details": {"note": "added"},
                "content_hash": "abc123",
            },
        ]

    def email_provenance(self, email_uid):
        return {"email_uid": email_uid, "ingestion_run": "run-1", "custody_events": []}

    def evidence_provenance(self, evidence_id):
        return {"evidence_id": evidence_id, "source_email": "uid-1", "chain": []}

    def top_contacts(self, email, limit=5):
        return [{"email": "bob@example.com", "count": 5}]


class MockDeps:
    _email_db = MockEmailDB()

    @staticmethod
    def get_retriever():
        return MagicMock()

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
    from src.tools import evidence

    fake_mcp = FakeMCP()
    evidence.register(fake_mcp, MockDeps)
    return fake_mcp


# ── Tests ────────────────────────────────────────────────────


class TestEvidenceAdd:
    @pytest.mark.asyncio
    async def test_add_evidence_happy_path(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_add"]
        from src.mcp_models import EvidenceAddInput

        params = EvidenceAddInput(
            email_uid="uid-1",
            category="bossing",
            key_quote="go with vendor A",
            summary="Decided vendor",
            relevance=4,
            notes="test",
        )
        result = await fn(params)
        data = json.loads(result)
        assert data["id"] >= 1
        assert data["category"] == "bossing"

    @pytest.mark.asyncio
    async def test_add_evidence_value_error(self):
        """When db.add_evidence raises ValueError, tool returns JSON error."""
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_add"]
        old_db = MockDeps._email_db

        class FailDB(MockEmailDB):
            def add_evidence(self, **kwargs):
                raise ValueError("Quote not found in email body")

        MockDeps._email_db = FailDB()
        try:
            from src.mcp_models import EvidenceAddInput

            params = EvidenceAddInput(
                email_uid="uid-1",
                category="bossing",
                key_quote="nonexistent quote",
                summary="bad",
                relevance=3,
            )
            result = await fn(params)
            data = json.loads(result)
            assert "error" in data
        finally:
            MockDeps._email_db = old_db


class TestEvidenceAddBatch:
    @pytest.mark.asyncio
    async def test_batch_add_all_succeed(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_add_batch"]
        from src.mcp_models import EvidenceAddBatchInput, EvidenceAddInput

        items = [
            EvidenceAddInput(
                email_uid="uid-1",
                category="harassment",
                key_quote="quote one",
                summary="item one",
                relevance=3,
            ),
            EvidenceAddInput(
                email_uid="uid-1",
                category="bossing",
                key_quote="quote two",
                summary="item two",
                relevance=5,
            ),
        ]
        params = EvidenceAddBatchInput(items=items)
        result = await fn(params)
        data = json.loads(result)
        assert data["total_added"] == 2
        assert data["total_failed"] == 0
        assert len(data["added"]) == 2

    @pytest.mark.asyncio
    async def test_batch_add_partial_failure(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_add_batch"]
        old_db = MockDeps._email_db

        call_count = [0]

        class PartialFailDB(MockEmailDB):
            def add_evidence(self, **kwargs):
                call_count[0] += 1
                if call_count[0] == 2:
                    raise ValueError("Bad quote")
                return {"id": call_count[0], **kwargs}

        MockDeps._email_db = PartialFailDB()
        try:
            from src.mcp_models import EvidenceAddBatchInput, EvidenceAddInput

            items = [
                EvidenceAddInput(
                    email_uid="uid-1",
                    category="bossing",
                    key_quote="good",
                    summary="ok",
                    relevance=3,
                ),
                EvidenceAddInput(
                    email_uid="uid-1",
                    category="bossing",
                    key_quote="bad",
                    summary="fail",
                    relevance=2,
                ),
            ]
            params = EvidenceAddBatchInput(items=items)
            result = await fn(params)
            data = json.loads(result)
            assert data["total_added"] == 1
            assert data["total_failed"] == 1
        finally:
            MockDeps._email_db = old_db


class TestEvidenceQuery:
    @pytest.mark.asyncio
    async def test_list_mode(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_query"]
        from src.mcp_models import EvidenceQueryInput

        params = EvidenceQueryInput(limit=10)
        result = await fn(params)
        data = json.loads(result)
        assert "items" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_search_mode(self):
        # First add some evidence
        fake_mcp = _register()
        MockDeps._email_db.add_evidence(
            email_uid="uid-1",
            category="bossing",
            key_quote="unreasonable deadline",
            summary="Pressure tactics",
            relevance=4,
        )
        fn = fake_mcp._tools["evidence_query"]
        from src.mcp_models import EvidenceQueryInput

        params = EvidenceQueryInput(query="deadline", limit=10)
        result = await fn(params)
        data = json.loads(result)
        assert "items" in data

    @pytest.mark.asyncio
    async def test_timeline_mode(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_query"]
        from src.mcp_models import EvidenceQueryInput

        params = EvidenceQueryInput(sort="date", limit=10)
        result = await fn(params)
        data = json.loads(result)
        assert "items" in data

    @pytest.mark.asyncio
    async def test_compact_mode_strips_quotes(self):
        """Default include_quotes=False should strip key_quote into preview."""
        fake_mcp = _register()
        MockDeps._email_db.add_evidence(
            email_uid="uid-1",
            category="bossing",
            key_quote="A" * 100,
            summary="long quote",
            relevance=3,
        )
        fn = fake_mcp._tools["evidence_query"]
        from src.mcp_models import EvidenceQueryInput

        params = EvidenceQueryInput(include_quotes=False, limit=10)
        result = await fn(params)
        data = json.loads(result)
        for item in data["items"]:
            assert "quote_preview" in item
            assert "key_quote" not in item

    @pytest.mark.asyncio
    async def test_search_compact_mode(self):
        fake_mcp = _register()
        MockDeps._email_db.add_evidence(
            email_uid="uid-1",
            category="general",
            key_quote="B" * 100,
            summary="search compact",
            relevance=2,
        )
        fn = fake_mcp._tools["evidence_query"]
        from src.mcp_models import EvidenceQueryInput

        params = EvidenceQueryInput(query="compact", include_quotes=False, limit=10)
        result = await fn(params)
        data = json.loads(result)
        for item in data["items"]:
            assert "quote_preview" in item

    @pytest.mark.asyncio
    async def test_timeline_compact_mode(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_query"]
        from src.mcp_models import EvidenceQueryInput

        params = EvidenceQueryInput(sort="date", include_quotes=False, limit=10)
        result = await fn(params)
        data = json.loads(result)
        for item in data["items"]:
            assert "quote_preview" in item


class TestEvidenceGet:
    @pytest.mark.asyncio
    async def test_get_existing_evidence(self):
        fake_mcp = _register()
        # Seed evidence
        item = MockDeps._email_db.add_evidence(
            email_uid="uid-1",
            category="harassment",
            key_quote="hostile remark",
            summary="Hostile",
            relevance=5,
        )
        fn = fake_mcp._tools["evidence_get"]
        from src.mcp_models import EvidenceGetInput

        params = EvidenceGetInput(evidence_id=item["id"])
        result = await fn(params)
        data = json.loads(result)
        assert data["id"] == item["id"]
        assert data["category"] == "harassment"

    @pytest.mark.asyncio
    async def test_get_nonexistent_evidence(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_get"]
        from src.mcp_models import EvidenceGetInput

        params = EvidenceGetInput(evidence_id=99999)
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data


class TestEvidenceUpdate:
    @pytest.mark.asyncio
    async def test_update_existing(self):
        fake_mcp = _register()
        item = MockDeps._email_db.add_evidence(
            email_uid="uid-1",
            category="general",
            key_quote="test quote",
            summary="before",
            relevance=2,
        )
        fn = fake_mcp._tools["evidence_update"]
        from src.mcp_models import EvidenceUpdateInput

        params = EvidenceUpdateInput(
            evidence_id=item["id"],
            summary="after",
            relevance=4,
        )
        result = await fn(params)
        data = json.loads(result)
        assert data["summary"] == "after"
        assert data["relevance"] == 4

    @pytest.mark.asyncio
    async def test_update_nonexistent(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_update"]
        from src.mcp_models import EvidenceUpdateInput

        params = EvidenceUpdateInput(evidence_id=99999, summary="nope")
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data


class TestEvidenceRemove:
    @pytest.mark.asyncio
    async def test_remove_existing(self):
        fake_mcp = _register()
        item = MockDeps._email_db.add_evidence(
            email_uid="uid-1",
            category="general",
            key_quote="to remove",
            summary="will delete",
            relevance=1,
        )
        fn = fake_mcp._tools["evidence_remove"]
        from src.mcp_models import EvidenceRemoveInput

        params = EvidenceRemoveInput(evidence_id=item["id"])
        result = await fn(params)
        data = json.loads(result)
        assert data["removed"] == item["id"]

    @pytest.mark.asyncio
    async def test_remove_nonexistent(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_remove"]
        from src.mcp_models import EvidenceRemoveInput

        params = EvidenceRemoveInput(evidence_id=99999)
        result = await fn(params)
        data = json.loads(result)
        assert "error" in data


class TestEvidenceVerify:
    @pytest.mark.asyncio
    async def test_verify_returns_stats(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_verify"]
        result = await fn()
        data = json.loads(result)
        assert "total" in data
        assert "verified" in data


class TestEvidenceExport:
    @pytest.mark.asyncio
    async def test_export_html(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_export"]
        from src.mcp_models import EvidenceExportInput

        with patch("src.evidence_exporter.EvidenceExporter") as mock_cls:
            mock_exp = MagicMock()
            mock_exp.export_file.return_value = {"path": "out.html", "count": 3}
            mock_cls.return_value = mock_exp
            params = EvidenceExportInput(output_path="out.html", format="html")
            result = await fn(params)
            data = json.loads(result)
            assert data["path"] == "out.html"


class TestEvidenceOverview:
    @pytest.mark.asyncio
    async def test_overview_returns_stats_and_categories(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_overview"]
        from src.mcp_models import EvidenceOverviewInput

        params = EvidenceOverviewInput()
        result = await fn(params)
        data = json.loads(result)
        assert "stats" in data
        assert "categories" in data

    @pytest.mark.asyncio
    async def test_overview_with_filter(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_overview"]
        from src.mcp_models import EvidenceOverviewInput

        params = EvidenceOverviewInput(category="bossing", min_relevance=3)
        result = await fn(params)
        data = json.loads(result)
        assert "stats" in data


class TestCustodyChain:
    @pytest.mark.asyncio
    async def test_custody_chain_compact(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["custody_chain"]
        from src.mcp_models import CustodyChainInput

        params = CustodyChainInput(compact=True, limit=10)
        result = await fn(params)
        data = json.loads(result)
        assert "events" in data
        assert "count" in data
        # Compact mode strips details and content_hash
        for event in data["events"]:
            assert "details" not in event
            assert "content_hash" not in event

    @pytest.mark.asyncio
    async def test_custody_chain_full(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["custody_chain"]
        from src.mcp_models import CustodyChainInput

        params = CustodyChainInput(compact=False, limit=10)
        result = await fn(params)
        data = json.loads(result)
        assert "events" in data
        # Full mode preserves details
        assert data["events"][0]["details"] == {"note": "added"}

    @pytest.mark.asyncio
    async def test_custody_chain_with_filters(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["custody_chain"]
        from src.mcp_models import CustodyChainInput

        params = CustodyChainInput(
            target_type="evidence",
            target_id="1",
            action="evidence_add",
        )
        result = await fn(params)
        data = json.loads(result)
        assert data["count"] >= 0


class TestEmailProvenance:
    @pytest.mark.asyncio
    async def test_provenance_returns_data(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_provenance"]
        from src.mcp_models import EmailProvenanceInput

        params = EmailProvenanceInput(email_uid="uid-1")
        result = await fn(params)
        data = json.loads(result)
        assert data["email_uid"] == "uid-1"


class TestEvidenceProvenance:
    @pytest.mark.asyncio
    async def test_evidence_provenance_returns_data(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["evidence_provenance"]
        from src.mcp_models import EvidenceProvenanceInput

        params = EvidenceProvenanceInput(evidence_id=1)
        result = await fn(params)
        data = json.loads(result)
        assert data["evidence_id"] == 1


class TestEmailDossier:
    @pytest.mark.asyncio
    async def test_dossier_preview(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_dossier"]
        from src.mcp_models import EmailDossierInput

        with patch("src.dossier_generator.DossierGenerator") as mock_cls:
            mock_gen = MagicMock()
            mock_gen.preview.return_value = {"evidence_count": 5, "categories": ["bossing"]}
            mock_cls.return_value = mock_gen
            params = EmailDossierInput(preview_only=True)
            result = await fn(params)
            data = json.loads(result)
            assert data["evidence_count"] == 5

    @pytest.mark.asyncio
    async def test_dossier_generate(self):
        fake_mcp = _register()
        fn = fake_mcp._tools["email_dossier"]
        from src.mcp_models import EmailDossierInput

        with (
            patch("src.dossier_generator.DossierGenerator") as mock_gen_cls,
            patch("src.network_analysis.CommunicationNetwork") as mock_net_cls,
        ):
            mock_gen = MagicMock()
            mock_gen.generate_file.return_value = {"path": "dossier.html", "status": "ok"}
            mock_gen_cls.return_value = mock_gen
            mock_net_cls.return_value = MagicMock()

            params = EmailDossierInput(
                output_path="dossier.html",
                title="Test Dossier",
                case_reference="CASE-001",
            )
            result = await fn(params)
            data = json.loads(result)
            assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_dossier_generate_without_network(self):
        """Network failure is graceful — dossier still generates."""
        fake_mcp = _register()
        fn = fake_mcp._tools["email_dossier"]
        from src.mcp_models import EmailDossierInput

        with (
            patch("src.dossier_generator.DossierGenerator") as mock_gen_cls,
            patch("src.network_analysis.CommunicationNetwork", side_effect=RuntimeError("no graph")),
        ):
            mock_gen = MagicMock()
            mock_gen.generate_file.return_value = {"path": "dossier.html", "status": "ok"}
            mock_gen_cls.return_value = mock_gen

            params = EmailDossierInput(output_path="dossier.html")
            result = await fn(params)
            data = json.loads(result)
            assert data["status"] == "ok"
