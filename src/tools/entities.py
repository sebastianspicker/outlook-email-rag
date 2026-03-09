"""Entity search and NLP entity MCP tools."""

from __future__ import annotations

import json

from ..mcp_models import (
    EntityNetworkInput,
    EntitySearchInput,
    EntityTimelineInput,
    FindPeopleInput,
    ListEntitiesInput,
)


def register(mcp, deps) -> None:
    """Register entity tools."""

    @mcp.tool(name="email_search_by_entity", annotations=deps.tool_annotations("Search by Entity"))
    async def email_search_by_entity(params: EntitySearchInput) -> str:
        """Find emails mentioning a specific entity (organization, URL, phone, etc.)."""
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            results = db.search_by_entity(params.entity, entity_type=params.entity_type, limit=params.limit)
            return json.dumps(results, indent=2)
        return await deps.offload(_run)

    @mcp.tool(name="email_list_entities", annotations=deps.tool_annotations("List Top Entities"))
    async def email_list_entities(params: ListEntitiesInput) -> str:
        """List most frequently mentioned entities in the email archive."""
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            results = db.top_entities(entity_type=params.entity_type, limit=params.limit)
            return json.dumps(results, indent=2)
        return await deps.offload(_run)

    @mcp.tool(name="email_entity_network", annotations=deps.tool_annotations("Entity Co-occurrences"))
    async def email_entity_network(params: EntityNetworkInput) -> str:
        """Find entities that co-occur with the given entity in the same emails."""
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            results = db.entity_co_occurrences(params.entity, limit=params.limit)
            return json.dumps(results, indent=2)
        return await deps.offload(_run)

    @mcp.tool(name="email_find_people", annotations=deps.tool_annotations("Find People in Emails"))
    async def email_find_people(params: FindPeopleInput) -> str:
        """Search emails by person name mentioned in the email body.

        Uses NLP-extracted person entities (names like 'John Smith', 'Dr. Mueller').
        Requires entity extraction during ingestion.

        Args:
            params: name (str) - person name to search, limit (int).

        Returns:
            JSON list of emails mentioning that person.
        """
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            results = db.people_in_emails(params.name, limit=params.limit)
            return json.dumps(results, indent=2)
        return await deps.offload(_run)

    @mcp.tool(name="email_entity_timeline", annotations=deps.tool_annotations("Entity Mention Timeline"))
    async def email_entity_timeline(params: EntityTimelineInput) -> str:
        """Show how often an entity appears over time.

        Track mention frequency of any entity (person, organization, etc.)
        across the email archive, grouped by day/week/month.

        Args:
            params: entity (str) - entity to track, period (str) - 'day'/'week'/'month'.

        Returns:
            JSON list of {period, count} entries.
        """
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            results = db.entity_timeline(params.entity, period=params.period)
            return json.dumps(results, indent=2)
        return await deps.offload(_run)
