"""Dedicated workplace case-analysis wrapper over the answer-context pipeline."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .case_analysis_common import as_dict
from .case_analysis_coverage import matter_coverage_ledger
from .case_analysis_review import annotate_reviewable_items, apply_review_overrides, review_governance_payload
from .case_analysis_scope import derive_case_analysis_query
from .case_analysis_transform import transform_case_analysis_payload
from .case_operator_intake import ingest_chat_exports
from .comparative_treatment import augment_comparative_treatment_with_sources
from .matter_file_ingestion import enrich_matter_manifest
from .matter_ingestion import build_matter_ingestion_report
from .mcp_models import EmailAnswerContextInput, EmailCaseAnalysisInput
from .multi_source_case_bundle import append_chat_log_sources, append_manifest_sources
from .trigger_retaliation import augment_retaliation_analysis_with_sources

if TYPE_CHECKING:
    from .tools.utils import ToolDepsProto


async def build_case_analysis_payload(deps: ToolDepsProto, params: EmailCaseAnalysisInput) -> dict[str, Any]:
    """Build the dedicated case-analysis payload as a Python object."""
    from .tools.search_answer_context import build_answer_context_payload

    answer_params = EmailAnswerContextInput(
        question=derive_case_analysis_query(params),
        max_results=params.max_results,
        evidence_mode=params.evidence_mode,
        case_scope=params.case_scope,
    )
    answer_payload = await build_answer_context_payload(deps, answer_params)
    normalized_chat_log_entries = [entry.model_dump(mode="json") for entry in params.chat_log_entries]
    if params.chat_exports:
        chat_export_ingestion_report = ingest_chat_exports([entry.model_dump(mode="json") for entry in params.chat_exports])
        answer_payload["chat_export_ingestion_report"] = chat_export_ingestion_report
        normalized_chat_log_entries.extend(
            [entry for entry in chat_export_ingestion_report.get("entries", []) if isinstance(entry, dict)]
        )
    if normalized_chat_log_entries:
        answer_payload["multi_source_case_bundle"] = append_chat_log_sources(
            answer_payload.get("multi_source_case_bundle"),
            chat_log_entries=normalized_chat_log_entries,
        )
    if params.matter_manifest is not None:
        manifest_payload = enrich_matter_manifest(params.matter_manifest.model_dump(mode="json"))
        answer_payload["multi_source_case_bundle"] = append_manifest_sources(
            answer_payload.get("multi_source_case_bundle"),
            matter_manifest=manifest_payload,
        )
        answer_payload["matter_ingestion_report"] = build_matter_ingestion_report(
            review_mode=params.review_mode,
            matter_manifest=manifest_payload,
            multi_source_case_bundle=answer_payload.get("multi_source_case_bundle"),
        )
    else:
        answer_payload["matter_ingestion_report"] = build_matter_ingestion_report(
            review_mode=params.review_mode,
            matter_manifest=None,
            multi_source_case_bundle=answer_payload.get("multi_source_case_bundle"),
        )
    if normalized_chat_log_entries or params.matter_manifest is not None:
        answer_payload["retaliation_analysis"] = augment_retaliation_analysis_with_sources(
            answer_payload.get("retaliation_analysis"),
            case_scope=params.case_scope,
            multi_source_case_bundle=answer_payload.get("multi_source_case_bundle"),
        )
        answer_payload["comparative_treatment"] = augment_comparative_treatment_with_sources(
            answer_payload.get("comparative_treatment"),
            case_bundle=answer_payload.get("case_bundle"),
            multi_source_case_bundle=answer_payload.get("multi_source_case_bundle"),
        )
    transformed = transform_case_analysis_payload(answer_payload, params)
    transformed = annotate_reviewable_items(transformed)

    workspace_id = str(as_dict(as_dict(transformed.get("matter_workspace"))).get("workspace_id") or "")
    overrides: list[dict[str, Any]] = []
    get_email_db = getattr(deps, "get_email_db", None)
    email_db = get_email_db() if callable(get_email_db) else None
    if workspace_id and email_db is not None and hasattr(email_db, "list_matter_review_overrides"):
        overrides = email_db.list_matter_review_overrides(
            workspace_id=workspace_id,
            apply_on_refresh_only=True,
        )
        transformed = apply_review_overrides(transformed, overrides)
    transformed["review_governance"] = review_governance_payload(
        workspace_id=workspace_id,
        overrides=overrides,
    )
    matter_persistence: dict[str, Any] | None = None
    if email_db is not None and hasattr(email_db, "persist_matter_snapshot"):
        matter_persistence = email_db.persist_matter_snapshot(
            payload=transformed,
            review_mode=params.review_mode,
            source_scope=params.source_scope,
        )
        transformed["matter_persistence"] = matter_persistence
    report = transformed.get("investigation_report")
    if isinstance(report, dict):
        report["review_governance"] = dict(transformed["review_governance"])
        if matter_persistence is not None:
            report["matter_persistence"] = dict(matter_persistence)
    return transformed


async def build_case_analysis(deps: ToolDepsProto, params: EmailCaseAnalysisInput) -> str:
    """Build the dedicated case-analysis payload."""
    transformed = await build_case_analysis_payload(deps, params)
    return json.dumps(transformed, indent=2)


_matter_coverage_ledger = matter_coverage_ledger
