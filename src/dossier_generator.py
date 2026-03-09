"""Proof dossier generator for legal evidence export."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .evidence_exporter import strip_html_tags

if TYPE_CHECKING:
    from .email_db import EmailDatabase
    from .network_analysis import CommunicationNetwork

logger = logging.getLogger(__name__)

_TEMPLATE_DIR = Path(__file__).parent / "templates"


class DossierGenerator:
    """Generate comprehensive proof dossiers combining evidence, emails, and analysis."""

    def __init__(
        self,
        email_db: EmailDatabase,
        network: CommunicationNetwork | None = None,
    ) -> None:
        self._db = email_db
        self._network = network

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

        # Gather source emails (deduplicated)
        email_uids = list({item.get("email_uid") for item in enriched_items if item.get("email_uid")})
        source_emails = []
        for uid in sorted(email_uids):
            row = self._db.conn.execute(
                "SELECT uid, sender_name, sender_email, date, subject, body_text, content_sha256 FROM emails WHERE uid = ?",
                (uid,),
            ).fetchone()
            if row:
                email = dict(row)
                # Strip HTML tags from body_text for readable display
                email["body_text"] = strip_html_tags(email.get("body_text"))
                source_emails.append(email)

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
            "evidence_items": enriched_items,
            "source_emails": source_emails,
            "include_relationships": include_relationships and bool(relationship_data),
            "relationship_data": relationship_data,
            "include_custody": include_custody,
            "custody_events": custody_events,
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
        html = result["html"]

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        if fmt == "pdf":
            try:
                from weasyprint import HTML  # type: ignore[import-untyped]

                HTML(string=html).write_pdf(output_path)
            except ImportError:
                # Fall back to HTML
                output_path = str(Path(output_path).with_suffix(".html"))
                Path(output_path).write_text(html, encoding="utf-8")
                fmt = "html"
        else:
            Path(output_path).write_text(html, encoding="utf-8")

        return {
            "output_path": output_path,
            "format": fmt,
            "evidence_count": result["evidence_count"],
            "email_count": result["email_count"],
            "dossier_hash": result["dossier_hash"],
        }

    @staticmethod
    def _render_template(variables: dict[str, Any]) -> str:
        """Render the dossier HTML template with simple string substitution.

        Uses a minimal Jinja2-like approach without requiring Jinja2 dependency.
        Processing order: conditionals -> loops -> variable substitution.
        """
        template_path = _TEMPLATE_DIR / "dossier.html"
        template = template_path.read_text(encoding="utf-8")

        # 1. Process conditional blocks first (before loops can expand them)
        template = _process_conditionals(template, variables)

        # 2. Process for loops
        template = _process_loops(template, variables)

        # 3. Simple variable substitution for {{ var }}
        for key, value in variables.items():
            if isinstance(value, (str, int, float, bool)):
                template = template.replace("{{ " + key + " }}", str(value))

        return template


def _process_conditionals(template: str, variables: dict[str, Any]) -> str:
    """Process {% if var %}...{% endif %} blocks."""
    import re

    # Handle {% if var %} ... {% else %} ... {% endif %}
    # and {% if var %} ... {% endif %}
    # Match innermost conditionals first (bodies must not contain {% if WORD %},
    # but allow {% if dotted.name %} which are loop-scoped and processed later)
    pattern = re.compile(
        r"\{%\s*if\s+(\w+)\s*%\}((?:(?!\{%\s*if\s+\w+\s*%\}).)*?)(?:\{%\s*else\s*%\}((?:(?!\{%\s*if\s+\w+\s*%\}).)*?))?\{%\s*endif\s*%\}",
        re.DOTALL,
    )

    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        true_block = match.group(2)
        false_block = match.group(3) or ""

        value = variables.get(var_name)
        if value:
            return true_block
        return false_block

    # Multiple passes for nested conditionals
    for _ in range(5):
        new_template = pattern.sub(replacer, template)
        if new_template == template:
            break
        template = new_template

    return template


def _replace_with_or_fallback(template: str, var_name: str, value: str) -> str:
    """Replace {{ var_name or 'fallback' }} patterns.

    If value is non-empty, uses value. Otherwise uses the fallback string.
    Handles both single and double quotes around the fallback.
    """
    import re

    pattern = re.compile(
        r"\{\{\s*" + re.escape(var_name) + r"\s+or\s+['\"]([^'\"]*?)['\"]\s*\}\}"
    )

    def replacer(match: re.Match) -> str:
        fallback = match.group(1)
        # value is already escaped by caller; fallback is a literal string from template
        return value if value else fallback

    return pattern.sub(replacer, template)


def _process_loops(template: str, variables: dict[str, Any]) -> str:
    """Process {% for item in items %}...{% endfor %} blocks."""
    import re

    pattern = re.compile(
        r"\{%\s*for\s+(\w+)\s+in\s+(\w+)\s*%\}(.*?)\{%\s*endfor\s*%\}",
        re.DOTALL,
    )

    def replacer(match: re.Match) -> str:
        item_name = match.group(1)
        list_name = match.group(2)
        body = match.group(3)

        items = variables.get(list_name, [])
        if not isinstance(items, list):
            return ""

        parts = []
        for idx, item in enumerate(items):
            rendered = body

            # Handle {{ item.field }} and {{ item.field or 'default' }} access
            if isinstance(item, dict):
                for field_key, field_val in item.items():
                    escaped_val = _escape_html(str(field_val)) if field_val is not None else ""
                    # First replace {{ item.field or 'fallback' }} patterns
                    rendered = _replace_with_or_fallback(
                        rendered, f"{item_name}.{field_key}", escaped_val,
                    )
                    # Then replace simple {{ item.field }}
                    rendered = rendered.replace(
                        "{{ " + f"{item_name}.{field_key}" + " }}",
                        escaped_val,
                    )

            # Handle {{ item }} for simple values
            rendered = rendered.replace("{{ " + item_name + " }}", _escape_html(str(item)))

            # Handle loop.last
            is_last = idx == len(items) - 1
            # {% if not loop.last %} ... {% endif %}
            rendered = re.sub(
                r"\{%\s*if\s+not\s+loop\.last\s*%\}(.*?)\{%\s*endif\s*%\}",
                lambda m: m.group(1) if not is_last else "",
                rendered,
                flags=re.DOTALL,
            )

            # Handle nested access for top_contacts[:5]
            if isinstance(item, dict):
                # Handle sub-loops like {% for c in person.top_contacts[:5] %}
                sub_loop_pattern = re.compile(
                    r"\{%\s*for\s+(\w+)\s+in\s+"
                    + re.escape(item_name)
                    + r"\.(\w+)(?:\[:(\d+)\])?\s*%\}(.*?)\{%\s*endfor\s*%\}",
                    re.DOTALL,
                )

                def sub_replacer(sub_match: re.Match) -> str:
                    sub_item_name = sub_match.group(1)
                    sub_field = sub_match.group(2)
                    sub_limit = int(sub_match.group(3)) if sub_match.group(3) else None
                    sub_body = sub_match.group(4)

                    sub_items = item.get(sub_field, [])
                    if sub_limit:
                        sub_items = sub_items[:sub_limit]

                    sub_parts = []
                    for sub_idx, sub_item in enumerate(sub_items):
                        sub_rendered = sub_body
                        if isinstance(sub_item, dict):
                            for sk, sv in sub_item.items():
                                escaped_sv = _escape_html(str(sv)) if sv is not None else ""
                                sub_rendered = _replace_with_or_fallback(
                                    sub_rendered, f"{sub_item_name}.{sk}", escaped_sv,
                                )
                                sub_rendered = sub_rendered.replace(
                                    "{{ " + f"{sub_item_name}.{sk}" + " }}",
                                    escaped_sv,
                                )
                        is_sub_last = sub_idx == len(sub_items) - 1
                        sub_rendered = re.sub(
                            r"\{%\s*if\s+not\s+loop\.last\s*%\}(.*?)\{%\s*endif\s*%\}",
                            lambda m: m.group(1) if not is_sub_last else "",
                            sub_rendered,
                            flags=re.DOTALL,
                        )
                        sub_parts.append(sub_rendered)
                    return "".join(sub_parts)

                rendered = sub_loop_pattern.sub(sub_replacer, rendered)

            parts.append(rendered)

        return "".join(parts)

    # Multiple passes for nested loops
    for _ in range(3):
        new_template = pattern.sub(replacer, template)
        if new_template == template:
            break
        template = new_template

    return template


def _escape_html(text: str) -> str:
    """Basic HTML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
