"""Diagnostic and maintenance MCP tools."""

from __future__ import annotations

from ..mcp_models import ReembedInput, ReingestBodiesInput, ReingestMetadataInput
from .utils import json_error, json_response

_deps = None


async def email_diagnostics() -> str:
    """Return embedding backend info, configuration, and sparse index status.

    Combines model/device/backend details with sparse vector and ColBERT
    status in a single call.
    """
    def _run():
        from ..config import get_settings

        retriever = _deps.get_retriever()
        settings = get_settings()
        db = _deps.get_email_db()
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
        # Sparse index status
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
        except Exception:  # noqa: BLE001
            pass
        return json_response(info)
    return await _deps.offload(_run)


async def email_reingest_bodies(params: ReingestBodiesInput) -> str:
    """Re-parse OLM to backfill body_text/body_html for existing SQLite rows.

    Required after upgrading from an older database that did not store
    full email bodies. Re-reads the OLM file and updates rows where
    body_text is NULL.

    With force=True, re-parses ALL emails and overwrites existing body
    text **and** header fields (subject, sender_name, sender_email).
    Use after fixing the OLM parser to update truncated/dirty bodies
    or to decode MIME encoded-word subjects stored from earlier ingestions.

    Args:
        params: olm_path (str) — path to the .olm file.
                force (bool) — overwrite all bodies and headers, not just NULL ones.

    Returns:
        JSON with update count and status message.
    """
    def _run():
        from ..ingest import reingest_bodies

        try:
            return json_response(reingest_bodies(params.olm_path, force=params.force))
        except FileNotFoundError:
            return json_error(f"OLM file not found: {params.olm_path}")
        except Exception as exc:  # noqa: BLE001
            return json_error(str(exc))
    return await _deps.offload(_run)


async def email_reembed(params: ReembedInput) -> str:
    """Re-chunk and re-embed all emails from corrected SQLite body text.

    Reads the (already fixed) body_text from SQLite, re-chunks each email,
    and upserts new embeddings into ChromaDB.  Run this after
    ``--reingest-bodies --force`` to rebuild search quality without a full
    reset-and-reingest cycle.

    Args:
        params: batch_size (int) — chunks per embedding batch (default 100).

    Returns:
        JSON with re-embed count, chunk stats, and status message.
    """
    def _run():
        from ..ingest import reembed

        try:
            return json_response(reembed(batch_size=params.batch_size))
        except Exception as exc:  # noqa: BLE001
            return json_error(str(exc))
    return await _deps.offload(_run)


async def email_reingest_metadata(params: ReingestMetadataInput) -> str:
    """Backfill v7 metadata for existing emails from an OLM archive.

    Re-parses the OLM file and updates existing SQLite rows with
    categories, thread_topic, inference_classification,
    is_calendar_message, references, and attachment details.
    Also inserts Exchange-extracted entities. Does not re-embed.

    Args:
        params: olm_path (str) — path to the .olm file.

    Returns:
        JSON with update count and status message.
    """
    def _run():
        from ..ingest import reingest_metadata

        try:
            return json_response(reingest_metadata(params.olm_path))
        except FileNotFoundError:
            return json_error(f"OLM file not found: {params.olm_path}")
        except Exception as exc:  # noqa: BLE001
            return json_error(str(exc))
    return await _deps.offload(_run)


async def email_reingest_analytics() -> str:
    """Backfill language detection and sentiment analysis for all emails.

    Scans emails where detected_language or sentiment_label is NULL,
    runs the zero-dependency language detector and sentiment analyzer,
    and batch-updates the rows. No OLM file needed — reads from SQLite.

    Returns:
        JSON with update count and status message.
    """
    def _run():
        from ..ingest import reingest_analytics

        try:
            return json_response(reingest_analytics())
        except Exception as exc:  # noqa: BLE001
            return json_error(str(exc))
    return await _deps.offload(_run)


def register(mcp_instance, deps) -> None:
    """Register diagnostic and maintenance tools."""
    global _deps
    _deps = deps

    ann = deps.tool_annotations
    mcp_instance.tool(name="email_diagnostics", annotations=ann("System Diagnostics"))(email_diagnostics)
    iwa = deps.idempotent_write_annotations
    mcp_instance.tool(name="email_reingest_bodies", annotations=iwa("Re-ingest Email Bodies"))(email_reingest_bodies)
    mcp_instance.tool(name="email_reembed", annotations=iwa("Re-embed All Emails"))(email_reembed)
    mcp_instance.tool(name="email_reingest_metadata", annotations=iwa("Re-ingest Metadata"))(email_reingest_metadata)
    mcp_instance.tool(name="email_reingest_analytics", annotations=iwa("Re-ingest Analytics"))(email_reingest_analytics)
