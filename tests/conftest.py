import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_chromadb_stub() -> None:
    if "chromadb" in sys.modules:
        return

    chromadb = types.ModuleType("chromadb")

    class _DummyCollection:
        def __init__(self) -> None:
            self._items = []

        def add(self, ids, embeddings, documents, metadatas):
            for idx, chunk_id in enumerate(ids):
                self._items.append(
                    {
                        "id": chunk_id,
                        "embedding": embeddings[idx],
                        "document": documents[idx],
                        "metadata": metadatas[idx],
                    }
                )

        def count(self):
            return len(self._items)

        def get(self, include=None, limit=None, offset=0):
            include = include or []
            limit = len(self._items) if limit is None else limit
            batch = self._items[offset : offset + limit]
            out = {"ids": [item["id"] for item in batch]}
            if "metadatas" in include:
                out["metadatas"] = [item["metadata"] for item in batch]
            if "documents" in include:
                out["documents"] = [item["document"] for item in batch]
            return out

        def query(self, query_embeddings, n_results, include=None, where=None):
            include = include or []
            candidates = self._items
            if where:
                def _match(meta):
                    return all(meta.get(k) == v for k, v in where.items())
                candidates = [item for item in candidates if _match(item["metadata"])]

            picked = candidates[:n_results]
            response = {"ids": [[item["id"] for item in picked]]}
            if "documents" in include:
                response["documents"] = [[item["document"] for item in picked]]
            if "metadatas" in include:
                response["metadatas"] = [[item["metadata"] for item in picked]]
            if "distances" in include:
                response["distances"] = [[0.1 for _ in picked]]
            return response

    class _DummyClient:
        def __init__(self, path=None, settings=None):
            self._collection = _DummyCollection()

        def get_or_create_collection(self, name, metadata=None):
            return self._collection

        def delete_collection(self, name):
            self._collection = _DummyCollection()

    chromadb.PersistentClient = _DummyClient
    sys.modules["chromadb"] = chromadb

    chromadb_config = types.ModuleType("chromadb.config")

    class _Settings:
        def __init__(self, anonymized_telemetry=False):
            self.anonymized_telemetry = anonymized_telemetry

    chromadb_config.Settings = _Settings
    sys.modules["chromadb.config"] = chromadb_config


def _ensure_sentence_transformers_stub() -> None:
    if "sentence_transformers" in sys.modules:
        return

    sentence_transformers = types.ModuleType("sentence_transformers")

    class SentenceTransformer:
        def __init__(self, model_name):
            self.model_name = model_name

        def encode(self, texts, show_progress_bar=False):
            return [[0.1, 0.2, 0.3] for _ in texts]

    sentence_transformers.SentenceTransformer = SentenceTransformer
    sys.modules["sentence_transformers"] = sentence_transformers


def _ensure_mcp_stub() -> None:
    if "mcp.server.fastmcp" in sys.modules:
        return

    mcp_mod = types.ModuleType("mcp")
    server_mod = types.ModuleType("mcp.server")
    fastmcp_mod = types.ModuleType("mcp.server.fastmcp")

    class FastMCP:
        def __init__(self, name):
            self.name = name

        def tool(self, *args, **kwargs):
            def _decorator(fn):
                return fn

            return _decorator

        def run(self):
            return None

    fastmcp_mod.FastMCP = FastMCP
    sys.modules["mcp"] = mcp_mod
    sys.modules["mcp.server"] = server_mod
    sys.modules["mcp.server.fastmcp"] = fastmcp_mod


_ensure_chromadb_stub()
_ensure_sentence_transformers_stub()
_ensure_mcp_stub()
