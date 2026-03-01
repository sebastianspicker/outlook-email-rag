import sys
import types


def test_ask_claude_handles_api_failures(monkeypatch):
    from src import cli

    class _Messages:
        @staticmethod
        def create(**kwargs):
            raise RuntimeError("upstream unavailable")

    class _AnthropicClient:
        def __init__(self, api_key):
            self.messages = _Messages()

    anthropic_mod = types.ModuleType("anthropic")
    anthropic_mod.Anthropic = _AnthropicClient

    monkeypatch.setitem(sys.modules, "anthropic", anthropic_mod)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")

    response = cli.ask_claude("test query", "retrieved context")
    assert "Claude request failed" in response
