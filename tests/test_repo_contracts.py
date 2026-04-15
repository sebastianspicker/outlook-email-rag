from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_env_example_includes_analytics_timezone():
    env_example = _read(".env.example")
    assert "ANALYTICS_TIMEZONE=" in env_example
    assert "RUNTIME_PROFILE=" in env_example
    assert "EMBEDDING_LOAD_MODE=" in env_example


def test_readme_install_flow_includes_package_install_for_console_scripts():
    readme = _read("README.md")
    assert "pip install -e ." in readme
    assert "docs/RUNTIME_TUNING.md" in readme
    assert "RUNTIME_PROFILE=quality" in readme
    assert "# RERANK_ENABLED=false" in readme
    assert "# HYBRID_ENABLED=false" in readme
    assert "# SPARSE_ENABLED=false" in readme
    assert "# COLBERT_RERANK_ENABLED=false" in readme


def test_env_example_uses_profile_first_quality_setup():
    env_example = _read(".env.example")
    assert "RUNTIME_PROFILE=quality" in env_example
    assert "# SPARSE_ENABLED=true" in env_example
    assert "# COLBERT_RERANK_ENABLED=true" in env_example
    assert "# RERANK_ENABLED=true" in env_example
    assert "# HYBRID_ENABLED=true" in env_example


def test_readme_does_not_hardcode_test_count_badge():
    readme = _read("README.md")
    assert not re.search(r"badge/tests-\d+", readme)
    assert "tests (2147+)" not in readme


def test_readme_mcp_tool_badge_matches_documented_tool_count():
    readme = _read("README.md")
    assert "badge/MCP_tools-58" in readme
    assert "Email RAG exposes 58 MCP tools." in readme
    assert "You should see all 58 tools listed beneath it" in readme


def test_readme_privacy_note_matches_first_run_download_boundary():
    readme = _read("README.md")
    assert "No API calls, no data leaves your machine." not in readme
    assert "Email content stays local" in readme
    assert "Hugging Face" in readme


def test_readme_routes_real_mailboxes_into_private_workspace():
    readme = _read("README.md")
    assert "private/ingest/my-export.olm" in readme
    assert "`private/` is ignored by Git" in readme
    assert "Keep tracked `data/` and `tests/fixtures/` content sanitized." in readme


def test_gitignore_excludes_private_local_matter_workspaces():
    gitignore = _read(".gitignore")
    assert "/private/" in gitignore
    assert "/data/private/" in gitignore
    assert "/tests/private/" in gitignore
    assert "/tests/fixtures/private/" in gitignore
    assert ".streamlit/secrets.toml" in gitignore


def test_topic_surfaces_are_demoted_from_stable_contract():
    readme = _read("README.md")
    compatibility = _read("docs/API_COMPATIBILITY.md")

    assert "default ingest workflow does not populate topic tables yet" in readme
    assert "Conditional Topic Surface" in compatibility
    assert "excluded from the stable `0.1.x` compatibility contract" in compatibility


def test_cli_reference_leads_with_subcommands_before_legacy_flags():
    cli_reference = _read("docs/CLI_REFERENCE.md")
    assert "## Subcommands (recommended)" in cli_reference
    assert "## Legacy Flat-Flag Reference" in cli_reference
    assert cli_reference.index("## Subcommands (recommended)") < cli_reference.index("## Legacy Flat-Flag Reference")
    assert "python -m src.cli search" in cli_reference
    assert "python -m src.cli --query" not in cli_reference


def test_diagnostics_docs_describe_resolved_runtime_summary():
    readme = _read("README.md")
    mcp_tools = _read("docs/MCP_TOOLS.md")
    compatibility = _read("docs/API_COMPATIBILITY.md")

    assert "shows resolved runtime settings, embedder/backend state, MCP budgets, and sparse-index status" in readme
    assert "resolved runtime profile/load mode/device/batch size" in mcp_tools
    assert "current embedder backend state" in mcp_tools
    assert "resolved runtime settings" in compatibility
    assert "current embedder/backend state" in compatibility


def test_response_time_docs_are_explicitly_sample_scoped():
    readme = _read("README.md")
    cli_reference = _read("docs/CLI_REFERENCE.md")
    mcp_tools = _read("docs/MCP_TOOLS.md")
    analysis_models = _read("src/mcp_models_analysis.py")

    assert "recent-sample response times per sender based on canonical reply pairs" in readme
    assert "Recent-sample response times per sender (canonical reply pairs)" in cli_reference
    assert "recent-sample response times per sender based on canonical reply pairs" in mcp_tools
    assert "recent-sample response times per sender based on canonical reply pairs" in analysis_models
    assert "average response times per sender" not in mcp_tools


def test_internal_operator_workspace_artifacts_are_not_checked_in():
    absent_paths = [
        "AGENTS.md",
        "HARNESS_PRINCIPLES.md",
        "code_review.md",
        "AUDIT.md",
        "CLAUDE.md",
        "docs/source-audit",
        "docs/agent/Prompt.md",
        "docs/agent/Plan.md",
        "docs/agent/Documentation.md",
        "docs/agent/Implement.md",
        "docs/agent/AutonomousHardStops.md",
        "docs/agent/RunModes.md",
        "docs/agent/AutonomyPolicy.md",
        "docs/agent/Goals.md",
        "docs/agent/RepoProfile.md",
        "docs/agent/Findings.md",
        "docs/agent/Backlog.md",
        "docs/agent/Topology.md",
        "docs/agent/VerificationMatrix.md",
        "docs/agent/Checkpoint.md",
        "docs/agent/Decisions.md",
        "docs/agent/ingestion_optimization_plan.md",
        "docs/agent/ingestion_optimization_progress.md",
        "docs/agent/qa_eval_plan.md",
        "docs/agent/qa_eval_captured_refresh.md",
    ]
    for relative_path in absent_paths:
        assert not (REPO_ROOT / relative_path).exists(), relative_path


def test_gitignore_keeps_internal_operator_artifacts_out_of_future_commits():
    gitignore = _read(".gitignore")
    expected_entries = [
        "AGENTS.md",
        "HARNESS_PRINCIPLES.md",
        "code_review.md",
        "AUDIT.md",
        "CLAUDE.md",
        "docs/source-audit/",
        "docs/agent/Prompt.md",
        "docs/agent/Plan.md",
        "docs/agent/Documentation.md",
        "docs/agent/Implement.md",
        "docs/agent/AutonomousHardStops.md",
        "docs/agent/RunModes.md",
        "docs/agent/AutonomyPolicy.md",
        "docs/agent/Goals.md",
        "docs/agent/RepoProfile.md",
        "docs/agent/Findings.md",
        "docs/agent/Backlog.md",
        "docs/agent/Topology.md",
        "docs/agent/VerificationMatrix.md",
        "docs/agent/Checkpoint.md",
        "docs/agent/Decisions.md",
        "docs/agent/ingestion_optimization_plan.md",
        "docs/agent/ingestion_optimization_progress.md",
        "docs/agent/qa_eval_plan.md",
        "docs/agent/qa_eval_captured_refresh.md",
    ]
    for entry in expected_entries:
        assert entry in gitignore


def test_readme_and_docs_index_reflect_public_documentation_surface():
    readme = _read("README.md")
    docs_index = _read("docs/README.md")

    assert "[docs/README.md](docs/README.md)" in readme
    assert "Available MCP Tool Families (58 tools)" in readme
    assert "A visual search interface that runs in your browser. This is the exploratory GUI" in readme
    assert "private/ingest/latest-export.olm" in readme
    assert "├── private/" in readme
    assert "[CLI_REFERENCE.md](CLI_REFERENCE.md)" in docs_index
    assert "[MCP_TOOLS.md](MCP_TOOLS.md)" in docs_index
    assert "The legal-support system ships with dedicated contract docs under [`agent/`](agent/)." in docs_index


def test_acceptance_matrix_tracks_ci_contracts():
    acceptance = _read("scripts/run_acceptance_matrix.sh")
    ci = _read(".github/workflows/ci.yml")

    expected_checks = [
        "python -m ruff check .",
        "python -m ruff format --check .",
        "python -m mypy src",
        "python -m pytest -q --tb=short --cov=src --cov-report=term-missing --cov-fail-under=80",
        "python -m bandit -r src -q -ll -ii",
        "python -m pip_audit -r requirements.txt --ignore-vuln CVE-2026-4539",
        "python scripts/streamlit_smoke.py",
    ]

    for check in expected_checks:
        assert check in acceptance
    for check in [
        "ruff check .",
        "ruff format --check .",
        "mypy src",
        "pytest -q --tb=short --cov=src --cov-report=term-missing --cov-fail-under=80",
        "bandit -r src -q -ll -ii",
        "python -m pip_audit -r requirements.txt --ignore-vuln CVE-2026-4539",
        "python scripts/streamlit_smoke.py",
    ]:
        assert check in ci

    assert "Skipping in local profile because pypi.org is unreachable from this environment." in acceptance


def test_acceptance_matrix_exposes_release_profile_with_required_dependency_audit():
    acceptance = _read("scripts/run_acceptance_matrix.sh")

    assert "Usage: bash scripts/run_acceptance_matrix.sh [local|ci|release]" in acceptance
    assert "Running release profile. Dependency audit is required and may not be skipped." in acceptance
    expected = "Release profile requires a real dependency-audit result, but pypi.org is unreachable from this environment."
    assert expected in acceptance
