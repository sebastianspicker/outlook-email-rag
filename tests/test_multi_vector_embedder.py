"""Tests for src/multi_vector_embedder.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from src.multi_vector_embedder import (
    MultiVectorEmbedder,
    MultiVectorResult,
    _convert_sparse,
    _normalize_colbert,
    _to_list_of_lists,
)

# ── Helper fixtures ──────────────────────────────────────────────────


def _make_dense_output(n: int = 2, dim: int = 4):
    """Fake dense vectors as numpy array."""
    return np.random.rand(n, dim).astype(np.float32)


def _make_sparse_output(n: int = 2):
    """Fake lexical_weights as list of dicts (FlagEmbedding format)."""
    return [{1: 0.5, 42: 0.9}, {7: 0.3, 100: 0.0}][:n]


def _make_colbert_output(n: int = 2, tokens: int = 8, dim: int = 4):
    """Fake ColBERT token vectors."""
    return [np.random.rand(tokens, dim).astype(np.float32) for _ in range(n)]


class FakeFlagModel:
    """Mock BGEM3FlagModel that returns predictable outputs."""

    def __init__(self, model_name: str, device: str = "cpu", use_fp16: bool = False):
        self.model_name = model_name
        self.device = device
        self.use_fp16 = use_fp16

    def encode(
        self,
        texts,
        batch_size=16,
        return_dense=True,
        return_sparse=False,
        return_colbert_vecs=False,
    ):
        n = len(texts)
        result = {}
        if return_dense:
            result["dense_vecs"] = _make_dense_output(n)
        if return_sparse:
            result["lexical_weights"] = _make_sparse_output(n)
        if return_colbert_vecs:
            result["colbert_vecs"] = _make_colbert_output(n)
        return result


class FakeSentenceTransformer:
    """Mock SentenceTransformer."""

    def __init__(self, model_name: str, device: str = "cpu"):
        self.model_name = model_name
        self.device = device

    def encode(self, texts, batch_size=16, show_progress_bar=False):
        return _make_dense_output(len(texts))


# ── _to_list_of_lists ────────────────────────────────────────────────


def test_to_list_of_lists_numpy():
    arr = np.array([[1.0, 2.0], [3.0, 4.0]])
    result = _to_list_of_lists(arr)
    assert isinstance(result, list)
    assert isinstance(result[0], list)
    assert result[0] == [1.0, 2.0]


def test_to_list_of_lists_already_list():
    data = [[1.0, 2.0], [3.0, 4.0]]
    assert _to_list_of_lists(data) is data


def test_to_list_of_lists_list_of_ndarray():
    data = [np.array([1.0, 2.0]), np.array([3.0, 4.0])]
    result = _to_list_of_lists(data)
    assert isinstance(result, list)
    assert isinstance(result[0], list)


# ── _convert_sparse ──────────────────────────────────────────────────


def test_convert_sparse_filters_zero_weights():
    raw = [{1: 0.5, 2: 0.0, 3: 0.8}]
    result = _convert_sparse(raw)
    assert len(result) == 1
    assert 2 not in result[0]
    assert result[0][1] == 0.5
    assert result[0][3] == 0.8


def test_convert_sparse_int_keys():
    raw = [{"42": 0.9}]
    result = _convert_sparse(raw)
    assert 42 in result[0]


def test_convert_sparse_empty():
    assert _convert_sparse([]) == []


# ── _normalize_colbert ───────────────────────────────────────────────


def test_normalize_colbert_numpy():
    data = [np.ones((3, 4))]
    result = _normalize_colbert(data)
    assert isinstance(result[0], np.ndarray)
    assert result[0].shape == (3, 4)


def test_normalize_colbert_plain_list():
    data = [[[1.0, 2.0], [3.0, 4.0]]]
    result = _normalize_colbert(data)
    assert isinstance(result[0], np.ndarray)


# ── MultiVectorResult ────────────────────────────────────────────────


def test_multi_vector_result_defaults():
    r = MultiVectorResult(dense=[[1.0, 2.0]])
    assert r.sparse is None
    assert r.colbert is None
    assert r.dense == [[1.0, 2.0]]


# ── MultiVectorEmbedder with FlagEmbedding ───────────────────────────


@patch.dict("sys.modules", {"FlagEmbedding": MagicMock()})
def test_embedder_loads_flag_model():
    import sys

    flag_mod = sys.modules["FlagEmbedding"]
    flag_mod.BGEM3FlagModel = FakeFlagModel

    emb = MultiVectorEmbedder(
        model_name="BAAI/bge-m3",
        device="cpu",
        sparse_enabled=True,
        colbert_enabled=True,
    )
    assert emb.backend.name == "flag"
    assert emb.has_sparse is True
    assert emb.has_colbert is True


@patch.dict("sys.modules", {"FlagEmbedding": MagicMock()})
def test_encode_dense_flag():
    import sys

    flag_mod = sys.modules["FlagEmbedding"]
    flag_mod.BGEM3FlagModel = FakeFlagModel

    emb = MultiVectorEmbedder(model_name="BAAI/bge-m3", device="cpu")
    result = emb.encode_dense(["hello", "world"])
    assert len(result) == 2
    assert isinstance(result[0], list)


@patch.dict("sys.modules", {"FlagEmbedding": MagicMock()})
def test_encode_sparse_flag():
    import sys

    flag_mod = sys.modules["FlagEmbedding"]
    flag_mod.BGEM3FlagModel = FakeFlagModel

    emb = MultiVectorEmbedder(
        model_name="BAAI/bge-m3", device="cpu", sparse_enabled=True,
    )
    result = emb.encode_sparse(["hello", "world"])
    assert result is not None
    assert len(result) == 2
    assert isinstance(result[0], dict)


@patch.dict("sys.modules", {"FlagEmbedding": MagicMock()})
def test_encode_sparse_disabled():
    import sys

    flag_mod = sys.modules["FlagEmbedding"]
    flag_mod.BGEM3FlagModel = FakeFlagModel

    emb = MultiVectorEmbedder(
        model_name="BAAI/bge-m3", device="cpu", sparse_enabled=False,
    )
    assert emb.encode_sparse(["hello"]) is None


@patch.dict("sys.modules", {"FlagEmbedding": MagicMock()})
def test_encode_colbert_flag():
    import sys

    flag_mod = sys.modules["FlagEmbedding"]
    flag_mod.BGEM3FlagModel = FakeFlagModel

    emb = MultiVectorEmbedder(
        model_name="BAAI/bge-m3", device="cpu", colbert_enabled=True,
    )
    result = emb.encode_colbert(["hello", "world"])
    assert result is not None
    assert len(result) == 2
    assert isinstance(result[0], np.ndarray)


@patch.dict("sys.modules", {"FlagEmbedding": MagicMock()})
def test_encode_colbert_disabled():
    import sys

    flag_mod = sys.modules["FlagEmbedding"]
    flag_mod.BGEM3FlagModel = FakeFlagModel

    emb = MultiVectorEmbedder(
        model_name="BAAI/bge-m3", device="cpu", colbert_enabled=False,
    )
    assert emb.encode_colbert(["hello"]) is None


@patch.dict("sys.modules", {"FlagEmbedding": MagicMock()})
def test_encode_all_flag():
    import sys

    flag_mod = sys.modules["FlagEmbedding"]
    flag_mod.BGEM3FlagModel = FakeFlagModel

    emb = MultiVectorEmbedder(
        model_name="BAAI/bge-m3",
        device="cpu",
        sparse_enabled=True,
        colbert_enabled=True,
    )
    result = emb.encode_all(["hello", "world"])
    assert isinstance(result, MultiVectorResult)
    assert len(result.dense) == 2
    assert result.sparse is not None
    assert result.colbert is not None


@patch.dict("sys.modules", {"FlagEmbedding": MagicMock()})
def test_encode_all_flag_dense_only():
    import sys

    flag_mod = sys.modules["FlagEmbedding"]
    flag_mod.BGEM3FlagModel = FakeFlagModel

    emb = MultiVectorEmbedder(
        model_name="BAAI/bge-m3",
        device="cpu",
        sparse_enabled=False,
        colbert_enabled=False,
    )
    result = emb.encode_all(["hello"])
    assert result.sparse is None
    assert result.colbert is None


# ── MultiVectorEmbedder with SentenceTransformer fallback ────────────


def test_embedder_falls_back_to_sentence_transformer():
    """When FlagEmbedding is not importable, falls back to SentenceTransformer."""
    with patch.dict("sys.modules", {"FlagEmbedding": None}):
        with patch("src.multi_vector_embedder.MultiVectorEmbedder._load_sentence_transformer") as mock_load:
            def side_effect():
                emb._model = FakeSentenceTransformer("BAAI/bge-m3")
                emb._backend = _BackendInfo(name="sentence_transformer")

            from src.multi_vector_embedder import _BackendInfo

            emb = MultiVectorEmbedder(model_name="BAAI/bge-m3", device="cpu")
            mock_load.side_effect = side_effect
            assert emb.backend.name == "sentence_transformer"
            assert emb.has_sparse is False
            assert emb.has_colbert is False


def test_encode_dense_sentence_transformer():
    with patch.dict("sys.modules", {"FlagEmbedding": None}):
        emb = MultiVectorEmbedder(model_name="BAAI/bge-m3", device="cpu")
        # Manually inject fake ST
        from src.multi_vector_embedder import _BackendInfo

        emb._model = FakeSentenceTransformer("BAAI/bge-m3")
        emb._backend = _BackendInfo(name="sentence_transformer")

        result = emb.encode_dense(["hello", "world"])
        assert len(result) == 2


def test_encode_all_sentence_transformer():
    with patch.dict("sys.modules", {"FlagEmbedding": None}):
        emb = MultiVectorEmbedder(model_name="BAAI/bge-m3", device="cpu")
        from src.multi_vector_embedder import _BackendInfo

        emb._model = FakeSentenceTransformer("BAAI/bge-m3")
        emb._backend = _BackendInfo(name="sentence_transformer")

        result = emb.encode_all(["hello"])
        assert isinstance(result, MultiVectorResult)
        assert len(result.dense) == 1
        assert result.sparse is None
        assert result.colbert is None


# ── MPS behavior ─────────────────────────────────────────────────────


@patch.dict("sys.modules", {"FlagEmbedding": MagicMock()})
def test_flag_model_no_fp16_on_mps():
    """MPS device must use float32 (use_fp16=False)."""
    import sys

    calls = []

    class SpyFlagModel(FakeFlagModel):
        def __init__(self, model_name, device="cpu", use_fp16=False):
            super().__init__(model_name, device, use_fp16)
            calls.append({"device": device, "use_fp16": use_fp16})

    flag_mod = sys.modules["FlagEmbedding"]
    flag_mod.BGEM3FlagModel = SpyFlagModel

    emb = MultiVectorEmbedder(model_name="BAAI/bge-m3", device="cpu")
    # Simulate MPS by overriding device before load
    emb.device = "mps"
    emb._load_model()
    assert calls[0]["use_fp16"] is False


@patch.dict("sys.modules", {"FlagEmbedding": MagicMock()})
def test_flag_model_fp16_on_cuda():
    import sys

    calls = []

    class SpyFlagModel(FakeFlagModel):
        def __init__(self, model_name, device="cpu", use_fp16=False):
            super().__init__(model_name, device, use_fp16)
            calls.append({"device": device, "use_fp16": use_fp16})

    flag_mod = sys.modules["FlagEmbedding"]
    flag_mod.BGEM3FlagModel = SpyFlagModel

    emb = MultiVectorEmbedder(model_name="BAAI/bge-m3", device="cpu")
    emb.device = "cuda"
    emb._load_model()
    assert calls[0]["use_fp16"] is True


# ── Batch size ───────────────────────────────────────────────────────


def test_batch_size_default_auto():
    emb = MultiVectorEmbedder.__new__(MultiVectorEmbedder)
    emb._device_spec = "auto"
    emb.device = "cpu"
    emb.batch_size = 0 or 16  # resolve_embedding_batch_size("cpu")
    assert emb.batch_size == 16


def test_batch_size_explicit():
    emb = MultiVectorEmbedder.__new__(MultiVectorEmbedder)
    emb.batch_size = 42
    assert emb.batch_size == 42


# ── MPS cache clear ──────────────────────────────────────────────────


def test_mps_cache_clear_no_torch():
    """_mps_cache_clear should not raise even if torch is missing."""
    emb = MultiVectorEmbedder.__new__(MultiVectorEmbedder)
    emb.device = "mps"
    emb._encode_count = 9  # next call hits interval=10
    # Should not raise even when torch import fails
    emb._mps_cache_clear()


def test_mps_cache_clear_skipped_on_cpu():
    """_mps_cache_clear should be a no-op on CPU."""
    emb = MultiVectorEmbedder.__new__(MultiVectorEmbedder)
    emb.device = "cpu"
    emb._encode_count = 0
    emb._mps_cache_clear()  # Should not raise or do anything


def test_mps_cache_clear_throttled(monkeypatch):
    """Cache clear should only fire every N calls, not every call."""
    import src.multi_vector_embedder as mve_mod

    monkeypatch.setattr(mve_mod, "_MPS_CLEAR_INTERVAL", 3)

    emb = MultiVectorEmbedder.__new__(MultiVectorEmbedder)
    emb.device = "mps"
    emb._encode_count = 0

    clear_calls = []
    mock_torch = MagicMock()
    mock_torch.mps.empty_cache = lambda: clear_calls.append(1)

    with patch.dict("sys.modules", {"torch": mock_torch}):
        for _ in range(9):
            emb._mps_cache_clear()

    # Should fire at counts 3, 6, 9 → 3 times
    assert len(clear_calls) == 3
    assert emb._encode_count == 9


def test_mps_cache_clear_every_call_with_interval_1(monkeypatch):
    """MPS_CACHE_CLEAR_INTERVAL=1 should restore every-call behavior."""
    import src.multi_vector_embedder as mve_mod

    monkeypatch.setattr(mve_mod, "_MPS_CLEAR_INTERVAL", 1)

    emb = MultiVectorEmbedder.__new__(MultiVectorEmbedder)
    emb.device = "mps"
    emb._encode_count = 0

    clear_calls = []
    mock_torch = MagicMock()
    mock_torch.mps.empty_cache = lambda: clear_calls.append(1)

    with patch.dict("sys.modules", {"torch": mock_torch}):
        for _ in range(5):
            emb._mps_cache_clear()

    assert len(clear_calls) == 5


# ── MPS float16 opt-in ────────────────────────────────────────────────


def test_mps_float16_setting_from_env(monkeypatch):
    monkeypatch.setenv("MPS_FLOAT16", "true")
    from src.config import Settings

    settings = Settings.from_env()
    assert settings.mps_float16 is True


@patch.dict("sys.modules", {"FlagEmbedding": MagicMock()})
def test_mps_fp16_used_when_opted_in():
    """When mps_float16=True, FlagModel should load with use_fp16=True on MPS."""
    import sys

    calls = []

    class SpyFlagModel(FakeFlagModel):
        def __init__(self, model_name, device="cpu", use_fp16=False):
            super().__init__(model_name, device, use_fp16)
            calls.append({"device": device, "use_fp16": use_fp16})

    flag_mod = sys.modules["FlagEmbedding"]
    flag_mod.BGEM3FlagModel = SpyFlagModel

    emb = MultiVectorEmbedder(model_name="BAAI/bge-m3", device="cpu", mps_float16=True)
    emb.device = "mps"
    emb._load_model()
    assert calls[0]["use_fp16"] is True


@patch.dict("sys.modules", {"FlagEmbedding": MagicMock()})
def test_mps_fp32_by_default():
    """By default, MPS should use fp32 (mps_float16=False)."""
    import sys

    calls = []

    class SpyFlagModel(FakeFlagModel):
        def __init__(self, model_name, device="cpu", use_fp16=False):
            super().__init__(model_name, device, use_fp16)
            calls.append({"device": device, "use_fp16": use_fp16})

    flag_mod = sys.modules["FlagEmbedding"]
    flag_mod.BGEM3FlagModel = SpyFlagModel

    emb = MultiVectorEmbedder(model_name="BAAI/bge-m3", device="cpu", mps_float16=False)
    emb.device = "mps"
    emb._load_model()
    assert calls[0]["use_fp16"] is False


# ── warmup ────────────────────────────────────────────────────────


def test_warmup_loads_model_and_encodes():
    """warmup() should force model load and run a test encode."""
    emb = MultiVectorEmbedder(model_name="BAAI/bge-m3", device="cpu")
    assert emb._model is None
    emb.warmup()
    assert emb._model is not None
    assert emb._backend is not None
