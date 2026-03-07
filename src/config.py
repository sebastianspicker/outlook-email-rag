"""Application configuration and logging helpers."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    """Environment-backed settings for the email RAG application."""

    chromadb_path: str = "data/chromadb"
    sqlite_path: str = "data/email_metadata.db"
    embedding_model: str = "all-MiniLM-L6-v2"
    collection_name: str = "emails"
    top_k: int = 10
    log_level: str = "INFO"

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
