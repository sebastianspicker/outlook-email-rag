"""Application configuration and logging helpers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache


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

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from environment variables with safe defaults."""
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
