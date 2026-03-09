"""Tests for proof dossier generation (Phase 3)."""

import os
import tempfile

import pytest

from src.dossier_generator import DossierGenerator
from src.email_db import EmailDatabase
from src.formatting import format_file_size, strip_html_tags


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


@pytest.fixture()
def gen(db):
    """DossierGenerator with populated database."""
    return DossierGenerator(db)


@pytest.fixture()
def gen_empty(db_empty):
    """DossierGenerator with empty database."""
    return DossierGenerator(db_empty)


# ── preview ──────────────────────────────────────────────────


def test_preview_returns_correct_counts(gen):
    """Preview should return accurate counts."""
    result = gen.preview()

    assert result["evidence_count"] == 3
    assert result["email_count"] == 3
    assert result["category_count"] == 3
    assert "harassment" in result["categories"]
    assert "discrimination" in result["categories"]


def test_preview_filters_by_relevance(gen):
    """Preview should respect min_relevance filter."""
    result = gen.preview(min_relevance=4)

    assert result["evidence_count"] == 2  # relevance 4 and 5


def test_preview_filters_by_category(gen):
    """Preview should respect category filter."""
    result = gen.preview(category="harassment")

    assert result["evidence_count"] == 1
    assert result["categories"] == ["harassment"]


def test_preview_empty_evidence(gen_empty):
    """Preview should handle no evidence gracefully."""
    result = gen_empty.preview()

    assert result["evidence_count"] == 0
    assert result["email_count"] == 0


# ── generate ─────────────────────────────────────────────────


def test_generate_returns_valid_html(gen):
    """Generate should return valid HTML."""
    result = gen.generate(title="Test Dossier")

    assert "html" in result
    assert "<html" in result["html"]
    assert "Test Dossier" in result["html"]


def test_generate_includes_evidence_items(gen):
    """HTML should contain evidence items."""
    result = gen.generate()

    assert result["evidence_count"] == 3
    assert "harassment" in result["html"]
    assert "discrimination" in result["html"]


def test_generate_includes_source_emails(gen):
    """HTML should contain source email appendix."""
    result = gen.generate()

    assert result["email_count"] == 3
    assert "Source Email Appendix" in result["html"]
    assert "uid-1" in result["html"]


def test_generate_includes_case_reference(gen):
    """Case reference should appear on cover page."""
    result = gen.generate(case_reference="CASE-2024-001")

    assert "CASE-2024-001" in result["html"]


def test_generate_includes_custodian(gen):
    """Custodian should appear on cover page."""
    result = gen.generate(custodian="Evidence Manager")

    assert "Evidence Manager" in result["html"]


def test_generate_has_dossier_hash(gen):
    """Dossier should have a SHA-256 integrity hash."""
    result = gen.generate()

    assert "dossier_hash" in result
    assert len(result["dossier_hash"]) == 64
    assert result["dossier_hash"] in result["html"]


def test_generate_filters_by_relevance(gen):
    """Generate should respect min_relevance filter."""
    result = gen.generate(min_relevance=5)

    assert result["evidence_count"] == 1  # Only relevance 5


def test_generate_filters_by_category(gen):
    """Generate should respect category filter."""
    result = gen.generate(category="harassment")

    assert result["evidence_count"] == 1


def test_generate_includes_custody_log(gen):
    """HTML should contain custody log when enabled."""
    result = gen.generate(include_custody=True)

    assert "Chain-of-Custody Log" in result["html"]


def test_generate_excludes_custody_when_disabled(gen):
    """HTML should not contain custody log when disabled."""
    result = gen.generate(include_custody=False)

    assert "Chain-of-Custody Log" not in result["html"]


def test_generate_empty_evidence(gen_empty):
    """Should produce minimal dossier for empty evidence."""
    result = gen_empty.generate(title="Empty Dossier")

    assert "html" in result
    assert result["evidence_count"] == 0
    assert "Empty Dossier" in result["html"]


def test_generate_has_generated_at(gen):
    """Result should include timestamp."""
    result = gen.generate()

    assert "generated_at" in result
    assert "UTC" in result["generated_at"]


# ── generate_file ────────────────────────────────────────────


def test_generate_file_writes_html(gen):
    """generate_file should write HTML to disk."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "dossier.html")
        result = gen.generate_file(path, title="File Test")

        assert result["output_path"] == path
        assert result["format"] == "html"
        assert os.path.exists(path)

        content = open(path, encoding="utf-8").read()
        assert "File Test" in content


def test_generate_file_creates_parent_dirs(gen):
    """generate_file should create parent directories."""
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


def test_generate_without_network(gen):
    """Should omit relationship section when no network."""
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


def test_dotted_conditionals_in_loop(gen):
    """{% if email.to %} inside {% for %} should render when field is truthy."""
    result = gen.generate()
    html = result["html"]

    # To field was populated via recipients table — should appear
    assert "<strong>To:</strong>" in html
    # Literal template tags should NOT survive
    assert "{% if email.to %}" not in html


def test_dotted_conditional_false_branch(gen):
    """{% if email.cc %} should not render CC line when cc is empty."""
    result = gen.generate()
    html = result["html"]

    # CC was not populated in fixtures → CC line should be absent
    assert "<strong>CC:</strong>" not in html
    assert "{% if email.cc %}" not in html


# ── evidence numbering and cross-references ──────────────────


def test_evidence_numbering(gen):
    """Evidence items should have E-1, E-2, E-3 numbers."""
    result = gen.generate()
    html = result["html"]

    assert "E-1" in html
    assert "E-2" in html
    assert "E-3" in html


def test_appendix_numbering(gen):
    """Source emails should have A-1, A-2, A-3 numbers."""
    result = gen.generate()
    html = result["html"]

    assert "A-1" in html
    assert "A-2" in html
    assert "A-3" in html


def test_cross_references(gen):
    """Evidence items should link to their source appendix."""
    result = gen.generate()
    html = result["html"]

    # Evidence items reference appendix numbers (link inside anchor tag)
    assert "Appendix" in html
    assert "#appendix-A-" in html


# ── executive summary ────────────────────────────────────────


def test_executive_summary_present(gen):
    """Executive summary should appear when evidence exists."""
    result = gen.generate()
    html = result["html"]

    assert "Executive Summary" in html
    assert "evidence items" in html
    assert "source emails" in html


def test_executive_summary_absent_when_empty(gen_empty):
    """Executive summary should not appear with no evidence."""
    result = gen_empty.generate()
    html = result["html"]

    assert "Executive Summary" not in html


def test_category_breakdown_table(gen):
    """Category breakdown table should list categories with counts."""
    result = gen.generate()
    html = result["html"]

    assert "Category Breakdown" in html
    assert "harassment" in html
    assert "discrimination" in html
    assert "retaliation" in html


def test_glossary_present(gen):
    """Category glossary should appear with definitions."""
    result = gen.generate()
    html = result["html"]

    assert "Category Definitions" in html
    # Check at least one definition text
    assert "Hostile behavior" in html


# ── quote highlighting and attachments ───────────────────────


def test_evidence_quote_banner(gen):
    """Quote text should appear in source email section as banners."""
    result = gen.generate()
    html = result["html"]

    # Evidence quotes are "evidence content" from fixtures
    assert "evidence-quote-banner" in html
    assert "evidence content" in html


def test_attachment_info_in_appendix(db, gen):
    """Emails with attachments should show attachment bar."""
    # Insert an attachment for uid-1
    db.conn.execute(
        "INSERT INTO attachments (email_uid, name, mime_type, size) VALUES (?, ?, ?, ?)",
        ("uid-1", "report.pdf", "application/pdf", 12345),
    )
    db.conn.commit()

    result = gen.generate()
    html = result["html"]

    assert "report.pdf" in html
    assert "attachment-bar" in html


def test_no_attachment_bar_when_none(gen):
    """Emails without attachments should not show attachment bar."""
    result = gen.generate()
    html = result["html"]

    # No attachments in basic fixtures — div should not appear (CSS class still in <style>)
    assert '<div class="attachment-bar">' not in html


# ── print/PDF and date formatting ────────────────────────────


def test_date_formatting(gen):
    """Dates should appear in human-readable format with month names."""
    result = gen.generate()
    html = result["html"]

    # "2024-01-11" should become "January 11, 2024"
    assert "January" in html


def test_created_at_shown(gen):
    """Evidence items should show when they were collected."""
    result = gen.generate()
    html = result["html"]

    assert "Collected:" in html


def test_content_sha256_fallback(db, gen):
    """Empty SHA-256 should show fallback text."""
    # Insert email with empty hash
    db.conn.execute(
        """INSERT INTO emails (uid, sender_email, sender_name, date, subject,
           body_text, body_html, has_attachments, attachment_count,
           priority, is_read, body_length, content_sha256)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 1, 10, ?)""",
        ("uid-nohash", "test@test.com", "Test", "2024-01-20", "No Hash",
         "Body", "<p>Body</p>", ""),
    )
    db.conn.execute(
        "INSERT INTO recipients(email_uid, address, display_name, type) VALUES(?,?,?,?)",
        ("uid-nohash", "r@test.com", "R", "to"),
    )
    db.conn.commit()
    db.add_evidence("uid-nohash", "general", "test quote", "Test", 1)

    result = gen.generate()
    html = result["html"]

    assert "(not available)" in html


def test_print_css_avoids_breaks(gen):
    """Print CSS should prevent page breaks inside items."""
    result = gen.generate()
    html = result["html"]

    assert "break-inside: avoid" in html


def test_print_css_removes_scroll(gen):
    """Print CSS should remove scroll containers."""
    result = gen.generate()
    html = result["html"]

    assert "max-height: none" in html


# ── evidence details: thread topic, notes ────────────────────


def test_thread_topic_shown(db, gen):
    """Thread topic should appear when populated on source email."""
    db.conn.execute(
        "UPDATE emails SET thread_topic = ? WHERE uid = ?",
        ("Budget Discussion", "uid-1"),
    )
    db.conn.commit()

    result = gen.generate()
    html = result["html"]

    assert "Budget Discussion" in html
    assert "<strong>Thread:</strong>" in html


def test_notes_hidden_when_empty(gen):
    """Notes label should not appear for items with no notes."""
    result = gen.generate()
    html = result["html"]

    # Default fixtures have empty notes — "Notes:" should be absent
    assert "<strong>Notes:</strong>" not in html


def test_notes_shown_when_present(db, gen):
    """Notes text should appear when populated."""
    # Update the first evidence item to have notes
    db.conn.execute(
        "UPDATE evidence_items SET notes = ? WHERE id = 1",
        ("Important context about this incident.",),
    )
    db.conn.commit()

    result = gen.generate()
    html = result["html"]

    assert "Important context about this incident." in html
    assert "<strong>Notes:</strong>" in html


# ── Commit 1: JS elimination — server-side rendering ─────────


def test_verified_badge_text(gen):
    """Verified badges should show 'Verified'/'Unverified' text, not raw values."""
    result = gen.generate()
    html = result["html"]

    assert "Verified" in html
    assert "data-verified" not in html


def test_no_javascript_in_output(gen):
    """No <script> tags should appear in the generated HTML."""
    result = gen.generate()
    html = result["html"]

    assert "<script>" not in html
    assert "<script " not in html


def test_strip_html_tags_removes_style_content():
    """strip_html_tags should remove <style> blocks including CSS text."""
    text = '<style>.foo { color: red; }</style><p>Hello</p>'
    result = strip_html_tags(text)
    assert "color: red" not in result
    assert "Hello" in result


def test_strip_html_tags_removes_script_content():
    """strip_html_tags should remove <script> blocks including JS code."""
    text = '<script>var x = 1; alert("hi");</script><p>Content</p>'
    result = strip_html_tags(text)
    assert "alert" not in result
    assert "Content" in result


def test_strip_html_tags_removes_html_comments():
    """strip_html_tags should remove HTML comments like <!--[if gte mso 9]-->."""
    text = '<!--[if gte mso 9]><xml><o:OfficeDocumentSettings></o:OfficeDocumentSettings></xml><![endif]-->Hello world'
    result = strip_html_tags(text)
    assert "<!--" not in result
    assert "mso" not in result
    assert "Hello world" in result


# ── Commit 2: Verification banner + evidence index table ─────


def test_verification_banner_all_verified(gen):
    """Green banner should appear when all quotes are verified."""
    result = gen.generate()
    html = result["html"]

    assert "banner-ok" in html or "banner-warn" in html
    assert "verification-banner" in html


def test_verification_banner_partial(db, gen):
    """Warning banner should appear when some quotes are unverified."""
    # Add an evidence item with a quote that won't match the body
    db.add_evidence("uid-1", "general", "nonexistent quote xyz", "Unverifiable", 2)
    result = gen.generate()
    html = result["html"]

    assert "banner-warn" in html
    assert "unverified" in html.lower()


def test_evidence_index_table(gen):
    """Evidence index table should appear with correct structure."""
    result = gen.generate()
    html = result["html"]

    assert "Index of Evidence" in html
    assert "evidence-index-table" in html
    assert "E-1" in html


def test_toc_includes_index(gen):
    """TOC should contain link to evidence index."""
    result = gen.generate()
    html = result["html"]

    assert "#evidence-index" in html


# ── Commit 3: Star-glyph relevance + B&W safety ─────────────


def test_relevance_stars(gen):
    """Relevance-5 item should show ★★★★★ stars."""
    result = gen.generate()
    html = result["html"]

    assert "\u2605\u2605\u2605\u2605\u2605" in html  # 5 filled stars
    assert "relevance-stars" in html  # Star span CSS class present


def test_relevance_stars_mixed(gen):
    """Relevance-3 item should show ★★★☆☆."""
    result = gen.generate()
    html = result["html"]

    assert "\u2605\u2605\u2605\u2606\u2606" in html  # 3 filled, 2 empty


def test_print_css_bw_badges(gen):
    """Print CSS should make badges B&W safe."""
    result = gen.generate()
    html = result["html"]

    assert "border: 1px solid #666" in html


# ── Commit 4: Richer source email metadata ───────────────────


def test_bcc_shown_in_appendix(db, gen):
    """BCC recipients should appear in source email header."""
    db.conn.execute(
        "INSERT INTO recipients(email_uid, address, display_name, type) VALUES(?,?,?,?)",
        ("uid-1", "secret@test.com", "Secret Person", "bcc"),
    )
    db.conn.commit()

    result = gen.generate()
    html = result["html"]

    assert "secret@test.com" in html
    assert "<strong>BCC:</strong>" in html


def test_folder_shown_in_appendix(db, gen):
    """Folder should appear in source email header when set."""
    db.conn.execute(
        "UPDATE emails SET folder = ? WHERE uid = ?",
        ("Inbox/Important", "uid-1"),
    )
    db.conn.commit()

    result = gen.generate()
    html = result["html"]

    assert "Inbox/Important" in html
    assert "<strong>Folder:</strong>" in html


def test_attachment_details_shown(db, gen):
    """Attachments should show name, mime_type, and size."""
    db.conn.execute(
        "INSERT INTO attachments (email_uid, name, mime_type, size) VALUES (?, ?, ?, ?)",
        ("uid-1", "contract.pdf", "application/pdf", 1048576),
    )
    db.conn.commit()

    result = gen.generate()
    html = result["html"]

    assert "contract.pdf" in html
    assert "application/pdf" in html
    assert "1.0 MB" in html


def test_format_file_size():
    """format_file_size should produce human-readable sizes."""
    assert format_file_size(None) == ""
    assert format_file_size(0) == ""
    assert format_file_size(500) == "500 B"
    assert format_file_size(1536) == "1.5 KB"
    assert format_file_size(2097152) == "2.0 MB"


def test_updated_at_shown_when_different(db, gen):
    """Updated date should appear when different from created_at."""
    db.conn.execute(
        "UPDATE evidence_items SET updated_at = ? WHERE id = 1",
        ("2024-06-15T10:00:00",),
    )
    db.conn.commit()

    result = gen.generate()
    html = result["html"]

    assert "<strong>Updated:</strong>" in html


# ── Commit 5: Scope, prepared-by, legal disclaimer ──────────


def test_prepared_by_on_cover(gen):
    """Prepared-by name should appear on cover page."""
    result = gen.generate(prepared_by="Jane Smith, Paralegal")
    html = result["html"]

    assert "Jane Smith, Paralegal" in html
    assert "Prepared by:" in html


def test_scope_section_no_filters(gen):
    """Scope should show archive size and 'No filters applied'."""
    result = gen.generate()
    html = result["html"]

    assert "No filters applied" in html
    assert "3</strong> emails" in html  # 3 emails in fixture


def test_scope_section_with_filters(gen):
    """Scope should list active filters."""
    result = gen.generate(category="harassment")
    html = result["html"]

    assert "Category: harassment" in html


def test_legal_disclaimer_present(gen):
    """Legal disclaimer with ESI language should appear."""
    result = gen.generate()
    html = result["html"]

    assert "electronically stored information" in html
    assert "does not constitute" in html
