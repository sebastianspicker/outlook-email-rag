"""Temporal email analysis using pandas."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .email_db import EmailDatabase

logger = logging.getLogger(__name__)


class TemporalAnalyzer:
    """Time-based analysis of email patterns."""

    def __init__(self, email_db: EmailDatabase) -> None:
        self._db = email_db

    def volume_over_time(
        self,
        period: str = "day",
        date_from: str | None = None,
        date_to: str | None = None,
        sender: str | None = None,
    ) -> list[dict[str, Any]]:
        """Email volume grouped by time period."""
        try:
            import pandas as pd
        except ImportError:
            return [{"error": "pandas not installed. Run: pip install pandas"}]

        dates = self._db.email_dates(date_from=date_from, date_to=date_to, sender=sender)
        if not dates:
            return []

        dt_series = pd.to_datetime(dates, errors="coerce", utc=True).dropna()
        if dt_series.empty:
            return []

        df = pd.DataFrame({"date": dt_series})

        # W-SUN = weeks ending on Sunday = starting Monday (ISO 8601)
        freq_map = {"day": "D", "week": "W-SUN", "month": "M"}
        freq = freq_map.get(period, "D")

        grouped = df.groupby(df["date"].dt.to_period(freq)).size()
        return [{"period": str(p), "count": int(c)} for p, c in grouped.items()]

    def activity_heatmap(self) -> list[dict[str, int]]:
        """Hour-of-day x day-of-week email counts."""
        try:
            import pandas as pd
        except ImportError:
            return [{"error": "pandas not installed. Run: pip install pandas"}]

        dates = self._db.email_dates()
        if not dates:
            return []

        dt_series = pd.to_datetime(dates, errors="coerce", utc=True).dropna()
        if dt_series.empty:
            return []

        df = pd.DataFrame({"date": dt_series})
        df["hour"] = df["date"].dt.hour
        df["day_of_week"] = df["date"].dt.dayofweek  # 0=Mon..6=Sun

        grouped = df.groupby(["day_of_week", "hour"]).size().reset_index(name="count")
        return [
            {"day_of_week": int(row["day_of_week"]), "hour": int(row["hour"]), "count": int(row["count"])}
            for _, row in grouped.iterrows()
        ]

    def response_times(self, sender: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Average response times per replier (in hours)."""
        pairs = self._db.response_pairs(sender=sender, limit=500)
        if not pairs:
            return []

        sender_times: dict[str, list[float]] = {}
        for pair in pairs:
            try:
                reply_dt = datetime.fromisoformat(pair["reply_date"])
                orig_dt = datetime.fromisoformat(pair["original_date"])
                # Normalize both to UTC to avoid naive/aware mismatch
                if reply_dt.tzinfo is not None:
                    reply_dt = reply_dt.astimezone(UTC).replace(tzinfo=None)
                else:
                    reply_dt = reply_dt.replace(tzinfo=None)
                if orig_dt.tzinfo is not None:
                    orig_dt = orig_dt.astimezone(UTC).replace(tzinfo=None)
                else:
                    orig_dt = orig_dt.replace(tzinfo=None)
                hours = (reply_dt - orig_dt).total_seconds() / 3600
                if hours < 0:
                    continue
                reply_sender = pair["reply_sender"]
                sender_times.setdefault(reply_sender, []).append(hours)
            except (ValueError, TypeError):
                continue

        result = []
        for email, times in sender_times.items():
            if times:
                avg = sum(times) / len(times)
                result.append(
                    {
                        "sender": email,
                        "avg_response_hours": round(avg, 1),
                        "response_count": len(times),
                    }
                )

        result.sort(key=lambda x: x["response_count"], reverse=True)
        return result[:limit]
