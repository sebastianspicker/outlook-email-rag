# Release Checklist

Use this checklist before publishing a new GitHub release.

## 1. Workspace Hygiene

1. Run cleanup dry-run:

```bash
bash scripts/clean_workspace.sh --dry-run
```

2. Run cleanup:

```bash
bash scripts/clean_workspace.sh
```

3. Optional full cleanup including virtual environment:

```bash
bash scripts/clean_workspace.sh --include-venv
```

4. Confirm internal-only diagnostics docs are not present in release content.

## 2. Quality and Security Gates

Run all required checks:

```bash
ruff check .
mypy src
pytest -q
bandit -r src -q
python -m pip_audit -r requirements.txt
```

Or run the CI profile wrapper:

```bash
bash scripts/run_acceptance_matrix.sh ci
```

## 3. Documentation Checks

1. Verify README contains:
   1. `How It Works` section.
   2. `Data Lifecycle` section.
   3. Mermaid diagrams that render correctly in GitHub.
2. Verify links resolve:
   1. `docs/API_COMPATIBILITY.md`
   2. `CHANGELOG.md`
   3. `SECURITY.md`
   4. `CONTRIBUTING.md`

## 4. Versioning and Notes

1. Update [CHANGELOG.md](../CHANGELOG.md) with final release notes and date.
2. Ensure breaking changes (if any) are clearly marked.

## 5. Tag and Publish

1. Create annotated tag:

```bash
git tag -a v0.1.0 -m "Release v0.1.0"
```

2. Push branch and tag:

```bash
git push origin <branch-name>
git push origin v0.1.0
```

3. Verify release workflow completed successfully.
