"""Export evidence collection as HTML report or CSV for legal review.

Renders the evidence table with verification status, category breakdown,
and an appendix containing the full source email text for each finding.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape

from .formatting import strip_html_tags, write_html_or_pdf

if TYPE_CHECKING:
    from .email_db import EmailDatabase

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


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

        # Gather full email bodies for the appendix (batch)
        all_uids = list({item["email_uid"] for item in items if item.get("email_uid")})
        batch = self._db.get_emails_full_batch(all_uids)
        appendix_emails = []
        for item in items:
            full = batch.get(item.get("email_uid", ""))
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
        if fmt.lower() == "csv":
            result = self.export_csv(min_relevance=min_relevance, category=category)
            Path(output_path).parent.mkdir(parents=True, exist_ok=True)
            Path(output_path).write_text(result["csv"], encoding="utf-8")
            return {
                "output_path": output_path,
                "format": "csv",
                "item_count": result["item_count"],
            }

        # HTML or PDF
        result = self.export_html(min_relevance=min_relevance, category=category)
        result_meta = write_html_or_pdf(result["html"], output_path, fmt)
        result_meta["item_count"] = result["item_count"]
        return result_meta

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
