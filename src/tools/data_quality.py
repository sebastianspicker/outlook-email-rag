"""Data quality and intelligence MCP tools."""

from __future__ import annotations

import sqlite3

from ..mcp_models import FindDuplicatesInput
from .utils import json_error, json_response, run_with_db


def register(mcp, deps) -> None:
    """Register data quality tools."""

    @mcp.tool(name="email_find_duplicates", annotations=deps.tool_annotations("Find Duplicate Emails"))
    async def email_find_duplicates(params: FindDuplicatesInput) -> str:
        """Find near-duplicate emails using character n-gram similarity."""
        def _work(db):
            from ..dedup_detector import DuplicateDetector

            duplicates = DuplicateDetector(db, threshold=params.threshold).find_duplicates(limit=params.limit)
            return json_response({"count": len(duplicates), "duplicates": duplicates})
        return await run_with_db(deps, _work)

    @mcp.tool(name="email_language_stats", annotations=deps.tool_annotations("Email Language Statistics"))
    async def email_language_stats() -> str:
        """Get language distribution across all indexed emails."""
        def _work(db):
            try:
                rows = db.conn.execute(
                    """
                    SELECT detected_language, COUNT(*) as cnt
                    FROM emails
                    WHERE detected_language IS NOT NULL AND detected_language != ''
                    GROUP BY detected_language
                    ORDER BY cnt DESC
                    """
                ).fetchall()
                if rows:
                    stats = [{"language": row["detected_language"], "count": row["cnt"]} for row in rows]
                    return json_response({"languages": stats})
            except sqlite3.OperationalError:
                return json_error(
                    "Language columns not found. Run email_reingest_analytics to populate language data."
                )
            return json_error("No language data available. Run email_reingest_analytics to populate language data.")
        return await run_with_db(deps, _work)

    @mcp.tool(name="email_sentiment_overview", annotations=deps.tool_annotations("Email Sentiment Overview"))
    async def email_sentiment_overview() -> str:
        """Get sentiment distribution across indexed emails."""
        def _work(db):
            try:
                rows = db.conn.execute(
                    """
                    SELECT sentiment_label, COUNT(*) as cnt,
                           ROUND(AVG(sentiment_score), 4) as avg_score
                    FROM emails
                    WHERE sentiment_label IS NOT NULL AND sentiment_label != ''
                    GROUP BY sentiment_label
                    ORDER BY cnt DESC
                    """
                ).fetchall()
                if rows:
                    stats = [
                        {"sentiment": row["sentiment_label"], "count": row["cnt"], "avg_score": row["avg_score"]}
                        for row in rows
                    ]
                    return json_response({"sentiments": stats})
            except sqlite3.OperationalError:
                return json_error(
                    "Sentiment columns not found. Run email_reingest_analytics to populate sentiment data."
                )
            return json_error("No sentiment data available. Run email_reingest_analytics to populate sentiment data.")
        return await run_with_db(deps, _work)
