"""Shared utilities for MCP tool modules — eliminates boilerplate."""

from __future__ import annotations

import json


async def run_with_db(deps, fn):
    """Offload ``fn(db)`` to a thread, returning DB_UNAVAILABLE if db is None."""
    def _run():
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        return fn(db)
    return await deps.offload(_run)


async def run_with_retriever(deps, fn):
    """Offload ``fn(retriever)`` to a thread."""
    return await deps.offload(lambda: fn(deps.get_retriever()))


def json_response(data, **kwargs):
    """Standardized JSON serialization for tool responses."""
    return json.dumps(data, indent=2, **kwargs)


def json_error(message):
    """Standardized error JSON."""
    return json.dumps({"error": message})


async def run_with_network(deps, fn):
    """Offload ``fn(db, network)`` with DB guard and cached CommunicationNetwork."""
    def _run():
        db = deps.get_email_db()
        if not db:
            return deps.DB_UNAVAILABLE
        net = getattr(db, "_cached_comm_network", None)
        if net is None:
            from ..network_analysis import CommunicationNetwork

            net = CommunicationNetwork(db)
            db._cached_comm_network = net
        return fn(db, net)
    return await deps.offload(_run)
