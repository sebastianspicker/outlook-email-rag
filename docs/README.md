# Documentation Guide

This directory contains the public reference material for Email RAG.

## Start Here

- [../README.md](../README.md) for installation, local-first privacy boundaries, and the main workflows
- [CLI_REFERENCE.md](CLI_REFERENCE.md) for the terminal interface
- [MCP_TOOLS.md](MCP_TOOLS.md) for the full MCP tool surface
- [RUNTIME_TUNING.md](RUNTIME_TUNING.md) for performance, model-loading, and Apple Silicon guidance
- [API_COMPATIBILITY.md](API_COMPATIBILITY.md) for interface-stability expectations

## Legal-Support Contracts

The legal-support system ships with dedicated contract docs under [`agent/`](agent/). These are product docs for the structured matter-review surfaces, not local run-state notes.

Key entry points:

- [`agent/case_full_pack.md`](agent/case_full_pack.md)
- [`agent/review_governance.md`](agent/review_governance.md)
- [`agent/matter_evidence_index.md`](agent/matter_evidence_index.md)
- [`agent/master_chronology.md`](agent/master_chronology.md)
- [`agent/lawyer_issue_matrix.md`](agent/lawyer_issue_matrix.md)
- [`agent/lawyer_briefing_memo.md`](agent/lawyer_briefing_memo.md)
- [`agent/case_dashboard.md`](agent/case_dashboard.md)

## Local-Only Data

- Put real Outlook exports and live matter files in `private/`
- Keep checked-in `data/` and `tests/fixtures/` content sanitized
- The Streamlit app is an exploratory surface; counsel-ready legal-support exports are CLI/MCP workflows
