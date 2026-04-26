# Deep Repository Familiarization + Audit

Date: 2026-04-26
Audit mode: architecture + codebase + scripts + integration + remediation pass

---

## 1) What the repository is

`outlook-email-rag` is a local-first platform for indexing and searching Outlook `.olm`
archives, with downstream evidence workflows and structured legal-support products.
It exposes three user surfaces over one shared runtime:

1. **CLI (`email-rag`, `email-rag-ingest`)** for operator-first deterministic workflows.
2. **MCP server/tools** for assistant-driven orchestration.
3. **Streamlit UI** for exploratory inspection and analysis.

Core design contract:
- keep sensitive content local,
- use hybrid retrieval over local indexes,
- preserve provenance and reproducibility,
- provide export/evidence/case workflows on top of the same runtime.

---

## 2) Inventory and topology

Command-driven inventory from this workspace:

- Python modules in `src/`: **270**
- Tool modules in `src/tools/`: **35**
- Automation scripts in `scripts/`: **13**
- Test modules in `tests/`: **386**

`bash scripts/topology_inventory.sh .` confirms a single Python service root with
`pyproject.toml` at repository top-level.

Large-module hotspots (by line count) that dominate complexity and future refactor effort:
- `src/ingest_pipeline.py` (~980 lines)
- `src/db_evidence.py` (~916 lines)
- `src/db_schema_migrations.py` (~833 lines)
- `src/matter_evidence_index_helpers.py` (~827 lines)
- `src/ingest_reingest.py` (~813 lines)
- `src/email_db.py` (~810 lines)

---

## 3) How features work together (integration map)

## 3.1 Ingestion and storage

- `src/ingest.py` is the user-facing ingestion entrypoint and argument layer.
- It delegates into ingest family modules (notably `src/ingest_pipeline.py`,
  `src/ingest_reingest.py`, and embedding/chunking helpers).
- Output state lands in:
  - **ChromaDB** for vector retrieval,
  - **SQLite** for metadata, evidence, provenance, and analytics surfaces.

## 3.2 Retrieval and ranking

Hybrid retrieval combines dense semantic, sparse/keyword channels, and optional reranking.
These retrieval primitives are then consumed by:
- CLI search commands,
- MCP search tools,
- Streamlit search pages.

## 3.3 Evidence and case workflows

On top of retrieval, the repository layers:
- evidence capture and export,
- chronology and case analysis,
- full-pack/counsel-pack style outputs,
- diagnostics and QA/evaluation utilities.

## 3.4 Surface unification

- `src/cli.py` provides unified dispatch for operator commands.
- `src/tools/__init__.py` registers MCP tool modules against shared dependencies.
- Docs position Streamlit as exploratory and CLI/MCP case outputs as authoritative.

---

## 4) Scripts and operational tooling audit

Scripts reviewed for role coverage:
- acceptance orchestration: `scripts/run_acceptance_matrix.sh`
- privacy scanning: `scripts/privacy_scan.py`
- dependency CVE gate: `scripts/dependency_audit.py`
- ingest/workspace cleanup and smoke probes:
  - `scripts/clean_ingest_reset.sh`
  - `scripts/ingest_smoke.py`
  - `scripts/wave_workflow_smoke.py`
  - `scripts/streamlit_smoke.py`
- inventory/ops helpers:
  - `scripts/topology_inventory.sh`
  - `scripts/private_runtime_current_env.sh`

General assessment:
- operational script coverage is broad and practical,
- quality/security/privacy gates exist and are codified,
- reproducibility is strongest when the environment matches project constraints
  (Python 3.11+ and dev dependencies installed).

---

## 5) Verification commands and outcomes

Executed in this environment:

1. `python --version`
2. `bash scripts/topology_inventory.sh .`
3. `python scripts/privacy_scan.py --tracked-only --json`
4. `ruff check src tests`
5. `python -c "import src"`
6. `PYENV_VERSION=3.11.14 python -c "import src; print('ok')"`
7. `python scripts/dependency_audit.py`

Outcomes:
- topology check: pass
- tracked privacy scan: pass (`[]`)
- ruff lint on `src` + `tests`: pass
- default interpreter import: now fails fast with actionable Python-version error
- Python 3.11 import: pass (`ok`)
- dependency audit wrapper: now emits explicit missing-tool guidance and exits cleanly

---

## 6) Issues discovered and remediation status

### P0 findings

No reproducible P0 data-loss/security break was found in this bounded pass.

### P1 findings

#### P1-01: Unsupported interpreter produced opaque downstream import failures

- Symptom before remediation: under Python 3.10, users encountered late/indirect import errors.
- Risk: operator confusion, wasted triage time, inconsistent bootstrap behavior.
- **Remediation applied:** package-level fail-fast runtime guard in `src/__init__.py`.
- Result: unsupported interpreters now fail immediately with explicit upgrade instruction.

### P2 findings

#### P2-01: `scripts/dependency_audit.py` failed with low-context module errors

- Symptom before remediation: `No module named pip_audit` without remediation guidance.
- Risk: weaker operator experience; slower verification unblocking.
- **Remediation applied:** explicit preflight check for `pip_audit` + actionable install hint.
- Result: deterministic and informative failure mode.

---

## 7) Refactor / dedup / optimization opportunities (next wave)

These are not all remediated in this single pass; they are prioritized follow-ups:

1. **Module decomposition of largest files (P2 maintainability):**
   split ingest/evidence/migration hot files into narrower units with explicit public APIs.
2. **Cross-surface command normalization (P2):**
   continue convergence of CLI + MCP option handling and shared validators.
3. **Performance profiling pass (P2/P1 depending findings):**
   benchmark ingest and retrieval hot paths with representative corpus sizes,
   then target data-structure/IO hotspots.
4. **Verification portability hardening (P2):**
   make environment preflight explicit in acceptance scripts (Python version + required tools)
   before running heavy checks.

---

## 8) Changes completed in this remediation iteration

1. Added package-level Python support guard in `src/__init__.py`.
2. Improved dependency-audit script preflight and diagnostics in
   `scripts/dependency_audit.py`.
3. Optimized history scanning in `scripts/privacy_scan.py` by replacing
   per-commit tree walks with a single `git rev-list --objects --all` pass.
4. Added explicit Python 3.11+ preflight enforcement in
   `scripts/run_acceptance_matrix.sh`.
5. Synced ingest reset preservation messaging in
   `scripts/clean_ingest_reset.sh` to include both
   `private/ingest/example-export.olm` and `private/ingest/my-export.olm`.

Related iterative verification ledger:
- `docs/agent/iterative_audit_loop_2026-04-26.md` (20 repeated passes over
  selected audit checks).

This audit pass is now **code + operations remediation**, not documentation-only.
