#!/usr/bin/env bash
set -euo pipefail

profile="${1:-local}"
python_bin="python"

if [[ "$profile" != "local" && "$profile" != "ci" && "$profile" != "release" ]]; then
  echo "Usage: bash scripts/run_acceptance_matrix.sh [local|ci|release]" >&2
  exit 2
fi

run_step() {
  local label="$1"
  shift
  echo
  echo "==> ${label}"
  "$@"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 127
  fi
}

require_command ruff
require_command python

if [[ "$profile" == "ci" ]]; then
  echo "Running CI profile. Ensure dependencies are installed: pip install -r requirements-dev.txt"
elif [[ "$profile" == "release" ]]; then
  echo "Running release profile. Dependency audit is required and may not be skipped."
fi

run_step "Lint (python -m ruff check .)" "$python_bin" -m ruff check .
run_step "Format check (python -m ruff format --check .)" "$python_bin" -m ruff format --check .
run_step "Type check (python -m mypy src)" "$python_bin" -m mypy src
run_step \
  "Test suite (python -m pytest -q --tb=short --cov=src --cov-report=term-missing --cov-fail-under=80; local thread caps enabled)" \
  env \
  OMP_NUM_THREADS=1 \
  OPENBLAS_NUM_THREADS=1 \
  MKL_NUM_THREADS=1 \
  VECLIB_MAXIMUM_THREADS=1 \
  NUMEXPR_NUM_THREADS=1 \
  "$python_bin" -m pytest -q --tb=short --cov=src --cov-report=term-missing --cov-fail-under=80
run_step \
  "Legal-support captured artifacts contract (python scripts/refresh_qa_eval_captured_reports.py --check --scenario legal_support --scenario legal_support_full_pack_goldens)" \
  "$python_bin" scripts/refresh_qa_eval_captured_reports.py --check --scenario legal_support --scenario legal_support_full_pack_goldens
run_step \
  "Exhaustive legal-support smoke tests (python -m pytest -q tests/test_case_analysis.py tests/test_case_analysis_exhaustive_persistence.py tests/test_matter_file_ingestion.py tests/test_case_full_pack.py tests/test_cli_subcommands_case.py tests/test_matter_snapshot_persistence.py tests/test_mcp_tools_case_full_pack.py tests/test_mcp_tools_legal_support.py tests/test_legal_support_exporter.py tests/test_qa_eval_legal_support_artifacts.py tests/test_qa_eval_legal_support_full_pack_goldens.py)" \
  "$python_bin" -m pytest -q tests/test_case_analysis.py tests/test_case_analysis_exhaustive_persistence.py tests/test_matter_file_ingestion.py tests/test_case_full_pack.py tests/test_cli_subcommands_case.py tests/test_matter_snapshot_persistence.py tests/test_mcp_tools_case_full_pack.py tests/test_mcp_tools_legal_support.py tests/test_legal_support_exporter.py tests/test_qa_eval_legal_support_artifacts.py tests/test_qa_eval_legal_support_full_pack_goldens.py
run_step "Real ingest smoke (python scripts/ingest_smoke.py)" "$python_bin" scripts/ingest_smoke.py
run_step "Case CLI counsel-pack help (python -m src.cli case counsel-pack --help)" "$python_bin" -m src.cli case counsel-pack --help
run_step "Case CLI full-pack help (python -m src.cli case full-pack --help)" "$python_bin" -m src.cli case full-pack --help
run_step "Streamlit smoke (python scripts/streamlit_smoke.py)" "$python_bin" scripts/streamlit_smoke.py
run_step "Security scan (python -m bandit -r src -q -ll -ii)" "$python_bin" -m bandit -r src -q -ll -ii

audit_cache_dir="$(mktemp -d)"
trap 'rm -rf "$audit_cache_dir"' EXIT

if [[ "$profile" == "local" ]] && ! "$python_bin" -c 'import socket; socket.getaddrinfo("pypi.org", 443)' >/dev/null 2>&1; then
  echo
  echo "==> Dependency audit (python -m pip_audit -r requirements.txt --ignore-vuln CVE-2026-4539)"
  echo "Skipping in local profile because pypi.org is unreachable from this environment."
elif [[ "$profile" == "release" ]] && ! "$python_bin" -c 'import socket; socket.getaddrinfo("pypi.org", 443)' >/dev/null 2>&1; then
  echo
  echo "==> Dependency audit (python -m pip_audit -r requirements.txt --ignore-vuln CVE-2026-4539)"
  echo "Release profile requires a real dependency-audit result, but pypi.org is unreachable from this environment." >&2
  exit 1
else
  run_step \
    "Dependency audit (python -m pip_audit -r requirements.txt --ignore-vuln CVE-2026-4539)" \
    env PIP_CACHE_DIR="$audit_cache_dir" \
    "$python_bin" -m pip_audit -r requirements.txt --ignore-vuln CVE-2026-4539
fi

echo
echo "Acceptance matrix profile '${profile}' passed."
