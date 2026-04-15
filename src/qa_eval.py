"""Public QA eval entrypoint with stable helper imports and runner surface."""

from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from typing import Any

from . import qa_eval_impl as _impl
from .qa_eval_impl import (  # noqa: F401
    LiveEvalDeps,
    QuestionCase,
    _display_path,
    _live_payload,
    _query_terms,
    _resolve_live_retriever,
    _results_payload_map,
    _serialize_case,
    _SQLiteEvalRetriever,
    build_failure_taxonomy,
    build_investigation_corpus_readiness,
    build_remediation_summary,
    default_live_report_path,
    default_remediation_report_path,
    evaluate_payload,
    load_eval_report,
    load_question_cases,
    repo_root,
    summarize_evaluation,
)
from .tools.utils import ToolDepsProto


def resolve_live_deps(*, preferred_backend: str = "auto") -> ToolDepsProto:
    """Resolve live eval deps while honoring wrapper-level monkeypatch seams."""
    _impl._resolve_live_retriever = _resolve_live_retriever
    return _impl.resolve_live_deps(preferred_backend=preferred_backend)


async def run_evaluation(
    *,
    questions_path: Path,
    results_path: Path | None = None,
    live_deps: ToolDepsProto | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run a minimal answer-context evaluation from captured or live payloads."""
    cases = load_question_cases(questions_path)
    if limit is not None:
        cases = cases[:limit]

    captured = _results_payload_map(results_path) if results_path else {}
    results: list[dict[str, Any]] = []

    for case in cases:
        if case.id in captured:
            payload = captured[case.id]
            source = "captured"
        elif live_deps is not None:
            payload = await _live_payload(case, live_deps)
            source = "live"
        else:
            raise ValueError(f"no payload available for case {case.id}; provide --results or --live")
        results.append(evaluate_payload(case, payload, source=source))

    return {
        "questions_path": _display_path(questions_path),
        "results_path": _display_path(results_path),
        "total_cases": len(cases),
        "cases": [_serialize_case(case) for case in cases],
        "results": results,
        "summary": summarize_evaluation(results),
        "failure_taxonomy": build_failure_taxonomy(cases, results),
        "source_counts": dict(sorted(Counter(result["source"] for result in results).items())),
        "live_backend": getattr(live_deps, "live_backend", None) if live_deps is not None else None,
        "investigation_corpus_readiness": build_investigation_corpus_readiness(
            cases=cases,
            results=results,
            live_deps=live_deps,
        ),
    }


def run_evaluation_sync(
    *,
    questions_path: Path,
    results_path: Path | None = None,
    live_deps: ToolDepsProto | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Synchronous wrapper for CLI use."""
    return asyncio.run(
        run_evaluation(
            questions_path=questions_path,
            results_path=results_path,
            live_deps=live_deps,
            limit=limit,
        )
    )
