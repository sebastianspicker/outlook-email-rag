from __future__ import annotations

import json

import pytest

from src.cli import parse_args
from src.cli_commands_case import (
    run_case_analyze_impl,
    run_case_counsel_pack_impl,
    run_case_full_pack_impl,
    run_case_prompt_preflight_impl,
    run_case_review_override_impl,
    run_case_review_snapshot_impl,
    run_case_review_status_impl,
)
from src.email_db import EmailDatabase
from src.legal_support_acceptance_fixtures import acceptance_case_ids


def test_case_analyze_parse_args() -> None:
    args = parse_args(["case", "analyze", "--input", "case.json", "--output", "report.json"])
    assert args.subcommand == "case"
    assert args.case_action == "analyze"
    assert args.input == "case.json"
    assert args.output == "report.json"
    assert args.format == "json"


def test_case_counsel_pack_parse_args() -> None:
    args = parse_args(
        [
            "case",
            "counsel-pack",
            "--case-scope",
            "scope.json",
            "--materials-dir",
            "matter",
            "--output",
            "handoff.zip",
        ]
    )
    assert args.subcommand == "case"
    assert args.case_action == "counsel-pack"
    assert args.case_scope == "scope.json"
    assert args.materials_dir == "matter"
    assert args.output == "handoff.zip"
    assert args.delivery_target == "counsel_handoff_bundle"
    assert args.delivery_format == "bundle"
    assert args.allow_blocked_exit_zero is False


def test_case_prompt_preflight_parse_args() -> None:
    args = parse_args(["case", "prompt-preflight", "--input", "matter.md", "--output-language", "de"])
    assert args.subcommand == "case"
    assert args.case_action == "prompt-preflight"
    assert args.input == "matter.md"
    assert args.output_language == "de"


def test_case_review_status_parse_args() -> None:
    args = parse_args(["case", "review-status", "--workspace-id", "workspace:123"])
    assert args.subcommand == "case"
    assert args.case_action == "review-status"
    assert args.workspace_id == "workspace:123"


def test_case_review_override_parse_args() -> None:
    args = parse_args(
        [
            "case",
            "review-override",
            "--workspace-id",
            "workspace:123",
            "--target-type",
            "chronology_entry",
            "--target-id",
            "CHR-001",
            "--review-state",
            "human_verified",
        ]
    )
    assert args.case_action == "review-override"
    assert args.target_type == "chronology_entry"
    assert args.target_id == "CHR-001"
    assert args.review_state == "human_verified"
    assert args.no_apply_on_refresh is False


def test_case_review_snapshot_parse_args() -> None:
    args = parse_args(["case", "review-snapshot", "--snapshot-id", "snapshot:123", "--review-state", "human_verified"])
    assert args.case_action == "review-snapshot"
    assert args.snapshot_id == "snapshot:123"
    assert args.review_state == "human_verified"


def test_case_full_pack_parse_args() -> None:
    args = parse_args(["case", "full-pack", "--prompt", "matter.md", "--materials-dir", "matter", "--output", "draft.json"])
    assert args.subcommand == "case"
    assert args.case_action == "full-pack"
    assert args.prompt == "matter.md"
    assert args.materials_dir == "matter"
    assert args.output == "draft.json"
    assert args.default_source_scope == "emails_and_attachments"
    assert args.assume_date_to_today is True
    assert args.delivery_target == "counsel_handoff_bundle"
    assert args.delivery_format == "bundle"
    assert args.compile_only is False
    assert args.allow_blocked_exit_zero is False


def test_run_case_analyze_impl_writes_output(tmp_path, monkeypatch) -> None:
    case_path = tmp_path / "case.json"
    output_path = tmp_path / "report.json"
    case_path.write_text(
        json.dumps(
            {
                "case_scope": {
                    "target_person": {"name": "Max Mustermann"},
                    "allegation_focus": ["exclusion"],
                    "analysis_goal": "internal_review",
                    "date_from": "2025-01-01",
                    "date_to": "2025-02-01",
                },
                "source_scope": "emails_only",
            }
        ),
        encoding="utf-8",
    )

    async def fake_build_case_analysis(_deps, params):
        assert params.source_scope == "emails_only"
        return json.dumps({"workflow": "case_analysis"})

    monkeypatch.setattr("src.cli_commands_case.build_case_analysis", fake_build_case_analysis)

    class Args:
        input = str(case_path)
        output = str(output_path)
        format = "json"

    run_case_analyze_impl(retriever=object(), get_email_db=lambda: None, args=Args())
    assert json.loads(output_path.read_text(encoding="utf-8"))["workflow"] == "case_analysis"


def test_run_case_prompt_preflight_impl_writes_output(tmp_path) -> None:
    prompt_path = tmp_path / "matter.md"
    output_path = tmp_path / "preflight.json"
    prompt_path.write_text(
        ("Claimant: Max Mustermann. Review retaliation from November 2023 to present based on the email corpus."),
        encoding="utf-8",
    )

    class Args:
        input = str(prompt_path)
        output = str(output_path)
        output_language = "en"

    run_case_prompt_preflight_impl(Args())
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["workflow"] == "case_prompt_preflight"
    assert payload["draft_case_scope"]["target_person"]["name"] == "Max Mustermann"
    missing_fields = {item["field"] for item in payload["missing_required_inputs"]}
    assert "case_scope.trigger_events" in missing_fields


def test_run_case_review_status_impl_prints_snapshot_and_override_state(capsys) -> None:
    db = EmailDatabase(":memory:")
    persist_result = db.persist_matter_snapshot(
        payload={
            "matter_workspace": {
                "workspace_id": "workspace:123",
                "matter": {"matter_id": "matter:123", "bundle_id": "bundle:123", "case_label": "Case A"},
            },
            "review_governance": {
                "review_state_counts": {
                    "machine_extracted": 1,
                    "human_verified": 0,
                    "disputed": 0,
                    "draft_only": 0,
                    "export_approved": 0,
                }
            },
            "matter_coverage_ledger": {"summary": {"coverage_status": "complete"}},
        },
        review_mode="exhaustive_matter_review",
        source_scope="mixed_case_file",
    )
    assert persist_result is not None
    db.upsert_matter_review_override(
        workspace_id="workspace:123",
        target_type="chronology_entry",
        target_id="CHR-001",
        review_state="human_verified",
        override_payload={"summary": "Human-reviewed chronology text."},
        source_evidence=[{"source_id": "email:uid-1"}],
    )

    args = type("Args", (), {"workspace_id": "workspace:123"})()
    run_case_review_status_impl(lambda: db, args)
    data = json.loads(capsys.readouterr().out)
    assert data["workflow"] == "case_review_status"
    assert data["latest_snapshot"]["workspace_id"] == "workspace:123"
    assert data["review_status"]["override_count"] == 1
    assert data["overrides"][0]["target_id"] == "CHR-001"
    db.close()


def test_run_case_review_override_impl_persists_override(tmp_path, capsys) -> None:
    db = EmailDatabase(":memory:")
    override_path = tmp_path / "override.json"
    source_evidence_path = tmp_path / "source-evidence.json"
    override_path.write_text(json.dumps({"summary": "Human-reviewed chronology text."}), encoding="utf-8")
    source_evidence_path.write_text(json.dumps([{"source_id": "email:uid-1"}]), encoding="utf-8")

    args = type(
        "Args",
        (),
        {
            "workspace_id": "workspace:123",
            "target_type": "chronology_entry",
            "target_id": "CHR-001",
            "review_state": "human_verified",
            "override_json": str(override_path),
            "machine_json": None,
            "source_evidence_json": str(source_evidence_path),
            "reviewer": "reviewer@example.org",
            "review_notes": "Confirmed by human review.",
            "no_apply_on_refresh": False,
        },
    )()

    run_case_review_override_impl(lambda: db, args)
    data = json.loads(capsys.readouterr().out)
    assert data["workspace_id"] == "workspace:123"
    assert data["target_type"] == "chronology_entry"
    assert data["review_state"] == "human_verified"
    overrides = db.list_matter_review_overrides(workspace_id="workspace:123")
    assert overrides[0]["override_payload"]["summary"] == "Human-reviewed chronology text."
    db.close()


def test_run_case_review_snapshot_impl_updates_snapshot_review_state(capsys) -> None:
    db = EmailDatabase(":memory:")
    persist_result = db.persist_matter_snapshot(
        payload={
            "matter_workspace": {
                "workspace_id": "workspace:123",
                "matter": {"matter_id": "matter:123", "bundle_id": "bundle:123", "case_label": "Case A"},
            },
            "review_governance": {
                "review_state_counts": {
                    "machine_extracted": 1,
                    "human_verified": 0,
                    "disputed": 0,
                    "draft_only": 0,
                    "export_approved": 0,
                }
            },
            "matter_coverage_ledger": {"summary": {"coverage_status": "complete"}},
        },
        review_mode="exhaustive_matter_review",
        source_scope="mixed_case_file",
    )
    assert persist_result is not None

    args = type(
        "Args",
        (),
        {
            "snapshot_id": persist_result["snapshot_id"],
            "review_state": "human_verified",
            "reviewer": "reviewer@example.org",
        },
    )()

    run_case_review_snapshot_impl(lambda: db, args)
    data = json.loads(capsys.readouterr().out)
    assert data["snapshot_id"] == persist_result["snapshot_id"]
    assert data["review_state"] == "human_verified"
    latest = db.latest_matter_snapshot(workspace_id="workspace:123")
    assert latest is not None
    assert latest["review_state"] == "human_verified"
    db.close()


def test_run_case_full_pack_impl_writes_blocked_output(tmp_path, capsys) -> None:
    prompt_path = tmp_path / "matter.md"
    materials_dir = tmp_path / "materials"
    materials_dir.mkdir()
    (materials_dir / "record.txt").write_text("Meeting note.", encoding="utf-8")
    prompt_path.write_text(
        "Claimant: Max Mustermann. Review retaliation from November 2023 to present based on the email corpus.",
        encoding="utf-8",
    )

    args = type(
        "Args",
        (),
        {
            "prompt": str(prompt_path),
            "materials_dir": str(materials_dir),
            "overrides": None,
            "output": None,
            "output_language": "en",
            "translation_mode": "translation_aware",
            "default_source_scope": "emails_and_attachments",
            "assume_date_to_today": True,
            "privacy_mode": "external_counsel_export",
            "delivery_target": "counsel_handoff_bundle",
            "delivery_format": "bundle",
            "compile_only": True,
            "allow_blocked_exit_zero": False,
        },
    )()

    exit_code = run_case_full_pack_impl(retriever=object(), get_email_db=lambda: None, args=args)
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert payload["workflow"] == "case_full_pack"
    assert payload["status"] == "blocked"
    blocker_fields = {item["field"] for item in payload["blockers"]}
    assert "case_scope.trigger_events" in blocker_fields
    assert payload["intake_compilation"]["override_suggestions"]["repair_mode"] == "explicit_override_required"


def test_run_case_full_pack_impl_applies_override_file(tmp_path, monkeypatch, capsys) -> None:
    prompt_path = tmp_path / "matter.md"
    materials_dir = tmp_path / "materials"
    overrides_path = tmp_path / "overrides.json"
    output_path = tmp_path / "handoff.bundle"
    materials_dir.mkdir()
    (materials_dir / "record.txt").write_text("Meeting note.", encoding="utf-8")
    prompt_path.write_text(
        "Claimant: Max Mustermann. Review retaliation from 2025-01-01 to 2025-06-30 based on the email corpus.",
        encoding="utf-8",
    )
    overrides_path.write_text(
        json.dumps(
            {
                "case_scope": {
                    "trigger_events": [{"trigger_type": "complaint", "date": "2025-03-01"}],
                    "alleged_adverse_actions": [{"action_type": "task_withdrawal", "date": "2025-03-05"}],
                    "target_person": {"name": "Max Mustermann", "email": "max@example.org"},
                }
            }
        ),
        encoding="utf-8",
    )

    args = type(
        "Args",
        (),
        {
            "prompt": str(prompt_path),
            "materials_dir": str(materials_dir),
            "overrides": str(overrides_path),
            "output": str(output_path),
            "output_language": "de",
            "translation_mode": "translation_aware",
            "default_source_scope": "emails_and_attachments",
            "assume_date_to_today": True,
            "privacy_mode": "external_counsel_export",
            "delivery_target": "counsel_handoff_bundle",
            "delivery_format": "bundle",
            "compile_only": False,
            "allow_blocked_exit_zero": False,
        },
    )()

    async def fake_execute_case_full_pack(_deps, params):
        assert params.output_path == str(output_path)
        assert params.intake_overrides["case_scope"]["trigger_events"][0]["trigger_type"] == "complaint"
        return {
            "workflow": "case_full_pack",
            "status": "completed",
            "export_result": {"output_path": str(output_path), "delivery_target": "counsel_handoff_bundle"},
        }

    monkeypatch.setattr("src.cli_commands_case.execute_case_full_pack", fake_execute_case_full_pack)

    exit_code = run_case_full_pack_impl(retriever=object(), get_email_db=lambda: None, args=args)
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["export_result"]["delivery_target"] == "counsel_handoff_bundle"


@pytest.mark.parametrize("case_id", acceptance_case_ids())
def test_run_case_full_pack_impl_realistic_fixture_cases(case_id, monkeypatch, tmp_path, capsys) -> None:
    from src.legal_support_acceptance_fixtures import build_fixture_answer_context, build_fixture_full_pack_input

    async def fake_build_answer_context_payload(_deps, _params):
        return build_fixture_answer_context(case_id)

    monkeypatch.setattr("src.tools.search_answer_context.build_answer_context_payload", fake_build_answer_context_payload)
    output_path = tmp_path / f"{case_id}.zip"
    params = build_fixture_full_pack_input(case_id, output_path=str(output_path))

    args = type(
        "Args",
        (),
        {
            "prompt": None,
            "materials_dir": params.materials_dir,
            "overrides": None,
            "output": str(output_path),
            "output_language": params.output_language,
            "translation_mode": params.translation_mode,
            "default_source_scope": params.default_source_scope,
            "assume_date_to_today": params.assume_date_to_today,
            "privacy_mode": params.privacy_mode,
            "delivery_target": params.delivery_target,
            "delivery_format": params.delivery_format,
            "compile_only": False,
            "allow_blocked_exit_zero": False,
        },
    )()

    prompt_path = tmp_path / f"{case_id}.md"
    prompt_path.write_text(params.prompt_text, encoding="utf-8")
    args.prompt = str(prompt_path)

    overrides_path = tmp_path / f"{case_id}.overrides.json"
    overrides_path.write_text(json.dumps(params.intake_overrides), encoding="utf-8")
    args.overrides = str(overrides_path)

    exit_code = run_case_full_pack_impl(retriever=object(), get_email_db=lambda: None, args=args)
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "completed"
    assert payload["export_result"]["output_path"] == str(output_path)
    assert output_path.exists()


def test_run_case_full_pack_impl_allows_legacy_zero_exit_for_blocked_payload(tmp_path, capsys) -> None:
    prompt_path = tmp_path / "matter.md"
    materials_dir = tmp_path / "materials"
    materials_dir.mkdir()
    (materials_dir / "record.txt").write_text("Meeting note.", encoding="utf-8")
    prompt_path.write_text(
        "Claimant: Max Mustermann. Review retaliation from November 2023 to present based on the email corpus.",
        encoding="utf-8",
    )

    args = type(
        "Args",
        (),
        {
            "prompt": str(prompt_path),
            "materials_dir": str(materials_dir),
            "overrides": None,
            "output": None,
            "output_language": "en",
            "translation_mode": "translation_aware",
            "default_source_scope": "emails_and_attachments",
            "assume_date_to_today": True,
            "privacy_mode": "external_counsel_export",
            "delivery_target": "counsel_handoff_bundle",
            "delivery_format": "bundle",
            "compile_only": True,
            "allow_blocked_exit_zero": True,
        },
    )()

    exit_code = run_case_full_pack_impl(retriever=object(), get_email_db=lambda: None, args=args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["status"] == "blocked"


def test_run_case_analyze_impl_accepts_stronger_mixed_source_case_intake(tmp_path, monkeypatch) -> None:
    case_path = tmp_path / "case-rich.json"
    output_path = tmp_path / "report-rich.json"
    case_path.write_text(
        json.dumps(
            {
                "case_scope": {
                    "target_person": {"name": "Max Mustermann", "email": "max@example.org"},
                    "suspected_actors": [{"name": "Erika Beispiel", "email": "erika@example.org"}],
                    "comparator_actors": [{"name": "Pat Vergleich", "email": "pat@example.org"}],
                    "allegation_focus": ["retaliation", "unequal_treatment"],
                    "analysis_goal": "lawyer_briefing",
                    "context_notes": "Projektkonflikt nach formeller Beschwerde.",
                    "trigger_events": [{"trigger_type": "complaint", "date": "2025-03-01"}],
                    "org_context": {
                        "reporting_lines": [
                            {
                                "manager": {"name": "Erika Beispiel", "email": "erika@example.org"},
                                "report": {"name": "Max Mustermann", "email": "max@example.org"},
                                "source": "supplied_fact",
                            }
                        ]
                    },
                    "date_from": "2025-01-01",
                    "date_to": "2025-06-30",
                },
                "source_scope": "mixed_case_file",
                "chat_log_entries": [
                    {
                        "platform": "Teams",
                        "participants": ["max@example.org", "erika@example.org"],
                        "date": "2025-03-02T09:00:00Z",
                        "text": "Bitte nicht erneut direkt an HR schreiben.",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def fake_build_case_analysis(_deps, params):
        assert params.source_scope == "mixed_case_file"
        assert len(params.case_scope.trigger_events) == 1
        assert len(params.case_scope.comparator_actors) == 1
        assert params.case_scope.org_context is not None
        assert len(params.chat_log_entries) == 1
        return json.dumps({"workflow": "case_analysis", "source_scope": params.source_scope})

    monkeypatch.setattr("src.cli_commands_case.build_case_analysis", fake_build_case_analysis)

    class Args:
        input = str(case_path)
        output = str(output_path)
        format = "json"

    run_case_analyze_impl(retriever=object(), get_email_db=lambda: None, args=Args())
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "workflow": "case_analysis",
        "source_scope": "mixed_case_file",
    }


def test_run_case_counsel_pack_impl_builds_manifest_backed_export(tmp_path, monkeypatch, capsys) -> None:
    scope_path = tmp_path / "scope.json"
    materials_dir = tmp_path / "materials"
    materials_dir.mkdir()
    (materials_dir / "teams-chat.html").write_text("<html><body><p>Teams export</p></body></html>", encoding="utf-8")
    output_path = tmp_path / "handoff.zip"
    scope_path.write_text(
        json.dumps(
            {
                "target_person": {"name": "Max Mustermann"},
                "allegation_focus": ["retaliation"],
                "analysis_goal": "lawyer_briefing",
                "date_from": "2025-01-01",
                "date_to": "2025-06-30",
            }
        ),
        encoding="utf-8",
    )

    async def fake_build_case_analysis(_deps, params):
        assert params.review_mode == "exhaustive_matter_review"
        assert params.source_scope == "mixed_case_file"
        assert params.matter_manifest is not None
        return json.dumps({"workflow": "case_analysis", "review_mode": params.review_mode})

    class _FakeExporter:
        def export_file(self, *, payload, output_path, delivery_target, delivery_format):
            assert payload["workflow"] == "case_analysis"
            assert delivery_target == "counsel_handoff_bundle"
            assert delivery_format == "bundle"
            return {"output_path": output_path, "delivery_target": delivery_target, "delivery_format": delivery_format}

    monkeypatch.setattr("src.cli_commands_case.build_case_analysis", fake_build_case_analysis)
    monkeypatch.setattr("src.cli_commands_case.LegalSupportExporter", lambda: _FakeExporter())

    args = type(
        "Args",
        (),
        {
            "case_scope": str(scope_path),
            "materials_dir": str(materials_dir),
            "output": str(output_path),
            "delivery_target": "counsel_handoff_bundle",
            "delivery_format": "bundle",
            "privacy_mode": "external_counsel_export",
            "output_language": "en",
            "translation_mode": "translation_aware",
        },
    )()

    run_case_counsel_pack_impl(retriever=object(), get_email_db=lambda: None, args=args)
    data = json.loads(capsys.readouterr().out)
    assert data["delivery_target"] == "counsel_handoff_bundle"
    assert data["delivery_format"] == "bundle"


def test_run_case_counsel_pack_impl_returns_blocked_payload_for_readiness_gate(tmp_path, monkeypatch, capsys) -> None:
    scope_path = tmp_path / "scope.json"
    materials_dir = tmp_path / "materials"
    materials_dir.mkdir()
    (materials_dir / "record.txt").write_text("Meeting note.", encoding="utf-8")
    output_path = tmp_path / "handoff.zip"
    scope_path.write_text(
        json.dumps(
            {
                "target_person": {"name": "Max Mustermann"},
                "allegation_focus": ["retaliation"],
                "analysis_goal": "lawyer_briefing",
                "date_from": "2025-01-01",
                "date_to": "2025-06-30",
            }
        ),
        encoding="utf-8",
    )

    async def fake_build_case_analysis(_deps, params):
        assert params.review_mode == "exhaustive_matter_review"
        return json.dumps(
            {
                "workflow": "case_analysis",
                "analysis_query": "retaliation review",
                "matter_persistence": {
                    "snapshot_id": "snapshot:test",
                    "workspace_id": "workspace:test",
                    "matter_id": "matter:test",
                    "review_state": "machine_extracted",
                },
                "matter_ingestion_report": {"completeness_status": "complete"},
                "matter_coverage_ledger": {"summary": {"coverage_status": "complete"}},
            }
        )

    monkeypatch.setattr("src.cli_commands_case.build_case_analysis", fake_build_case_analysis)

    args = type(
        "Args",
        (),
        {
            "case_scope": str(scope_path),
            "materials_dir": str(materials_dir),
            "output": str(output_path),
            "delivery_target": "counsel_handoff_bundle",
            "delivery_format": "bundle",
            "privacy_mode": "external_counsel_export",
            "output_language": "en",
            "translation_mode": "translation_aware",
        },
    )()

    exit_code = run_case_counsel_pack_impl(retriever=object(), get_email_db=lambda: None, args=args)
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 1
    assert data["workflow"] == "case_counsel_pack"
    assert data["status"] == "blocked"
    assert data["delivery_target"] == "counsel_handoff_bundle"
    assert data["blockers"] == [
        {
            "field": "snapshot_review_state:machine_extracted",
            "severity": "blocking",
            "reason": "Counsel-facing export remains blocked until the recorded readiness issue is resolved.",
        }
    ]
    readiness = data["export_metadata"]["counsel_export_readiness"]
    assert readiness["ready"] is False
    assert readiness["blockers"] == ["snapshot_review_state:machine_extracted"]


def test_run_case_counsel_pack_impl_allows_legacy_zero_exit_for_blocked_payload(tmp_path, monkeypatch, capsys) -> None:
    scope_path = tmp_path / "scope.json"
    materials_dir = tmp_path / "materials"
    materials_dir.mkdir()
    (materials_dir / "record.txt").write_text("Meeting note.", encoding="utf-8")
    output_path = tmp_path / "handoff.zip"
    scope_path.write_text(
        json.dumps(
            {
                "target_person": {"name": "Max Mustermann"},
                "allegation_focus": ["retaliation"],
                "analysis_goal": "lawyer_briefing",
                "date_from": "2025-01-01",
                "date_to": "2025-06-30",
            }
        ),
        encoding="utf-8",
    )

    async def fake_build_case_analysis(_deps, _params):
        return json.dumps(
            {
                "matter_persistence": {"review_state": "machine_extracted"},
                "matter_ingestion_report": {"completeness_status": "complete"},
                "matter_coverage_ledger": {"summary": {"coverage_status": "complete"}},
            }
        )

    monkeypatch.setattr("src.cli_commands_case.build_case_analysis", fake_build_case_analysis)

    args = type(
        "Args",
        (),
        {
            "case_scope": str(scope_path),
            "materials_dir": str(materials_dir),
            "output": str(output_path),
            "delivery_target": "counsel_handoff_bundle",
            "delivery_format": "bundle",
            "privacy_mode": "external_counsel_export",
            "output_language": "en",
            "translation_mode": "translation_aware",
            "allow_blocked_exit_zero": True,
        },
    )()

    exit_code = run_case_counsel_pack_impl(retriever=object(), get_email_db=lambda: None, args=args)
    data = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert data["status"] == "blocked"
