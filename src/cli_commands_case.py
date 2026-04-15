"""Dedicated case-analysis CLI helpers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .case_analysis import build_case_analysis
from .case_full_pack import execute_case_full_pack
from .case_operator_intake import build_manifest_from_materials_dir, matter_manifest_has_chat_artifacts
from .case_prompt_intake import build_case_prompt_preflight
from .legal_support_exporter import LegalSupportExporter
from .mcp_models import (
    EmailCaseAnalysisInput,
    EmailCaseFullPackInput,
    EmailCasePromptPreflightInput,
    EmailLegalSupportExportInput,
)


class _SyncToolDeps:
    """Minimal ToolDeps-compatible adapter for CLI execution."""

    def __init__(self, retriever: Any, email_db: Any):
        self._retriever = retriever
        self._email_db = email_db

    def get_retriever(self) -> Any:
        return self._retriever

    def get_email_db(self) -> Any:
        return self._email_db

    async def offload(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        if args or kwargs:
            return fn(*args, **kwargs)
        return fn()

    def tool_annotations(self, title: str) -> Any:
        return title

    def write_tool_annotations(self, title: str) -> Any:
        return title

    def idempotent_write_annotations(self, title: str) -> Any:
        return title

    DB_UNAVAILABLE = json.dumps({"error": "SQLite database not available. Run ingestion first."})

    def sanitize(self, text: str) -> str:
        return text


def run_case_analyze_impl(retriever: Any, get_email_db: Callable[[], Any], args: Any) -> None:
    """Run the exploratory/raw-input case-analysis workflow from the CLI."""
    raw_input = Path(args.input).read_text(encoding="utf-8")
    params = EmailCaseAnalysisInput.model_validate_json(raw_input)
    deps = _SyncToolDeps(retriever, get_email_db())
    rendered = asyncio.run(build_case_analysis(deps, params))
    if getattr(args, "output", None):
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered)


def run_case_prompt_preflight_impl(args: Any) -> None:
    """Build a bounded draft case intake from a natural-language matter prompt file."""
    prompt_text = Path(args.input).read_text(encoding="utf-8")
    params = EmailCasePromptPreflightInput.model_validate(
        {
            "prompt_text": prompt_text,
            "output_language": getattr(args, "output_language", "en"),
        }
    )
    rendered = json.dumps(build_case_prompt_preflight(params), ensure_ascii=False, indent=2)
    if getattr(args, "output", None):
        Path(args.output).write_text(rendered, encoding="utf-8")
    else:
        print(rendered)


def _require_email_db(get_email_db: Callable[[], Any]) -> Any:
    db = get_email_db()
    if db is None:
        raise ValueError("SQLite database not available. Run ingestion first.")
    return db


def _load_optional_json(path: str | None, *, default: Any) -> Any:
    if not path:
        return default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def run_case_counsel_pack_impl(retriever: Any, get_email_db: Callable[[], Any], args: Any) -> int:
    """Build a manifest-backed counsel pack from a case scope file plus materials directory."""
    case_scope = json.loads(Path(args.case_scope).read_text(encoding="utf-8"))
    materials_dir = Path(args.materials_dir).expanduser()
    if not materials_dir.exists() or not materials_dir.is_dir():
        raise ValueError(f"materials_dir must be an existing directory: {materials_dir}")
    manifest = build_manifest_from_materials_dir(args.materials_dir)
    source_scope = "mixed_case_file" if matter_manifest_has_chat_artifacts(manifest) else "emails_and_attachments"
    params = EmailLegalSupportExportInput.model_validate(
        {
            "case_scope": case_scope,
            "source_scope": source_scope,
            "review_mode": "exhaustive_matter_review",
            "matter_manifest": manifest,
            "delivery_target": args.delivery_target,
            "delivery_format": args.delivery_format,
            "output_path": args.output,
            "privacy_mode": args.privacy_mode,
            "output_language": args.output_language,
            "translation_mode": args.translation_mode,
        }
    )
    deps = _SyncToolDeps(retriever, get_email_db())
    payload = asyncio.run(build_case_analysis(deps, params))
    rendered_payload = json.loads(payload)
    exporter = LegalSupportExporter()
    target = str(params.delivery_target or "").strip()
    counsel_export_status = getattr(exporter, "counsel_export_status", None)
    if target in {"counsel_handoff", "counsel_handoff_bundle"} and callable(counsel_export_status):
        export_status = counsel_export_status(payload=rendered_payload)
        if not bool(export_status.get("ready")):
            blockers = [str(item) for item in export_status.get("blockers", []) if item]
            result = {
                "workflow": "case_counsel_pack",
                "status": "blocked",
                "delivery_target": params.delivery_target,
                "delivery_format": params.delivery_format,
                "output_path": params.output_path,
                "analysis_query": str(rendered_payload.get("analysis_query") or ""),
                "blockers": [
                    {
                        "field": blocker,
                        "severity": "blocking",
                        "reason": "Counsel-facing export remains blocked until the recorded readiness issue is resolved.",
                    }
                    for blocker in blockers
                ],
                "export_metadata": export_status.get("export_metadata"),
            }
            print(json.dumps(result, ensure_ascii=False))
            return 0 if bool(getattr(args, "allow_blocked_exit_zero", False)) else 1
    result = exporter.export_file(
        payload=rendered_payload,
        output_path=params.output_path,
        delivery_target=params.delivery_target,
        delivery_format=params.delivery_format,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


def run_case_review_status_impl(get_email_db: Callable[[], Any], args: Any) -> None:
    """Inspect current review-governance state for one persisted matter workspace."""
    db = _require_email_db(get_email_db)
    latest_snapshot = db.latest_matter_snapshot(workspace_id=args.workspace_id)
    payload = {
        "workflow": "case_review_status",
        "workspace_id": args.workspace_id,
        "latest_snapshot": latest_snapshot,
        "snapshots": db.list_matter_snapshots(workspace_id=args.workspace_id),
        "review_status": db.matter_review_status_summary(workspace_id=args.workspace_id),
        "overrides": db.list_matter_review_overrides(workspace_id=args.workspace_id),
    }
    print(json.dumps(payload, ensure_ascii=False))


def run_case_review_override_impl(get_email_db: Callable[[], Any], args: Any) -> None:
    """Persist one bounded human review override for a matter workspace item."""
    db = _require_email_db(get_email_db)
    override_payload = _load_optional_json(args.override_json, default={})
    machine_payload = _load_optional_json(args.machine_json, default={})
    source_evidence = _load_optional_json(args.source_evidence_json, default=[])
    result = db.upsert_matter_review_override(
        workspace_id=args.workspace_id,
        target_type=args.target_type,
        target_id=args.target_id,
        review_state=args.review_state,
        override_payload=override_payload if isinstance(override_payload, dict) else {},
        machine_payload=machine_payload if isinstance(machine_payload, dict) else {},
        source_evidence=source_evidence if isinstance(source_evidence, list) else [],
        reviewer=args.reviewer,
        review_notes=args.review_notes,
        apply_on_refresh=not bool(getattr(args, "no_apply_on_refresh", False)),
    )
    print(json.dumps(result, ensure_ascii=False))


def run_case_review_snapshot_impl(get_email_db: Callable[[], Any], args: Any) -> None:
    """Promote or otherwise update one persisted matter snapshot review state."""
    db = _require_email_db(get_email_db)
    result = db.set_matter_snapshot_review_state(
        snapshot_id=args.snapshot_id,
        review_state=args.review_state,
        reviewer=args.reviewer,
    )
    print(json.dumps(result, ensure_ascii=False))


def run_case_full_pack_impl(retriever: Any, get_email_db: Callable[[], Any], args: Any) -> int:
    """Compile and, when possible, execute the full-pack workflow from prompt plus materials."""
    prompt_text = Path(args.prompt).read_text(encoding="utf-8")
    intake_overrides: dict[str, object] = {}
    if getattr(args, "overrides", None):
        loaded = json.loads(Path(args.overrides).read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            intake_overrides = loaded
        else:
            raise ValueError("overrides must be a JSON object when provided.")
    params = EmailCaseFullPackInput.model_validate(
        {
            "prompt_text": prompt_text,
            "materials_dir": args.materials_dir,
            "output_language": args.output_language,
            "translation_mode": args.translation_mode,
            "default_source_scope": args.default_source_scope,
            "assume_date_to_today": args.assume_date_to_today,
            "compile_only": getattr(args, "compile_only", False),
            "privacy_mode": args.privacy_mode,
            "delivery_target": args.delivery_target,
            "delivery_format": args.delivery_format,
            "output_path": getattr(args, "output", None),
            "intake_overrides": intake_overrides,
        }
    )
    deps = _SyncToolDeps(retriever, get_email_db())
    payload = asyncio.run(execute_case_full_pack(deps, params))
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if str(payload.get("status") or "") == "blocked" and not bool(getattr(args, "allow_blocked_exit_zero", False)):
        return 1
    return 0
