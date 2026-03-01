from src.retriever import SearchResult


def test_single_query_sanitizes_claude_answer(monkeypatch, capsys):
    from src import cli

    class DummyRetriever:
        def search_filtered(self, **kwargs):
            return [
                SearchResult(
                    chunk_id="x",
                    text="hello",
                    metadata={"subject": "Hi", "sender_email": "a@example.com"},
                    distance=0.1,
                )
            ]

        def format_results_for_claude(self, results):
            return "context"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
    monkeypatch.setattr(cli, "ask_claude", lambda query, context: "safe\x1b]8;;https://evil.test\x07ok")

    code = cli.run_single_query(
        DummyRetriever(),
        query="hello",
        raw=False,
        no_claude=False,
        as_json=False,
        top_k=1,
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "evil.test" not in out
    assert "\x1b" not in out
