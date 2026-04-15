"""Payload-shaping helpers for answer-context evidence output."""

from __future__ import annotations

from typing import Any

from ..mcp_models import EmailAnswerContextInput
from .search_answer_context_budget import _estimated_json_chars
from .search_answer_context_evidence_helpers import _as_dict, _as_list
from .search_answer_context_rendering import _question_requests_exact_wording


def _answer_context_search_kwargs(params: EmailAnswerContextInput, top_k: int) -> dict[str, Any]:
    """Build ``search_filtered`` kwargs for the answer-context tool."""
    exact_wording = _question_requests_exact_wording(params.question)
    auto_case_scope_recall = params.case_scope is not None and not exact_wording
    kwargs: dict[str, Any] = {
        "query": params.question,
        "top_k": top_k,
    }
    if params.sender is not None:
        kwargs["sender"] = params.sender
    if params.subject is not None:
        kwargs["subject"] = params.subject
    if params.folder is not None:
        kwargs["folder"] = params.folder
    if params.has_attachments is not None:
        kwargs["has_attachments"] = params.has_attachments
    if params.email_type is not None:
        kwargs["email_type"] = params.email_type
    if params.date_from is not None:
        kwargs["date_from"] = params.date_from
    elif params.case_scope is not None and params.case_scope.date_from is not None:
        kwargs["date_from"] = params.case_scope.date_from
    if params.date_to is not None:
        kwargs["date_to"] = params.date_to
    elif params.case_scope is not None and params.case_scope.date_to is not None:
        kwargs["date_to"] = params.case_scope.date_to
    if params.rerank:
        kwargs["rerank"] = True
    if params.hybrid or auto_case_scope_recall:
        kwargs["hybrid"] = True
    if auto_case_scope_recall:
        kwargs["expand_query"] = True
    return kwargs


def _compact_retaliation_analysis_payload(retaliation_analysis: dict[str, Any]) -> dict[str, Any]:
    """Return a compact retaliation payload for tight case-evidence budgets."""
    timeline_assessment = (
        (retaliation_analysis.get("retaliation_timeline_assessment") or {}) if isinstance(retaliation_analysis, dict) else {}
    )
    return {
        "version": str(retaliation_analysis.get("version") or ""),
        "trigger_event_count": int(retaliation_analysis.get("trigger_event_count") or 0),
        "trigger_events": [
            {
                "trigger_type": str(event.get("trigger_type") or ""),
                "date": str(event.get("date") or ""),
                "assessment": {
                    "status": str(_as_dict(event.get("assessment")).get("status") or ""),
                    "analysis_quality": str(_as_dict(event.get("assessment")).get("analysis_quality") or ""),
                    "confounder_signals": [
                        str(item) for item in _as_list(_as_dict(event.get("assessment")).get("confounder_signals")) if item
                    ],
                },
            }
            for event in _as_list(retaliation_analysis.get("trigger_events"))[:2]
            if isinstance(event, dict)
        ],
        "retaliation_timeline_assessment": {
            "version": str(_as_dict(timeline_assessment).get("version") or ""),
            "protected_activity_timeline": [
                dict(entry)
                for entry in _as_list(_as_dict(timeline_assessment).get("protected_activity_timeline"))[:1]
                if isinstance(entry, dict)
            ],
            "temporal_correlation_analysis": [
                dict(entry)
                for entry in _as_list(_as_dict(timeline_assessment).get("temporal_correlation_analysis"))[:1]
                if isinstance(entry, dict)
            ],
            "overall_evidentiary_rating": dict(_as_dict(timeline_assessment).get("overall_evidentiary_rating") or {}),
        },
    }


def _retrieval_diagnostics(
    retriever: Any,
    *,
    candidate_count: int,
    attachment_candidate_count: int,
) -> dict[str, Any]:
    """Return visible retrieval diagnostics for answer-context and case-analysis callers."""
    debug = _as_dict(getattr(retriever, "last_search_debug", getattr(retriever, "_last_search_debug", None)))
    profile = _as_dict(debug.get("legal_support_profile"))
    intents = [str(item) for item in _as_list(profile.get("intents")) if str(item).strip()]
    suggested_terms = [str(item) for item in _as_list(profile.get("suggested_terms")) if str(item).strip()][:3]
    diagnostics = {
        "used_query_expansion": bool(debug.get("used_query_expansion")),
        "expand_query_requested": bool(debug.get("expand_query_requested")),
        "use_hybrid": bool(debug.get("use_hybrid")),
        "use_rerank": bool(debug.get("use_rerank")),
        "fetch_size": int(debug.get("fetch_size") or 0),
        "legal_support_profile": {
            "is_legal_support": bool(profile.get("is_legal_support")),
            "intents": intents,
            "suggested_terms": suggested_terms,
        },
        "result_mix": {
            "body_candidates": candidate_count,
            "attachment_candidates": attachment_candidate_count,
            "total_candidates": candidate_count + attachment_candidate_count,
        },
    }
    executed_query = str(debug.get("executed_query") or "").strip()
    original_query = str(debug.get("original_query") or "").strip()
    query_expansion_suffix = str(debug.get("query_expansion_suffix") or "").strip()
    if original_query:
        diagnostics["original_query"] = original_query
    if executed_query:
        diagnostics["executed_query"] = executed_query
    if executed_query and executed_query != original_query:
        diagnostics["query_changed"] = True
    if query_expansion_suffix:
        diagnostics["query_expansion_suffix"] = query_expansion_suffix
    legal_support_profile = _as_dict(diagnostics.get("legal_support_profile"))
    result_mix = _as_dict(diagnostics.get("result_mix"))
    if bool(legal_support_profile.get("is_legal_support")) and int(result_mix.get("total_candidates") or 0) == 0:
        diagnostics["suspected_failure_mode"] = "retrieval_recall_gap"
        diagnostics["review_note"] = "No evidence candidates were retrieved; review retrieval before downstream analysis."
    return diagnostics


def _public_retrieval_diagnostics(
    retrieval_diagnostics: dict[str, Any],
    *,
    compact_search: bool,
) -> dict[str, Any]:
    """Return a budget-safe retrieval diagnostics payload for answer-context output."""
    profile = _as_dict(retrieval_diagnostics.get("legal_support_profile"))
    payload: dict[str, Any] = {
        "used_query_expansion": bool(retrieval_diagnostics.get("used_query_expansion")),
        "use_hybrid": bool(retrieval_diagnostics.get("use_hybrid")),
    }
    original_query = str(retrieval_diagnostics.get("original_query") or "").strip()
    executed_query = str(retrieval_diagnostics.get("executed_query") or "").strip()
    query_expansion_suffix = str(retrieval_diagnostics.get("query_expansion_suffix") or "").strip()
    if compact_search:
        if original_query:
            payload["original_query"] = original_query
        if executed_query:
            payload["executed_query"] = executed_query
        if query_expansion_suffix:
            payload["query_expansion_suffix"] = query_expansion_suffix
        if bool(profile.get("is_legal_support")):
            payload["legal_support_profile"] = {
                "is_legal_support": True,
                "intents": [str(item) for item in _as_list(profile.get("intents")) if str(item).strip()],
            }
        suspected_failure_mode = str(retrieval_diagnostics.get("suspected_failure_mode") or "")
        if suspected_failure_mode:
            payload["suspected_failure_mode"] = suspected_failure_mode
        return payload

    if retrieval_diagnostics.get("expand_query_requested"):
        payload["expand_query_requested"] = True
    if retrieval_diagnostics.get("use_rerank"):
        payload["use_rerank"] = True
    if retrieval_diagnostics.get("fetch_size"):
        payload["fetch_size"] = int(retrieval_diagnostics.get("fetch_size") or 0)
    if retrieval_diagnostics.get("query_changed"):
        payload["query_changed"] = True
    if original_query:
        payload["original_query"] = original_query
    if executed_query:
        payload["executed_query"] = executed_query
    if query_expansion_suffix:
        payload["query_expansion_suffix"] = query_expansion_suffix

    is_legal_support = bool(profile.get("is_legal_support"))
    if is_legal_support:
        payload["legal_support_profile"] = {
            "is_legal_support": True,
            "intents": [str(item) for item in _as_list(profile.get("intents")) if str(item).strip()],
            "suggested_terms": [str(item) for item in _as_list(profile.get("suggested_terms")) if str(item).strip()][:3],
        }
    suspected_failure_mode = str(retrieval_diagnostics.get("suspected_failure_mode") or "")
    if suspected_failure_mode:
        payload["suspected_failure_mode"] = suspected_failure_mode
        payload["review_note"] = str(retrieval_diagnostics.get("review_note") or "")
    return payload


def _compact_optional_case_surfaces(payload: dict[str, Any], *, budget: int) -> int:
    """Drop lowest-priority case-analysis sidecars until the payload fits the budget."""
    removed = 0
    for key in (
        "investigation_report",
        "quote_attribution_metrics",
        "communication_graph",
        "retaliation_analysis",
        "comparative_treatment",
        "case_patterns",
        "behavioral_strength_rubric",
        "evidence_table",
        "finding_evidence_index",
        "multi_source_case_bundle",
    ):
        if _estimated_json_chars(payload) <= budget:
            break
        if key in payload:
            payload.pop(key, None)
            removed += 1
    if removed > 0:
        payload["_case_surface_compaction"] = {
            "status": "omitted_optional_case_surfaces",
            "removed_count": removed,
        }
    return removed
