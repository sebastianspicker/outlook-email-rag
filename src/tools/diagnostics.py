"""Diagnostic and maintenance MCP tools."""

from __future__ import annotations

import logging
from typing import Any

from ..mcp_models import EmailAdminInput
from .utils import ToolDepsProto, get_deps, json_error, json_response

logger = logging.getLogger(__name__)

# Thread-safety note: _deps is written once during single-threaded module
# registration at import time, then only read by tool handlers.
_deps: ToolDepsProto | None = None


def _d() -> ToolDepsProto:
    """Return the module-level deps, asserting it was set by ``register()``."""
    return get_deps(_deps)


async def email_diagnostics(deps: ToolDepsProto) -> str:
    """Return embedding backend info, configuration, and sparse index status."""

    def _run() -> str:
        from ..config import get_settings

        retriever = deps.get_retriever()
        settings = get_settings()
        db = deps.get_email_db()
        info: dict = {
            "embedding_model": settings.embedding_model,
            "device": str(getattr(retriever.embedder, "device", "unknown")),
            "backend": type(getattr(retriever.embedder, "_model", retriever.embedder)).__name__,
            "sparse_enabled": getattr(settings, "sparse_enabled", False),
            "colbert_rerank_enabled": getattr(settings, "colbert_rerank_enabled", False),
            "batch_size": getattr(settings, "embedding_batch_size", 0),
        }
        multi = getattr(retriever, "embedder", None)
        if multi:
            info["has_sparse"] = getattr(multi, "has_sparse", False)
            info["has_colbert"] = getattr(multi, "has_colbert", False)
        info["mcp_profile"] = settings.mcp_model_profile
        info["mcp_budget"] = {
            "max_body_chars": settings.mcp_max_body_chars,
            "max_response_tokens": settings.mcp_max_response_tokens,
            "max_full_body_chars": settings.mcp_max_full_body_chars,
            "max_json_response_chars": settings.mcp_max_json_response_chars,
            "max_triage_results": settings.mcp_max_triage_results,
            "max_search_results": settings.mcp_max_search_results,
        }
        info["sparse_vector_count"] = 0
        info["sparse_index_built"] = False
        if db:
            count_method = getattr(db, "sparse_vector_count", None)
            if count_method:
                info["sparse_vector_count"] = count_method()
        try:
            sparse_idx = getattr(retriever, "_sparse_index", None)
            if sparse_idx:
                info["sparse_index_built"] = getattr(sparse_idx, "_built", False)
        except Exception:
            logger.debug("Sparse index diagnostics unavailable", exc_info=True)
        return json_response(info)

    return await deps.offload(_run)


async def email_reingest_bodies(deps: ToolDepsProto, olm_path: str, force: bool = False) -> str:
    """Re-parse OLM to backfill body_text/body_html for existing SQLite rows."""

    def _run() -> str:
        from ..ingest import reingest_bodies

        try:
            return json_response(reingest_bodies(olm_path, force=force))
        except FileNotFoundError:
            return json_error(f"OLM file not found: {olm_path}")
        except Exception as exc:
            return json_error(f"Body reingestion failed: {type(exc).__name__}")

    return await deps.offload(_run)


async def email_reembed(deps: ToolDepsProto, batch_size: int = 100) -> str:
    """Re-chunk and re-embed all emails from corrected SQLite body text."""

    def _run() -> str:
        from ..ingest import reembed

        try:
            return json_response(reembed(batch_size=batch_size))
        except Exception as exc:
            return json_error(f"Re-embedding failed: {type(exc).__name__}")

    return await deps.offload(_run)


async def email_reingest_metadata(deps: ToolDepsProto, olm_path: str) -> str:
    """Backfill v7 metadata for existing emails from an OLM archive."""

    def _run() -> str:
        from ..ingest import reingest_metadata

        try:
            return json_response(reingest_metadata(olm_path))
        except FileNotFoundError:
            return json_error(f"OLM file not found: {olm_path}")
        except Exception as exc:
            return json_error(f"Metadata reingestion failed: {type(exc).__name__}")

    return await deps.offload(_run)


async def email_reingest_analytics(deps: ToolDepsProto) -> str:
    """Backfill language detection and sentiment analysis for all emails."""

    def _run() -> str:
        from ..ingest import reingest_analytics

        try:
            return json_response(reingest_analytics())
        except Exception as exc:
            return json_error(f"Analytics reingestion failed: {type(exc).__name__}")

    return await deps.offload(_run)


def register(mcp_instance: Any, deps: ToolDepsProto) -> None:
    """Register admin tools."""
    global _deps
    _deps = deps

    @mcp_instance.tool(
        name="email_admin",
        annotations=deps.idempotent_write_annotations("Admin & Diagnostics"),
    )
    async def email_admin(params: EmailAdminInput) -> str:
        """Admin and diagnostic operations in one tool.

        action='diagnostics': show embedding model, device, sparse/ColBERT status.
        action='reingest_bodies': re-parse OLM bodies (requires olm_path).
        action='reembed': re-embed all chunks from SQLite body text.
        action='reingest_metadata': backfill v7 metadata (requires olm_path).
        action='reingest_analytics': backfill language/sentiment data.
        """
        if params.action == "diagnostics":
            return await email_diagnostics(deps)
        if params.action == "reingest_bodies":
            if not params.olm_path:
                return json_error("olm_path is required for reingest_bodies.")
            return await email_reingest_bodies(deps, params.olm_path, force=params.force)
        if params.action == "reembed":
            return await email_reembed(deps, batch_size=params.batch_size)
        if params.action == "reingest_metadata":
            if not params.olm_path:
                return json_error("olm_path is required for reingest_metadata.")
            return await email_reingest_metadata(deps, params.olm_path)
        if params.action == "reingest_analytics":
            return await email_reingest_analytics(deps)
        return json_error(
            f"Invalid action: {params.action}. Use 'diagnostics', 'reingest_bodies', "
            "'reembed', 'reingest_metadata', or 'reingest_analytics'."
        )
