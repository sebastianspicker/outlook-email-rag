"""Email browsing and export MCP tools."""

from __future__ import annotations

from ..mcp_models import (
    BrowseInput,
    EmailExportInput,
    GetFullEmailInput,
)
from .utils import json_error, json_response, run_with_db


def register(mcp, deps) -> None:
    """Register browse and export tools."""

    @mcp.tool(
        name="email_export",
        annotations=deps.idempotent_write_annotations("Export Email as HTML/PDF"),
    )
    async def email_export(params: EmailExportInput) -> str:
        """Export a single email or conversation thread as formatted HTML/PDF.

        Provide exactly one of uid (single email) or conversation_id (thread).
        Produces a mail-client-style printout with full headers (From, To, CC,
        BCC, Date, Subject), body text, and attachment listings.

        Args:
            params: uid or conversation_id, optional output_path, format ('html'/'pdf').

        Returns:
            JSON with output_path/html content, email_count, and subject.
        """
        def _work(db):
            from ..email_exporter import EmailExporter

            exporter = EmailExporter(db)
            if params.uid:
                if params.output_path:
                    result = exporter.export_single_file(
                        params.uid, params.output_path, fmt=params.format,
                    )
                else:
                    result = exporter.export_single_html(params.uid)
            else:
                if params.output_path:
                    result = exporter.export_thread_file(
                        params.conversation_id, params.output_path, fmt=params.format,
                    )
                else:
                    result = exporter.export_thread_html(params.conversation_id)
            return json_response(result)
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="email_browse",
        annotations=deps.tool_annotations("Browse Emails Sequentially"),
    )
    async def email_browse(params: BrowseInput) -> str:
        """Browse emails sequentially in pages for systematic review.

        Use for reading through emails in order without a search query.
        For targeted searches, use email_search or email_search_structured instead.
        """
        def _work(db):
            page = db.list_emails_paginated(
                offset=params.offset, limit=params.limit,
                folder=params.folder, sender=params.sender,
                category=params.category, sort_order=params.sort_order.upper(),
            )

            if params.include_body:
                for email in page["emails"]:
                    full = db.get_email_full(email["uid"])
                    if full:
                        email["body_text"] = full.get("body_text", "")

            return json_response(page)
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="email_get_full",
        annotations=deps.tool_annotations("Get Full Email"),
    )
    async def email_get_full(params: GetFullEmailInput) -> str:
        """Read the complete body of a specific email identified by UID.

        Use after finding an email via search to read its full content.
        Required before evidence_add to extract exact quotes from the body text.
        """
        def _work(db):
            email = db.get_email_full(params.uid)
            if not email:
                return json_error(f"Email not found: {params.uid}")
            return json_response(email)
        return await run_with_db(deps, _work)
