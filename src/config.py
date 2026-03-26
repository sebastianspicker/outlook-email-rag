"""Application configuration and logging helpers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache

# ── Model-aware MCP response profiles ─────────────────────────
#
# Budget presets tuned for each Claude model's context window size.
# Selected via ``MCP_MODEL_PROFILE`` env var (default: ``"auto"`` = sonnet).
#
# 3-tier precedence for every MCP budget knob:
#   1. Env var override (e.g. ``MCP_MAX_BODY_CHARS=1000``) — always wins.
#   2. Profile default — from the dict below for the active profile.
#   3. Hardcoded default — the ``Settings`` dataclass field default (used
#      only when no env var is set AND no profile is active).
#
# Profiles:
#   "haiku"  — Tight budgets for models with ~200K context.  Keeps MCP
#              responses small to leave room for the model's own reasoning.
#   "sonnet" — Balanced budgets for models with ~200K context.  Good
#              default for most use-cases.  Also used as the "auto" alias.
#   "opus"   — Generous budgets for models with ~1M context.  Allows
#              large result sets and full email bodies.
#
# Keys map 1:1 to ``Settings`` field names (which map 1:1 to env vars
# via UPPER_SNAKE_CASE, e.g. ``mcp_max_body_chars`` -> ``MCP_MAX_BODY_CHARS``).
MODEL_PROFILES: dict[str, dict[str, int]] = {
    "haiku": {
        "mcp_max_body_chars": 300,  # chars per email body in search results
        "mcp_max_response_tokens": 4000,  # total token budget for search responses
        "mcp_max_full_body_chars": 5000,  # soft limit for email_deep_context body
        "mcp_max_json_response_chars": 16000,  # safety cap on JSON tool responses
        "mcp_max_triage_results": 30,  # max results per email_triage call
        "mcp_max_search_results": 15,  # max results per email_search_structured call
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
MODEL_PROFILES["auto"] = MODEL_PROFILES["sonnet"]  # "auto" is an alias for "sonnet"


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
        pass  # torch not installed — fall back to CPU
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
    """Environment-backed settings for the email RAG application.

    Every field has a corresponding env var (UPPER_SNAKE_CASE of the field
    name).  Boolean fields accept ``"1"``, ``"true"``, or ``"yes"``
    (case-insensitive).  Integer fields are parsed via ``_int_from_env``
    with min/max clamping.  MCP budget fields use the 3-tier precedence
    described in ``MODEL_PROFILES`` above.
    """

    # Directory for ChromaDB vector storage.
    # Env: CHROMADB_PATH  |  Default: "data/chromadb"
    chromadb_path: str = "data/chromadb"

    # Path to the SQLite metadata database (email headers, entities, evidence, etc.).
    # Env: SQLITE_PATH  |  Default: "data/email_metadata.db"
    sqlite_path: str = "data/email_metadata.db"

    # HuggingFace model name or local path for the embedding model.
    # Env: EMBEDDING_MODEL  |  Default: "BAAI/bge-m3"
    embedding_model: str = "BAAI/bge-m3"

    # ChromaDB collection name for email chunks.
    # Env: COLLECTION_NAME  |  Default: "emails"
    collection_name: str = "emails"

    # Default number of results returned by search queries.
    # Env: TOP_K  |  Valid: 1-1000  |  Default: 10
    top_k: int = 10

    # Python logging level for the application.
    # Env: LOG_LEVEL  |  Valid: DEBUG, INFO, WARNING, ERROR, CRITICAL  |  Default: "INFO"
    log_level: str = "INFO"

    # Enable cross-encoder reranking (uses rerank_model below).
    # ColBERT reranking is tried first when colbert_rerank_enabled is also set;
    # cross-encoder is the fallback.
    # Env: RERANK_ENABLED  |  Valid: 1/true/yes  |  Default: False
    rerank_enabled: bool = False

    # HuggingFace model name for cross-encoder reranking.
    # Env: RERANK_MODEL  |  Default: "BAAI/bge-reranker-v2-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"

    # Enable hybrid search combining semantic embeddings with BM25/sparse keyword search.
    # Env: HYBRID_ENABLED  |  Valid: 1/true/yes  |  Default: False
    hybrid_enabled: bool = False

    # Env: DEVICE  |  Valid: "auto", "cpu", "mps", "cuda"  |  Default: "auto"
    # "auto" probes MPS (Apple Silicon GPU) -> CUDA -> CPU.
    device: str = "auto"

    # Enable sparse vector storage and retrieval via the in-memory inverted index.
    # Preferred over BM25Index when sparse vectors are available from BGE-M3.
    # Env: SPARSE_ENABLED  |  Valid: 1/true/yes  |  Default: False
    sparse_enabled: bool = False

    # Enable ColBERT token-level reranking using BGE-M3 ColBERT vectors.
    # Tried before cross-encoder reranking in retriever._apply_rerank().
    # Env: COLBERT_RERANK_ENABLED  |  Valid: 1/true/yes  |  Default: False
    colbert_rerank_enabled: bool = False

    # Batch size for embedding model encode calls. 0 = auto-detect based on
    # device and system memory (see resolve_embedding_batch_size()).
    # Env: EMBEDDING_BATCH_SIZE  |  Valid: 0-256  |  Default: 0 (auto)
    embedding_batch_size: int = 0

    # Use float16 precision on MPS for ~2x throughput. Requires
    # PYTORCH_ENABLE_MPS_FALLBACK=1 (set automatically when device=mps).
    # Env: MPS_FLOAT16  |  Valid: 1/true/yes  |  Default: False
    mps_float16: bool = False

    # Max characters per email body snippet in search/browse results.
    # Bodies exceeding this are truncated with "...". 0 = unlimited.
    # Env: MCP_MAX_BODY_CHARS  |  Default: profile-dependent (sonnet: 500)
    mcp_max_body_chars: int = 500

    # Max total estimated tokens for serialized search responses.
    # serialize_results() stops emitting results when this budget is exceeded.
    # 0 = unlimited.
    # Env: MCP_MAX_RESPONSE_TOKENS  |  Default: profile-dependent (sonnet: 8000)
    mcp_max_response_tokens: int = 8000

    # Soft character limit for full email bodies returned by email_deep_context.
    # 0 = unlimited.
    # Env: MCP_MAX_FULL_BODY_CHARS  |  Default: profile-dependent (sonnet: 10000)
    mcp_max_full_body_chars: int = 10000

    # Safety cap on the total character length of JSON tool responses.
    # Roughly ~4 chars per token. 0 = unlimited.
    # Env: MCP_MAX_JSON_RESPONSE_CHARS  |  Default: profile-dependent (sonnet: 32000)
    mcp_max_json_response_chars: int = 32000

    # Active model profile name. Determines default values for all MCP budget
    # fields above. "auto" is an alias for "sonnet".
    # Env: MCP_MODEL_PROFILE  |  Valid: "auto", "haiku", "sonnet", "opus"  |  Default: "auto"
    mcp_model_profile: str = "auto"

    # Max results returned per email_triage call (clamped server-side).
    # Env: MCP_MAX_TRIAGE_RESULTS  |  Default: profile-dependent (sonnet: 50)
    mcp_max_triage_results: int = 50

    # Max results returned per email_search_structured call (clamped server-side).
    # Env: MCP_MAX_SEARCH_RESULTS  |  Default: profile-dependent (sonnet: 30)
    mcp_max_search_results: int = 30

    @classmethod
    def from_env(cls) -> Settings:
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
                "MCP_MAX_JSON_RESPONSE_CHARS",
                profile["mcp_max_json_response_chars"],
                min_value=0,
            ),
            mcp_model_profile=profile_name,
            mcp_max_triage_results=_int_from_env("MCP_MAX_TRIAGE_RESULTS", profile["mcp_max_triage_results"], min_value=1),
            mcp_max_search_results=_int_from_env("MCP_MAX_SEARCH_RESULTS", profile["mcp_max_search_results"], min_value=1),
        )


@lru_cache(maxsize=1)  # maxsize=1: only one Settings instance is ever cached
def get_settings() -> Settings:
    """Return process-level cached settings.

    Thread-safety note: ``functools.lru_cache`` is thread-safe in CPython
    3.9+ — concurrent calls may compute the value twice during the first
    race, but the cache will store exactly one instance and all subsequent
    calls return it without locking.  Since ``Settings`` is a frozen
    dataclass and ``from_env()`` is side-effect-free, a redundant call is
    harmless.
    """
    return Settings.from_env()


def resolve_runtime_settings(
    chromadb_path: str | None = None,
    embedding_model: str | None = None,
    collection_name: str | None = None,
    sqlite_path: str | None = None,
) -> Settings:
    """Derive runtime settings from env defaults with optional overrides."""
    from dataclasses import replace

    base = get_settings()
    overrides: dict[str, str] = {}
    if chromadb_path:
        overrides["chromadb_path"] = chromadb_path
    if sqlite_path:
        overrides["sqlite_path"] = sqlite_path
    if embedding_model:
        overrides["embedding_model"] = embedding_model
    if collection_name:
        overrides["collection_name"] = collection_name
    return replace(base, **overrides)  # type: ignore[arg-type]  # all override values are str fields


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
    """Parse an integer env var, clamping to [min_value, max_value] or returning default."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
        if value < min_value:
            return min_value
        if max_value is not None and value > max_value:
            return max_value
        return value
    except ValueError:
        return default
