"""Shared utilities for MCP tool modules — eliminates boilerplate."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from mcp.types import ToolAnnotations

    from ..email_db import EmailDatabase
    from ..retriever import EmailRetriever

logger = logging.getLogger(__name__)


class ToolDepsProto(Protocol):
    """Protocol describing the ToolDeps interface injected by mcp_server.

    Avoids circular imports while giving mypy full attr-defined checking.
    """

    def get_retriever(self) -> EmailRetriever: ...

    def get_email_db(self) -> EmailDatabase | None: ...

    async def offload(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any: ...

    def tool_annotations(self, title: str) -> ToolAnnotations: ...

    def write_tool_annotations(self, title: str) -> ToolAnnotations: ...

    def idempotent_write_annotations(self, title: str) -> ToolAnnotations: ...

    DB_UNAVAILABLE: str

    def sanitize(self, text: str) -> str: ...


def get_deps(deps: ToolDepsProto | None) -> ToolDepsProto:
    """Return *deps* after asserting it has been initialized by ``register()``."""
    assert deps is not None, "Tool module not registered — call register() first"
    return deps


async def run_with_db(deps: ToolDepsProto, fn: Callable[..., str]) -> str:
    """Offload ``fn(db)`` to a thread, returning DB_UNAVAILABLE if db is None."""

    def _run() -> str:
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        return fn(db)

    return await deps.offload(_run)


async def run_with_retriever(deps: ToolDepsProto, fn: Callable[..., str]) -> str:
    """Offload ``fn(retriever)`` to a thread."""
    return await deps.offload(lambda: fn(deps.get_retriever()))


def _sanitize_floats(obj: Any) -> Any:
    """Replace NaN/Inf float values with None to produce valid JSON."""
    import math

    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


def json_response(data: Any, *, max_chars: int | None = None, **kwargs: Any) -> str:
    """Standardized JSON serialization with optional size guard.

    If the serialized JSON exceeds *max_chars* (default from
    ``mcp_max_json_response_chars`` setting), the largest top-level list
    is trimmed to fit and a ``_truncated`` metadata key is added.
    """
    data = _sanitize_floats(data)
    raw = json.dumps(data, indent=2, **kwargs)

    if max_chars is None:
        from ..config import get_settings

        max_chars = get_settings().mcp_max_json_response_chars

    if max_chars > 0 and len(raw) > max_chars:
        return _truncate_json(data, raw, max_chars, **kwargs)
    return raw


def _truncate_json(data: dict[str, Any], raw: str, max_chars: int, **kwargs: Any) -> str:
    """Trim the largest list in *data* until the JSON fits *max_chars*.

    Works on a shallow copy of *data* so the caller's dict is not mutated.
    """
    if not isinstance(data, dict):
        if isinstance(data, list):
            data = {"results": data}
            raw = json.dumps(data, **kwargs)
            if len(raw) <= max_chars:
                return raw
            return _truncate_json(data, raw, max_chars, **kwargs)
        # Can't intelligently trim non-dict responses — wrap in valid JSON
        truncated = raw[:max_chars]
        return json.dumps({"data": truncated, "_truncated": True})

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
        # No list to trim — wrap in valid JSON structure
        return json.dumps(
            {
                "data": raw[:max_chars],
                "_truncated": True,
                "note": "Response too large; truncated to fit limit.",
            }
        )
    if largest_len <= 1:
        if len(raw) <= max_chars:
            return raw
        return json.dumps({"data": raw[:max_chars], "_truncated": True, "note": "Single item exceeds response budget."})

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
            "note": (
                f"Response trimmed to fit {max_chars:,} char limit. "
                "Use limit/offset parameters to paginate through remaining results."
            ),
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
        largest_key,
        largest_len,
        best,
        len(result),
    )
    return result


def json_error(message: str) -> str:
    """Standardized error JSON."""
    return json.dumps({"error": message, "success": False})


_network_lock = threading.Lock()


async def run_with_network(deps: ToolDepsProto, fn: Callable[..., str]) -> str:
    """Offload ``fn(db, network)`` with DB guard and cached CommunicationNetwork.

    Thread-safe: uses ``_network_lock`` to prevent concurrent threads from
    both creating a ``CommunicationNetwork`` when ``_cached_comm_network`` is
    not yet set.  The lock only contends during the one-time initialization;
    subsequent calls see the cached instance immediately.
    """

    def _run() -> str:
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        net = getattr(db, "_cached_comm_network", None)
        if net is None:
            with _network_lock:
                # Double-check under lock — another thread may have created it.
                net = getattr(db, "_cached_comm_network", None)
                if net is None:
                    from ..network_analysis import CommunicationNetwork

                    try:
                        net = CommunicationNetwork(db)
                    except Exception as exc:
                        return json_error(f"Network analysis unavailable: {type(exc).__name__}")
                    db._cached_comm_network = net  # type: ignore[attr-defined]
        return fn(db, net)

    return await deps.offload(_run)
