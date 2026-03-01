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
    offset = 0
    while True:
        batch = collection.get(include=[], limit=page_size, offset=offset)
        ids = batch.get("ids") or []
        if not ids:
            break

        for value in ids:
            yield value

        offset += len(ids)


def iter_collection_metadatas(collection, page_size: int = DEFAULT_PAGE_SIZE) -> Generator[dict[str, Any], None, None]:
    """Iterate all metadata rows in a collection using pagination."""
    offset = 0
    while True:
        batch = collection.get(include=["metadatas"], limit=page_size, offset=offset)
        metadatas = batch.get("metadatas") or []
        if not metadatas:
            break

        for metadata in metadatas:
            if metadata:
                yield metadata

        offset += len(metadatas)
