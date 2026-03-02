"""Small conversion helpers shared across embedding/retrieval modules."""

from __future__ import annotations

from typing import Any


def to_builtin_list(value: Any) -> Any:
    """Convert tensor/ndarray-like values to Python lists when needed."""
    if hasattr(value, "tolist"):
        return value.tolist()
    return value
