"""Evidence management, chain of custody, and proof dossier MCP tools."""

from __future__ import annotations

import json

from mcp.types import ToolAnnotations

from ..mcp_models import (
    CustodyChainInput,
    DossierGenerateInput,
    DossierPreviewInput,
    EmailProvenanceInput,
    EvidenceAddBatchInput,
    EvidenceAddInput,
    EvidenceExportInput,
    EvidenceGetInput,
    EvidenceListInput,
    EvidenceProvenanceInput,
    EvidenceRemoveInput,
    EvidenceSearchInput,
    EvidenceTimelineInput,
    EvidenceUpdateInput,
)


def register(mcp, deps) -> None:
    """Register evidence, custody, and dossier tools."""

    # ── Chain of Custody ──────────────────────────────────────────

    @mcp.tool(
        name="custody_chain",
        annotations=deps.tool_annotations("View Chain-of-Custody Audit Trail"),
    )
    async def custody_chain(params: CustodyChainInput) -> str:
        """View the chain-of-custody audit trail for evidence handling.

        Shows a chronological log of all evidence lifecycle events:
        ingestion, evidence additions/updates/removals, and exports.
        Use to verify evidence handling history and integrity.
        """
        db = deps.get_email_db()
        events = db.get_custody_chain(
            target_type=params.target_type,
            target_id=params.target_id,
            action=params.action,
            limit=params.limit,
        )
        return json.dumps({"events": events, "count": len(events)}, indent=2, default=str)

    @mcp.tool(
        name="email_provenance",
        annotations=deps.tool_annotations("Email Provenance & Source Tracing"),
    )
    async def email_provenance(params: EmailProvenanceInput) -> str:
        """Full provenance for an email: OLM source hash, ingestion run, custody events.

        One call gives complete traceability from source file to stored email.
        Use to verify an email's origin and integrity for legal evidence.
        """
        db = deps.get_email_db()
        result = db.email_provenance(params.email_uid)
        return json.dumps(result, indent=2, default=str)

    @mcp.tool(
        name="evidence_provenance",
        annotations=deps.tool_annotations("Evidence Provenance & Chain"),
    )
    async def evidence_provenance(params: EvidenceProvenanceInput) -> str:
        """Full evidence chain: item details + source email provenance + modification history + content hashes.

        Combines evidence item, source email traceability, and all custody
        events in one call. Use to present complete provenance to a lawyer.
        """
        db = deps.get_email_db()
        result = db.evidence_provenance(params.evidence_id)
        return json.dumps(result, indent=2, default=str)

    # ── Proof Dossier ─────────────────────────────────────────────

    @mcp.tool(
        name="dossier_generate",
        annotations=ToolAnnotations(
            title="Generate Proof Dossier",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    async def dossier_generate(params: DossierGenerateInput) -> str:
        """Generate a comprehensive proof dossier combining evidence, source emails,
        relationship analysis, and chain-of-custody log.

        The main 'export everything for the lawyer' tool. Produces a
        self-contained HTML or PDF document with integrity hash.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE

        network = None
        try:
            from ..network_analysis import CommunicationNetwork

            network = CommunicationNetwork(db)
        except Exception:
            pass

        from ..dossier_generator import DossierGenerator

        gen = DossierGenerator(db, network=network)
        result = gen.generate_file(
            output_path=params.output_path,
            fmt=params.format,
            title=params.title,
            case_reference=params.case_reference,
            custodian=params.custodian,
            min_relevance=params.min_relevance,
            category=params.category,
            include_relationships=params.include_relationships,
            include_custody=params.include_custody,
            persons_of_interest=params.persons_of_interest,
        )
        return json.dumps(result, indent=2)

    @mcp.tool(
        name="dossier_preview",
        annotations=deps.tool_annotations("Preview Dossier Contents"),
    )
    async def dossier_preview(params: DossierPreviewInput) -> str:
        """Preview what a dossier would contain without generating it.

        Token-efficient: returns counts, categories, and date range.
        Use to check scope before full generation.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE

        from ..dossier_generator import DossierGenerator

        gen = DossierGenerator(db)
        result = gen.preview(
            min_relevance=params.min_relevance,
            category=params.category,
        )
        return json.dumps(result, indent=2)

    # ── Evidence Management ───────────────────────────────────────

    @mcp.tool(
        name="evidence_add",
        annotations=deps.write_tool_annotations("Add Evidence Item"),
    )
    async def evidence_add(params: EvidenceAddInput) -> str:
        """Add an evidence item linked to a specific email.

        The key_quote MUST be an exact substring from the email body — it is
        automatically verified against stored body text. Unverified quotes are
        flagged. Use email_get_full to read the full email body before extracting
        a quote. Sender, date, recipients, and subject are auto-populated.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        try:
            result = db.add_evidence(
                email_uid=params.email_uid,
                category=params.category,
                key_quote=params.key_quote,
                summary=params.summary,
                relevance=params.relevance,
                notes=params.notes,
            )
            return json.dumps(result, indent=2)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

    @mcp.tool(
        name="evidence_list",
        annotations=deps.tool_annotations("List Evidence Items"),
    )
    async def evidence_list(params: EvidenceListInput) -> str:
        """List evidence items with optional filters.

        Use to review the evidence collection. Filter by category, minimum
        relevance, or specific email UID. Returns paginated results sorted by date.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        result = db.list_evidence(
            category=params.category,
            min_relevance=params.min_relevance,
            email_uid=params.email_uid,
            limit=params.limit,
            offset=params.offset,
        )
        return json.dumps(result, indent=2)

    @mcp.tool(
        name="evidence_get",
        annotations=deps.tool_annotations("Get Evidence Item"),
    )
    async def evidence_get(params: EvidenceGetInput) -> str:
        """Get a single evidence item with full details including quote and verification status."""
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        item = db.get_evidence(params.evidence_id)
        if not item:
            return json.dumps({"error": f"Evidence item not found: {params.evidence_id}"})
        return json.dumps(item, indent=2)

    @mcp.tool(
        name="evidence_update",
        annotations=deps.write_tool_annotations("Update Evidence Item"),
    )
    async def evidence_update(params: EvidenceUpdateInput) -> str:
        """Update an evidence item's category, quote, summary, relevance, or notes.

        Only the fields you provide will be changed. If key_quote is updated,
        the quote is re-verified against the email body.

        Args:
            evidence_id: ID of the evidence item to update.
            category: New category (optional).
            key_quote: New key quote (optional, will be re-verified).
            summary: New summary (optional).
            relevance: New relevance rating (optional).
            notes: New notes (optional).
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        updated = db.update_evidence(
            params.evidence_id,
            category=params.category,
            key_quote=params.key_quote,
            summary=params.summary,
            relevance=params.relevance,
            notes=params.notes,
        )
        if not updated:
            return json.dumps({"error": f"Evidence item not found: {params.evidence_id}"})
        item = db.get_evidence(params.evidence_id)
        return json.dumps(item, indent=2)

    @mcp.tool(
        name="evidence_remove",
        annotations=deps.write_tool_annotations("Remove Evidence Item"),
    )
    async def evidence_remove(params: EvidenceRemoveInput) -> str:
        """Remove an evidence item by ID.

        Args:
            evidence_id: ID of the evidence item to remove.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        removed = db.remove_evidence(params.evidence_id)
        if not removed:
            return json.dumps({"error": f"Evidence item not found: {params.evidence_id}"})
        return json.dumps({"removed": params.evidence_id})

    @mcp.tool(
        name="evidence_verify",
        annotations=deps.write_tool_annotations("Verify Evidence Quotes"),
    )
    async def evidence_verify() -> str:
        """Re-verify all evidence quotes against source email body text.

        Run periodically to catch incorrectly entered quotes. Checks each
        key_quote appears (case-insensitive) in the linked email's body_text
        and updates the verified status. Critical for ensuring zero hallucination.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        result = db.verify_evidence_quotes()
        return json.dumps(result, indent=2)

    @mcp.tool(
        name="evidence_export",
        annotations=deps.tool_annotations("Export Evidence Report"),
    )
    async def evidence_export(params: EvidenceExportInput) -> str:
        """Export the evidence collection as an HTML report or CSV file.

        HTML includes summary, evidence list with verification status, and full
        source email appendix. CSV can be opened in Excel. Filter by category
        or minimum relevance to create focused exports.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE

        from ..evidence_exporter import EvidenceExporter

        exporter = EvidenceExporter(db)
        result = exporter.export_file(
            output_path=params.output_path,
            fmt=params.format,
            min_relevance=params.min_relevance,
            category=params.category,
        )
        return json.dumps(result, indent=2)

    @mcp.tool(
        name="evidence_stats",
        annotations=deps.tool_annotations("Evidence Statistics"),
    )
    async def evidence_stats() -> str:
        """Get statistics about the evidence collection.

        Returns total items, verified/unverified counts, breakdown by category
        and relevance level.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        return json.dumps(db.evidence_stats(), indent=2)

    @mcp.tool(
        name="evidence_add_batch",
        annotations=deps.write_tool_annotations("Batch Add Evidence Items"),
    )
    async def evidence_add_batch(params: EvidenceAddBatchInput) -> str:
        """Add multiple evidence items in one call (up to 20).

        Each item is independent — if one fails, others still succeed.
        Use when you find multiple evidence items in one search session.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        added: list[dict] = []
        failed: list[dict] = []
        for item in params.items:
            try:
                result = db.add_evidence(
                    email_uid=item.email_uid,
                    category=item.category,
                    key_quote=item.key_quote,
                    summary=item.summary,
                    relevance=item.relevance,
                    notes=item.notes,
                )
                added.append(result)
            except ValueError as exc:
                failed.append({"email_uid": item.email_uid, "error": str(exc)})
        return json.dumps({
            "added": added,
            "failed": failed,
            "total_added": len(added),
            "total_failed": len(failed),
        }, indent=2)

    @mcp.tool(
        name="evidence_search",
        annotations=deps.tool_annotations("Search Evidence Items"),
    )
    async def evidence_search(params: EvidenceSearchInput) -> str:
        """Search within existing evidence items by text.

        Searches across key_quote, summary, and notes fields. Use to check
        whether evidence about a topic has already been collected.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        result = db.search_evidence(
            query=params.query,
            category=params.category,
            min_relevance=params.min_relevance,
            limit=params.limit,
        )
        return json.dumps(result, indent=2)

    @mcp.tool(
        name="evidence_timeline",
        annotations=deps.tool_annotations("Evidence Timeline"),
    )
    async def evidence_timeline(params: EvidenceTimelineInput) -> str:
        """View evidence in chronological order to build a narrative.

        Returns evidence items sorted by date ascending. Use to identify
        patterns of behavior over time and construct a legal timeline.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        items = db.evidence_timeline(
            category=params.category,
            min_relevance=params.min_relevance,
        )
        return json.dumps({"items": items, "total": len(items)}, indent=2)

    @mcp.tool(
        name="evidence_categories",
        annotations=deps.tool_annotations("Evidence Categories"),
    )
    async def evidence_categories() -> str:
        """List all evidence categories with current item counts.

        Returns all 10 canonical categories (discrimination, harassment,
        sexual_harassment, insult, bossing, retaliation, exclusion,
        microaggression, hostile_environment, other) with counts.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        return json.dumps(db.evidence_categories(), indent=2)
