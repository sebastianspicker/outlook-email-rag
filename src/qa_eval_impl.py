"""Helpers for evaluating answer-context quality against labeled questions."""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from . import qa_eval_live as _live
from . import qa_eval_taxonomy as _taxonomy
from .mcp_models import EmailAnswerContextInput
from .qa_eval_cases import QuestionCase, _load_json, load_question_cases
from .qa_eval_scoring import evaluate_payload, summarize_evaluation
from .tools.search_answer_context import build_answer_context
from .tools.utils import ToolDepsProto

LiveEvalDeps = _live.LiveEvalDeps
_SQLiteEvalRetriever = _live._SQLiteEvalRetriever
_query_terms = _live._query_terms
_resolve_live_retriever = _live._resolve_live_retriever
default_live_report_path = _live.default_live_report_path
default_remediation_report_path = _live.default_remediation_report_path
repo_root = _live.repo_root
build_failure_taxonomy = _taxonomy.build_failure_taxonomy
build_investigation_corpus_readiness = _taxonomy.build_investigation_corpus_readiness
build_remediation_summary = _taxonomy.build_remediation_summary


def _display_path(path: Path | None) -> str | None:
    """Serialize repo-owned paths without leaking local absolute filesystem roots."""
    if path is None:
        return None
    try:
        return str(path.relative_to(repo_root()))
    except ValueError:
        return str(path)


def resolve_live_deps(*, preferred_backend: str = "auto") -> ToolDepsProto:
    return _live.resolve_live_deps(
        preferred_backend=preferred_backend,
        resolve_retriever=_resolve_live_retriever,
    )


def _results_payload_map(path: Path) -> dict[str, dict[str, Any]]:
    raw = _load_json(path)
    if isinstance(raw, dict) and "results" in raw and isinstance(raw["results"], list):
        mapped: dict[str, dict[str, Any]] = {}
        for item in raw["results"]:
            case_id = str(item["id"])
            payload = item.get("payload")
            if not isinstance(payload, dict):
                raise ValueError(f"results payload for case {case_id} must be an object")
            mapped[case_id] = payload
        return mapped
    if isinstance(raw, dict):
        return {str(case_id): payload for case_id, payload in raw.items()}
    raise ValueError("results file must be an object keyed by case id or a {'results': [...]} object")


def load_eval_report(path: Path) -> dict[str, Any]:
    """Load a saved eval report JSON document."""
    return _load_json(path)


async def _live_payload(case: QuestionCase, deps: ToolDepsProto) -> dict[str, Any]:
    params = EmailAnswerContextInput(
        question=case.question,
        evidence_mode=case.evidence_mode,  # type: ignore[arg-type]
        case_scope=case.case_scope,
        **case.filters,
    )
    raw = await build_answer_context(deps, params)
    return json.loads(raw)


def _serialize_case(case: QuestionCase) -> dict[str, Any]:
    payload = asdict(case)
    if case.case_scope is not None:
        payload["case_scope"] = case.case_scope.model_dump(mode="json")
    return payload


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
