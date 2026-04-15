import json
from pathlib import Path


def test_default_live_report_path_uses_agent_report_convention():
    from src.qa_eval import default_live_report_path

    path = default_live_report_path(Path("docs/agent/qa_eval_questions.core.json"))

    assert path.name == "qa_eval_report.core.live.json"
    assert str(path).endswith("docs/agent/qa_eval_report.core.live.json")


def test_default_live_report_path_uses_backend_specific_suffix():
    from src.qa_eval import default_live_report_path

    path = default_live_report_path(Path("docs/agent/qa_eval_questions.live_expanded.json"), backend="embedding")

    assert path.name == "qa_eval_report.live_expanded.embedding.live.json"
    assert str(path).endswith("docs/agent/qa_eval_report.live_expanded.embedding.live.json")


def test_default_remediation_report_path_uses_agent_report_convention():
    from src.qa_eval import default_remediation_report_path

    path = default_remediation_report_path(Path("docs/agent/qa_eval_report.live_expanded.live.json"))

    assert path.name == "qa_eval_remediation.live_expanded.live.json"
    assert str(path).endswith("docs/agent/qa_eval_remediation.live_expanded.live.json")


def test_query_terms_extracts_lowercase_natural_language_tokens():
    from src.qa_eval import _query_terms

    terms = _query_terms("Which email had attachments and discussed Configurator 2 Blueprints?")

    assert "which" not in terms
    assert "attachments" not in terms
    assert "configurator" in terms
    assert "blueprints" in terms


def test_query_terms_drop_mailbox_noise_but_keep_topic_words():
    from src.qa_eval import _query_terms

    terms = _query_terms("Which image-only message was titled Manual and who sent the HARICA certificate mail?")

    assert "message" not in terms
    assert "sent" not in terms
    assert "mail" not in terms
    assert "manual" in terms
    assert "harica" in terms


def test_run_qa_eval_live_defaults_to_persistent_report(monkeypatch, tmp_path: Path, capsys):
    import scripts.run_qa_eval as runner

    questions_path = tmp_path / "qa_eval_questions.core.json"
    questions_path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    output_path = tmp_path / "qa_eval_report.core.live.json"

    monkeypatch.setattr(runner, "ROOT", tmp_path)

    def fake_resolve_live_deps(*, preferred_backend="auto"):
        assert preferred_backend == "auto"
        return object()

    def fake_default_live_report_path(path: Path, *, backend=None) -> Path:
        assert path == questions_path
        assert backend is None
        return output_path

    def fake_run_evaluation_sync(*, questions_path, results_path=None, live_deps=None, limit=None):
        assert questions_path == questions_path
        assert live_deps is not None
        return {"summary": {"total_cases": 0}, "results": []}

    monkeypatch.setattr("src.qa_eval.resolve_live_deps", fake_resolve_live_deps)
    monkeypatch.setattr("src.qa_eval.default_live_report_path", fake_default_live_report_path)
    monkeypatch.setattr("src.qa_eval.run_evaluation_sync", fake_run_evaluation_sync)

    exit_code = runner.main(["--questions", str(questions_path), "--live"])

    assert exit_code == 0
    assert output_path.exists()
    status = json.loads(capsys.readouterr().out)
    assert status["mode"] == "live"
    assert status["status"] == "ok"
    assert status["output"] == str(output_path)
    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted["summary"]["total_cases"] == 0
    assert persisted["results"] == []


def test_run_qa_eval_live_embedding_reexecs_into_project_venv(monkeypatch, tmp_path: Path):
    import scripts.run_qa_eval as runner

    questions_path = tmp_path / "qa_eval_questions.core.json"
    questions_path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    venv_python = tmp_path / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")

    monkeypatch.setattr(runner, "_interpreter_has_module", lambda name: False)
    monkeypatch.setattr(runner, "_project_venv_python", lambda: venv_python)

    seen: dict[str, object] = {}

    class _Completed:
        returncode = 0

    def fake_run(cmd, cwd):
        seen["cmd"] = cmd
        seen["cwd"] = cwd
        return _Completed()

    monkeypatch.setattr("subprocess.run", fake_run)

    exit_code = runner.main(["--questions", str(questions_path), "--live", "--live-backend", "embedding"])

    assert exit_code == 0
    assert seen["cmd"] == [
        str(venv_python),
        str(runner.__file__),
        "--questions",
        str(questions_path),
        "--live",
        "--live-backend",
        "embedding",
    ]


def test_run_qa_eval_live_writes_blocked_report(monkeypatch, tmp_path: Path, capsys):
    import scripts.run_qa_eval as runner

    questions_path = tmp_path / "qa_eval_questions.core.json"
    questions_path.write_text(json.dumps({"cases": []}), encoding="utf-8")
    output_path = tmp_path / "qa_eval_report.core.live.json"

    def fake_resolve_live_deps(*, preferred_backend="auto"):
        assert preferred_backend == "auto"
        raise ModuleNotFoundError("No module named 'chromadb'")

    def fake_default_live_report_path(path: Path, *, backend=None) -> Path:
        assert path == questions_path
        assert backend is None
        return output_path

    monkeypatch.setattr("src.qa_eval.resolve_live_deps", fake_resolve_live_deps)
    monkeypatch.setattr("src.qa_eval.default_live_report_path", fake_default_live_report_path)

    exit_code = runner.main(["--questions", str(questions_path), "--live"])

    assert exit_code == 1
    assert output_path.exists()
    status = json.loads(capsys.readouterr().out)
    assert status["mode"] == "live"
    assert status["status"] == "blocked"
    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted["live_status"]["status"] == "blocked"
    assert persisted["live_status"]["error_type"] == "ModuleNotFoundError"
    assert "chromadb" in persisted["live_status"]["error"]


def test_run_qa_eval_remediation_writes_persistent_summary(monkeypatch, tmp_path: Path, capsys):
    import scripts.run_qa_eval as runner

    report_path = tmp_path / "qa_eval_report.live_expanded.live.json"
    report_path.write_text(
        json.dumps(
            {
                "summary": {
                    "total_cases": 3,
                    "bucket_counts": {"fact_lookup": 1, "thread_process": 2},
                    "top_1_correctness": {"scorable": 3, "passed": 1, "failed": 2},
                    "support_uid_hit_top_3": {"scorable": 3, "passed": 1, "failed": 2},
                    "confidence_calibration_match": {"scorable": 3, "passed": 1, "failed": 2},
                },
                "failure_taxonomy": {
                    "total_flagged_cases": 2,
                    "ranked_categories": [
                        {
                            "category": "retrieval_recall",
                            "flagged_cases": 2,
                            "failed_cases": 2,
                            "weak_cases": 0,
                            "case_ids": ["fact-1", "thread-1"],
                            "drivers": ["no_supported_hit"],
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "qa_eval_remediation.live_expanded.live.json"

    monkeypatch.setattr(runner, "ROOT", tmp_path)

    exit_code = runner.main(["--remediation-from", str(report_path), "--output", str(output_path)])

    assert exit_code == 0
    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted["total_cases"] == 3
    assert persisted["failure_taxonomy"]["ranked_categories"][0]["category"] == "retrieval_recall"
    assert json.loads(capsys.readouterr().out)["immediate_next_targets"][0]["category"] == "retrieval_recall"
