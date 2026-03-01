

def test_settings_defaults(monkeypatch):
    monkeypatch.delenv("CHROMADB_PATH", raising=False)
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.delenv("COLLECTION_NAME", raising=False)
    monkeypatch.delenv("TOP_K", raising=False)
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)

    from src.config import Settings

    settings = Settings.from_env()
    assert settings.chromadb_path == "data/chromadb"
    assert settings.embedding_model == "all-MiniLM-L6-v2"
    assert settings.collection_name == "emails"
    assert settings.top_k == 10
    assert settings.claude_model


def test_settings_top_k_rejects_invalid_env(monkeypatch):
    monkeypatch.setenv("TOP_K", "0")

    from src.config import Settings

    settings = Settings.from_env()
    assert settings.top_k == 10


def test_settings_top_k_clamps_large_env(monkeypatch):
    monkeypatch.setenv("TOP_K", "5000")

    from src.config import Settings

    settings = Settings.from_env()
    assert settings.top_k == 1000


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
