# Test and Acceptance Matrix

Last updated: March 2, 2026

## Scope

This matrix defines release acceptance checks for:

- static quality and security gates
- behavior-level regression protection
- manual UI acceptance for Streamlit flows

## Prerequisites

Install tooling and dependencies before running gate profiles:

```bash
pip install -r requirements-dev.txt
```

## Gate Profiles

| Profile | Purpose | Command |
| --- | --- | --- |
| local | Day-to-day verification in mixed local environments | `bash scripts/run_acceptance_matrix.sh` |
| ci | Pull-request and merge gate aligned with GitHub Actions | `bash scripts/run_acceptance_matrix.sh ci` |

## Automated Acceptance Matrix

| ID | Area | Acceptance Criteria | Verification |
| --- | --- | --- | --- |
| A-01 | Linting | No lint violations in repository code | `ruff check .` |
| A-02 | Type Safety (CI) | `src/` type checks with no errors | `mypy src` |
| A-03 | Type Safety (Local) | Type checks run in local envs where optional deps may be absent | `mypy src --ignore-missing-imports` |
| A-04 | Test Suite | All unit/integration tests pass | `pytest -q` |
| A-05 | Security Static Analysis | No Bandit findings at configured threshold | `bandit -r src -q` |
| A-06 | Dependency Vulnerability Audit | No known vulnerabilities in runtime dependencies | `python -m pip_audit -r requirements.txt` |

## Feature Coverage Matrix

| ID | Feature Area | Expected Behavior | Coverage |
| --- | --- | --- | --- |
| F-01 | CLI output modes | `--json` and `--format` behave correctly and validate combinations | `tests/test_cli_filters.py`, `tests/test_cli_validation.py` |
| F-02 | CLI advanced filtering | sender/subject/folder/date/min-score filter parsing and validation | `tests/test_cli_filters.py`, `tests/test_cli_validation.py` |
| F-03 | Ingestion QoL stats | `chunks_skipped` and `batches_written` are computed and summarized | `tests/test_ingest.py` |
| F-04 | Retriever filtering | sender/date/subject/folder/min-score filtering produces correct result sets | `tests/test_retriever_filter_recall.py`, `tests/test_retriever_stats.py` |
| F-05 | MCP structured output | structured tool validates inputs and forwards supported filters | `tests/test_mcp_tools.py` |
| F-06 | Sanitization hardening | untrusted control sequences are sanitized in CLI/MCP output | `tests/test_cli_sanitization.py`, `tests/test_cli_answer_safety.py`, `tests/test_mcp_tools.py` |
| F-07 | Streamlit helper logic | sorting, filter chips, and export payload metadata are correct | `tests/test_web_ui.py` |

## Manual UAT Matrix (Streamlit)

Run UI:

```bash
streamlit run src/web_app.py
```

| ID | Scenario | Steps | Expected Result |
| --- | --- | --- | --- |
| U-01 | Filtered search | Enter query + sender + subject + folder + date range + min relevance | Returned results satisfy all active filters |
| U-02 | Sort behavior | Run search and switch sort mode (Relevance/Newest/Oldest/Sender) | Result order updates deterministically by selected mode |
| U-03 | Result details | Open first result, inspect preview and full chunk expansion | Metadata, relevance bar, preview truncation, and full text rendering are correct |
| U-04 | Export fidelity | Download JSON after filtered/sorted search | JSON contains `query`, `results`, `filters`, `sort_by`, and `generated_at` |
| U-05 | Empty state | Run search expected to match nothing | UI shows non-error empty-state guidance message |

## Release Exit Criteria

A release is acceptable when all conditions are met:

1. CI profile gates pass.
2. Local profile gates pass in maintainer environment.
3. No unresolved high-severity security findings.
4. Manual UAT scenarios U-01 through U-05 pass.

## Latest Verification Snapshot

Recorded on March 2, 2026:

- `bash scripts/run_acceptance_matrix.sh` -> passed
- `source .venv/bin/activate && bash scripts/run_acceptance_matrix.sh ci` -> passed
