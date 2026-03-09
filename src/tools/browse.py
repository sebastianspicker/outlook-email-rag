"""Email browsing, export, and re-ingestion MCP tools."""

from __future__ import annotations

import json

from ..mcp_models import (
    BrowseInput,
    ExportSingleInput,
    ExportThreadInput,
    GetFullEmailInput,
    ReembedInput,
    ReingestBodiesInput,
)


def register(mcp, deps) -> None:
    """Register browse and export tools."""

    @mcp.tool(
        name="email_export_thread",
        annotations=deps.tool_annotations("Export Thread as HTML/PDF"),
    )
    async def email_export_thread(params: ExportThreadInput) -> str:
        """Export a conversation thread as formatted HTML/PDF.

        Produces a mail-client-style printout with full headers (From, To, CC,
        BCC, Date, Subject), body text, and attachment listings for every email
        in the thread.

        Args:
            params: conversation_id, optional output_path, format ('html'/'pdf').

        Returns:
            JSON with output_path/html content, email_count, and subject.
        """
        from ..email_exporter import EmailExporter

        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE

        exporter = EmailExporter(db)
        if params.output_path:
            result = exporter.export_thread_file(
                params.conversation_id, params.output_path, fmt=params.format
            )
        else:
            result = exporter.export_thread_html(params.conversation_id)
        return json.dumps(result, indent=2)

    @mcp.tool(
        name="email_export_single",
        annotations=deps.tool_annotations("Export Single Email as HTML/PDF"),
    )
    async def email_export_single(params: ExportSingleInput) -> str:
        """Export a single email as formatted HTML/PDF.

        Args:
            params: uid, optional output_path, format ('html'/'pdf').

        Returns:
            JSON with output_path/html content, email_count, and subject.
        """
        from ..email_exporter import EmailExporter

        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE

        exporter = EmailExporter(db)
        if params.output_path:
            result = exporter.export_single_file(
                params.uid, params.output_path, fmt=params.format
            )
        else:
            result = exporter.export_single_html(params.uid)
        return json.dumps(result, indent=2)

    @mcp.tool(
        name="email_browse",
        annotations=deps.tool_annotations("Browse Emails Sequentially"),
    )
    async def email_browse(params: BrowseInput) -> str:
        """Browse emails sequentially in pages for systematic review.

        Use for reading through emails in order without a search query.
        For targeted searches, use email_search or email_search_structured instead.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE

        page = db.list_emails_paginated(
            offset=params.offset,
            limit=params.limit,
            folder=params.folder,
            sender=params.sender,
            sort_order=params.sort_order.upper(),
        )

        if params.include_body:
            for email in page["emails"]:
                full = db.get_email_full(email["uid"])
                if full:
                    email["body_text"] = full.get("body_text", "")

        return json.dumps(page, indent=2)

    @mcp.tool(
        name="email_get_full",
        annotations=deps.tool_annotations("Get Full Email"),
    )
    async def email_get_full(params: GetFullEmailInput) -> str:
        """Read the complete body of a specific email identified by UID.

        Use after finding an email via search to read its full content.
        Required before evidence_add to extract exact quotes from the body text.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE

        email = db.get_email_full(params.uid)
        if not email:
            return json.dumps({"error": f"Email not found: {params.uid}"})

        return json.dumps(email, indent=2)

    @mcp.tool(
        name="email_reingest_bodies",
        annotations=deps.tool_annotations("Re-ingest Email Bodies"),
    )
    async def email_reingest_bodies(params: ReingestBodiesInput) -> str:
        """Re-parse OLM to backfill body_text/body_html for existing SQLite rows.

        Required after upgrading from an older database that did not store
        full email bodies. Re-reads the OLM file and updates rows where
        body_text is NULL.

        With force=True, re-parses ALL emails and overwrites existing body
        text **and** header fields (subject, sender_name, sender_email).
        Use after fixing the OLM parser to update truncated/dirty bodies
        or to decode MIME encoded-word subjects stored from earlier ingestions.

        Args:
            params: olm_path (str) — path to the .olm file.
                    force (bool) — overwrite all bodies and headers, not just NULL ones.

        Returns:
            JSON with update count and status message.
        """
        from ..ingest import reingest_bodies

        try:
            result = reingest_bodies(params.olm_path, force=params.force)
            return json.dumps(result, indent=2)
        except FileNotFoundError:
            return json.dumps({"error": f"OLM file not found: {params.olm_path}"})
        except Exception as exc:
            return json.dumps({"error": str(exc)})

    @mcp.tool(
        name="email_reembed",
        annotations=deps.tool_annotations("Re-embed All Emails"),
    )
    async def email_reembed(params: ReembedInput) -> str:
        """Re-chunk and re-embed all emails from corrected SQLite body text.

        Reads the (already fixed) body_text from SQLite, re-chunks each email,
        and upserts new embeddings into ChromaDB.  Run this after
        ``--reingest-bodies --force`` to rebuild search quality without a full
        reset-and-reingest cycle.

        Args:
            params: batch_size (int) — chunks per embedding batch (default 100).

        Returns:
            JSON with re-embed count, chunk stats, and status message.
        """
        from ..ingest import reembed

        try:
            result = reembed(batch_size=params.batch_size)
            return json.dumps(result, indent=2)
        except Exception as exc:
            return json.dumps({"error": str(exc)})
