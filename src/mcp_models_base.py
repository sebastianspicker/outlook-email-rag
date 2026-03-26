"""Base classes and utilities for MCP tool input models.

Two base classes eliminate repeated ``model_config`` declarations:

- ``StrictInput``: strips whitespace from strings, forbids extra fields.
- ``PlainInput``: forbids extra fields only (no string stripping).

``DateRangeInput`` is a mixin providing ISO-date validation for
``date_from`` / ``date_to`` pairs.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .validation import parse_iso_date
from .validation import validate_date_window as ensure_valid_date_window


def _validate_output_path(v: str | None) -> str | None:
    """Reject null bytes and path-traversal components in output file paths."""
    if v is None:
        return v
    if "\x00" in v:
        raise ValueError("output_path must not contain null bytes")
    if ".." in Path(v).parts:
        raise ValueError("output_path must not traverse parent directories with '..'")
    return v


# ── Base Classes ─────────────────────────────────────────────


class StrictInput(BaseModel):
    """Base for MCP inputs with whitespace stripping."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")


class PlainInput(BaseModel):
    """Base for MCP inputs without whitespace stripping."""

    model_config = ConfigDict(extra="forbid")


class DateRangeInput(BaseModel):
    """Reusable date-range validation mixin for MCP inputs."""

    date_from: str | None = Field(
        default=None,
        description="Start date in YYYY-MM-DD format (inclusive). E.g., '2023-01-01'.",
    )
    date_to: str | None = Field(
        default=None,
        description="End date in YYYY-MM-DD format (inclusive). E.g., '2023-12-31'.",
    )

    @field_validator("date_from", "date_to")
    @classmethod
    def validate_iso_date(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return parse_iso_date(value)

    @model_validator(mode="after")
    def validate_date_window(self):
        ensure_valid_date_window(self.date_from, self.date_to)
        return self
