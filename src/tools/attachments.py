"""Attachment browsing and discovery MCP tools."""

from __future__ import annotations

from typing import Optional

from pydantic import Field

from ..mcp_models import StrictInput
from .utils import json_response, run_with_db


class ListAttachmentsInput(StrictInput):
    """Input for browsing attachments."""

    filename: Optional[str] = Field(default=None, description="Filter by filename (partial match).")
    extension: Optional[str] = Field(default=None, description="Filter by file extension, e.g. 'pdf'.")
    mime_type: Optional[str] = Field(default=None, description="Filter by MIME type (partial match).")
    sender: Optional[str] = Field(default=None, description="Filter by sender name or email (partial match).")
    limit: int = Field(default=50, ge=1, le=200, description="Max attachments to return.")
    offset: int = Field(default=0, ge=0, description="Pagination offset.")


class SearchByAttachmentInput(StrictInput):
    """Input for finding emails by attachment."""

    filename: Optional[str] = Field(default=None, description="Match attachment filename (partial).")
    extension: Optional[str] = Field(default=None, description="Match file extension, e.g. 'xlsx'.")
    mime_type: Optional[str] = Field(default=None, description="Match MIME type (partial).")
    limit: int = Field(default=50, ge=1, le=200, description="Max emails to return.")


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
