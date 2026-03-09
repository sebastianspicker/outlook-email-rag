"""Data quality and intelligence MCP tools."""

from __future__ import annotations

import json

from ..mcp_models import FindDuplicatesInput


def register(mcp, deps) -> None:
    """Register data quality tools."""

    @mcp.tool(name="email_find_duplicates", annotations=deps.tool_annotations("Find Duplicate Emails"))
    async def email_find_duplicates(params: FindDuplicatesInput) -> str:
        """Find near-duplicate emails using character n-gram similarity."""
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE
            from ..dedup_detector import DuplicateDetector

            detector = DuplicateDetector(db, threshold=params.threshold)
            duplicates = detector.find_duplicates(limit=params.limit)
            return json.dumps({"count": len(duplicates), "duplicates": duplicates}, indent=2)
        return await deps.offload(_run)

    @mcp.tool(name="email_language_stats", annotations=deps.tool_annotations("Email Language Statistics"))
    async def email_language_stats() -> str:
        """Get language distribution across all indexed emails."""
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE

            # Query detected_language if available
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
                    return json.dumps({"languages": stats}, indent=2)
            except Exception:
                pass
            return json.dumps({"error": "No language data available. Re-ingest with language detection enabled."})
        return await deps.offload(_run)

    @mcp.tool(name="email_sentiment_overview", annotations=deps.tool_annotations("Email Sentiment Overview"))
    async def email_sentiment_overview() -> str:
        """Get sentiment distribution across indexed emails."""
        def _run():
            db = deps.get_email_db()
            if not db:
                return deps.DB_UNAVAILABLE

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
                    return json.dumps({"sentiments": stats}, indent=2)
            except Exception:
                pass
            return json.dumps({"error": "No sentiment data available. Re-ingest with sentiment analysis enabled."})
        return await deps.offload(_run)
