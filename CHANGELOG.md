# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to semantic versioning principles for public interfaces.

## [Unreleased]

### Added

- Release governance documents: `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`.
- Release process documents: `docs/RELEASE_CHECKLIST.md`, `docs/API_COMPATIBILITY.md`.
- GitHub contribution templates and release workflow.
- Workspace cleanup utility: `scripts/clean_workspace.sh`.

### Changed

- README now includes explicit "How It Works" and "Data Lifecycle" diagrams.
- Public interface stability policy is now documented and linked.

### Breaking

- None.

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
