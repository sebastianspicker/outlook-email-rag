"""Scope and classification helpers for case-analysis payloads."""

from __future__ import annotations

from typing import Any

from .case_analysis_common import as_dict, warning
from .case_intake import build_case_intake_guidance
from .case_operator_intake import matter_manifest_has_chat_artifacts
from .mcp_models import EmailCaseAnalysisInput

_PROMPT_CRITICAL_SURFACES: tuple[tuple[str, str], ...] = (
    ("case_patterns", "corpus_behavioral_review"),
    ("finding_evidence_index", "finding_evidence_index"),
    ("investigation_report", "investigation_report"),
)


def _has_surface_payload(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        return True
    if isinstance(value, list):
        return bool(value)
    return True


def _surface_omissions(
    *,
    answer_payload: dict[str, Any],
    final_payload: dict[str, Any] | None = None,
) -> list[str]:
    payload = final_payload if isinstance(final_payload, dict) else answer_payload
    omitted: list[str] = []
    for surface_id, _label in _PROMPT_CRITICAL_SURFACES:
        if not _has_surface_payload(payload.get(surface_id)):
            omitted.append(surface_id)
    return omitted


def derive_case_analysis_query(params: EmailCaseAnalysisInput) -> str:
    """Return a conservative retrieval query for one case-analysis run."""
    if params.analysis_query:
        return params.analysis_query.strip()

    case_scope = params.case_scope
    target = case_scope.target_person
    focus = ", ".join(case_scope.allegation_focus)
    target_bits = [target.name.strip()]
    if target.email:
        target_bits.append(target.email.strip())

    actor_bits = [
        " ".join(part for part in [actor.name.strip(), (actor.email or "").strip()] if part).strip()
        for actor in case_scope.suspected_actors[:3]
    ]
    comparator_bits = [
        " ".join(part for part in [actor.name.strip(), (actor.email or "").strip()] if part).strip()
        for actor in case_scope.comparator_actors[:3]
    ]
    context = " ".join((case_scope.context_notes or "").split())
    if len(context) > 180:
        context = context[:177].rstrip() + "..."

    query_parts = [
        "workplace case analysis",
        f"target {' '.join(bit for bit in target_bits if bit)}",
        f"focus {focus}",
    ]
    if actor_bits:
        query_parts.append("suspected actors " + "; ".join(bit for bit in actor_bits if bit))
    if comparator_bits:
        query_parts.append("comparators " + "; ".join(bit for bit in comparator_bits if bit))
    if case_scope.trigger_events:
        trigger_types = [
            str(event.trigger_type).replace("_", " ")
            for event in case_scope.trigger_events[:3]
            if getattr(event, "trigger_type", None)
        ]
        if trigger_types:
            query_parts.append("trigger events " + ", ".join(trigger_types))
    if case_scope.employment_issue_tracks:
        query_parts.append("issue tracks " + ", ".join(case_scope.employment_issue_tracks[:4]))
    if case_scope.employment_issue_tags:
        query_parts.append("issue tags " + ", ".join(case_scope.employment_issue_tags[:6]))
    if context:
        query_parts.append(context)
    return ". ".join(part for part in query_parts if part)


def case_scope_quality(params: EmailCaseAnalysisInput) -> dict[str, Any]:
    """Return machine-readable scope quality and downgrade markers."""
    case_scope = params.case_scope
    required_fields_present = [
        "target_person",
        "allegation_focus",
        "analysis_goal",
        "date_from",
        "date_to",
    ]
    missing_required_fields: list[str] = []
    guidance = build_case_intake_guidance(case_scope)
    recommended_presence = {
        field: field not in set(guidance.get("missing_recommended_fields", []))
        for field in ("suspected_actors", "comparator_actors", "trigger_events", "org_context", "context_notes")
    }
    missing_recommended_fields = [field for field, present in recommended_presence.items() if not present]
    warnings = [dict(item) for item in guidance.get("warnings", []) if isinstance(item, dict)]
    if (
        params.source_scope == "mixed_case_file"
        and not params.chat_log_entries
        and not params.chat_exports
        and not matter_manifest_has_chat_artifacts(
            params.matter_manifest.model_dump(mode="json") if params.matter_manifest is not None else None
        )
    ):
        warnings.append(
            warning(
                code="mixed_case_file_declared_without_native_chat_support",
                severity="info",
                message=(
                    "Mixed case files need either structured chat rows, native chat exports, or manifest-backed chat artifacts."
                ),
                affects=["multi_source_case_bundle", "analysis_limits"],
            )
        )

    status = "complete"
    if missing_required_fields:
        status = "insufficient"
    elif warnings or missing_recommended_fields:
        status = "degraded"

    return {
        "status": status,
        "required_fields_present": required_fields_present,
        "missing_required_fields": missing_required_fields,
        "recommended_fields_present": list(guidance.get("recommended_fields_present", [])),
        "missing_recommended_fields": missing_recommended_fields,
        "downgrade_reasons": [str(item["code"]) for item in warnings],
        "warnings": warnings,
        "recommended_next_inputs": [dict(item) for item in guidance.get("recommended_next_inputs", []) if isinstance(item, dict)],
        "supports_retaliation_analysis": bool(guidance.get("supports_retaliation_analysis")),
        "supports_comparator_analysis": bool(guidance.get("supports_comparator_analysis")),
        "supports_power_analysis": bool(guidance.get("supports_power_analysis")),
        "review_mode": params.review_mode,
        "has_matter_manifest": params.matter_manifest is not None,
    }


def inject_scope_warnings_into_report(
    report: dict[str, Any] | None,
    case_scope_quality_payload: dict[str, Any],
) -> dict[str, Any] | None:
    """Mirror structured scope warnings into the visible missing-information section."""
    if not isinstance(report, dict):
        return report
    warnings = [item for item in case_scope_quality_payload.get("warnings", []) if isinstance(item, dict)]
    if not warnings:
        return report

    report_copy = dict(report)
    sections = dict(report_copy.get("sections") or {})
    missing_information = dict(sections.get("missing_information") or {})
    entries = list(missing_information.get("entries") or [])
    existing_ids = {str(entry.get("entry_id") or "") for entry in entries if isinstance(entry, dict)}
    for item in warnings:
        entry_id = f"scope_warning:{item['code']}"
        if entry_id in existing_ids:
            continue
        entries.append(
            {
                "entry_id": entry_id,
                "statement": str(item.get("message") or ""),
                "supporting_finding_ids": [],
                "supporting_citation_ids": [],
                "supporting_uids": [],
                "warning_code": str(item.get("code") or ""),
                "warning_severity": str(item.get("severity") or ""),
                "affects": [str(affect) for affect in item.get("affects", []) if affect],
            }
        )
    missing_information["entries"] = entries
    missing_information["status"] = "supported" if entries else missing_information.get("status", "insufficient_evidence")
    missing_information["insufficiency_reason"] = "" if entries else missing_information.get("insufficiency_reason", "")
    sections["missing_information"] = missing_information
    report_copy["sections"] = sections
    summary = dict(report_copy.get("summary") or {})
    if summary:
        summary["supported_section_count"] = sum(
            1 for section in sections.values() if isinstance(section, dict) and section.get("status") == "supported"
        )
        summary["insufficient_section_count"] = (
            int(summary.get("section_count") or len(sections)) - summary["supported_section_count"]
        )
        report_copy["summary"] = summary
    return report_copy


def analysis_limits(
    params: EmailCaseAnalysisInput,
    payload: dict[str, Any],
    case_scope_quality_payload: dict[str, Any],
    final_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return explicit analysis-limit disclosures."""
    multi_source = payload.get("multi_source_case_bundle")
    missing_source_types: list[str] = []
    if isinstance(multi_source, dict):
        summary = multi_source.get("summary")
        if isinstance(summary, dict):
            missing_source_types = [str(item) for item in summary.get("missing_source_types", []) if item]

    notes: list[str] = []
    if (
        params.source_scope == "mixed_case_file"
        and not params.chat_log_entries
        and not params.chat_exports
        and not matter_manifest_has_chat_artifacts(
            params.matter_manifest.model_dump(mode="json") if params.matter_manifest is not None else None
        )
    ):
        notes.append("mixed_case_file_declared_but_native_chat_log_support_is_not_yet_implemented")
    if "chat_log" in missing_source_types and not (
        params.chat_log_entries
        or params.chat_exports
        or matter_manifest_has_chat_artifacts(
            params.matter_manifest.model_dump(mode="json") if params.matter_manifest is not None else None
        )
    ):
        notes.append("chat_log_source_type_not_available_in_current_case_bundle")
    if params.review_mode == "retrieval_only":
        notes.append("review_mode_is_retrieval_only")
    elif payload.get("matter_ingestion_report") is None:
        notes.append("exhaustive_review_requested_without_matter_ingestion_report")
    packing = as_dict(payload.get("_packed"))
    case_surface_compaction = as_dict(payload.get("_case_surface_compaction"))
    omitted_surfaces = _surface_omissions(answer_payload=payload, final_payload=final_payload)
    if bool(packing.get("applied")):
        notes.append("payload_packing_applied")
    if int(case_surface_compaction.get("removed_count") or 0) > 0:
        notes.append("case_surface_compaction_removed_surfaces")
    if omitted_surfaces:
        notes.append("prompt_critical_surfaces_omitted")

    return {
        "source_scope": params.source_scope,
        "review_mode": params.review_mode,
        "missing_source_types": missing_source_types,
        "downgrade_reasons": list(case_scope_quality_payload.get("downgrade_reasons", [])),
        "scope_warnings": [dict(item) for item in case_scope_quality_payload.get("warnings", []) if isinstance(item, dict)],
        "matter_manifest_supplied": params.matter_manifest is not None,
        "completeness_status": str(as_dict(payload.get("matter_ingestion_report")).get("completeness_status") or ""),
        "packing": {
            "applied": bool(packing.get("applied")),
            "budget_chars": int(packing.get("budget_chars") or 0),
            "estimated_chars_before": int(packing.get("estimated_chars_before") or 0),
            "estimated_chars_after": int(packing.get("estimated_chars_after") or 0),
            "truncated": dict(packing.get("truncated") or {}),
            "deduplicated": dict(packing.get("deduplicated") or {}),
        },
        "case_surface_compaction": {
            "removed_count": int(case_surface_compaction.get("removed_count") or 0),
            "removed": [str(item) for item in case_surface_compaction.get("removed", []) if str(item).strip()],
        },
        "omitted_case_analysis_surfaces": omitted_surfaces,
        "prompt_complete_behavioral_review": not omitted_surfaces,
        "notes": notes,
    }


def review_classification(
    params: EmailCaseAnalysisInput,
    payload: dict[str, Any],
    *,
    final_payload: dict[str, Any] | None = None,
    analysis_limits_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a machine-readable classification for review truthfulness."""
    completeness_status = str(as_dict(payload.get("matter_ingestion_report")).get("completeness_status") or "")
    manifest_supplied = params.matter_manifest is not None
    is_exhaustive_review = params.review_mode == "exhaustive_matter_review"
    may_present_as_full_review = is_exhaustive_review and manifest_supplied and completeness_status == "complete"
    omission_summary = analysis_limits_payload or {}
    omitted_surfaces = [
        str(item) for item in omission_summary.get("omitted_case_analysis_surfaces", []) if str(item).strip()
    ]

    if may_present_as_full_review and omitted_surfaces:
        classification = "compacted_exhaustive_review_with_omitted_critical_surfaces"
        reason = (
            "Manifest-backed exhaustive review completed with complete supplied-artifact accounting, "
            "but packed compaction omitted prompt-critical analytical surfaces: "
            + ", ".join(omitted_surfaces)
            + "."
        )
        may_present_as_full_review = False
    elif may_present_as_full_review:
        classification = "counsel_grade_exhaustive_review"
        reason = "Manifest-backed exhaustive review completed with complete supplied-artifact accounting."
    elif is_exhaustive_review and manifest_supplied:
        classification = "manifest_backed_but_not_yet_complete"
        reason = "Exhaustive review was requested with a supplied matter manifest, but completeness accounting is not complete."
    elif is_exhaustive_review:
        classification = "exhaustive_requested_without_manifest"
        reason = "Exhaustive review was requested, but no matter manifest was supplied."
    else:
        classification = "retrieval_bounded_exploratory_review"
        reason = "The current run is retrieval-bounded and must not be presented as a full matter-file review."

    return {
        "review_mode": params.review_mode,
        "classification": classification,
        "is_exhaustive_review": is_exhaustive_review,
        "matter_manifest_supplied": manifest_supplied,
        "completeness_status": completeness_status,
        "may_be_presented_as_full_matter_review": may_present_as_full_review,
        "counsel_use_status": (
            "counsel_grade_exhaustive_review" if may_present_as_full_review else "bounded_or_incomplete_review_only"
        ),
        "reason": reason,
    }
