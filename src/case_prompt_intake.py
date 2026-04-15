"""Bounded prompt-to-intake preflight for legal-support case workflows."""

from __future__ import annotations

from typing import Any

from .case_prompt_intake_helpers import (
    CASE_PROMPT_PREFLIGHT_VERSION,
    _analysis_goal,
    _candidate_structures,
    _compact,
    _extract_dates,
    _issue_hints,
    _missing_inputs,
    _named_people,
    _source_scope,
    matter_text,
)


def build_case_prompt_preflight(params: Any) -> dict[str, Any]:
    """Return a bounded prompt-to-intake preflight payload."""
    prompt_text = _compact(getattr(params, "prompt_text", ""))
    factual_text = matter_text(prompt_text)
    allegation_focus, issue_tracks = _issue_hints(factual_text)
    dates = _extract_dates(
        factual_text,
        today=str(getattr(params, "today", "")),
        assume_date_to_today=bool(getattr(params, "assume_date_to_today", True)),
    )
    people = _named_people(factual_text)
    target_rows = people["target_person"]
    suspected_rows = people["suspected_actors"]
    comparator_rows = people["comparator_actors"]
    candidate_structures = _candidate_structures(factual_text, comparator_rows)
    missing_required_inputs = _missing_inputs(
        target_rows=target_rows,
        dates=dates,
        allegation_focus=allegation_focus,
        issue_tracks=issue_tracks,
        comparators=comparator_rows,
        prompt_text=factual_text,
    )
    recommended_source_scope = _source_scope(prompt_text, str(getattr(params, "default_source_scope", "emails_and_attachments")))
    draft_case_scope: dict[str, Any] = {
        "target_person": target_rows[0] if target_rows else None,
        "suspected_actors": suspected_rows,
        "comparator_actors": comparator_rows,
        "date_from": dates.get("date_from"),
        "date_to": dates.get("date_to"),
        "allegation_focus": allegation_focus,
        "analysis_goal": _analysis_goal(prompt_text),
        "context_notes": factual_text[:4000],
        "employment_issue_tracks": issue_tracks,
    }
    recommended_next_inputs = [
        {
            "field": item["field"],
            "recommendation": item["reason"],
        }
        for item in missing_required_inputs
    ]
    if "retaliation" in allegation_focus:
        recommended_next_inputs.append(
            {
                "field": "case_scope.trigger_events",
                "recommendation": (
                    "Add dated trigger events and dated post-trigger actions before relying on retaliation framing."
                ),
            }
        )
    if {"unequal_treatment", "discrimination"} & set(allegation_focus):
        recommended_next_inputs.append(
            {
                "field": "case_scope.comparator_equivalence_notes",
                "recommendation": "Explain why the proposed comparators are meaningfully comparable.",
            }
        )
    recommended_next_inputs = list(
        {
            (item["field"], item["recommendation"]): item
            for item in recommended_next_inputs
            if _compact(item.get("field")) and _compact(item.get("recommendation"))
        }.values()
    )
    prompt_limits = [
        "This preflight does not prove facts; it only drafts intake fields from the supplied prompt.",
        "Dedicated legal-support products still require exhaustive manifest-backed review.",
    ]
    if missing_required_inputs:
        prompt_limits.append("The current prompt is not yet complete enough for a full structured case run.")

    return {
        "version": CASE_PROMPT_PREFLIGHT_VERSION,
        "workflow": "case_prompt_preflight",
        "output_language": str(getattr(params, "output_language", "en")),
        "analysis_goal": draft_case_scope["analysis_goal"],
        "recommended_source_scope": recommended_source_scope,
        "draft_case_scope": draft_case_scope,
        "draft_case_analysis_input": {
            "case_scope": draft_case_scope,
            "source_scope": recommended_source_scope,
            "review_mode": "retrieval_only",
        },
        "candidate_structures": candidate_structures,
        "extraction_summary": {
            "named_target_candidates": target_rows,
            "named_suspected_actor_candidates": suspected_rows,
            "named_comparator_candidates": comparator_rows,
            "issue_hints": allegation_focus,
            "issue_track_hints": issue_tracks,
            "date_candidates": dates["explicit_dates"],
            "used_today_for_open_ended_range": dates["used_today_for_open_ended_range"],
            "candidate_counts": dict(candidate_structures["summary"]),
        },
        "missing_required_inputs": missing_required_inputs,
        "recommended_next_inputs": recommended_next_inputs,
        "ready_for_case_analysis": not missing_required_inputs,
        "supports_exhaustive_legal_support": False,
        "prompt_limits": prompt_limits,
    }
