#!/usr/bin/env bash
set -euo pipefail

profile="${1:-local}"

if [[ "$profile" != "local" && "$profile" != "ci" ]]; then
  echo "Usage: bash scripts/run_acceptance_matrix.sh [local|ci]" >&2
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
require_command mypy
require_command pytest
require_command bandit
require_command python

if [[ "$profile" == "ci" ]]; then
  echo "Running CI profile. Ensure dependencies are installed: pip install -r requirements-dev.txt"
fi

run_step "Lint (ruff)" ruff check .

if [[ "$profile" == "ci" ]]; then
  run_step "Type check (mypy src)" mypy src
else
  run_step "Type check (mypy src --ignore-missing-imports)" mypy src --ignore-missing-imports
fi

run_step "Test suite (pytest -q)" pytest -q
run_step "Security scan (bandit -r src -q)" bandit -r src -q

audit_cache_dir="$(mktemp -d)"
trap 'rm -rf "$audit_cache_dir"' EXIT
run_step \
  "Dependency audit (python -m pip_audit -r requirements.txt)" \
  env PIP_CACHE_DIR="$audit_cache_dir" \
  bash -lc 'python -m pip_audit -r requirements.txt 2> >(grep -v "Cache entry deserialization failed, entry ignored" >&2)'

echo
echo "Acceptance matrix profile '${profile}' passed."
