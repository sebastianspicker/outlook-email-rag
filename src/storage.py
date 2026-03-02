"""Shared ChromaDB storage helpers."""

from __future__ import annotations

import os
from collections.abc import Generator
from typing import Any

import chromadb
from chromadb.config import Settings as ChromaSettings

DEFAULT_PAGE_SIZE = 1000


def get_chroma_client(chromadb_path: str):
    """Create a persistent Chroma client at the given path."""
    os.makedirs(chromadb_path, exist_ok=True)
    return chromadb.PersistentClient(
        path=chromadb_path,
        settings=ChromaSettings(anonymized_telemetry=False),
    )


def get_collection(client, collection_name: str):
    """Return the canonical email collection."""
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
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
