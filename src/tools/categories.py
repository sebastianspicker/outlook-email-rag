"""Category and calendar browsing MCP tools."""

from __future__ import annotations

import json

from pydantic import Field

from ..mcp_models import DateRangeInput, PlainInput, StrictInput


class ListCategoriesInput(PlainInput):
    """Input for listing categories."""

    limit: int = Field(default=50, ge=1, le=200, description="Max categories to return.")


class BrowseCalendarInput(DateRangeInput, StrictInput):
    """Input for browsing calendar/meeting emails."""

    limit: int = Field(default=30, ge=1, le=100, description="Max emails to return.")


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
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE

        cats = db.category_counts()
        if not cats:
            return json.dumps({"categories": [], "message": "No categories found."})

        return json.dumps({
            "categories": cats[:params.limit],
            "total": len(cats),
        }, indent=2)

    @mcp.tool(
        name="email_browse_calendar",
        annotations=deps.tool_annotations("Browse Calendar Emails"),
    )
    async def email_browse_calendar(params: BrowseCalendarInput) -> str:
        """Browse calendar/meeting invitation emails with optional date filtering.

        Returns emails that were identified as calendar messages by Outlook.
        """
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE

        emails = db.calendar_emails(
            date_from=params.date_from,
            date_to=params.date_to,
            limit=params.limit,
        )
        return json.dumps({
            "emails": emails,
            "count": len(emails),
        }, indent=2)
