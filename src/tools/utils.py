"""Shared utilities for MCP tool modules — eliminates boilerplate."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class ToolDepsProto(Protocol):
    """Protocol describing the ToolDeps interface injected by mcp_server.

    Avoids circular imports while giving mypy full attr-defined checking.
    """

    @staticmethod
    def get_retriever() -> Any: ...

    @staticmethod
    def get_email_db() -> Any: ...

    @staticmethod
    async def offload(fn: Any, *args: Any, **kwargs: Any) -> Any: ...

    @staticmethod
    def tool_annotations(title: str) -> Any: ...

    @staticmethod
    def write_tool_annotations(title: str) -> Any: ...

    @staticmethod
    def idempotent_write_annotations(title: str) -> Any: ...

    DB_UNAVAILABLE: str

    @staticmethod
    def sanitize(text: str) -> str: ...


def get_deps(deps: ToolDepsProto | None) -> ToolDepsProto:
    """Return *deps* after asserting it has been initialized by ``register()``."""
    assert deps is not None, "Tool module not registered — call register() first"
    return deps


async def run_with_db(deps: ToolDepsProto, fn: Callable) -> Any:
    """Offload ``fn(db)`` to a thread, returning DB_UNAVAILABLE if db is None."""
    def _run():
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        return fn(db)
    return await deps.offload(_run)


async def run_with_retriever(deps: ToolDepsProto, fn: Callable) -> Any:
    """Offload ``fn(retriever)`` to a thread."""
    return await deps.offload(lambda: fn(deps.get_retriever()))


def json_response(data, *, max_chars: int | None = None, **kwargs):
    """Standardized JSON serialization with optional size guard.

    If the serialized JSON exceeds *max_chars* (default from
    ``mcp_max_json_response_chars`` setting), the largest top-level list
    is trimmed to fit and a ``_truncated`` metadata key is added.
    """
    raw = json.dumps(data, indent=2, **kwargs)

    if max_chars is None:
        from ..config import get_settings
        max_chars = get_settings().mcp_max_json_response_chars

    if max_chars > 0 and len(raw) > max_chars:
        return _truncate_json(data, raw, max_chars, **kwargs)
    return raw


def _truncate_json(data, raw: str, max_chars: int, **kwargs) -> str:
    """Trim the largest list in *data* until the JSON fits *max_chars*.

    Works on a shallow copy of *data* so the caller's dict is not mutated.
    """
    if not isinstance(data, dict):
        # Can't intelligently trim non-dict responses — hard-truncate
        return raw[:max_chars] + '\n... [response truncated]'

    # Work on a copy so we don't mutate the caller's dict
    data = {**data}

    # Find the largest list value in the top-level dict
    largest_key = None
    largest_len = 0
    for key, val in data.items():
        if isinstance(val, list) and len(val) > largest_len:
            largest_key = key
            largest_len = len(val)

    if largest_key is None:
        # No list to trim — hard-truncate
        return raw[:max_chars] + '\n... [response truncated]'
    if largest_len <= 1:
        # Single-item list — return as-is; per-tool compact mode handles item size
        return raw

    # Binary search for how many items fit
    lo, hi = 1, largest_len
    original_list = data[largest_key]
    best = 1

    while lo <= hi:
        mid = (lo + hi) // 2
        data[largest_key] = original_list[:mid]
        data["_truncated"] = {
            "field": largest_key,
            "shown": mid,
            "original_count": largest_len,
            "note": f"Response trimmed to fit {max_chars} char limit. Use limit/offset to paginate.",
        }
        candidate = json.dumps(data, indent=2, **kwargs)
        if len(candidate) <= max_chars:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    # Apply best fit
    data[largest_key] = original_list[:best]
    data["_truncated"] = {
        "field": largest_key,
        "shown": best,
        "original_count": largest_len,
        "note": f"Response trimmed to fit {max_chars} char limit. Use limit/offset to paginate.",
    }
    result = json.dumps(data, indent=2, **kwargs)
    logger.debug(
        "json_response truncated %s from %d to %d items (%d chars)",
        largest_key, largest_len, best, len(result),
    )
    return result


def json_error(message: str) -> str:
    """Standardized error JSON."""
    return json.dumps({"error": message})


async def run_with_network(deps: ToolDepsProto, fn: Callable) -> Any:
    """Offload ``fn(db, network)`` with DB guard and cached CommunicationNetwork."""
    def _run():
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        net = getattr(db, "_cached_comm_network", None)
        if net is None:
            from ..network_analysis import CommunicationNetwork

            try:
                net = CommunicationNetwork(db)
            except Exception as exc:
                return json_error(f"Network analysis unavailable: {exc}")
            db._cached_comm_network = net
        return fn(db, net)
    return await deps.offload(_run)
