## Summary

Describe what changed and why.

## Validation

- [ ] `ruff check .`
- [ ] `mypy src`
- [ ] `pytest -q`
- [ ] `bandit -r src -q`
- [ ] `python -m pip_audit -r requirements.txt`

## Compatibility and Risk

- [ ] CLI compatibility reviewed (`docs/API_COMPATIBILITY.md`)
- [ ] MCP compatibility reviewed (`docs/API_COMPATIBILITY.md`)
- [ ] Breaking change? If yes, documented in `CHANGELOG.md`

## Documentation

- [ ] README updated (if behavior changed)
- [ ] CHANGELOG updated
- [ ] New/updated flags or tools documented
