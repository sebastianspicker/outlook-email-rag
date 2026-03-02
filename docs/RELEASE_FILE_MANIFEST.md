# Release File Manifest

This manifest records what belongs in the public GitHub release package.

## Keep (Public Release)

1. Runtime and interfaces:
   1. `src/`
   2. `requirements.txt`
   3. `requirements-dev.txt`
2. Documentation and policy:
   1. `README.md`
   2. `CHANGELOG.md`
   3. `CONTRIBUTING.md`
   4. `SECURITY.md`
   5. `CODE_OF_CONDUCT.md`
   6. `docs/TEST_ACCEPTANCE_MATRIX.md`
   7. `docs/RELEASE_CHECKLIST.md`
   8. `docs/API_COMPATIBILITY.md`
3. Repo automation:
   1. `.github/workflows/ci.yml`
   2. `.github/workflows/release.yml`
   3. `.github/pull_request_template.md`
   4. `.github/ISSUE_TEMPLATE/bug_report.md`
   5. `.github/ISSUE_TEMPLATE/feature_request.md`
4. Utility scripts:
   1. `scripts/run_acceptance_matrix.sh`
   2. `scripts/clean_workspace.sh`

## Remove (Do Not Ship)

1. Internal diagnostics:
   1. `docs/DEEP_CODE_INSPECTION_FINDINGS.md`
2. Local runtime and cache artifacts:
   1. `.mypy_cache/`
   2. `.pytest_cache/`
   3. `.ruff_cache/`
   4. `__pycache__/`
   5. `.venv/` (optional cleanup, local environment)
3. Temporary files:
   1. `*.log`
   2. `*.tmp`
   3. `*.bak`
   4. `*.orig`
   5. `*.sqlite3`
   6. `*.db`
