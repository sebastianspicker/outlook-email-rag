"""Proof dossier generator for legal evidence export."""

from __future__ import annotations

import hashlib
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .evidence_exporter import strip_html_tags

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

        # Number evidence items
        for idx, item in enumerate(enriched_items, 1):
            item["evidence_number"] = f"E-{idx}"

        # Gather source emails (deduplicated)
        email_uids = list({item.get("email_uid") for item in enriched_items if item.get("email_uid")})
        source_emails = []
        for uid in sorted(email_uids):
            full = self._db.get_email_full(uid)
            if full:
                email = {
                    "uid": full["uid"],
                    "sender_name": full.get("sender_name", ""),
                    "sender_email": full.get("sender_email", ""),
                    "date": full.get("date", ""),
                    "subject": full.get("subject", ""),
                    "body_text": strip_html_tags(full.get("body_text")),
                    "content_sha256": full.get("content_sha256", ""),
                    "to": ", ".join(full.get("to", [])),
                    "cc": ", ".join(full.get("cc", [])),
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
            email["attachment_names"] = ", ".join(
                a.get("name", "") for a in attachments if a.get("name")
            )
            email["has_attachments"] = "1" if attachments else ""

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

        has_evidence = "1" if enriched_items else ""
        has_glossary = "1" if glossary_items else ""

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
    # Match innermost conditionals first. Body must not contain {% if WORD %}
    # (non-dotted), but CAN contain complete {% if x.y %}...{% endif %} blocks
    # (dotted conditionals processed later inside loops).
    dotted_block = r"\{%\s*if\s+\w+\.\w+\s*%\}.*?\{%\s*endif\s*%\}"
    safe_char = r"(?!\{%\s*if\s+\w+\s*%\})."
    body = f"(?:{dotted_block}|{safe_char})*?"
    pattern = re.compile(
        r"\{%\s*if\s+(\w+)\s*%\}(" + body + r")(?:\{%\s*else\s*%\}(" + body + r"))?\{%\s*endif\s*%\}",
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

    # Body can contain dotted sub-loops ({% for x in item.field %}) which have
    # their own {% endfor %} — skip those so the outer loop doesn't break.
    dotted_for = (
        r"\{%\s*for\s+\w+\s+in\s+\w+\.\w+(?:\[:\d+\])?\s*%\}"
        r".*?"
        r"\{%\s*endfor\s*%\}"
    )
    safe_char = r"(?!\{%\s*for\s+\w+\s+in\s+\w+\s*%\})."
    loop_body = f"(?:{dotted_for}|{safe_char})*?"
    pattern = re.compile(
        r"\{%\s*for\s+(\w+)\s+in\s+(\w+)\s*%\}(" + loop_body + r")\{%\s*endfor\s*%\}",
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

            # Resolve {% if item_name.field %}...{% endif %} inside loop body
            if isinstance(item, dict):
                rendered = _resolve_dotted_conditionals(rendered, item_name, item)

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
                        if isinstance(sub_item, dict):
                            sub_rendered = _resolve_dotted_conditionals(
                                sub_rendered, sub_item_name, sub_item,
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


def _resolve_dotted_conditionals(text: str, item_name: str, item: dict) -> str:
    """Resolve {% if item_name.field %}...{% endif %} using item dict values."""
    import re

    pattern = re.compile(
        r"\{%\s*if\s+" + re.escape(item_name) + r"\.(\w+)\s*%\}"
        r"(.*?)"
        r"(?:\{%\s*else\s*%\}(.*?))?"
        r"\{%\s*endif\s*%\}",
        re.DOTALL,
    )

    def _replacer(match: re.Match) -> str:
        field_name = match.group(1)
        true_block = match.group(2)
        false_block = match.group(3) or ""
        value = item.get(field_name)
        return true_block if value else false_block

    # Multiple passes for nested conditionals
    for _ in range(5):
        new_text = pattern.sub(_replacer, text)
        if new_text == text:
            break
        text = new_text

    return text


def _escape_html(text: str) -> str:
    """Basic HTML escaping."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
