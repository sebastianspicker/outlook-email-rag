"""Shared ChromaDB storage helpers."""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

DEFAULT_PAGE_SIZE = 1000

# HNSW tuning for bulk ingestion.  batch_size delays graph construction until
# N elements are buffered, avoiding O(n) rebalancing on every .add() call.
# num_threads parallelises the index build across CPU cores.
HNSW_DEFAULTS: dict[str, object] = {
    "hnsw:space": "cosine",
    "hnsw:batch_size": 100_000,
    "hnsw:sync_threshold": 100_000,
    "hnsw:num_threads": os.cpu_count() or 4,
    "hnsw:M": 16,
    "hnsw:construction_ef": 128,
}


def to_builtin_list(value: Any) -> Any:
    """Convert tensor/ndarray-like values to Python lists when needed."""
    if hasattr(value, "tolist"):
        return value.tolist()
    return value


def get_chroma_client(chromadb_path: str):
    """Create a persistent Chroma client at the given path."""
    os.makedirs(chromadb_path, exist_ok=True)
    return chromadb.PersistentClient(
        path=chromadb_path,
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def get_collection(client, collection_name: str, *, hnsw_overrides: dict[str, object] | None = None):
    """Return the canonical email collection.

    ``hnsw_overrides`` can customise HNSW parameters for bulk-import vs. search
    workloads.  They are only applied when the collection is **created**; an
    existing collection keeps its original settings.
    """
    metadata = dict(HNSW_DEFAULTS)
    if hnsw_overrides:
        metadata.update(hnsw_overrides)
    return client.get_or_create_collection(
        name=collection_name,
        metadata=metadata,
    )


def iter_collection_ids(collection, page_size: int = DEFAULT_PAGE_SIZE) -> Generator[str, None, None]:
    """Iterate all IDs in a collection using pagination."""
    yield from _iter_collection_rows(
        collection,
        include=[],
        field_name="ids",
        page_size=page_size,
    )


def iter_collection_metadatas(collection, page_size: int = DEFAULT_PAGE_SIZE) -> Generator[dict[str, Any], None, None]:
    """Iterate all metadata rows in a collection using pagination."""
    for metadata in _iter_collection_rows(
        collection,
        include=["metadatas"],
        field_name="metadatas",
        page_size=page_size,
    ):
        if metadata:
            yield metadata


def _iter_collection_rows(
    collection,
    include: list[str],
    field_name: str,
    page_size: int,
) -> Generator[Any, None, None]:
    """Internal paginated iterator for ChromaDB collection fields."""
    offset = 0
    while True:
        batch = collection.get(include=include, limit=page_size, offset=offset)
        rows = batch.get(field_name) or []
        if not rows:
            break

        for row in rows:
            yield row

        offset += len(rows)
