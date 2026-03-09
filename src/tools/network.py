"""Network analysis MCP tools."""

from __future__ import annotations

import json

from ..mcp_models import (
    CommunicationBetweenInput,
    CoordinatedTimingInput,
    NetworkAnalysisInput,
    RelationshipPathsInput,
    RelationshipSummaryInput,
    SharedRecipientsInput,
    TopContactsInput,
)


def _get_network(deps):
    """Get or create a cached CommunicationNetwork for the current db."""
    db = deps.get_email_db()
    if not db:
        return None, None
    # Cache on the db instance to avoid rebuilding the graph per tool call
    net = getattr(db, "_cached_comm_network", None)
    if net is None:
        from ..network_analysis import CommunicationNetwork

        net = CommunicationNetwork(db)
        db._cached_comm_network = net
    return db, net


def register(mcp, deps) -> None:
    """Register network analysis tools."""

    @mcp.tool(name="email_top_contacts", annotations=deps.tool_annotations("Top Email Contacts"))
    async def email_top_contacts(params: TopContactsInput) -> str:
        """Find top communication partners for an email address.

        Returns contacts ranked by total bidirectional email frequency
        (emails sent to + received from each partner).
        """
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            contacts = db.top_contacts(params.email_address, limit=params.limit)
            return json.dumps(contacts, indent=2)
        return await deps.offload(_run)

    @mcp.tool(
        name="email_communication_between",
        annotations=deps.tool_annotations("Communication Between Two Contacts"),
    )
    async def email_communication_between(params: CommunicationBetweenInput) -> str:
        """Get bidirectional communication stats between two email addresses."""
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            result = db.communication_between(params.email_a, params.email_b)
            return json.dumps(result, indent=2)
        return await deps.offload(_run)

    @mcp.tool(name="email_network_analysis", annotations=deps.tool_annotations("Email Network Analysis"))
    async def email_network_analysis(params: NetworkAnalysisInput) -> str:
        """Analyze the communication network: centrality, communities, bridge nodes."""
        def _run():
            db, net = _get_network(deps)
            if not db:
                return deps.DB_UNAVAILABLE
            result = net.network_analysis(top_n=params.top_n)
            return json.dumps(result, indent=2)
        return await deps.offload(_run)

    @mcp.tool(
        name="relationship_paths",
        annotations=deps.tool_annotations("Find Communication Paths"),
    )
    async def relationship_paths(params: RelationshipPathsInput) -> str:
        """Find communication paths between two people via intermediaries.

        Shows how person A connects to person B through shared contacts.
        Useful for mapping relationships in investigations and legal analysis.
        """
        def _run():
            db, net = _get_network(deps)
            if not db:
                return deps.DB_UNAVAILABLE
            paths = net.find_paths(
                source=params.source,
                target=params.target,
                max_hops=params.max_hops,
                top_k=params.top_k,
            )
            return json.dumps({"paths": paths, "count": len(paths)}, indent=2)
        return await deps.offload(_run)

    @mcp.tool(
        name="shared_recipients",
        annotations=deps.tool_annotations("Find Shared Recipients"),
    )
    async def shared_recipients(params: SharedRecipientsInput) -> str:
        """Find recipients common to multiple senders.

        Identifies who is 'in the loop' on coordinated communications.
        Useful for discovering shared targets or information brokers.
        """
        def _run():
            db, net = _get_network(deps)
            if not db:
                return deps.DB_UNAVAILABLE
            results = net.shared_recipients(
                email_addresses=params.email_addresses,
                min_shared=params.min_shared,
            )
            return json.dumps({"shared_recipients": results, "count": len(results)}, indent=2)
        return await deps.offload(_run)

    @mcp.tool(
        name="coordinated_timing",
        annotations=deps.tool_annotations("Detect Coordinated Timing"),
    )
    async def coordinated_timing(params: CoordinatedTimingInput) -> str:
        """Detect time windows where multiple senders were emailing together.

        Finds synchronized communication patterns: periods where multiple
        people were actively emailing within the same time window.
        """
        def _run():
            db, net = _get_network(deps)
            if not db:
                return deps.DB_UNAVAILABLE
            windows = net.coordinated_timing(
                email_addresses=params.email_addresses,
                window_hours=params.window_hours,
                min_events=params.min_events,
            )
            return json.dumps({"windows": windows, "count": len(windows)}, indent=2)
        return await deps.offload(_run)

    @mcp.tool(
        name="relationship_summary",
        annotations=deps.tool_annotations("Person Relationship Profile"),
    )
    async def relationship_summary(params: RelationshipSummaryInput) -> str:
        """Comprehensive relationship profile for one person.

        One-call profile: top contacts, community membership, bridge score,
        send/receive ratio. Saves tokens compared to multiple separate calls.
        """
        def _run():
            db, net = _get_network(deps)
            if not db:
                return deps.DB_UNAVAILABLE
            result = net.relationship_summary(
                email_address=params.email_address,
                limit=params.limit,
            )
            return json.dumps(result, indent=2)
        return await deps.offload(_run)
