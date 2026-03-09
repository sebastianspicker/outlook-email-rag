"""Export evidence collection as HTML report or CSV for legal review.

Renders the evidence table with verification status, category breakdown,
and an appendix containing the full source email text for each finding.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from html import unescape
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

if TYPE_CHECKING:
    from .email_db import EmailDatabase

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"

# Regex to strip HTML tags (handles multi-line tags)
_HTML_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
# Collapse runs of whitespace (but preserve paragraph breaks)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


def strip_html_tags(text: str | None) -> str:
    """Strip HTML tags from text, returning clean plain text.

    Handles common HTML email patterns: converts <br> and block elements
    to newlines, strips all remaining tags, and decodes HTML entities.
    """
    if not text:
        return ""
    # Quick check: if there are no HTML tags or entities, return as-is
    if "<" not in text and "&" not in text:
        return text
    # Text with entities but no tags — just decode entities
    if "<" not in text:
        return unescape(text)
    # Replace <br>, <br/>, <br /> with newlines
    result = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # Replace block-level closing tags with newlines for readability
    result = re.sub(
        r"</(?:p|div|tr|li|h[1-6]|blockquote|pre|table|thead|tbody|tfoot)>",
        "\n",
        result,
        flags=re.IGNORECASE,
    )
    # Strip all remaining HTML tags
    result = _HTML_TAG_RE.sub("", result)
    # Decode HTML entities (&amp; → &, &lt; → <, &#8230; → …, etc.)
    result = unescape(result)
    # Collapse excessive blank lines
    result = _MULTI_BLANK_RE.sub("\n\n", result)
    return result.strip()


class EvidenceExporter:
    """Export evidence items as HTML reports or CSV files."""

    def __init__(self, email_db: EmailDatabase) -> None:
        self._db = email_db
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=True,
        )
        def _strip_html_safe(value: str | None) -> Markup:
            """Strip HTML tags then escape for safe Jinja2 rendering."""
            cleaned = strip_html_tags(value)
            return Markup(escape(cleaned))

        self._env.filters["strip_html"] = _strip_html_safe

    # ── Public API ────────────────────────────────────────────

    def export_html(
        self,
        min_relevance: int | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Export evidence as a styled HTML report.

        Returns:
            {"html": str, "item_count": int}
            or {"error": str} on failure.
        """
        items = self._get_filtered_items(min_relevance, category)
        if not items:
            return {"html": "", "item_count": 0}

        # Gather full email bodies for the appendix
        appendix_emails = []
        for item in items:
            full = self._db.get_email_full(item["email_uid"])
            if full:
                appendix_emails.append({
                    "evidence_id": item["id"],
                    "email": full,
                    "key_quote": item.get("key_quote", ""),
                    "category": item.get("category", ""),
                })

        stats = self._db.evidence_stats()
        verified_count = sum(1 for i in items if i.get("verified"))
        total_count = len(items)

        # Date range from evidence items
        dates = [i["date"] for i in items if i.get("date")]
        date_range = {}
        if dates:
            date_range = {"earliest": min(dates)[:10], "latest": max(dates)[:10]}

        from datetime import datetime, timezone
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        template = self._env.get_template("evidence_report.html")
        html = template.render(
            items=items,
            stats=stats,
            appendix_emails=appendix_emails,
            verified_count=verified_count,
            total_count=total_count,
            date_range=date_range,
            generated_at=generated_at,
        )
        return {"html": html, "item_count": total_count}

    def export_csv(
        self,
        min_relevance: int | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Export evidence as CSV text.

        Returns:
            {"csv": str, "item_count": int}
        """
        items = self._get_filtered_items(min_relevance, category)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "id", "date", "sender_name", "sender_email", "recipients",
            "subject", "category", "key_quote", "summary", "relevance",
            "verified", "notes", "email_uid", "created_at", "updated_at",
        ])
        for item in items:
            writer.writerow([
                item.get("id", ""),
                item.get("date", ""),
                item.get("sender_name", ""),
                item.get("sender_email", ""),
                item.get("recipients", ""),
                item.get("subject", ""),
                item.get("category", ""),
                item.get("key_quote", ""),
                item.get("summary", ""),
                item.get("relevance", ""),
                "yes" if item.get("verified") else "no",
                item.get("notes", ""),
                item.get("email_uid", ""),
                item.get("created_at", ""),
                item.get("updated_at", ""),
            ])

        return {"csv": output.getvalue(), "item_count": len(items)}

    def export_file(
        self,
        output_path: str,
        fmt: str = "html",
        min_relevance: int | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Write evidence report to a file.

        Args:
            output_path: Destination file path.
            fmt: 'html', 'csv', or 'pdf'.
            min_relevance: Optional minimum relevance filter.
            category: Optional category filter.

        Returns:
            {"output_path": str, "format": str, "item_count": int}
        """
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        if fmt.lower() == "csv":
            result = self.export_csv(min_relevance=min_relevance, category=category)
            Path(output_path).write_text(result["csv"], encoding="utf-8")
            return {
                "output_path": output_path,
                "format": "csv",
                "item_count": result["item_count"],
            }

        # HTML or PDF
        result = self.export_html(min_relevance=min_relevance, category=category)

        if fmt.lower() == "pdf":
            try:
                from weasyprint import HTML  # type: ignore[import-untyped]

                HTML(string=result["html"]).write_pdf(output_path)
                return {
                    "output_path": output_path,
                    "format": "pdf",
                    "item_count": result["item_count"],
                }
            except ImportError:
                html_path = str(Path(output_path).with_suffix(".html"))
                Path(html_path).write_text(result["html"], encoding="utf-8")
                return {
                    "output_path": html_path,
                    "format": "html",
                    "item_count": result["item_count"],
                    "note": "weasyprint not installed; saved as HTML. Install with: pip install weasyprint",
                }

        # Default: HTML
        Path(output_path).write_text(result["html"], encoding="utf-8")
        return {
            "output_path": output_path,
            "format": "html",
            "item_count": result["item_count"],
        }

    # ── Internal ──────────────────────────────────────────────

    def _get_filtered_items(
        self,
        min_relevance: int | None = None,
        category: str | None = None,
    ) -> list[dict]:
        """Fetch evidence items with optional filters."""
        result = self._db.list_evidence(
            min_relevance=min_relevance,
            category=category,
            limit=10000,
        )
        return result["items"]
