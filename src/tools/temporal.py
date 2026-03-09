"""Temporal analysis MCP tools."""

from __future__ import annotations

from ..mcp_models import ResponseTimesInput, VolumeOverTimeInput
from .utils import json_response, run_with_db


def register(mcp, deps) -> None:
    """Register temporal analysis tools."""

    @mcp.tool(name="email_volume_over_time", annotations=deps.tool_annotations("Email Volume Over Time"))
    async def email_volume_over_time(params: VolumeOverTimeInput) -> str:
        """Get email volume grouped by time period (day/week/month)."""
        def _work(db):
            from ..temporal_analysis import TemporalAnalyzer

            return json_response(TemporalAnalyzer(db).volume_over_time(
                period=params.period, date_from=params.date_from,
                date_to=params.date_to, sender=params.sender,
            ))
        return await run_with_db(deps, _work)

    @mcp.tool(name="email_activity_pattern", annotations=deps.tool_annotations("Email Activity Heatmap"))
    async def email_activity_pattern() -> str:
        """Get email activity heatmap: hour-of-day x day-of-week counts."""
        def _work(db):
            from ..temporal_analysis import TemporalAnalyzer

            return json_response(TemporalAnalyzer(db).activity_heatmap())
        return await run_with_db(deps, _work)

    @mcp.tool(name="email_response_times", annotations=deps.tool_annotations("Email Response Times"))
    async def email_response_times(params: ResponseTimesInput) -> str:
        """Get average response times per sender (in hours)."""
        def _work(db):
            from ..temporal_analysis import TemporalAnalyzer

            return json_response(TemporalAnalyzer(db).response_times(
                sender=params.sender, limit=params.limit,
            ))
        return await run_with_db(deps, _work)
