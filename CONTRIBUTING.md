# Contributing

Thanks for contributing to `outlook-email-rag`.

## Development Setup

1. Create and activate a virtual environment.
2. Install development dependencies.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Local Quality Gates

Run the same checks expected by CI:

```bash
bash scripts/run_acceptance_matrix.sh ci
```

For day-to-day development, use:

```bash
bash scripts/run_acceptance_matrix.sh
```

## Coding Standards

1. Keep changes scoped and atomic.
2. Prefer explicit validation and defensive error handling for untrusted input.
3. Add or update tests with behavior changes.
4. Keep CLI and MCP interfaces stable per [docs/API_COMPATIBILITY.md](docs/API_COMPATIBILITY.md).

## Pull Request Expectations

1. Open PRs with a clear summary and risk notes.
2. Include test coverage for new behavior and regressions.
3. Confirm local acceptance matrix results in the PR description.
4. If behavior changes are user-facing, update [README.md](README.md) and [CHANGELOG.md](CHANGELOG.md).

## Branching

Use short, descriptive branch names with the `codex/` prefix when possible, for example:

- `codex/fix-cli-date-validation`
- `codex/docs-release-readme`
