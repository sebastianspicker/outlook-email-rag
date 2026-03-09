"""Tests for proof dossier generation (Phase 3)."""

import os
import tempfile

import pytest

from src.dossier_generator import DossierGenerator
from src.email_db import EmailDatabase


@pytest.fixture()
def db():
    """Create a temporary EmailDatabase with evidence data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        database = EmailDatabase(db_path)

        # Insert sample emails
        for i in range(1, 4):
            database.conn.execute(
                """INSERT INTO emails (uid, sender_email, sender_name, date, subject,
                   body_text, body_html, has_attachments, attachment_count,
                   priority, is_read, body_length, content_sha256)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 1, 50, ?)""",
                (
                    f"uid-{i}", f"sender{i}@test.com", f"Sender {i}",
                    f"2024-01-{10 + i:02d}", f"Subject {i}",
                    f"Body text {i} with evidence content here.",
                    f"<p>Body text {i}</p>",
                    f"sha256-fake-{i}",
                ),
            )
            database.conn.execute(
                "INSERT INTO recipients(email_uid, address, display_name, type) VALUES(?,?,?,?)",
                (f"uid-{i}", "recipient@test.com", "Recipient", "to"),
            )

        # Add communication edges for relationship tests
        database.conn.execute(
            "INSERT INTO communication_edges(sender_email, recipient_email, email_count) VALUES(?,?,?)",
            ("sender1@test.com", "sender2@test.com", 5),
        )

        database.conn.commit()

        # Add evidence items
        database.add_evidence("uid-1", "harassment", "evidence content", "Summary 1", 5)
        database.add_evidence("uid-2", "discrimination", "evidence content", "Summary 2", 3)
        database.add_evidence("uid-3", "retaliation", "evidence content", "Summary 3", 4)

        yield database
        database.close()


@pytest.fixture()
def db_empty():
    """Database with no evidence."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_empty.db")
        database = EmailDatabase(db_path)
        yield database
        database.close()


# ── preview ──────────────────────────────────────────────────


def test_preview_returns_correct_counts(db):
    """Preview should return accurate counts."""
    gen = DossierGenerator(db)
    result = gen.preview()

    assert result["evidence_count"] == 3
    assert result["email_count"] == 3
    assert result["category_count"] == 3
    assert "harassment" in result["categories"]
    assert "discrimination" in result["categories"]


def test_preview_filters_by_relevance(db):
    """Preview should respect min_relevance filter."""
    gen = DossierGenerator(db)
    result = gen.preview(min_relevance=4)

    assert result["evidence_count"] == 2  # relevance 4 and 5


def test_preview_filters_by_category(db):
    """Preview should respect category filter."""
    gen = DossierGenerator(db)
    result = gen.preview(category="harassment")

    assert result["evidence_count"] == 1
    assert result["categories"] == ["harassment"]


def test_preview_empty_evidence(db_empty):
    """Preview should handle no evidence gracefully."""
    gen = DossierGenerator(db_empty)
    result = gen.preview()

    assert result["evidence_count"] == 0
    assert result["email_count"] == 0


# ── generate ─────────────────────────────────────────────────


def test_generate_returns_valid_html(db):
    """Generate should return valid HTML."""
    gen = DossierGenerator(db)
    result = gen.generate(title="Test Dossier")

    assert "html" in result
    assert "<html" in result["html"]
    assert "Test Dossier" in result["html"]


def test_generate_includes_evidence_items(db):
    """HTML should contain evidence items."""
    gen = DossierGenerator(db)
    result = gen.generate()

    assert result["evidence_count"] == 3
    assert "harassment" in result["html"]
    assert "discrimination" in result["html"]


def test_generate_includes_source_emails(db):
    """HTML should contain source email appendix."""
    gen = DossierGenerator(db)
    result = gen.generate()

    assert result["email_count"] == 3
    assert "Source Email Appendix" in result["html"]
    assert "uid-1" in result["html"]


def test_generate_includes_case_reference(db):
    """Case reference should appear on cover page."""
    gen = DossierGenerator(db)
    result = gen.generate(case_reference="CASE-2024-001")

    assert "CASE-2024-001" in result["html"]


def test_generate_includes_custodian(db):
    """Custodian should appear on cover page."""
    gen = DossierGenerator(db)
    result = gen.generate(custodian="Evidence Manager")

    assert "Evidence Manager" in result["html"]


def test_generate_has_dossier_hash(db):
    """Dossier should have a SHA-256 integrity hash."""
    gen = DossierGenerator(db)
    result = gen.generate()

    assert "dossier_hash" in result
    assert len(result["dossier_hash"]) == 64
    assert result["dossier_hash"] in result["html"]


def test_generate_filters_by_relevance(db):
    """Generate should respect min_relevance filter."""
    gen = DossierGenerator(db)
    result = gen.generate(min_relevance=5)

    assert result["evidence_count"] == 1  # Only relevance 5


def test_generate_filters_by_category(db):
    """Generate should respect category filter."""
    gen = DossierGenerator(db)
    result = gen.generate(category="harassment")

    assert result["evidence_count"] == 1


def test_generate_includes_custody_log(db):
    """HTML should contain custody log when enabled."""
    gen = DossierGenerator(db)
    result = gen.generate(include_custody=True)

    assert "Chain-of-Custody Log" in result["html"]


def test_generate_excludes_custody_when_disabled(db):
    """HTML should not contain custody log when disabled."""
    gen = DossierGenerator(db)
    result = gen.generate(include_custody=False)

    assert "Chain-of-Custody Log" not in result["html"]


def test_generate_empty_evidence(db_empty):
    """Should produce minimal dossier for empty evidence."""
    gen = DossierGenerator(db_empty)
    result = gen.generate(title="Empty Dossier")

    assert "html" in result
    assert result["evidence_count"] == 0
    assert "Empty Dossier" in result["html"]


def test_generate_has_generated_at(db):
    """Result should include timestamp."""
    gen = DossierGenerator(db)
    result = gen.generate()

    assert "generated_at" in result
    assert "UTC" in result["generated_at"]


# ── generate_file ────────────────────────────────────────────


def test_generate_file_writes_html(db):
    """generate_file should write HTML to disk."""
    gen = DossierGenerator(db)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "dossier.html")
        result = gen.generate_file(path, title="File Test")

        assert result["output_path"] == path
        assert result["format"] == "html"
        assert os.path.exists(path)

        content = open(path, encoding="utf-8").read()
        assert "File Test" in content


def test_generate_file_creates_parent_dirs(db):
    """generate_file should create parent directories."""
    gen = DossierGenerator(db)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "subdir", "deep", "dossier.html")
        result = gen.generate_file(path, title="Deep Test")

        assert os.path.exists(path)
        assert result["evidence_count"] == 3


# ── relationship section ─────────────────────────────────────


def test_generate_with_network(db):
    """Should include relationship section when network is provided."""
    from src.network_analysis import CommunicationNetwork

    net = CommunicationNetwork(db)
    gen = DossierGenerator(db, network=net)
    result = gen.generate(include_relationships=True)

    assert "Relationship Analysis" in result["html"]


def test_generate_without_network(db):
    """Should omit relationship section when no network."""
    gen = DossierGenerator(db, network=None)
    result = gen.generate(include_relationships=True)

    # No network provided, so no relationship data
    assert "Relationship Analysis" not in result["html"]


def test_generate_with_persons_of_interest(db):
    """Should focus relationship analysis on specified persons."""
    from src.network_analysis import CommunicationNetwork

    net = CommunicationNetwork(db)
    gen = DossierGenerator(db, network=net)
    result = gen.generate(persons_of_interest=["sender1@test.com"])

    assert "sender1@test.com" in result["html"]


# ── dotted conditionals in loops ─────────────────────────────


def test_dotted_conditionals_in_loop(db):
    """{% if email.to %} inside {% for %} should render when field is truthy."""
    gen = DossierGenerator(db)
    result = gen.generate()
    html = result["html"]

    # To field was populated via recipients table — should appear
    assert "<strong>To:</strong>" in html
    # Literal template tags should NOT survive
    assert "{% if email.to %}" not in html


def test_dotted_conditional_false_branch(db):
    """{% if email.cc %} should not render CC line when cc is empty."""
    gen = DossierGenerator(db)
    result = gen.generate()
    html = result["html"]

    # CC was not populated in fixtures → CC line should be absent
    assert "<strong>CC:</strong>" not in html
    assert "{% if email.cc %}" not in html


# ── evidence numbering and cross-references ──────────────────


def test_evidence_numbering(db):
    """Evidence items should have E-1, E-2, E-3 numbers."""
    gen = DossierGenerator(db)
    result = gen.generate()
    html = result["html"]

    assert "E-1" in html
    assert "E-2" in html
    assert "E-3" in html


def test_appendix_numbering(db):
    """Source emails should have A-1, A-2, A-3 numbers."""
    gen = DossierGenerator(db)
    result = gen.generate()
    html = result["html"]

    assert "A-1" in html
    assert "A-2" in html
    assert "A-3" in html


def test_cross_references(db):
    """Evidence items should link to their source appendix."""
    gen = DossierGenerator(db)
    result = gen.generate()
    html = result["html"]

    # Evidence items reference appendix numbers (link inside anchor tag)
    assert "Appendix" in html
    assert "#appendix-A-" in html
