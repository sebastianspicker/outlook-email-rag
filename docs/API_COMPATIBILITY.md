# API Compatibility Policy

This document defines the public interface compatibility contract for `outlook-email-rag`.

## Scope

Stable public interfaces for the `0.1.x` release series:

1. CLI arguments and behavior in `python -m src.cli`.
2. MCP tool names and parameter schemas exposed by `python -m src.mcp_server`.

## CLI Compatibility Contract

The following CLI capabilities are stable for `0.1.x`:

1. Query path:
   1. `--query` / `-q`
   2. `--top-k`
   3. `--sender`
   4. `--subject`
   5. `--folder`
   6. `--date-from`
   7. `--date-to`
   8. `--min-score`
   9. `--json` and `--format {text,json}`
   10. `--raw`
   11. `--no-claude`
2. Operational path:
   1. `--stats`
   2. `--list-senders N`
   3. `--reset-index --yes`
3. Validation semantics:
   1. `--date-from` and `--date-to` are ISO `YYYY-MM-DD`.
   2. `--date-from` must be less than or equal to `--date-to`.
   3. `--min-score` is bounded to `[0.0, 1.0]`.
   4. `--top-k` is a positive integer and bounded.

## MCP Compatibility Contract

The following tool names are stable for `0.1.x`:

1. `email_search`
2. `email_search_by_sender`
3. `email_search_by_date`
4. `email_list_senders`
5. `email_stats`
6. `email_search_structured`

Stable MCP input schema summary:

1. `email_search`
   1. `query: str` (required)
   2. `top_k: int` (optional, bounded)
2. `email_search_by_sender`
   1. `query: str` (required)
   2. `sender: str` (required)
   3. `top_k: int` (optional, bounded)
3. `email_search_by_date`
   1. `query: str` (required)
   2. `date_from: str | null` (optional, ISO date)
   3. `date_to: str | null` (optional, ISO date)
   4. `top_k: int` (optional, bounded)
4. `email_list_senders`
   1. `limit: int` (optional, bounded)
5. `email_stats`
   1. no parameters
6. `email_search_structured`
   1. `query: str` (required)
   2. `date_from: str | null` (optional, ISO date)
   3. `date_to: str | null` (optional, ISO date)
   4. `top_k: int` (optional, bounded)
   5. `sender: str | null` (optional)
   6. `subject: str | null` (optional)
   7. `folder: str | null` (optional)
   8. `min_score: float | null` (optional, bounded)

## Breaking Change Rule

A breaking change is any modification that invalidates existing automation or user workflows relying on the stable interfaces above, including:

1. Removing or renaming stable CLI flags.
2. Changing expected semantics of stable flags.
3. Removing or renaming MCP tools.
4. Altering MCP parameter names/types/requiredness in an incompatible way.

Breaking changes require:

1. A version bump.
2. A `Breaking` entry in [CHANGELOG.md](../CHANGELOG.md).
3. Updated migration guidance in release notes.
