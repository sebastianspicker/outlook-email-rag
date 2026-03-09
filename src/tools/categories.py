"""Category and calendar browsing MCP tools."""

from __future__ import annotations

from ..mcp_models import BrowseCalendarInput, ListCategoriesInput
from .utils import json_response, run_with_db


def register(mcp, deps) -> None:
    """Register category and calendar tools."""

    @mcp.tool(
        name="email_list_categories",
        annotations=deps.tool_annotations("List Email Categories"),
    )
    async def email_list_categories(params: ListCategoriesInput) -> str:
        """List all Outlook categories with email counts.

        Returns category names and how many emails are tagged with each.
        Useful for filtering searches by category.
        """
        def _work(db):
            cats = db.category_counts()
            if not cats:
                return json_response({"categories": [], "message": "No categories found."})
            return json_response({"categories": cats[:params.limit], "total": len(cats)})
        return await run_with_db(deps, _work)

    @mcp.tool(
        name="email_browse_calendar",
        annotations=deps.tool_annotations("Browse Calendar Emails"),
    )
    async def email_browse_calendar(params: BrowseCalendarInput) -> str:
        """Browse calendar/meeting invitation emails with optional date filtering.

        Returns emails that were identified as calendar messages by Outlook.
        """
        def _work(db):
            emails = db.calendar_emails(
                date_from=params.date_from, date_to=params.date_to, limit=params.limit,
            )
            return json_response({"emails": emails, "count": len(emails)})
        return await run_with_db(deps, _work)
