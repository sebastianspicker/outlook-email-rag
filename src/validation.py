"""Shared validation helpers for date-like CLI/MCP/UI inputs."""

from __future__ import annotations

from datetime import date


def parse_iso_date(value: str) -> str:
    """Validate and return a YYYY-MM-DD date string."""
    date.fromisoformat(value)
    return value


def normalize_optional_iso_date(value: str | None) -> str | None:
    """Trim an optional date string and validate when present."""
    if value is None:
        return None
    clean = value.strip()
    if not clean:
        return None
    return parse_iso_date(clean)


def validate_date_window(date_from: str | None, date_to: str | None) -> None:
    """Ensure the date range is non-inverted when both bounds exist."""
    if date_from and date_to and date_from > date_to:
        raise ValueError("date_from cannot be later than date_to")
