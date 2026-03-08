"""Multi-vector embedding abstraction for BGE-M3.

Provides a unified interface over SentenceTransformer (dense-only fallback)
and FlagEmbedding's BGEM3FlagModel (dense + sparse + ColBERT).  All three
retrieval modes are extracted from a single forward pass when FlagEmbedding
is available, maximizing throughput on Apple Silicon MPS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from .config import resolve_device, resolve_embedding_batch_size

logger = logging.getLogger(__name__)


@dataclass
class MultiVectorResult:
    """Output from a multi-vector encode pass."""

    dense: list[list[float]]
    sparse: list[dict[int, float]] | None = None
    colbert: list[np.ndarray] | None = None


@dataclass
class _BackendInfo:
    """Tracks which backend is active and its capabilities."""

    name: str  # "flag" or "sentence_transformer"
    has_sparse: bool = False
    has_colbert: bool = False


class MultiVectorEmbedder:
    """Unified interface: BGEM3FlagModel (preferred) or SentenceTransformer fallback.

    Capabilities:
        - ``encode_dense()``: always available (both backends)
        - ``encode_sparse()``: only with FlagEmbedding + ``sparse_enabled``
        - ``encode_colbert()``: only with FlagEmbedding + ``colbert_enabled``
        - ``encode_all()``: single forward pass returning all enabled modes
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        device: str = "auto",
        sparse_enabled: bool = False,
        colbert_enabled: bool = False,
        batch_size: int = 0,
    ) -> None:
        self.model_name = model_name
        self._device_spec = device
        self.device = resolve_device(device)
        self._sparse_enabled = sparse_enabled
        self._colbert_enabled = colbert_enabled
        self.batch_size = batch_size or resolve_embedding_batch_size(self.device)

        self._model: Any = None
        self._backend: _BackendInfo | None = None

    @property
    def backend(self) -> _BackendInfo:
        """Return backend info, loading model if needed."""
        if self._backend is None:
            self._load_model()
        assert self._backend is not None
        return self._backend

    @property
    def has_sparse(self) -> bool:
        return self.backend.has_sparse

    @property
    def has_colbert(self) -> bool:
        return self.backend.has_colbert

    def _load_model(self) -> None:
        """Load the best available model backend."""
        if self._model is not None:
            return

        # Try FlagEmbedding first (unlocks sparse + ColBERT)
        if self._try_load_flag_model():
            return

        # Fall back to SentenceTransformer (dense only)
        self._load_sentence_transformer()

    def _try_load_flag_model(self) -> bool:
        """Attempt to load BGEM3FlagModel from FlagEmbedding.

        Returns True on success, False if FlagEmbedding is not installed.
        """
        try:
            from FlagEmbedding import BGEM3FlagModel  # type: ignore[import-untyped]
        except ImportError:
            return False

        use_fp16 = self.device not in ("mps", "cpu")
        logger.info(
            "Loading BGEM3FlagModel: %s (device=%s, fp16=%s, sparse=%s, colbert=%s)",
            self.model_name,
            self.device,
            use_fp16,
            self._sparse_enabled,
            self._colbert_enabled,
        )

        self._model = BGEM3FlagModel(
            self.model_name,
            device=self.device,
            use_fp16=use_fp16,
        )
        self._backend = _BackendInfo(
            name="flag",
            has_sparse=self._sparse_enabled,
            has_colbert=self._colbert_enabled,
        )
        return True

    def _load_sentence_transformer(self) -> None:
        """Load SentenceTransformer as dense-only fallback."""
        from sentence_transformers import SentenceTransformer

        logger.info(
            "Loading SentenceTransformer: %s (device=%s) — sparse/ColBERT unavailable",
            self.model_name,
            self.device,
        )
        self._model = SentenceTransformer(self.model_name, device=self.device)
        self._backend = _BackendInfo(name="sentence_transformer")

    def _ensure_loaded(self) -> None:
        if self._model is None:
            self._load_model()

    def encode_dense(self, texts: list[str]) -> list[list[float]]:
        """Encode texts to dense embeddings (always available)."""
        self._ensure_loaded()

        if self.backend.name == "flag":
            output = self._model.encode(
                texts,
                batch_size=self.batch_size,
                return_dense=True,
                return_sparse=False,
                return_colbert_vecs=False,
            )
            vecs = output["dense_vecs"]
            self._mps_cache_clear()
            return _to_list_of_lists(vecs)

        # SentenceTransformer path
        vecs = self._model.encode(texts, batch_size=self.batch_size, show_progress_bar=False)
        return _to_list_of_lists(vecs)

    def encode_sparse(self, texts: list[str]) -> list[dict[int, float]] | None:
        """Encode texts to learned sparse vectors.

        Returns None if FlagEmbedding is not available or sparse is disabled.
        """
        if not self.has_sparse:
            return None

        self._ensure_loaded()
        output = self._model.encode(
            texts,
            batch_size=self.batch_size,
            return_dense=False,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        self._mps_cache_clear()
        return _convert_sparse(output["lexical_weights"])

    def encode_colbert(self, texts: list[str]) -> list[np.ndarray] | None:
        """Encode texts to ColBERT token-level vectors.

        Returns None if FlagEmbedding is not available or ColBERT is disabled.
        """
        if not self.has_colbert:
            return None

        self._ensure_loaded()
        output = self._model.encode(
            texts,
            batch_size=self.batch_size,
            return_dense=False,
            return_sparse=False,
            return_colbert_vecs=True,
        )
        self._mps_cache_clear()
        return _normalize_colbert(output["colbert_vecs"])

    def encode_all(self, texts: list[str]) -> MultiVectorResult:
        """Single forward pass returning all enabled modes.

        This is the most efficient path: one model.encode() call extracts
        dense, sparse, and ColBERT in parallel.
        """
        self._ensure_loaded()

        if self.backend.name == "flag":
            return_sparse = self._sparse_enabled
            return_colbert = self._colbert_enabled

            output = self._model.encode(
                texts,
                batch_size=self.batch_size,
                return_dense=True,
                return_sparse=return_sparse,
                return_colbert_vecs=return_colbert,
            )
            self._mps_cache_clear()

            dense = _to_list_of_lists(output["dense_vecs"])
            sparse = _convert_sparse(output["lexical_weights"]) if return_sparse else None
            colbert = _normalize_colbert(output["colbert_vecs"]) if return_colbert else None

            return MultiVectorResult(dense=dense, sparse=sparse, colbert=colbert)

        # SentenceTransformer: dense only
        vecs = self._model.encode(texts, batch_size=self.batch_size, show_progress_bar=False)
        return MultiVectorResult(dense=_to_list_of_lists(vecs))

    def _mps_cache_clear(self) -> None:
        """Free MPS GPU cache after batch processing."""
        if self.device == "mps":
            try:
                import torch

                if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
                    torch.mps.empty_cache()
            except ImportError:
                pass


def _to_list_of_lists(vecs: Any) -> list[list[float]]:
    """Convert numpy/tensor array to list of lists."""
    if hasattr(vecs, "tolist"):
        return vecs.tolist()
    if isinstance(vecs, list) and len(vecs) > 0 and hasattr(vecs[0], "tolist"):
        return [v.tolist() for v in vecs]
    return vecs


def _convert_sparse(lexical_weights: list[dict]) -> list[dict[int, float]]:
    """Normalize FlagEmbedding lexical_weights to {token_id: weight} dicts."""
    result = []
    for weights in lexical_weights:
        converted: dict[int, float] = {}
        for token_id, weight in weights.items():
            tid = int(token_id)
            w = float(weight)
            if w > 0:
                converted[tid] = w
        result.append(converted)
    return result


def _normalize_colbert(colbert_vecs: list[Any]) -> list[np.ndarray]:
    """Ensure ColBERT vectors are numpy arrays."""
    result = []
    for vecs in colbert_vecs:
        if isinstance(vecs, np.ndarray):
            result.append(vecs)
        elif hasattr(vecs, "numpy"):
            result.append(vecs.numpy())
        else:
            result.append(np.array(vecs))
    return result
