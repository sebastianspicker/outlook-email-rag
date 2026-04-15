"""Stable projection helpers for realistic legal-support acceptance outputs."""

from __future__ import annotations

from typing import Any


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def build_golden_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a stable cross-product golden subset for acceptance drift detection."""
    full_case_analysis = payload.get("full_case_analysis") if isinstance(payload, dict) else {}
    if not isinstance(full_case_analysis, dict):
        full_case_analysis = {}
    issue_rows = [
        {
            "issue_id": row.get("issue_id"),
            "title": row.get("title"),
            "legal_relevance_status": row.get("legal_relevance_status"),
            "missing_proof": sorted(str(item) for item in _as_list(row.get("missing_proof")) if str(item).strip()),
        }
        for row in _as_list((full_case_analysis.get("lawyer_issue_matrix") or {}).get("rows"))
        if isinstance(row, dict)
    ]
    return {
        "workflow": str(payload.get("workflow") or ""),
        "status": str(payload.get("status") or ""),
        "analysis_query": str(full_case_analysis.get("analysis_query") or ""),
        "matter_ingestion_summary": (full_case_analysis.get("matter_ingestion_report") or {}).get("summary", {}),
        "matter_ingestion_status": (full_case_analysis.get("matter_ingestion_report") or {}).get("completeness_status", ""),
        "coverage_status": ((full_case_analysis.get("matter_coverage_ledger") or {}).get("summary") or {}).get(
            "coverage_status", ""
        ),
        "analysis_limit_notes": list(_as_list((full_case_analysis.get("analysis_limits") or {}).get("notes"))),
        "evidence_rows": [
            {
                "exhibit_id": row.get("exhibit_id"),
                "document_type": row.get("document_type"),
                "main_issue_tags": row.get("main_issue_tags"),
                "source_id": row.get("source_id"),
                "strength": ((row.get("exhibit_reliability") or {}).get("strength")),
            }
            for row in _as_list((full_case_analysis.get("matter_evidence_index") or {}).get("rows"))[:8]
            if isinstance(row, dict)
        ],
        "provenance_examples": [
            {
                "source_id": row.get("source_id"),
                "source_conflict_status": row.get("source_conflict_status"),
                "source_language": row.get("source_language"),
            }
            for row in _as_list((full_case_analysis.get("matter_evidence_index") or {}).get("rows"))[:5]
            if isinstance(row, dict)
        ],
        "chronology_entries": [
            {
                "chronology_id": row.get("chronology_id"),
                "date": row.get("date"),
                "title": row.get("title"),
                "issue_category": row.get("issue_category"),
                "source_id": row.get("source_id"),
            }
            for row in _as_list((full_case_analysis.get("master_chronology") or {}).get("entries"))[:10]
            if isinstance(row, dict)
        ],
        "comparator_points": [
            {
                "comparator_point_id": row.get("comparator_point_id"),
                "issue_id": row.get("issue_id"),
                "comparison_strength": row.get("comparison_strength"),
                "comparison_quality": row.get("comparison_quality"),
            }
            for row in _as_list((full_case_analysis.get("comparative_treatment") or {}).get("comparator_points"))
            if isinstance(row, dict)
        ],
        "issue_rows": issue_rows,
        "skeptical_weaknesses": [
            {
                "weakness_id": row.get("weakness_id"),
                "title": row.get("title"),
                "severity": row.get("severity"),
            }
            for row in _as_list((full_case_analysis.get("skeptical_employer_review") or {}).get("weaknesses"))[:8]
            if isinstance(row, dict)
        ],
        "memo_sections": {
            "executive_summary": [
                row.get("text")
                for row in _as_list(
                    ((full_case_analysis.get("lawyer_briefing_memo") or {}).get("sections") or {}).get("executive_summary")
                )[:5]
                if isinstance(row, dict)
            ],
            "strongest_evidence": [
                row.get("text")
                for row in _as_list(
                    ((full_case_analysis.get("lawyer_briefing_memo") or {}).get("sections") or {}).get("strongest_evidence")
                )[:5]
                if isinstance(row, dict)
            ],
        },
        "draft_preflight": (full_case_analysis.get("controlled_factual_drafting") or {}).get("framing_preflight", {}),
        "dashboard_cards": {
            key: [
                {
                    "entry_id": row.get("entry_id"),
                    "title": row.get("title"),
                    "summary": row.get("summary"),
                    "evidence_hint": row.get("evidence_hint"),
                }
                for row in value[:6]
                if isinstance(row, dict)
            ]
            for key, value in ((full_case_analysis.get("case_dashboard") or {}).get("cards") or {}).items()
            if isinstance(value, list)
        },
        "retaliation_points": [
            {
                "retaliation_point_id": row.get("retaliation_point_id"),
                "support_strength": row.get("support_strength"),
                "analysis_quality": row.get("analysis_quality"),
                "counterargument": row.get("counterargument"),
            }
            for row in _as_list((full_case_analysis.get("retaliation_analysis") or {}).get("retaliation_points"))
            if isinstance(row, dict)
        ],
        "cross_output_checks": [
            {
                "check_id": row.get("check_id"),
                "status": row.get("status"),
            }
            for row in _as_list((full_case_analysis.get("cross_output_consistency") or {}).get("checks"))
            if isinstance(row, dict)
        ],
    }
