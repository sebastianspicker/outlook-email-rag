"""Proof dossier generator for legal evidence export."""

from __future__ import annotations

import hashlib
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader

from .formatting import format_date, format_file_size, strip_html_tags, write_html_or_pdf

if TYPE_CHECKING:
    from .email_db import EmailDatabase
    from .network_analysis import CommunicationNetwork

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"

CATEGORY_GLOSSARY: dict[str, str] = {
    "bossing": "Intimidation, power abuse, or unreasonable demands by a superior",
    "harassment": "Hostile behavior, bullying, or unwanted conduct targeting an individual",
    "discrimination": "Unequal treatment based on protected characteristics (age, gender, race, etc.)",
    "retaliation": "Adverse action taken in response to reporting, complaining, or whistleblowing",
    "hostile_environment": "Pattern of conduct creating a toxic or intimidating workplace atmosphere",
    "micromanagement": "Excessive control, undermining autonomy, or constant surveillance of work",
    "exclusion": "Deliberate isolation from meetings, decisions, communications, or social activities",
    "gaslighting": "Denying facts, rewriting history, or questioning competence to undermine confidence",
    "workload": "Unreasonable assignments, impossible deadlines, or deliberate work overload",
    "general": "Other relevant evidence not fitting a specific category",
}


class DossierGenerator:
    """Generate comprehensive proof dossiers combining evidence, emails, and analysis."""

    def __init__(
        self,
        email_db: EmailDatabase,
        network: CommunicationNetwork | None = None,
    ) -> None:
        self._db = email_db
        self._network = network
        self._env = Environment(
            loader=FileSystemLoader(str(_TEMPLATE_DIR)),
            autoescape=True,
        )

    def preview(
        self,
        min_relevance: int | None = None,
        category: str | None = None,
    ) -> dict[str, Any]:
        """Preview dossier contents without generating HTML.

        Token-efficient check: returns counts and summary only.
        """
        evidence = self._db.evidence_timeline(
            category=category, min_relevance=min_relevance
        )

        email_uids = list({item["email_uid"] for item in evidence if item.get("email_uid")})
        categories = list({item["category"] for item in evidence})

        date_range = {}
        if evidence:
            dates = [e["date"] for e in evidence if e.get("date")]
            if dates:
                date_range = {"earliest": min(dates), "latest": max(dates)}

        return {
            "evidence_count": len(evidence),
            "email_count": len(email_uids),
            "categories": sorted(categories),
            "category_count": len(categories),
            "date_range": date_range,
            "verified_count": sum(1 for e in evidence if e.get("verified")),
        }

    def generate(
        self,
        title: str = "Proof Dossier",
        case_reference: str = "",
        custodian: str = "",
        prepared_by: str = "",
        min_relevance: int | None = None,
        category: str | None = None,
        include_relationships: bool = True,
        include_custody: bool = True,
        persons_of_interest: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generate a complete proof dossier as HTML.

        Returns:
            {"html": str, "evidence_count": int, "email_count": int,
             "dossier_hash": str, "generated_at": str}
        """
        generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        # Gather evidence
        evidence_items = self._db.evidence_timeline(
            category=category, min_relevance=min_relevance
        )
        # Enrich with email_uid from full evidence records
        enriched_items = []
        for item in evidence_items:
            full = self._db.get_evidence(item["id"])
            if full:
                enriched_items.append(full)
            else:
                enriched_items.append(item)

        # Number evidence items, format dates, enrich with thread topic and notes
        for idx, item in enumerate(enriched_items, 1):
            item["evidence_number"] = f"E-{idx}"
            item["date_formatted"] = format_date(item.get("date"))
            item["created_at_formatted"] = format_date(item.get("created_at"))
            # Fetch thread_topic from source email
            uid = item.get("email_uid")
            if uid:
                row = self._db.conn.execute(
                    "SELECT thread_topic FROM emails WHERE uid = ?", (uid,),
                ).fetchone()
                item["thread_topic"] = (row["thread_topic"] if row and row["thread_topic"] else "")
            else:
                item["thread_topic"] = ""
            # Clean up notes — hide "None" and empty strings
            raw_notes = item.get("notes") or ""
            item["notes"] = raw_notes if raw_notes and raw_notes != "None" else ""
            item["has_notes"] = bool(item["notes"])
            # Compute verified badge text server-side (JS won't run in PDF)
            is_verified = bool(item.get("verified"))
            item["verified_text"] = "Verified" if is_verified else "Unverified"
            item["verified_class"] = "badge-verified" if is_verified else "badge-unverified"
            # Star-glyph relevance (readable in B&W print)
            rel = int(item.get("relevance") or 0)
            item["relevance_stars"] = "\u2605" * rel + "\u2606" * (5 - rel)
            # Updated-at display (show only when different from created_at)
            updated_raw = item.get("updated_at") or ""
            created_raw = item.get("created_at") or ""
            item["updated_at_formatted"] = format_date(updated_raw) if updated_raw else ""
            item["has_updated"] = bool(updated_raw and updated_raw != created_raw)
            # Recipients from source email
            item["recipients"] = item.get("recipients") or ""

        # Gather source emails (deduplicated)
        email_uids = list({item.get("email_uid") for item in enriched_items if item.get("email_uid")})
        source_emails = []
        for uid in sorted(email_uids):
            full = self._db.get_email_full(uid)
            if full:
                raw_date = full.get("date", "")
                raw_sha = full.get("content_sha256", "")
                email = {
                    "uid": full["uid"],
                    "sender_name": full.get("sender_name", ""),
                    "sender_email": full.get("sender_email", ""),
                    "date": raw_date,
                    "date_formatted": format_date(raw_date),
                    "subject": full.get("subject", ""),
                    "body_text": strip_html_tags(full.get("body_text")),
                    "content_sha256": raw_sha,
                    "content_sha256_display": raw_sha or "(not available)",
                    "to": ", ".join(full.get("to", [])),
                    "cc": ", ".join(full.get("cc", [])),
                    "bcc": ", ".join(full.get("bcc", [])),
                    "folder": full.get("folder", ""),
                }
                source_emails.append(email)

        # Number source emails and cross-reference with evidence
        uid_to_appendix: dict[str, str] = {}
        for idx, email in enumerate(source_emails, 1):
            email["appendix_number"] = f"A-{idx}"
            uid_to_appendix[email["uid"]] = f"A-{idx}"
            refs = [it["evidence_number"] for it in enriched_items if it.get("email_uid") == email["uid"]]
            email["evidence_refs_str"] = ", ".join(refs)

        for item in enriched_items:
            item["appendix_ref"] = uid_to_appendix.get(item.get("email_uid", ""), "")

        # Build quote map for highlighting in source emails
        email_quotes: dict[str, list[dict[str, str]]] = defaultdict(list)
        for item in enriched_items:
            uid = item.get("email_uid")
            quote = item.get("key_quote")
            if uid and quote:
                email_quotes[uid].append({
                    "quote": str(quote),
                    "evidence_number": item.get("evidence_number", ""),
                    "category": item.get("category", ""),
                })

        # Enrich source emails with quotes and attachment data
        for email in source_emails:
            uid = email["uid"]
            email["evidence_quotes"] = email_quotes.get(uid, [])
            full = self._db.get_email_full(uid)
            attachments = full.get("attachments", []) if full else []
            email["attachment_count"] = str(len(attachments))
            email["attachment_list"] = [
                {
                    "name": a.get("name", "unnamed"),
                    "mime_type": a.get("mime_type", ""),
                    "size_display": format_file_size(a.get("size")),
                }
                for a in attachments
            ]
            email["has_attachments"] = bool(attachments)

        # Gather relationship data
        relationship_data = []
        if include_relationships and self._network:
            targets = persons_of_interest or []
            if not targets:
                # Auto-detect from evidence senders
                targets = list({
                    item.get("sender_email")
                    for item in enriched_items
                    if item.get("sender_email")
                })
            for email_addr in sorted(targets)[:10]:  # Cap at 10
                profile = self._network.relationship_summary(email_addr)
                relationship_data.append(profile)

        # Gather custody events
        custody_events = []
        if include_custody:
            custody_events = self._db.get_custody_chain(limit=500)

        # Count categories and verified
        categories = {item.get("category") for item in enriched_items if item.get("category")}
        verified_count = sum(1 for item in enriched_items if item.get("verified"))

        # Executive summary data
        category_counts = Counter(
            item.get("category") for item in enriched_items if item.get("category")
        )
        category_breakdown = [
            {"category": c, "count": str(n)}
            for c, n in sorted(category_counts.items(), key=lambda x: -x[1])
        ]
        dates = [item.get("date") for item in enriched_items if item.get("date")]
        date_earliest = min(dates)[:10] if dates else ""
        date_latest = max(dates)[:10] if dates else ""
        unique_sender_count = len({
            item.get("sender_email") for item in enriched_items if item.get("sender_email")
        })
        dominant_category = category_breakdown[0]["category"] if category_breakdown else ""
        dominant_count = category_breakdown[0]["count"] if category_breakdown else "0"

        # Glossary filtered to present categories
        glossary_items = [
            {"category": cat, "definition": CATEGORY_GLOSSARY[cat]}
            for cat in sorted(categories)
            if cat in CATEGORY_GLOSSARY
        ]

        has_evidence = bool(enriched_items)
        has_glossary = bool(glossary_items)

        # Verification banner data
        total_evidence = len(enriched_items)
        unverified_count = total_evidence - verified_count
        all_verified = total_evidence > 0 and unverified_count == 0
        verification_banner_class = "banner-ok" if all_verified else "banner-warn"

        # Evidence index table (compact overview for navigation)
        evidence_index = []
        for item in enriched_items:
            raw_date = item.get("date", "")
            date_short = raw_date[:10] if raw_date else ""
            sender_short = item.get("sender_name") or item.get("sender_email", "")
            if len(sender_short) > 30:
                sender_short = sender_short[:27] + "..."
            summary_raw = item.get("summary", "")
            summary_short = summary_raw[:80] + "..." if len(summary_raw) > 80 else summary_raw
            idx_rel = int(item.get("relevance") or 0)
            evidence_index.append({
                "evidence_number": item.get("evidence_number", ""),
                "category": item.get("category", ""),
                "date_short": date_short,
                "sender_short": sender_short,
                "summary_short": summary_short,
                "relevance": item.get("relevance", ""),
                "relevance_stars": "\u2605" * idx_rel + "\u2606" * (5 - idx_rel),
            })
        has_evidence_index = bool(evidence_index)

        # Scope data — what the dossier covers
        scope_parts = []
        if category:
            scope_parts.append(f"Category: {category}")
        if min_relevance:
            scope_parts.append(f"Minimum relevance: {min_relevance}/5")
        scope_filter_text = (
            "Filters applied: " + ", ".join(scope_parts) + "."
            if scope_parts
            else "No filters applied \u2014 all evidence items included."
        )
        # Archive totals from lightweight SQL
        archive_row = self._db.conn.execute(
            "SELECT COUNT(*) as total, MIN(date) as earliest, MAX(date) as latest FROM emails"
        ).fetchone()
        archive_total = archive_row["total"] if archive_row else 0
        archive_earliest = (archive_row["earliest"] or "")[:10] if archive_row else ""
        archive_latest = (archive_row["latest"] or "")[:10] if archive_row else ""

        # Render template
        template_vars = {
            "title": title,
            "case_reference": case_reference,
            "custodian": custodian,
            "generated_at": generated_at,
            "evidence_count": len(enriched_items),
            "email_count": len(source_emails),
            "verified_count": verified_count,
            "category_count": len(categories),
            "date_earliest": date_earliest,
            "date_latest": date_latest,
            "unique_sender_count": unique_sender_count,
            "dominant_category": dominant_category,
            "dominant_count": dominant_count,
            "category_breakdown": category_breakdown,
            "glossary_items": glossary_items,
            "has_evidence": has_evidence,
            "has_glossary": has_glossary,
            "evidence_items": enriched_items,
            "source_emails": source_emails,
            "include_relationships": include_relationships and bool(relationship_data),
            "relationship_data": relationship_data,
            "include_custody": include_custody,
            "custody_events": custody_events,
            "all_verified": all_verified,
            "unverified_count": unverified_count,
            "verification_banner_class": verification_banner_class,
            "evidence_index": evidence_index,
            "has_evidence_index": has_evidence_index,
            "prepared_by": prepared_by,
            "scope_filter_text": scope_filter_text,
            "archive_total": archive_total,
            "archive_earliest": archive_earliest,
            "archive_latest": archive_latest,
            "dossier_hash": "",  # Placeholder, computed after render
        }

        # Use a fixed placeholder for the hash, render, then compute the
        # hash on the final document and replace the placeholder in-place.
        # This ensures the embedded hash matches the actual file content.
        hash_placeholder = "%%DOSSIER_SHA256_HASH%%"
        template_vars["dossier_hash"] = hash_placeholder
        html = self._render_template(template_vars)

        # Compute hash of the final document (with placeholder still in it)
        # then replace placeholder → the hash itself is NOT part of the
        # hashed content, which is standard for self-referencing hashes.
        dossier_hash = hashlib.sha256(html.encode("utf-8")).hexdigest()
        html = html.replace(hash_placeholder, dossier_hash)

        return {
            "html": html,
            "evidence_count": len(enriched_items),
            "email_count": len(source_emails),
            "dossier_hash": dossier_hash,
            "generated_at": generated_at,
        }

    def generate_file(
        self,
        output_path: str,
        fmt: str = "html",
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate and write dossier to a file.

        Returns:
            {"output_path": str, "format": str, "evidence_count": int, "dossier_hash": str}
        """
        result = self.generate(**kwargs)
        result_meta = write_html_or_pdf(result["html"], output_path, fmt)
        result_meta.update(
            evidence_count=result["evidence_count"],
            email_count=result["email_count"],
            dossier_hash=result["dossier_hash"],
        )
        return result_meta

    def _render_template(self, variables: dict[str, Any]) -> str:
        """Render the dossier HTML template with Jinja2."""
        template = self._env.get_template("dossier.html")
        return template.render(**variables)


