"""Tests for progressive multi-pass scan sessions."""

from __future__ import annotations

import json
import time

import pytest


@pytest.fixture(autouse=True)
def _clean_sessions():
    """Ensure scan sessions are clean between tests."""
    from src.scan_session import _sessions

    _sessions.clear()
    yield
    _sessions.clear()


# ── Unit tests for scan_session module ────────────────────────


class TestScanSessionCore:
    def test_auto_create_on_first_access(self):
        from src.scan_session import get_session

        s = get_session("test1")
        assert s is not None
        assert s.scan_id == "test1"
        assert len(s.seen_uids) == 0
        assert len(s.candidates) == 0

    def test_get_nonexistent_returns_none_without_auto_create(self):
        from src.scan_session import get_session

        s = get_session("nonexistent", auto_create=False)
        assert s is None

    def test_same_scan_id_returns_same_session(self):
        from src.scan_session import get_session

        s1 = get_session("test")
        s1.seen_uids.add("uid1")
        s2 = get_session("test")
        assert "uid1" in s2.seen_uids

    def test_different_scan_ids_are_independent(self):
        from src.scan_session import get_session

        s1 = get_session("a")
        s1.seen_uids.add("uid1")
        s2 = get_session("b")
        assert "uid1" not in s2.seen_uids


class TestFilterSeen:
    def _make_result(self, uid, score=0.5):
        """Create a minimal SearchResult-like object."""

        class FakeResult:
            def __init__(self, uid, score):
                self.metadata = {"uid": uid}
                self.score = score

        return FakeResult(uid, score)

    def test_first_call_returns_all(self):
        from src.scan_session import filter_seen

        results = [self._make_result("uid1"), self._make_result("uid2")]
        new, meta = filter_seen("test", results)
        assert len(new) == 2
        assert meta["new_count"] == 2
        assert meta["excluded_count"] == 0
        assert meta["seen_total"] == 2

    def test_second_call_excludes_seen(self):
        from src.scan_session import filter_seen

        results1 = [self._make_result("uid1"), self._make_result("uid2")]
        filter_seen("test", results1)

        results2 = [
            self._make_result("uid2"),  # seen
            self._make_result("uid3"),  # new
        ]
        new, meta = filter_seen("test", results2)
        assert len(new) == 1
        assert new[0].metadata["uid"] == "uid3"
        assert meta["new_count"] == 1
        assert meta["excluded_count"] == 1
        assert meta["seen_total"] == 3

    def test_filter_seen_with_dicts(self):
        from src.scan_session import filter_seen

        results = [{"uid": "uid1", "text": "hello"}, {"uid": "uid2", "text": "world"}]
        new, _meta = filter_seen("test", results)
        assert len(new) == 2

        results2 = [{"uid": "uid1", "text": "hello again"}]
        new2, meta2 = filter_seen("test", results2)
        assert len(new2) == 0
        assert meta2["excluded_count"] == 1

    def test_filter_seen_handles_missing_uid(self):
        from src.scan_session import filter_seen

        results = [{"text": "no uid"}, self._make_result("uid1")]
        new, meta = filter_seen("test", results)
        # Item without UID passes through
        assert len(new) == 2
        assert meta["new_count"] == 2

    def test_scan_metadata_fields(self):
        from src.scan_session import filter_seen

        results = [self._make_result("uid1")]
        _, meta = filter_seen("test_session", results)
        assert meta["scan_id"] == "test_session"
        assert "new_count" in meta
        assert "excluded_count" in meta
        assert "seen_total" in meta
        assert "candidates_count" in meta


class TestCandidates:
    def test_flag_candidates_basic(self):
        from src.scan_session import flag_candidates, get_session

        added, total = flag_candidates("test", ["uid1", "uid2"], label="bossing", phase=1, score=0.9)
        assert added == 2
        assert total == 2
        s = get_session("test")
        assert "uid1" in s.candidates
        assert s.candidates["uid1"].label == "bossing"
        assert s.candidates["uid1"].phase == 1
        assert s.candidates["uid1"].score == 0.9

    def test_flag_adds_to_seen(self):
        from src.scan_session import flag_candidates, get_session

        flag_candidates("test", ["uid1"], label="relevant")
        s = get_session("test")
        assert "uid1" in s.seen_uids

    def test_flag_skips_existing(self):
        from src.scan_session import flag_candidates, get_session

        flag_candidates("test", ["uid1"], label="maybe", phase=1)
        added, total = flag_candidates("test", ["uid1"], label="bossing", phase=2)
        assert added == 0  # not newly added -- already flagged
        assert total == 1
        s = get_session("test")
        # Original label is preserved (skip, not overwrite)
        assert s.candidates["uid1"].label == "maybe"
        assert s.candidates["uid1"].phase == 1

    def test_get_candidates_unfiltered(self):
        from src.scan_session import flag_candidates, get_candidates

        flag_candidates("test", ["uid1"], label="bossing", phase=1)
        flag_candidates("test", ["uid2"], label="harassment", phase=2)
        candidates = get_candidates("test")
        assert len(candidates) == 2

    def test_get_candidates_filter_by_label(self):
        from src.scan_session import flag_candidates, get_candidates

        flag_candidates("test", ["uid1"], label="bossing")
        flag_candidates("test", ["uid2"], label="harassment")
        candidates = get_candidates("test", label="bossing")
        assert len(candidates) == 1
        assert candidates[0]["uid"] == "uid1"

    def test_get_candidates_filter_by_phase(self):
        from src.scan_session import flag_candidates, get_candidates

        flag_candidates("test", ["uid1"], label="bossing", phase=1)
        flag_candidates("test", ["uid2"], label="bossing", phase=2)
        candidates = get_candidates("test", phase=2)
        assert len(candidates) == 1
        assert candidates[0]["uid"] == "uid2"

    def test_get_candidates_empty_session(self):
        from src.scan_session import get_candidates

        candidates = get_candidates("nonexistent")
        assert candidates == []


class TestSessionStatus:
    def test_status_fields(self):
        from src.scan_session import flag_candidates, get_session, session_status

        get_session("test").seen_uids.update(["uid1", "uid2", "uid3"])
        flag_candidates("test", ["uid1"], label="bossing", phase=1)
        flag_candidates("test", ["uid2"], label="harassment", phase=2)
        status = session_status("test")
        assert status["scan_id"] == "test"
        assert status["seen_count"] == 3
        assert status["candidate_count"] == 2
        assert status["candidates_by_label"] == {"bossing": 1, "harassment": 1}
        assert status["candidates_by_phase"] == {1: 1, 2: 1}
        assert "age_seconds" in status

    def test_status_nonexistent(self):
        from src.scan_session import session_status

        assert session_status("nonexistent") is None


class TestSessionReset:
    def test_reset_existing(self):
        from src.scan_session import get_session, reset_session

        get_session("test")
        assert reset_session("test") is True

    def test_reset_nonexistent(self):
        from src.scan_session import reset_session

        assert reset_session("nonexistent") is False

    def test_reset_all(self):
        from src.scan_session import get_session, reset_all_sessions

        get_session("a")
        get_session("b")
        count = reset_all_sessions()
        assert count == 2

    def test_session_expiry(self, monkeypatch):
        from src.scan_session import _sessions, get_session

        monkeypatch.setenv("SCAN_SESSION_TTL", "1")
        s = get_session("test")
        s.last_accessed = time.time() - 10  # expired
        # Next access triggers cleanup
        result = get_session("test", auto_create=False)
        assert result is None
        assert "test" not in _sessions


# ── Pydantic model tests ─────────────────────────────────────


class TestEmailScanInputModel:
    def test_defaults(self):
        from src.mcp_models import EmailScanInput

        m = EmailScanInput(action="status", scan_id="test")
        assert m.action == "status"
        assert m.scan_id == "test"
        assert m.uids is None
        assert m.label is None
        assert m.phase is None
        assert m.score is None

    def test_scan_id_required(self):
        from src.mcp_models import EmailScanInput

        with pytest.raises(ValueError):
            EmailScanInput(action="status")

    def test_scan_id_max_length(self):
        from src.mcp_models import EmailScanInput

        with pytest.raises(ValueError):
            EmailScanInput(action="status", scan_id="x" * 101)

    def test_uids_max_50(self):
        from src.mcp_models import EmailScanInput

        with pytest.raises(ValueError):
            EmailScanInput(
                action="flag",
                scan_id="test",
                uids=[f"uid{i}" for i in range(51)],
                label="relevant",
            )

    def test_phase_bounds(self):
        from src.mcp_models import EmailScanInput

        with pytest.raises(ValueError):
            EmailScanInput(action="flag", scan_id="test", phase=0)
        with pytest.raises(ValueError):
            EmailScanInput(action="flag", scan_id="test", phase=4)

    def test_score_bounds(self):
        from src.mcp_models import EmailScanInput

        with pytest.raises(ValueError):
            EmailScanInput(action="flag", scan_id="test", score=1.5)


class TestScanIdOnSearchModels:
    def test_triage_scan_id_optional(self):
        from src.mcp_models import EmailTriageInput

        m = EmailTriageInput(query="test")
        assert m.scan_id is None
        m2 = EmailTriageInput(query="test", scan_id="sess1")
        assert m2.scan_id == "sess1"

    def test_structured_scan_id_optional(self):
        from src.mcp_models import EmailSearchStructuredInput

        m = EmailSearchStructuredInput(query="test")
        assert m.scan_id is None
        m2 = EmailSearchStructuredInput(query="test", scan_id="sess1")
        assert m2.scan_id == "sess1"

    def test_find_similar_scan_id_optional(self):
        from src.mcp_models import FindSimilarInput

        m = FindSimilarInput(uid="uid1")
        assert m.scan_id is None
        m2 = FindSimilarInput(uid="uid1", scan_id="sess1")
        assert m2.scan_id == "sess1"


# ── Integration tests for scan-aware search tools ─────────────


def _make_search_result(uid="x", text="hello", distance=0.25):
    from src.retriever import SearchResult

    return SearchResult(
        chunk_id=f"chunk_{uid}",
        text=text,
        metadata={"uid": uid, "subject": "Hi", "sender_email": "a@example.com"},
        distance=distance,
    )


class _ScanRetriever:
    """Retriever that returns configurable results for scan testing."""

    def __init__(self, results):
        self._results = results
        self.captured_kwargs = {}

    def search_filtered(self, **kwargs):
        self.captured_kwargs = kwargs
        return list(self._results)

    def search(self, query, top_k=10):
        return list(self._results)

    def serialize_results(self, query, results):
        return {"query": query, "count": len(results), "results": []}

    def format_results_for_claude(self, results):
        return "formatted"

    def stats(self):
        return {"total_emails": 100, "date_range": {}, "unique_senders": 5}


@pytest.mark.asyncio
async def test_triage_without_scan_id_unchanged(monkeypatch):
    """Triage without scan_id behaves exactly as before."""
    from src import mcp_server
    from src.config import get_settings
    from src.mcp_models import EmailTriageInput

    get_settings.cache_clear()
    retriever = _ScanRetriever([_make_search_result("uid1")])
    monkeypatch.setattr(mcp_server, "get_retriever", lambda: retriever)

    try:
        params = EmailTriageInput(query="test")
        from src.tools.search import email_triage

        result = await email_triage(params)
        data = json.loads(result)
        assert "_scan" not in data
        assert data["count"] == 1
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_triage_with_scan_id_returns_scan_meta(monkeypatch):
    from src import mcp_server
    from src.config import get_settings
    from src.mcp_models import EmailTriageInput

    get_settings.cache_clear()
    retriever = _ScanRetriever([_make_search_result("uid1"), _make_search_result("uid2")])
    monkeypatch.setattr(mcp_server, "get_retriever", lambda: retriever)

    try:
        params = EmailTriageInput(query="test", scan_id="sess1")
        from src.tools.search import email_triage

        result = await email_triage(params)
        data = json.loads(result)
        assert "_scan" in data
        assert data["_scan"]["scan_id"] == "sess1"
        assert data["_scan"]["new_count"] == 2
        assert data["_scan"]["excluded_count"] == 0
        assert data["_scan"]["seen_total"] == 2
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_triage_second_call_excludes_seen(monkeypatch):
    from src import mcp_server
    from src.config import get_settings
    from src.mcp_models import EmailTriageInput

    get_settings.cache_clear()
    results_batch = [_make_search_result("uid1"), _make_search_result("uid2")]
    retriever = _ScanRetriever(results_batch)
    monkeypatch.setattr(mcp_server, "get_retriever", lambda: retriever)

    try:
        from src.tools.search import email_triage

        # First call
        params1 = EmailTriageInput(query="test", scan_id="sess1")
        await email_triage(params1)

        # Second call with overlapping + new
        retriever._results = [_make_search_result("uid2"), _make_search_result("uid3")]
        params2 = EmailTriageInput(query="more", scan_id="sess1")
        result2 = await email_triage(params2)
        data2 = json.loads(result2)
        assert data2["_scan"]["new_count"] == 1  # uid3 only
        assert data2["_scan"]["excluded_count"] == 1  # uid2 excluded
        assert data2["count"] == 1
    finally:
        get_settings.cache_clear()


@pytest.mark.asyncio
async def test_search_with_scan_id_excludes_seen(monkeypatch):
    from src import mcp_server
    from src.config import get_settings
    from src.mcp_models import EmailSearchStructuredInput

    get_settings.cache_clear()
    retriever = _ScanRetriever([_make_search_result("uid1"), _make_search_result("uid2")])
    monkeypatch.setattr(mcp_server, "get_retriever", lambda: retriever)

    try:
        from src.tools.search import email_search_structured

        # First call
        params1 = EmailSearchStructuredInput(query="test", scan_id="sess1")
        result1 = await email_search_structured(params1)
        data1 = json.loads(result1)
        assert data1["_scan"]["new_count"] == 2

        # Second call — same UIDs excluded
        params2 = EmailSearchStructuredInput(query="more", scan_id="sess1")
        result2 = await email_search_structured(params2)
        data2 = json.loads(result2)
        assert data2["_scan"]["excluded_count"] == 2
        assert data2["_scan"]["new_count"] == 0
    finally:
        get_settings.cache_clear()


# ── email_scan tool tests ─────────────────────────────────────


@pytest.mark.asyncio
async def test_email_scan_status_nonexistent():
    from src.mcp_models import EmailScanInput

    # Minimal deps mock
    class _Deps:
        @staticmethod
        def write_tool_annotations(title):
            from mcp.types import ToolAnnotations

            return ToolAnnotations(title=title)

        @staticmethod
        def get_deps():
            return _Deps

    # Import the function directly since it's registered as a closure
    from src import scan_session

    params = EmailScanInput(action="status", scan_id="nonexistent")
    # Call session_status directly (tool is a closure)
    status = scan_session.session_status(params.scan_id)
    assert status is None


@pytest.mark.asyncio
async def test_email_scan_flag_then_candidates():
    from src import scan_session

    # Flag some candidates
    scan_session.flag_candidates("test", ["uid1", "uid2"], label="bossing", phase=1, score=0.8)

    # Get candidates
    candidates = scan_session.get_candidates("test", label="bossing")
    assert len(candidates) == 2
    assert candidates[0]["label"] == "bossing"
    assert candidates[0]["phase"] == 1

    # Status
    status = scan_session.session_status("test")
    assert status["candidate_count"] == 2
    assert status["candidates_by_label"]["bossing"] == 2


@pytest.mark.asyncio
async def test_email_scan_reset():
    from src import scan_session

    scan_session.get_session("test")
    assert scan_session.reset_session("test") is True
    assert scan_session.session_status("test") is None


# ── Thread-safety tests (Bug 2) ────────────────────────────────


class TestFilterSeenThreadSafety:
    """Verify concurrent filter_seen calls produce no duplicate UIDs."""

    def test_concurrent_filter_seen_no_duplicates(self):
        """Two threads calling filter_seen with overlapping UIDs should dedup."""
        import threading

        from src.scan_session import filter_seen, get_session

        scan_id = "thread_test"
        get_session(scan_id)  # pre-create

        # Build two batches with overlapping UIDs
        batch_a = [{"uid": f"uid_{i}"} for i in range(50)]
        batch_b = [{"uid": f"uid_{i}"} for i in range(25, 75)]  # overlap: 25-49

        results_a = []
        results_b = []

        def run_a():
            new, _meta = filter_seen(scan_id, batch_a)
            results_a.extend(r["uid"] for r in new)

        def run_b():
            new, _meta = filter_seen(scan_id, batch_b)
            results_b.extend(r["uid"] for r in new)

        t1 = threading.Thread(target=run_a)
        t2 = threading.Thread(target=run_b)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # Combined results must have no duplicates
        all_uids = results_a + results_b
        assert len(all_uids) == len(set(all_uids)), (
            f"Duplicate UIDs detected: {len(all_uids)} total vs {len(set(all_uids))} unique"
        )
        # Total unique UIDs should be 75 (uid_0..uid_74)
        assert len(set(all_uids)) == 75

    def test_concurrent_flag_candidates_count(self):
        """Two threads calling flag_candidates should not double-count."""
        import threading

        from src.scan_session import flag_candidates, get_session

        scan_id = "flag_test"
        get_session(scan_id)

        uids_a = [f"uid_{i}" for i in range(30)]
        uids_b = [f"uid_{i}" for i in range(15, 45)]  # overlap: 15-29

        added_counts = []

        def run_a():
            added, _total = flag_candidates(scan_id, uids_a, "label_a")
            added_counts.append(added)

        def run_b():
            added, _total = flag_candidates(scan_id, uids_b, "label_b")
            added_counts.append(added)

        t1 = threading.Thread(target=run_a)
        t2 = threading.Thread(target=run_b)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        session = get_session(scan_id)
        # Total unique candidates should be 45
        assert len(session.candidates) == 45

    def test_concurrent_get_candidates_during_flag(self):
        """get_candidates must not crash when flag_candidates mutates dict concurrently.

        Before the fix, get_candidates iterated session.candidates without
        holding session.lock, which could raise RuntimeError on dict mutation.
        """
        import threading

        from src.scan_session import flag_candidates, get_candidates, get_session

        scan_id = "read_write_test"
        get_session(scan_id)

        errors: list[Exception] = []
        iterations = 50
        barrier = threading.Barrier(2)

        def writer():
            """Flag new candidates while reader iterates."""
            barrier.wait()
            for i in range(iterations):
                flag_candidates(scan_id, [f"uid_{i}"], label="test", phase=1)

        def reader():
            """Continuously read candidates during writes."""
            barrier.wait()
            try:
                for _ in range(iterations):
                    candidates = get_candidates(scan_id)
                    # Verify consistency: every item must have expected fields
                    for c in candidates:
                        assert "uid" in c
                        assert "label" in c
            except Exception as exc:
                errors.append(exc)

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)
        t_write.start()
        t_read.start()
        t_write.join()
        t_read.join()

        assert not errors, f"get_candidates raised during concurrent mutation: {errors}"

    def test_concurrent_session_status_during_flag(self):
        """session_status must not crash when flag_candidates mutates dict concurrently.

        Before the fix, session_status iterated session.candidates.values()
        without holding session.lock.
        """
        import threading

        from src.scan_session import flag_candidates, get_session, session_status

        scan_id = "status_write_test"
        get_session(scan_id)

        errors: list[Exception] = []
        iterations = 50
        barrier = threading.Barrier(2)

        def writer():
            barrier.wait()
            for i in range(iterations):
                flag_candidates(scan_id, [f"uid_{i}"], label="test", phase=1)

        def reader():
            barrier.wait()
            try:
                for _ in range(iterations):
                    status = session_status(scan_id)
                    assert status is not None
                    assert "seen_count" in status
                    assert "candidate_count" in status
            except Exception as exc:
                errors.append(exc)

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)
        t_write.start()
        t_read.start()
        t_write.join()
        t_read.join()

        assert not errors, f"session_status raised during concurrent mutation: {errors}"
