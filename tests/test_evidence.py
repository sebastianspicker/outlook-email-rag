"""Tests for evidence management in EmailDatabase."""

from __future__ import annotations

import pytest

from src.email_db import EmailDatabase
from src.parse_olm import Email


def _make_email(**overrides) -> Email:
    defaults = {
        "message_id": "<msg1@example.com>",
        "subject": "Meeting notes",
        "sender_name": "Alice Manager",
        "sender_email": "alice@company.com",
        "to": ["Bob <bob@company.com>"],
        "cc": [],
        "bcc": [],
        "date": "2024-03-15T10:30:00",
        "body_text": "You are not welcome here. We don't need people like you.",
        "body_html": "",
        "folder": "Inbox",
        "has_attachments": False,
    }
    defaults.update(overrides)
    return Email(**defaults)


# ── Schema ────────────────────────────────────────────────────


def test_evidence_table_exists():
    db = EmailDatabase(":memory:")
    tables = {
        row[0]
        for row in db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert "evidence_items" in tables
    db.close()


def test_evidence_table_columns():
    db = EmailDatabase(":memory:")
    cols = {
        row[1]
        for row in db.conn.execute("PRAGMA table_info(evidence_items)").fetchall()
    }
    expected = {
        "id", "email_uid", "category", "key_quote", "summary", "relevance",
        "sender_name", "sender_email", "date", "recipients", "subject",
        "notes", "created_at", "updated_at", "verified",
    }
    assert expected.issubset(cols)
    db.close()


# ── add_evidence ──────────────────────────────────────────────


def test_add_evidence_valid():
    db = EmailDatabase(":memory:")
    email = _make_email()
    db.insert_email(email)

    result = db.add_evidence(
        email_uid=email.uid,
        category="discrimination",
        key_quote="We don't need people like you.",
        summary="Exclusionary language targeting the recipient.",
        relevance=5,
    )

    assert result["id"] is not None
    assert result["category"] == "discrimination"
    assert result["relevance"] == 5
    assert result["sender_email"] == "alice@company.com"
    assert result["sender_name"] == "Alice Manager"
    assert result["subject"] == "Meeting notes"
    assert result["date"] == "2024-03-15T10:30:00"
    assert result["verified"] == 1  # Quote exists in body
    db.close()


def test_add_evidence_auto_populates_recipients():
    db = EmailDatabase(":memory:")
    email = _make_email(to=["Bob <bob@company.com>", "Carol <carol@company.com>"])
    db.insert_email(email)

    result = db.add_evidence(
        email_uid=email.uid,
        category="harassment",
        key_quote="You are not welcome here.",
        summary="Hostile statement.",
        relevance=4,
    )

    assert "bob@company.com" in result["recipients"]
    assert "carol@company.com" in result["recipients"]
    db.close()


def test_add_evidence_invalid_uid():
    db = EmailDatabase(":memory:")
    with pytest.raises(ValueError, match="Email not found"):
        db.add_evidence(
            email_uid="nonexistent_uid",
            category="discrimination",
            key_quote="test quote",
            summary="test",
            relevance=3,
        )
    db.close()


def test_add_evidence_quote_verified_when_present():
    db = EmailDatabase(":memory:")
    email = _make_email(body_text="This is clearly inappropriate behavior.")
    db.insert_email(email)

    result = db.add_evidence(
        email_uid=email.uid,
        category="harassment",
        key_quote="clearly inappropriate behavior",
        summary="Test.",
        relevance=3,
    )
    assert result["verified"] == 1
    db.close()


def test_add_evidence_quote_not_verified_when_missing():
    db = EmailDatabase(":memory:")
    email = _make_email(body_text="Normal email text.")
    db.insert_email(email)

    result = db.add_evidence(
        email_uid=email.uid,
        category="general",
        key_quote="This quote does not exist in the email",
        summary="Test.",
        relevance=1,
    )
    assert result["verified"] == 0
    db.close()


def test_add_evidence_quote_verified_case_insensitive():
    db = EmailDatabase(":memory:")
    email = _make_email(body_text="You are NOT welcome here.")
    db.insert_email(email)

    result = db.add_evidence(
        email_uid=email.uid,
        category="bossing",
        key_quote="you are not welcome here",
        summary="Hostile statement.",
        relevance=4,
    )
    assert result["verified"] == 1
    db.close()


def test_add_evidence_with_notes():
    db = EmailDatabase(":memory:")
    email = _make_email()
    db.insert_email(email)

    result = db.add_evidence(
        email_uid=email.uid,
        category="gaslighting",
        key_quote="don't need people like you",
        summary="Derogatory language.",
        relevance=4,
        notes="Pattern of repeated insults since 2022.",
    )
    assert result["notes"] == "Pattern of repeated insults since 2022."
    db.close()


# ── list_evidence ─────────────────────────────────────────────


def _seed_evidence(db: EmailDatabase) -> list[dict]:
    """Insert 3 emails and 4 evidence items for testing."""
    e1 = _make_email(message_id="<m1@x>", body_text="You are incompetent.", date="2024-01-10T10:00:00")
    e2 = _make_email(message_id="<m2@x>", body_text="This is your fault.", date="2024-02-15T10:00:00")
    e3 = _make_email(message_id="<m3@x>", body_text="You should leave.", date="2024-03-20T10:00:00")
    db.insert_email(e1)
    db.insert_email(e2)
    db.insert_email(e3)

    items = [
        db.add_evidence(e1.uid, "gaslighting", "You are incompetent", "Gaslighting.", 4),
        db.add_evidence(e2.uid, "bossing", "This is your fault", "Blame-shifting.", 3),
        db.add_evidence(e3.uid, "harassment", "You should leave", "Hostile push-out.", 5),
        db.add_evidence(e1.uid, "discrimination", "You are incompetent", "Targeting disability.", 5),
    ]
    return items


def test_list_evidence_all():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    result = db.list_evidence()
    assert result["total"] == 4
    assert len(result["items"]) == 4
    db.close()


def test_list_evidence_filter_category():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    result = db.list_evidence(category="gaslighting")
    assert result["total"] == 1
    assert result["items"][0]["category"] == "gaslighting"
    db.close()


def test_list_evidence_filter_min_relevance():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    result = db.list_evidence(min_relevance=5)
    assert result["total"] == 2
    for item in result["items"]:
        assert item["relevance"] >= 5
    db.close()


def test_list_evidence_filter_email_uid():
    db = EmailDatabase(":memory:")
    items = _seed_evidence(db)

    uid = items[0]["email_uid"]
    result = db.list_evidence(email_uid=uid)
    assert result["total"] == 2  # Two items linked to e1
    db.close()


def test_list_evidence_pagination():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    page1 = db.list_evidence(limit=2, offset=0)
    page2 = db.list_evidence(limit=2, offset=2)

    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 2
    ids1 = {i["id"] for i in page1["items"]}
    ids2 = {i["id"] for i in page2["items"]}
    assert ids1.isdisjoint(ids2)
    db.close()


def test_list_evidence_empty():
    db = EmailDatabase(":memory:")
    result = db.list_evidence()
    assert result["total"] == 0
    assert result["items"] == []
    db.close()


# ── get_evidence ──────────────────────────────────────────────


def test_get_evidence_valid():
    db = EmailDatabase(":memory:")
    email = _make_email()
    db.insert_email(email)
    added = db.add_evidence(email.uid, "bossing", "not welcome", "Test.", 3)

    item = db.get_evidence(added["id"])
    assert item is not None
    assert item["id"] == added["id"]
    assert item["category"] == "bossing"
    db.close()


def test_get_evidence_invalid():
    db = EmailDatabase(":memory:")
    assert db.get_evidence(999) is None
    db.close()


# ── update_evidence ───────────────────────────────────────────


def test_update_evidence_fields():
    db = EmailDatabase(":memory:")
    email = _make_email()
    db.insert_email(email)
    added = db.add_evidence(email.uid, "general", "not welcome", "Initial.", 2)

    updated = db.update_evidence(added["id"], category="bossing", relevance=4, notes="Updated note")
    assert updated is True

    item = db.get_evidence(added["id"])
    assert item["category"] == "bossing"
    assert item["relevance"] == 4
    assert item["notes"] == "Updated note"
    db.close()


def test_update_evidence_invalid_id():
    db = EmailDatabase(":memory:")
    assert db.update_evidence(999, category="test") is False
    db.close()


def test_update_evidence_reverifies_quote():
    db = EmailDatabase(":memory:")
    email = _make_email(body_text="Original text here. New quote in body.")
    db.insert_email(email)
    added = db.add_evidence(email.uid, "general", "Original text here", "Test.", 2)
    assert db.get_evidence(added["id"])["verified"] == 1

    # Update to a quote that doesn't exist
    db.update_evidence(added["id"], key_quote="This quote is fabricated")
    item = db.get_evidence(added["id"])
    assert item["verified"] == 0

    # Update to a quote that does exist
    db.update_evidence(added["id"], key_quote="New quote in body")
    item = db.get_evidence(added["id"])
    assert item["verified"] == 1
    db.close()


def test_update_evidence_no_valid_fields():
    db = EmailDatabase(":memory:")
    email = _make_email()
    db.insert_email(email)
    added = db.add_evidence(email.uid, "general", "not welcome", "Test.", 2)

    # Passing no recognized fields returns False
    assert db.update_evidence(added["id"], bogus_field="value") is False
    db.close()


# ── remove_evidence ───────────────────────────────────────────


def test_remove_evidence_valid():
    db = EmailDatabase(":memory:")
    email = _make_email()
    db.insert_email(email)
    added = db.add_evidence(email.uid, "gaslighting", "not welcome", "Test.", 3)

    assert db.remove_evidence(added["id"]) is True
    assert db.get_evidence(added["id"]) is None
    db.close()


def test_remove_evidence_invalid():
    db = EmailDatabase(":memory:")
    assert db.remove_evidence(999) is False
    db.close()


# ── verify_evidence_quotes ────────────────────────────────────


def test_verify_evidence_quotes():
    db = EmailDatabase(":memory:")
    e1 = _make_email(message_id="<m1@x>", body_text="Real quote here.")
    e2 = _make_email(message_id="<m2@x>", body_text="Different text.")
    db.insert_email(e1)
    db.insert_email(e2)

    # One verified, one not
    db.add_evidence(e1.uid, "harassment", "Real quote here", "Test.", 3)
    db.add_evidence(e2.uid, "gaslighting", "Fabricated quote", "Test.", 2)

    result = db.verify_evidence_quotes()
    assert result["verified"] == 1
    assert result["failed"] == 1
    assert result["total"] == 2
    assert len(result["failures"]) == 1
    assert result["failures"][0]["key_quote_preview"].startswith("Fabricated")
    db.close()


def test_verify_evidence_quotes_empty():
    db = EmailDatabase(":memory:")
    result = db.verify_evidence_quotes()
    assert result["verified"] == 0
    assert result["failed"] == 0
    assert result["total"] == 0
    db.close()


# ── evidence_stats ────────────────────────────────────────────


def test_evidence_stats():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    stats = db.evidence_stats()
    assert stats["total"] == 4
    assert stats["verified"] + stats["unverified"] == 4

    categories = {c["category"] for c in stats["by_category"]}
    assert "gaslighting" in categories
    assert "bossing" in categories
    assert "harassment" in categories
    assert "discrimination" in categories

    assert len(stats["by_relevance"]) > 0
    db.close()


def test_evidence_stats_empty():
    db = EmailDatabase(":memory:")
    stats = db.evidence_stats()
    assert stats["total"] == 0
    assert stats["verified"] == 0
    assert stats["unverified"] == 0
    assert stats["by_category"] == []
    assert stats["by_relevance"] == []
    db.close()


# ── search_evidence ──────────────────────────────────────────


def test_search_evidence_by_quote():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    result = db.search_evidence("incompetent")
    assert result["total"] == 2  # Two items with "incompetent" in quote
    assert result["query"] == "incompetent"
    db.close()


def test_search_evidence_by_summary():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    result = db.search_evidence("Blame-shifting")
    assert result["total"] == 1
    assert result["items"][0]["category"] == "bossing"
    db.close()


def test_search_evidence_by_notes():
    db = EmailDatabase(":memory:")
    email = _make_email(body_text="Bad behavior here.")
    db.insert_email(email)
    db.add_evidence(email.uid, "general", "Bad behavior", "Test.", 2, notes="Pattern since 2020.")

    result = db.search_evidence("Pattern since")
    assert result["total"] == 1
    db.close()


def test_search_evidence_with_category_filter():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    result = db.search_evidence("incompetent", category="gaslighting")
    assert result["total"] == 1
    assert result["items"][0]["category"] == "gaslighting"
    db.close()


def test_search_evidence_with_relevance_filter():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    result = db.search_evidence("incompetent", min_relevance=5)
    assert result["total"] == 1
    assert result["items"][0]["relevance"] == 5
    db.close()


def test_search_evidence_no_results():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    result = db.search_evidence("nonexistent phrase xyz")
    assert result["total"] == 0
    assert result["items"] == []
    db.close()


# ── evidence_timeline ────────────────────────────────────────


def test_evidence_timeline_all():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    items = db.evidence_timeline()
    assert len(items) == 4
    # Should be sorted by date ascending
    dates = [i["date"] for i in items]
    assert dates == sorted(dates)
    db.close()


def test_evidence_timeline_filter_category():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    items = db.evidence_timeline(category="harassment")
    assert len(items) == 1
    assert items[0]["category"] == "harassment"
    db.close()


def test_evidence_timeline_filter_relevance():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    items = db.evidence_timeline(min_relevance=5)
    assert len(items) == 2
    for item in items:
        assert item["relevance"] >= 5
    db.close()


def test_evidence_timeline_empty():
    db = EmailDatabase(":memory:")
    items = db.evidence_timeline()
    assert items == []
    db.close()


def test_evidence_timeline_offset():
    """Timeline with offset skips earlier items."""
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    all_items = db.evidence_timeline()
    assert len(all_items) == 4

    offset_items = db.evidence_timeline(limit=10, offset=2)
    assert len(offset_items) == 2
    # Should be the last 2 items chronologically
    assert offset_items[0]["date"] == all_items[2]["date"]
    assert offset_items[1]["date"] == all_items[3]["date"]
    db.close()


def test_evidence_timeline_offset_beyond_results():
    """Offset beyond total should return empty."""
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    items = db.evidence_timeline(offset=100)
    assert items == []
    db.close()


# ── evidence_categories ──────────────────────────────────────


def test_evidence_categories_all_canonical():
    db = EmailDatabase(":memory:")
    cats = db.evidence_categories()
    assert len(cats) == 10
    names = [c["category"] for c in cats]
    assert "discrimination" in names
    assert "harassment" in names
    assert "gaslighting" in names
    assert "general" in names
    db.close()


def test_evidence_categories_with_counts():
    db = EmailDatabase(":memory:")
    _seed_evidence(db)

    cats = db.evidence_categories()
    cat_map = {c["category"]: c["count"] for c in cats}
    assert cat_map["gaslighting"] == 1
    assert cat_map["bossing"] == 1
    assert cat_map["harassment"] == 1
    assert cat_map["discrimination"] == 1
    assert cat_map["micromanagement"] == 0  # Not in seed data
    db.close()


def test_evidence_categories_empty():
    db = EmailDatabase(":memory:")
    cats = db.evidence_categories()
    assert all(c["count"] == 0 for c in cats)
    db.close()
