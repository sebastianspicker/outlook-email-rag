import os


def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("CHROMADB_PATH", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("COLLECTION_NAME", raising=False)
    monkeypatch.delenv("TOP_K", raising=False)
    monkeypatch.delenv("DEVICE", raising=False)

    from src.config import Settings

    settings = Settings.from_env()
    assert settings.chromadb_path == "data/chromadb"
    assert settings.embedding_model == "BAAI/bge-m3"
    assert settings.collection_name == "emails"
    assert settings.top_k == 10
    assert settings.device == "auto"


def test_settings_top_k_clamps_below_min_env(monkeypatch):
    monkeypatch.setenv("TOP_K", "0")

    from src.config import Settings

    settings = Settings.from_env()
    assert settings.top_k == 1  # clamped to min_value, not default


def test_settings_top_k_clamps_large_env(monkeypatch):
    monkeypatch.setenv("TOP_K", "5000")

    from src.config import Settings

    settings = Settings.from_env()
    assert settings.top_k == 1000


def test_resolve_runtime_settings_uses_defaults(monkeypatch):
    monkeypatch.delenv("CHROMADB_PATH", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("COLLECTION_NAME", raising=False)

    from src.config import resolve_runtime_settings

    settings = resolve_runtime_settings()
    assert settings.chromadb_path == "data/chromadb"
    assert settings.embedding_model == "BAAI/bge-m3"
    assert settings.collection_name == "emails"


def test_resolve_runtime_settings_applies_overrides():
    from src.config import resolve_runtime_settings

    settings = resolve_runtime_settings(
        chromadb_path="/tmp/db",
        embedding_model="mini-test-model",
        collection_name="mail-test",
    )
    assert settings.chromadb_path == "/tmp/db"
    assert settings.embedding_model == "mini-test-model"
    assert settings.collection_name == "mail-test"


def test_settings_device_from_env(monkeypatch):
    monkeypatch.setenv("DEVICE", "cpu")

    from src.config import Settings

    settings = Settings.from_env()
    assert settings.device == "cpu"


# --- resolve_device tests ---


def test_resolve_device_explicit_passthrough():
    from src.config import resolve_device

    assert resolve_device("cpu") == "cpu"
    assert resolve_device("mps") == "mps"
    assert resolve_device("cuda") == "cuda"


def test_resolve_device_auto_mps(monkeypatch):
    """resolve_device('auto') should return 'mps' when MPS is available."""
    import types

    mock_torch = types.ModuleType("torch")
    mock_backends = types.ModuleType("torch.backends")
    mock_mps = types.SimpleNamespace(is_available=lambda: True)
    mock_backends.mps = mock_mps
    mock_torch.backends = mock_backends
    mock_torch.cuda = types.SimpleNamespace(is_available=lambda: False)

    monkeypatch.setitem(__import__("sys").modules, "torch", mock_torch)

    from src.config import resolve_device

    assert resolve_device("auto") == "mps"


def test_resolve_device_auto_cuda(monkeypatch):
    """resolve_device('auto') should return 'cuda' when only CUDA is available."""
    import types

    mock_torch = types.ModuleType("torch")
    mock_backends = types.ModuleType("torch.backends")
    mock_mps = types.SimpleNamespace(is_available=lambda: False)
    mock_backends.mps = mock_mps
    mock_torch.backends = mock_backends
    mock_torch.cuda = types.SimpleNamespace(is_available=lambda: True)

    monkeypatch.setitem(__import__("sys").modules, "torch", mock_torch)

    from src.config import resolve_device

    assert resolve_device("auto") == "cuda"


def test_resolve_device_auto_cpu_fallback(monkeypatch):
    """resolve_device('auto') should fall back to 'cpu' when nothing is available."""
    import types

    mock_torch = types.ModuleType("torch")
    mock_backends = types.ModuleType("torch.backends")
    mock_mps = types.SimpleNamespace(is_available=lambda: False)
    mock_backends.mps = mock_mps
    mock_torch.backends = mock_backends
    mock_torch.cuda = types.SimpleNamespace(is_available=lambda: False)

    monkeypatch.setitem(__import__("sys").modules, "torch", mock_torch)

    from src.config import resolve_device

    assert resolve_device("auto") == "cpu"


def test_resolve_device_auto_no_torch(monkeypatch):
    """resolve_device('auto') should return 'cpu' when torch is not installed."""
    monkeypatch.setitem(__import__("sys").modules, "torch", None)

    from src.config import resolve_device

    assert resolve_device("auto") == "cpu"


def test_get_system_memory_gb_returns_positive():
    from src.config import _get_system_memory_gb

    mem = _get_system_memory_gb()
    assert mem > 0


def test_resolve_embedding_batch_size_mps_memory_tiers(monkeypatch):
    from src import config

    monkeypatch.setattr(config, "resolve_device", lambda _d: "mps")

    monkeypatch.setattr(config, "_get_system_memory_gb", lambda: 8.0)
    assert config.resolve_embedding_batch_size("mps") == 16

    monkeypatch.setattr(config, "_get_system_memory_gb", lambda: 16.0)
    assert config.resolve_embedding_batch_size("mps") == 32

    monkeypatch.setattr(config, "_get_system_memory_gb", lambda: 36.0)
    assert config.resolve_embedding_batch_size("mps") == 48

    monkeypatch.setattr(config, "_get_system_memory_gb", lambda: 64.0)
    assert config.resolve_embedding_batch_size("mps") == 48


def test_resolve_embedding_batch_size_mps_detection_fallback(monkeypatch):
    """When os.sysconf raises, _get_system_memory_gb returns 8.0 → MPS batch = 16."""
    from src import config

    def _raise(*_a):
        raise ValueError("unsupported")

    monkeypatch.setattr(os, "sysconf", _raise)
    monkeypatch.setattr(config, "resolve_device", lambda _d: "mps")

    assert config._get_system_memory_gb() == 8.0
    assert config.resolve_embedding_batch_size("mps") == 16


def test_iter_collection_ids_returns_all_pages():
    from src.storage import iter_collection_ids

    class DummyCollection:
        def __init__(self):
            self.calls = []

        def get(self, include, limit, offset):
            self.calls.append((include, limit, offset))
            if offset == 0:
                return {"ids": ["a", "b"]}
            if offset == 2:
                return {"ids": ["c"]}
            return {"ids": []}

    collection = DummyCollection()
    values = list(iter_collection_ids(collection, page_size=2))
    assert values == ["a", "b", "c"]


def test_iter_collection_metadatas_returns_all_pages_and_skips_empty():
    from src.storage import iter_collection_metadatas

    class DummyCollection:
        def get(self, include, limit, offset):
            if offset == 0:
                return {"metadatas": [{"uid": "1"}, None]}
            if offset == 2:
                return {"metadatas": [{"uid": "2"}]}
            return {"metadatas": []}

    collection = DummyCollection()
    values = list(iter_collection_metadatas(collection, page_size=2))
    assert values == [{"uid": "1"}, {"uid": "2"}]
