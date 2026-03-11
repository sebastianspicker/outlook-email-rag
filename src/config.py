"""Application configuration and logging helpers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache

# ── Model-aware MCP response profiles ─────────────────────────
# Each profile sets defaults for all MCP budget knobs.
# Per-variable env overrides always take precedence over profile defaults.
MODEL_PROFILES: dict[str, dict[str, int]] = {
    "haiku": {
        "mcp_max_body_chars": 300,
        "mcp_max_response_tokens": 4000,
        "mcp_max_full_body_chars": 5000,
        "mcp_max_json_response_chars": 16000,
        "mcp_max_triage_results": 30,
        "mcp_max_search_results": 15,
    },
    "sonnet": {
        "mcp_max_body_chars": 500,
        "mcp_max_response_tokens": 8000,
        "mcp_max_full_body_chars": 10000,
        "mcp_max_json_response_chars": 32000,
        "mcp_max_triage_results": 50,
        "mcp_max_search_results": 30,
    },
    "opus": {
        "mcp_max_body_chars": 800,
        "mcp_max_response_tokens": 16000,
        "mcp_max_full_body_chars": 20000,
        "mcp_max_json_response_chars": 64000,
        "mcp_max_triage_results": 100,
        "mcp_max_search_results": 50,
    },
}
MODEL_PROFILES["auto"] = MODEL_PROFILES["sonnet"]


def _get_system_memory_gb() -> float:
    """Return total system memory in GB. Falls back to 8.0 on error."""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return (pages * page_size) / (1024**3)
    except (ValueError, OSError, AttributeError):
        return 8.0


def resolve_device(device: str = "auto") -> str:
    """Resolve device string to the best available compute backend.

    ``"auto"`` probes for MPS (Apple Silicon GPU), then CUDA, then falls
    back to CPU.  Any other value is returned as-is.

    When MPS is selected, sets ``PYTORCH_ENABLE_MPS_FALLBACK=1`` so ops
    not yet implemented on MPS fall back to CPU transparently.
    """
    if device != "auto":
        if device == "mps":
            os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
        return device
    try:
        import torch

        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except ImportError:
        pass
    return "cpu"


def resolve_embedding_batch_size(device: str = "auto") -> int:
    """Return a sensible default embedding batch size for the device.

    MPS batch size is auto-tuned based on available system memory.
    BGE-M3 weights are ~1.2 GB; larger batches use more activation memory
    but remain well within headroom on 16 GB+ unified memory machines.
    """
    resolved = device if device != "auto" else resolve_device(device)
    if resolved == "mps":
        mem_gb = _get_system_memory_gb()
        if mem_gb >= 36:
            return 48
        if mem_gb >= 16:
            return 32
        if mem_gb >= 8:
            return 16
        return 8
    if resolved == "cuda":
        return 32
    return 16


@dataclass(frozen=True)
class Settings:
    """Environment-backed settings for the email RAG application."""

    chromadb_path: str = "data/chromadb"
    sqlite_path: str = "data/email_metadata.db"
    embedding_model: str = "BAAI/bge-m3"
    collection_name: str = "emails"
    top_k: int = 10
    log_level: str = "INFO"
    rerank_enabled: bool = False
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    hybrid_enabled: bool = False
    device: str = "auto"
    sparse_enabled: bool = False
    colbert_rerank_enabled: bool = False
    embedding_batch_size: int = 0  # 0 = auto-detect via resolve_embedding_batch_size
    mps_float16: bool = False
    mcp_max_body_chars: int = 500  # 0 = unlimited
    mcp_max_response_tokens: int = 8000  # 0 = unlimited
    mcp_max_full_body_chars: int = 10000  # soft limit for email_get_full; 0 = unlimited
    mcp_max_json_response_chars: int = 32000  # safety net for JSON tools; ~8K tokens; 0 = unlimited
    mcp_model_profile: str = "auto"
    mcp_max_triage_results: int = 50  # max results for email_triage
    mcp_max_search_results: int = 30  # max results for email_search_structured

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables with safe defaults.

        MCP budget knobs use profile defaults: env override > profile > hardcoded.
        """
        profile_name = os.getenv("MCP_MODEL_PROFILE", "auto").lower()
        profile = MODEL_PROFILES.get(profile_name, MODEL_PROFILES["auto"])
        if profile_name not in MODEL_PROFILES:
            profile_name = "auto"

        return cls(
            chromadb_path=os.getenv("CHROMADB_PATH", cls.chromadb_path),
            sqlite_path=os.getenv("SQLITE_PATH", cls.sqlite_path),
            embedding_model=os.getenv("EMBEDDING_MODEL", cls.embedding_model),
            collection_name=os.getenv("COLLECTION_NAME", cls.collection_name),
            top_k=_int_from_env("TOP_K", cls.top_k, min_value=1, max_value=1000),
            log_level=os.getenv("LOG_LEVEL", cls.log_level).upper(),
            rerank_enabled=os.getenv("RERANK_ENABLED", "").lower() in ("1", "true", "yes"),
            rerank_model=os.getenv("RERANK_MODEL", cls.rerank_model),
            hybrid_enabled=os.getenv("HYBRID_ENABLED", "").lower() in ("1", "true", "yes"),
            device=os.getenv("DEVICE", cls.device),
            sparse_enabled=os.getenv("SPARSE_ENABLED", "").lower() in ("1", "true", "yes"),
            colbert_rerank_enabled=os.getenv("COLBERT_RERANK_ENABLED", "").lower() in ("1", "true", "yes"),
            embedding_batch_size=_int_from_env("EMBEDDING_BATCH_SIZE", cls.embedding_batch_size, min_value=0, max_value=256),
            mps_float16=os.getenv("MPS_FLOAT16", "").lower() in ("1", "true", "yes"),
            mcp_max_body_chars=_int_from_env("MCP_MAX_BODY_CHARS", profile["mcp_max_body_chars"], min_value=0),
            mcp_max_response_tokens=_int_from_env("MCP_MAX_RESPONSE_TOKENS", profile["mcp_max_response_tokens"], min_value=0),
            mcp_max_full_body_chars=_int_from_env("MCP_MAX_FULL_BODY_CHARS", profile["mcp_max_full_body_chars"], min_value=0),
            mcp_max_json_response_chars=_int_from_env(
                "MCP_MAX_JSON_RESPONSE_CHARS", profile["mcp_max_json_response_chars"], min_value=0,
            ),
            mcp_model_profile=profile_name,
            mcp_max_triage_results=_int_from_env("MCP_MAX_TRIAGE_RESULTS", profile["mcp_max_triage_results"], min_value=1),
            mcp_max_search_results=_int_from_env("MCP_MAX_SEARCH_RESULTS", profile["mcp_max_search_results"], min_value=1),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-level cached settings."""
    return Settings.from_env()


def resolve_runtime_settings(
    chromadb_path: str | None = None,
    embedding_model: str | None = None,
    collection_name: str | None = None,
    sqlite_path: str | None = None,
) -> Settings:
    """Derive runtime settings from env defaults with optional overrides."""
    base = get_settings()
    return Settings(
        chromadb_path=chromadb_path or base.chromadb_path,
        sqlite_path=sqlite_path or base.sqlite_path,
        embedding_model=embedding_model or base.embedding_model,
        collection_name=collection_name or base.collection_name,
        top_k=base.top_k,
        log_level=base.log_level,
        rerank_enabled=base.rerank_enabled,
        rerank_model=base.rerank_model,
        hybrid_enabled=base.hybrid_enabled,
        device=base.device,
        sparse_enabled=base.sparse_enabled,
        colbert_rerank_enabled=base.colbert_rerank_enabled,
        embedding_batch_size=base.embedding_batch_size,
        mps_float16=base.mps_float16,
        mcp_max_body_chars=base.mcp_max_body_chars,
        mcp_max_response_tokens=base.mcp_max_response_tokens,
        mcp_max_full_body_chars=base.mcp_max_full_body_chars,
        mcp_max_json_response_chars=base.mcp_max_json_response_chars,
        mcp_model_profile=base.mcp_model_profile,
        mcp_max_triage_results=base.mcp_max_triage_results,
        mcp_max_search_results=base.mcp_max_search_results,
    )


def configure_logging(level: str | None = None) -> None:
    """Configure root logging once for CLI-style entrypoints."""
    settings = get_settings()
    chosen_level = (level or settings.log_level).upper()
    numeric_level = getattr(logging, chosen_level, logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _int_from_env(name: str, default: int, min_value: int = 1, max_value: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        if value < min_value:
            return default
        if max_value is not None and value > max_value:
            return max_value
        return value
    except ValueError:
        return default
