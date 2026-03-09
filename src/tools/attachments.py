"""Attachment browsing and discovery MCP tools."""

from __future__ import annotations

from ..mcp_models import ListAttachmentsInput, SearchByAttachmentInput
from .utils import json_response, run_with_db


def register(mcp, deps) -> None:
    """Register attachment discovery tools."""

    @mcp.tool(
        name="email_list_attachments",
        annotations=deps.tool_annotations("List Attachments"),
    )
    async def email_list_attachments(params: ListAttachmentsInput) -> str:
        """Browse all attachments in the archive with optional filters.

        Lists attachment name, type, size, and the email they belong to.
        Filter by filename, extension, MIME type, or sender. Supports pagination.
        """
        def _work(db):
            result = db.list_attachments(
                filename=params.filename, extension=params.extension,
                mime_type=params.mime_type, sender=params.sender,
                limit=params.limit, offset=params.offset,
            )
            return json_response(result)
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="email_search_by_attachment",
        annotations=deps.tool_annotations("Search by Attachment"),
    )
    async def email_search_by_attachment(params: SearchByAttachmentInput) -> str:
        """Find emails that have attachments matching the given criteria.

        Returns emails with their matching attachment names. Filter by
        filename (partial match), file extension, or MIME type.
        """
        def _work(db):
            results = db.search_emails_by_attachment(
                filename=params.filename, extension=params.extension,
                mime_type=params.mime_type, limit=params.limit,
            )
            return json_response({"emails": results, "count": len(results)})
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="email_attachment_stats",
        annotations=deps.tool_annotations("Attachment Statistics"),
    )
    async def email_attachment_stats() -> str:
        """Get aggregate statistics about attachments in the archive.

        Returns total count, total size, emails with attachments,
        distribution by file extension, and most common filenames.
        """
        def _work(db):
            return json_response(db.attachment_stats())
        return await run_with_db(deps, _work)
