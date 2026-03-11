"""Evidence management, chain of custody, and proof dossier MCP tools."""

from __future__ import annotations

from ..mcp_models import (
    CustodyChainInput,
    EmailDossierInput,
    EmailProvenanceInput,
    EvidenceAddBatchInput,
    EvidenceAddInput,
    EvidenceExportInput,
    EvidenceGetInput,
    EvidenceOverviewInput,
    EvidenceProvenanceInput,
    EvidenceQueryInput,
    EvidenceRemoveInput,
    EvidenceUpdateInput,
)
from .utils import json_error, json_response, run_with_db


def _compact_evidence_items(items: list[dict]) -> None:
    """Strip heavy fields from evidence items for compact mode (in-place)."""
    for item in items:
        quote = item.pop("key_quote", "")
        item["quote_preview"] = (quote[:80] + "...") if len(quote) > 80 else quote
        item.pop("notes", None)
        item.pop("content_hash", None)


def register(mcp, deps) -> None:
    """Register evidence, custody, and dossier tools."""

    # ── Chain of Custody ──────────────────────────────────────────

    @mcp.tool(
        name="custody_chain",
        annotations=deps.tool_annotations("View Chain-of-Custody Audit Trail"),
    )
    async def custody_chain(params: CustodyChainInput) -> str:
        """View the chain-of-custody audit trail for evidence handling.

        Shows a chronological log of all evidence lifecycle events.
        Compact mode (default) omits verbose details JSON.
        """
        def _work(db):
            events = db.get_custody_chain(
                target_type=params.target_type, target_id=params.target_id,
                action=params.action, limit=params.limit,
            )
            if params.compact:
                for event in events:
                    event.pop("details", None)
                    event.pop("content_hash", None)
            return json_response({"events": events, "count": len(events)}, default=str)
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="email_provenance",
        annotations=deps.tool_annotations("Email Provenance & Source Tracing"),
    )
    async def email_provenance(params: EmailProvenanceInput) -> str:
        """Full provenance for an email: OLM source hash, ingestion run, custody events."""
        def _work(db):
            return json_response(db.email_provenance(params.email_uid), default=str)
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="evidence_provenance",
        annotations=deps.tool_annotations("Evidence Provenance & Chain"),
    )
    async def evidence_provenance(params: EvidenceProvenanceInput) -> str:
        """Full evidence chain: item details + source email provenance + modification history."""
        def _work(db):
            return json_response(db.evidence_provenance(params.evidence_id), default=str)
        return await run_with_db(deps, _work)

    # ── Proof Dossier ─────────────────────────────────────────────

    @mcp.tool(
        name="email_dossier",
        annotations=deps.idempotent_write_annotations("Generate/Preview Proof Dossier"),
    )
    async def email_dossier(params: EmailDossierInput) -> str:
        """Generate or preview a comprehensive proof dossier.

        Set preview_only=True to check scope (counts, categories, date range)
        before generating. Default generates a full HTML/PDF dossier combining
        evidence, source emails, relationship analysis, and chain of custody.
        """
        def _work(db):
            if params.preview_only:
                from ..dossier_generator import DossierGenerator
                return json_response(DossierGenerator(db).preview(
                    min_relevance=params.min_relevance, category=params.category,
                ))

            network = None
            try:
                from ..network_analysis import CommunicationNetwork
                network = CommunicationNetwork(db)
            except Exception:
                pass

            from ..dossier_generator import DossierGenerator

            gen = DossierGenerator(db, network=network)
            result = gen.generate_file(
                output_path=params.output_path, fmt=params.format,
                title=params.title, case_reference=params.case_reference,
                custodian=params.custodian, prepared_by=params.prepared_by,
                min_relevance=params.min_relevance,
                category=params.category, include_relationships=params.include_relationships,
                include_custody=params.include_custody,
                persons_of_interest=params.persons_of_interest,
            )
            return json_response(result)
        return await run_with_db(deps, _work)

    # ── Evidence Management ───────────────────────────────────────

    @mcp.tool(
        name="evidence_add",
        annotations=deps.write_tool_annotations("Add Evidence Item"),
    )
    async def evidence_add(params: EvidenceAddInput) -> str:
        """Add an evidence item linked to a specific email.

        The key_quote MUST be an exact substring from the email body — it is
        automatically verified against stored body text. Use email_deep_context
        to read the full email body before extracting a quote.
        """
        def _work(db):
            try:
                return json_response(db.add_evidence(
                    email_uid=params.email_uid, category=params.category,
                    key_quote=params.key_quote, summary=params.summary,
                    relevance=params.relevance, notes=params.notes,
                ))
            except ValueError as exc:
                return json_error(str(exc))
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="evidence_query",
        annotations=deps.tool_annotations("Query Evidence Items"),
    )
    async def evidence_query(params: EvidenceQueryInput) -> str:
        """List, search, or view evidence timeline in one tool.

        Omit query to list all evidence. Set query to search text.
        Use sort='date' for chronological timeline view.
        Filter by category, min_relevance, or email_uid.
        """
        def _work(db):
            if params.query:
                # Search mode
                result = db.search_evidence(
                    query=params.query, category=params.category,
                    min_relevance=params.min_relevance, limit=params.limit,
                )
                if not params.include_quotes:
                    _compact_evidence_items(result["items"])
                return json_response(result)

            if params.sort == "date":
                # Timeline mode
                items = db.evidence_timeline(
                    category=params.category, min_relevance=params.min_relevance,
                    limit=params.limit, offset=params.offset,
                )
                if not params.include_quotes:
                    _compact_evidence_items(items)
                return json_response({"items": items, "total": len(items)})

            # List mode
            result = db.list_evidence(
                category=params.category, min_relevance=params.min_relevance,
                email_uid=params.email_uid, limit=params.limit, offset=params.offset,
            )
            if not params.include_quotes:
                _compact_evidence_items(result["items"])
            return json_response(result)
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="evidence_get",
        annotations=deps.tool_annotations("Get Evidence Item"),
    )
    async def evidence_get(params: EvidenceGetInput) -> str:
        """Get a single evidence item with full details including quote and verification status."""
        def _work(db):
            item = db.get_evidence(params.evidence_id)
            if not item:
                return json_error(f"Evidence item not found: {params.evidence_id}")
            return json_response(item)
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="evidence_update",
        annotations=deps.write_tool_annotations("Update Evidence Item"),
    )
    async def evidence_update(params: EvidenceUpdateInput) -> str:
        """Update an evidence item's category, quote, summary, relevance, or notes."""
        def _work(db):
            updated = db.update_evidence(
                params.evidence_id,
                category=params.category, key_quote=params.key_quote,
                summary=params.summary, relevance=params.relevance, notes=params.notes,
            )
            if not updated:
                return json_error(f"Evidence item not found: {params.evidence_id}")
            return json_response(db.get_evidence(params.evidence_id))
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="evidence_remove",
        annotations=deps.write_tool_annotations("Remove Evidence Item"),
    )
    async def evidence_remove(params: EvidenceRemoveInput) -> str:
        """Remove an evidence item by ID."""
        def _work(db):
            removed = db.remove_evidence(params.evidence_id)
            if not removed:
                return json_error(f"Evidence item not found: {params.evidence_id}")
            return json_response({"removed": params.evidence_id})
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="evidence_verify",
        annotations=deps.write_tool_annotations("Verify Evidence Quotes"),
    )
    async def evidence_verify() -> str:
        """Re-verify all evidence quotes against source email body text."""
        return await run_with_db(deps, lambda db: json_response(db.verify_evidence_quotes()))

    @mcp.tool(
        name="evidence_export",
        annotations=deps.idempotent_write_annotations("Export Evidence Report"),
    )
    async def evidence_export(params: EvidenceExportInput) -> str:
        """Export the evidence collection as an HTML report or CSV file."""
        def _work(db):
            from ..evidence_exporter import EvidenceExporter

            return json_response(EvidenceExporter(db).export_file(
                output_path=params.output_path, fmt=params.format,
                min_relevance=params.min_relevance, category=params.category,
            ))
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="evidence_overview",
        annotations=deps.tool_annotations("Evidence Overview"),
    )
    async def evidence_overview(params: EvidenceOverviewInput) -> str:
        """Evidence statistics and category breakdown in one call.

        Returns total items, verified/unverified counts, breakdown by category
        and relevance level, plus all category counts.
        """
        def _work(db):
            stats = db.evidence_stats(
                category=params.category, min_relevance=params.min_relevance,
            )
            categories = db.evidence_categories()
            return json_response({"stats": stats, "categories": categories})
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="evidence_add_batch",
        annotations=deps.write_tool_annotations("Batch Add Evidence Items"),
    )
    async def evidence_add_batch(params: EvidenceAddBatchInput) -> str:
        """Add multiple evidence items in one call (up to 20).

        Each item is independent — if one fails, others still succeed.
        """
        def _work(db):
            added: list[dict] = []
            failed: list[dict] = []
            for item in params.items:
                try:
                    result = db.add_evidence(
                        email_uid=item.email_uid, category=item.category,
                        key_quote=item.key_quote, summary=item.summary,
                        relevance=item.relevance, notes=item.notes,
                    )
                    added.append(result)
                except ValueError as exc:
                    failed.append({"email_uid": item.email_uid, "error": str(exc)})
            return json_response({
                "added": added, "failed": failed,
                "total_added": len(added), "total_failed": len(failed),
            })
        return await run_with_db(deps, _work)
