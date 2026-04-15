from __future__ import annotations

import pytest

from src.case_analysis import build_case_analysis_payload
from src.email_db import EmailDatabase
from src.mcp_models import EmailCaseAnalysisInput


@pytest.mark.asyncio
async def test_build_case_analysis_payload_enriches_file_backed_manifest_and_persists_snapshot(monkeypatch, tmp_path) -> None:
    note_path = tmp_path / "meeting-note.txt"
    note_path.write_text("Meeting note about SBV participation and follow-up.", encoding="utf-8")

    params = EmailCaseAnalysisInput.model_validate(
        {
            "case_scope": {
                "target_person": {"name": "Max Mustermann", "email": "max@example.org"},
                "suspected_actors": [{"name": "Erika Beispiel", "email": "erika@example.org"}],
                "allegation_focus": ["retaliation"],
                "analysis_goal": "lawyer_briefing",
                "date_from": "2025-01-01",
                "date_to": "2025-06-30",
            },
            "source_scope": "mixed_case_file",
            "review_mode": "exhaustive_matter_review",
            "chat_log_entries": [
                {
                    "source_id": "chat-1",
                    "platform": "Teams",
                    "date": "2025-03-12",
                    "text": "Please keep SBV informed.",
                }
            ],
            "matter_manifest": {
                "manifest_id": "matter-1",
                "artifacts": [
                    {
                        "source_id": "manifest:note:1",
                        "source_class": "meeting_note",
                        "source_path": str(note_path),
                        "date": "2025-03-11",
                    }
                ],
            },
        }
    )

    async def fake_build_answer_context_payload(_deps, _answer_params):
        return {
            "case_bundle": {
                "bundle_id": "case-123",
                "scope": {
                    "case_label": "Case 123",
                    "target_person": {"name": "Max Mustermann", "email": "max@example.org"},
                    "suspected_actors": [{"name": "Erika Beispiel", "email": "erika@example.org"}],
                    "allegation_focus": ["retaliation"],
                    "analysis_goal": "lawyer_briefing",
                    "date_from": "2025-01-01",
                    "date_to": "2025-06-30",
                },
            },
            "actor_identity_graph": {
                "actors": [
                    {
                        "actor_id": "actor-manager",
                        "primary_email": "erika@example.org",
                        "display_names": ["Erika Beispiel"],
                    }
                ]
            },
            "multi_source_case_bundle": {
                "summary": {"source_type_counts": {"email": 1}, "missing_source_types": []},
                "sources": [
                    {
                        "source_id": "email:uid-1",
                        "source_type": "email",
                        "uid": "uid-1",
                        "actor_id": "actor-manager",
                        "title": "Status",
                        "date": "2025-03-15T10:00:00",
                        "snippet": "We will send the summary.",
                        "source_weighting": {"text_available": True, "can_corroborate_or_contradict": True},
                        "source_reliability": {"level": "high", "basis": "authored_email_body"},
                        "provenance": {"evidence_handle": "email:uid-1"},
                        "chronology_anchor": {"date": "2025-03-15T10:00:00"},
                    }
                ],
                "source_links": [],
                "source_type_profiles": [],
                "chronology_anchors": [],
            },
            "finding_evidence_index": {"findings": []},
            "evidence_table": {"row_count": 0},
            "candidates": [
                {
                    "uid": "uid-1",
                    "date": "2025-03-15T10:00:00",
                    "sender_name": "Erika Beispiel",
                    "sender_email": "erika@example.org",
                    "subject": "Status",
                    "snippet": "We will send the summary.",
                    "language_rhetoric": {"authored_text": {"signal_count": 0, "signals": []}},
                    "message_findings": {"authored_text": {"behavior_candidates": [], "counter_indicators": []}},
                }
            ],
            "timeline": {
                "events": [
                    {
                        "date": "2025-03-15",
                        "summary": "Status note",
                        "uid": "uid-1",
                    }
                ]
            },
            "investigation_report": {
                "summary": {"section_count": 1, "supported_section_count": 1, "insufficient_section_count": 0},
                "sections": {
                    "missing_information": {
                        "section_id": "missing_information",
                        "title": "Missing Information / Further Evidence Needed",
                        "status": "supported",
                        "entries": [],
                    }
                },
            },
        }

    monkeypatch.setattr("src.tools.search_answer_context.build_answer_context_payload", fake_build_answer_context_payload)

    db = EmailDatabase(":memory:")

    class MockDeps:
        @staticmethod
        def get_email_db():
            return db

    payload = await build_case_analysis_payload(MockDeps(), params)

    assert payload["matter_ingestion_report"]["summary"]["total_supplied_artifacts"] == 1
    assert payload["matter_ingestion_report"]["artifacts"][0]["extraction_state"] == "text_extracted"
    assert payload["matter_coverage_ledger"]["summary"]["coverage_status"] == "complete"
    assert payload["matter_coverage_ledger"]["summary"]["stage_counts"]["supplied"] >= 3
    assert payload["matter_persistence"]["workspace_id"].startswith("workspace:")
    manifest_row = next(row for row in payload["matter_coverage_ledger"]["rows"] if row["source_id"] == "manifest:note:1")
    assert manifest_row["stage_flags"]["supplied"] is True
    assert manifest_row["stage_flags"]["extracted"] is True
    assert "issue_ids" in manifest_row["lineage"]

    snapshots = db.list_matter_snapshots(workspace_id=payload["matter_persistence"]["workspace_id"])
    assert len(snapshots) == 1
    stored = db.get_matter_snapshot(snapshot_id=payload["matter_persistence"]["snapshot_id"])
    assert stored is not None
    assert stored["matter_coverage_ledger"]["summary"]["total_source_count"] >= 3
    db.close()
