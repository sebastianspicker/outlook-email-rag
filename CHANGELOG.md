# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to semantic versioning principles for public interfaces.

## [Unreleased]

### Added

- `src/__main__.py`: `python -m src` now starts the MCP server directly.
- `.claude/settings.json`: project-level MCP server registration — Claude Code auto-discovers tools when the project is opened.
- `pyproject.toml`: added `[project]` and `[build-system]` sections; `pip install -e .[dev]` now works.
- New MCP tool `email_list_folders`: lists archive folders with email counts.
- New MCP tool `email_ingest`: triggers `.olm` ingestion directly from Claude Code.
- `--cc` filter flag for CLI search.
- `--version` flag for CLI.
- `list_folders()` method on `EmailRetriever`.
- CC recipient filter (`cc` parameter) in `EmailRetriever.search_filtered()` and `email_search_structured` MCP tool.
- Streamlit UI: date picker widgets replace manual date text inputs.
- Streamlit UI: pagination (20 results per page) with prev/next navigation.
- Streamlit UI: CC filter field in search form.
- Streamlit UI: improved empty-state message with ingestion instructions.
- Shared `positive_int()` validator in `src/validation.py`.

### Changed

- Removed `anthropic` SDK dependency — MCP server is now the sole Claude integration point.
- Removed `ask_claude()` synthesis from CLI; results are always shown as formatted retrieval output.
- Removed `--no-claude` and `--raw` CLI flags (replaced by `--format {text,json}`).
- Removed `ANTHROPIC_API_KEY` and `CLAUDE_MODEL` from configuration.
- Removed over-engineered governance docs: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `docs/RELEASE_CHECKLIST.md`, `docs/RELEASE_FILE_MANIFEST.md`, `docs/TEST_ACCEPTANCE_MATRIX.md`, GitHub issue/PR templates, and release workflow.
- Removed `src/converters.py`; `to_builtin_list()` moved to `src/storage.py`.
- Removed backward-compat wrapper methods `search_by_sender()` and `search_by_date()` from `EmailRetriever`; callers now use `search_filtered()` directly.
- `email_search_by_sender` and `email_search_by_date` MCP tools now call `search_filtered()` directly.
- Simplified `_serialize_results` helper in MCP server to direct `retriever.serialize_results()` call.
- Removed trivial wrapper functions `_sanitize_terminal_text()` (CLI) and `_sanitize_untrusted_text()` (MCP server).
- Ingest progress log interval reduced from 500 to 100 emails.
- README updated to reflect Claude-native architecture and current feature set.

### Breaking

- `--no-claude` and `--raw` CLI flags removed. Use `--format {text,json}` instead.
- `EmailRetriever.search_by_sender()` and `search_by_date()` methods removed. Use `search_filtered()`.
- `src.converters` module removed. Import `to_builtin_list` from `src.storage`.

## [0.1.0] - 2026-03-02

### Added

- Initial public release for local Outlook email RAG.
- Interfaces:
  - CLI for search and operational commands.
  - MCP server tools for agent integration.
  - Optional local Streamlit UI.
- Safety and quality gates:
  - Linting, typing, tests, static security scan, dependency audit.

### Security

- Input validation and output sanitization for untrusted email content pathways.
- Safe XML parsing constraints for OLM ingestion.

### Policy

- CLI and MCP interfaces are considered stable for this release series. Breaking changes require an explicit changelog `Breaking` entry and version bump.
- See [API compatibility policy](docs/API_COMPATIBILITY.md).
