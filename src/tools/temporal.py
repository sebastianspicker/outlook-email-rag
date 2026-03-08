"""Temporal analysis MCP tools."""

from __future__ import annotations

import json

from ..mcp_models import ResponseTimesInput, VolumeOverTimeInput


def register(mcp, deps) -> None:
    """Register temporal analysis tools."""

    @mcp.tool(name="email_volume_over_time", annotations=deps.tool_annotations("Email Volume Over Time"))
    async def email_volume_over_time(params: VolumeOverTimeInput) -> str:
        """Get email volume grouped by time period (day/week/month)."""
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        from ..temporal_analysis import TemporalAnalyzer

        analyzer = TemporalAnalyzer(db)
        result = analyzer.volume_over_time(
            period=params.period,
            date_from=params.date_from,
            date_to=params.date_to,
            sender=params.sender,
        )
        return json.dumps(result, indent=2)

    @mcp.tool(name="email_activity_pattern", annotations=deps.tool_annotations("Email Activity Heatmap"))
    async def email_activity_pattern() -> str:
        """Get email activity heatmap: hour-of-day x day-of-week counts."""
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        from ..temporal_analysis import TemporalAnalyzer

        analyzer = TemporalAnalyzer(db)
        result = analyzer.activity_heatmap()
        return json.dumps(result, indent=2)

    @mcp.tool(name="email_response_times", annotations=deps.tool_annotations("Email Response Times"))
    async def email_response_times(params: ResponseTimesInput) -> str:
        """Get average response times per sender (in hours)."""
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        from ..temporal_analysis import TemporalAnalyzer

        analyzer = TemporalAnalyzer(db)
        result = analyzer.response_times(sender=params.sender, limit=params.limit)
        return json.dumps(result, indent=2)
