from __future__ import annotations

import pytest

from src.case_analysis import (
    derive_case_analysis_query,
    transform_case_analysis_payload,
)
from src.mcp_models import EmailCaseAnalysisInput, EmailLegalSupportInput

from ._case_analysis_integration_cases import *  # noqa: F403
from .helpers.case_analysis_fixtures import case_payload as _case_payload


def test_email_case_analysis_input_requires_case_dates() -> None:
    payload = _case_payload()
    assert isinstance(payload["case_scope"], dict)
    payload["case_scope"].pop("date_from")
    with pytest.raises(ValueError, match=r"case_scope\.date_from is required"):
        EmailCaseAnalysisInput.model_validate(payload)


def test_email_case_analysis_input_requires_chat_logs_for_mixed_case_file() -> None:
    payload = _case_payload()
    payload["source_scope"] = "mixed_case_file"
    with pytest.raises(
        ValueError,
        match="mixed_case_file requires at least one of chat_log_entries, chat_exports, or matter_manifest chat artifacts",
    ):
        EmailCaseAnalysisInput.model_validate(payload)


def test_email_case_analysis_input_requires_manifest_for_exhaustive_review() -> None:
    payload = _case_payload()
    payload["review_mode"] = "exhaustive_matter_review"
    with pytest.raises(ValueError, match="exhaustive_matter_review requires matter_manifest"):
        EmailCaseAnalysisInput.model_validate(payload)


def test_email_legal_support_input_defaults_to_exhaustive_review_and_requires_manifest() -> None:
    payload = _case_payload()
    with pytest.raises(ValueError, match="exhaustive_matter_review requires matter_manifest"):
        EmailLegalSupportInput.model_validate(payload)


def test_email_legal_support_input_rejects_retrieval_only_mode() -> None:
    payload = _case_payload()
    payload["review_mode"] = "retrieval_only"
    payload["matter_manifest"] = {
        "manifest_id": "matter-legal-1",
        "artifacts": [
            {
                "source_id": "manifest:email:1",
                "source_class": "formal_document",
                "title": "Case summary",
                "date": "2025-03-10",
                "text": "Document text.",
            }
        ],
    }
    with pytest.raises(ValueError, match="Dedicated legal-support tools require review_mode='exhaustive_matter_review'"):
        EmailLegalSupportInput.model_validate(payload)


def test_email_case_analysis_input_accepts_native_chat_exports_for_mixed_case_file(tmp_path) -> None:
    payload = _case_payload()
    export_path = tmp_path / "teams-export.html"
    export_path.write_text(
        (
            "<html><body>"
            "[2025-03-01 09:10] Max Mustermann: Please keep this off email for now.\n"
            "[2025-03-01 09:12] Erika Beispiel: We will discuss this later."
            "</body></html>"
        ),
        encoding="utf-8",
    )
    payload["source_scope"] = "mixed_case_file"
    payload["chat_exports"] = [
        {
            "source_path": str(export_path),
            "platform": "Teams",
            "title": "Teams export",
        }
    ]
    params = EmailCaseAnalysisInput.model_validate(payload)
    assert len(params.chat_exports) == 1
    assert params.chat_exports[0].platform == "Teams"


def test_transform_case_analysis_payload_adds_quality_and_message_appendix() -> None:
    params = EmailCaseAnalysisInput.model_validate(_case_payload())
    answer_payload = {
        "search": {
            "top_k": 8,
            "date_from": "2025-01-01",
            "date_to": "2025-06-30",
            "hybrid": False,
            "rerank": False,
        },
        "case_bundle": {"bundle_id": "case-123"},
        "power_context": {"missing_org_context": True},
        "case_patterns": {"summary": {"behavior_cluster_count": 1}},
        "retaliation_analysis": {
            "trigger_event_count": 0,
            "anchor_requirement_status": "explicit_trigger_confirmation_required",
            "protected_activity_candidate_count": 2,
            "adverse_action_candidate_count": 1,
            "source_backed_candidate_counts": {"protected_activity": 1, "adverse_actions": 0},
            "retaliation_timeline_assessment": {
                "version": "1",
                "protected_activity_timeline": [],
                "adverse_action_timeline": [],
                "temporal_correlation_analysis": [],
                "strongest_retaliation_indicators": [],
                "strongest_non_retaliatory_explanations": [],
                "overall_evidentiary_rating": {"rating": "insufficient_timing_record"},
            },
        },
        "comparative_treatment": {"summary": {"available_comparator_count": 0}},
        "actor_identity_graph": {
            "actors": [
                {
                    "actor_id": "actor-manager",
                    "primary_email": "erika@example.org",
                    "display_names": ["Erika Beispiel"],
                    "role_hints": ["manager"],
                }
            ]
        },
        "communication_graph": {"graph_findings": []},
        "multi_source_case_bundle": {
            "summary": {"missing_source_types": ["chat_log"]},
            "sources": [
                {
                    "source_id": "meeting:uid-1:meeting_data",
                    "source_type": "meeting_note",
                    "uid": "uid-1",
                    "actor_id": "actor-manager",
                    "title": "Gesprächsprotokoll",
                    "date": "2025-03-14",
                    "snippet": "Wir werden die SBV beteiligen und eine schriftliche Zusammenfassung senden.",
                    "source_weighting": {"text_available": True},
                    "chronology_anchor": {"date": "2025-03-14"},
                },
                {
                    "source_id": "email:uid-1",
                    "source_type": "email",
                    "uid": "uid-1",
                    "actor_id": "actor-manager",
                    "title": "Status",
                    "date": "2025-03-15",
                    "snippet": "Wir werden die schriftliche Zusammenfassung vorerst nicht senden.",
                    "source_weighting": {"text_available": True, "can_corroborate_or_contradict": True},
                    "chronology_anchor": {"date": "2025-03-15"},
                },
            ],
        },
        "finding_evidence_index": {
            "findings": [
                {
                    "finding_id": "finding-1",
                    "finding_label": "Escalation Pressure",
                    "evidence_strength": {"label": "strong_indicator"},
                    "alternative_explanations": ["Possible policy deadline."],
                    "counter_indicators": ["Operational urgency cannot be excluded."],
                    "supporting_evidence": [
                        {
                            "message_or_document_id": "uid-1",
                            "citation_id": "finding-1:citation-1",
                        }
                    ],
                }
            ]
        },
        "evidence_table": {"row_count": 1},
        "behavioral_strength_rubric": {"version": "1"},
        "investigation_report": {
            "summary": {
                "section_count": 11,
                "supported_section_count": 7,
                "insufficient_section_count": 1,
            },
            "sections": {
                "missing_information": {
                    "section_id": "missing_information",
                    "title": "Missing Information / Further Evidence Needed",
                    "status": "insufficient_evidence",
                    "entries": [],
                    "insufficiency_reason": "No missing information recorded.",
                }
            },
        },
        "candidates": [
            {
                "uid": "uid-1",
                "date": "2025-03-15T10:00:00",
                "sender_name": "Erika Beispiel",
                "sender_email": "erika@example.org",
                "subject": "Status",
                "snippet": "Please just comply with the updated process.",
                "language_rhetoric": {
                    "authored_text": {
                        "signal_count": 1,
                        "signals": [
                            {
                                "signal_id": "dismissiveness",
                                "label": "Dismissiveness",
                                "confidence": "medium",
                            }
                        ],
                    }
                },
                "message_findings": {
                    "authored_text": {
                        "behavior_candidates": [
                            {
                                "behavior_id": "escalation",
                                "label": "Escalation",
                                "confidence": "medium",
                            }
                        ],
                        "counter_indicators": ["Possible policy deadline."],
                        "tone_summary": "Tense and directive.",
                        "relevant_wording": [
                            {
                                "text": "Please comply today.",
                                "source_scope": "authored_text",
                                "basis_id": "dismissiveness",
                            }
                        ],
                        "omissions_or_process_signals": [
                            {
                                "signal": "sbv_not_included",
                                "summary": "SBV was not included in the message despite the process context.",
                            }
                        ],
                        "included_actors": ["Erika Beispiel", "Max Mustermann"],
                        "excluded_actors": ["SBV"],
                        "communication_classification": {
                            "primary_class": "controlling",
                            "applied_classes": ["tense", "controlling"],
                            "confidence": "medium",
                            "rationale": "Directive wording plus process pressure.",
                        },
                    }
                },
            }
        ],
    }

    transformed = transform_case_analysis_payload(answer_payload, params)
    assert transformed["case_analysis_version"] == "1"
    assert transformed["workflow"] == "case_analysis"
    assert transformed["review_classification"]["classification"] == "retrieval_bounded_exploratory_review"
    assert transformed["review_classification"]["may_be_presented_as_full_matter_review"] is False
    assert transformed["search"] == answer_payload["search"]
    assert transformed["bilingual_workflow"]["output_language"] == "en"
    assert transformed["bilingual_workflow"]["primary_source_language"] == "de"
    assert transformed["case_scope_quality"]["status"] == "degraded"
    assert "retaliation_focus_without_trigger_events" in transformed["case_scope_quality"]["downgrade_reasons"]
    assert "power_focused_review_without_org_context" in transformed["case_scope_quality"]["downgrade_reasons"]
    assert "high_stakes_goal_without_context_notes" in transformed["case_scope_quality"]["downgrade_reasons"]
    assert transformed["matter_evidence_index"]["row_count"] == 2
    assert transformed["matter_evidence_index"]["rows"][0]["source_language"] == "de"
    assert transformed["matter_evidence_index"]["rows"][0]["quoted_evidence"]["original_text"].startswith("Wir werden")
    assert transformed["lawyer_issue_matrix"]["row_count"] == 0
    assert transformed["lawyer_issue_matrix"]["bilingual_rendering"]["output_language"] == "en"
    assert transformed["retaliation_timeline_assessment"]["overall_evidentiary_rating"]["rating"] == "insufficient_timing_record"
    assert transformed["retaliation_timeline_assessment"]["anchor_requirement_status"] == (
        "explicit_trigger_confirmation_required"
    )
    assert transformed["retaliation_timeline_assessment"]["protected_activity_candidate_count"] == 2
    assert transformed["actor_identity_graph"]["actors"][0]["actor_id"] == "actor-manager"
    assert transformed["actor_map"]["actor_count"] == 1
    assert transformed["actor_map"]["actors"][0]["status"]["decision_maker"] is False
    assert transformed["witness_map"]["primary_decision_makers"] == []
    assert transformed["witness_question_packs"]["pack_count"] >= 1
    assert transformed["promise_contradiction_analysis"]["summary"]["promise_action_row_count"] >= 1
    assert transformed["promise_contradiction_analysis"]["summary"]["contradiction_row_count"] >= 1
    assert transformed["lawyer_briefing_memo"]["memo_format"] == "lawyer_onboarding_brief"
    assert transformed["lawyer_briefing_memo"]["sections"]["executive_summary"]
    assert transformed["lawyer_briefing_memo"]["bilingual_rendering"]["preserve_original_quotations"] is True
    assert transformed["controlled_factual_drafting"]["drafting_format"] == "controlled_factual_drafting"
    assert transformed["controlled_factual_drafting"]["framing_preflight"]["allegation_ceiling"]["ceiling_level"]
    assert transformed["controlled_factual_drafting"]["bilingual_rendering"]["translation_mode"] == "translation_aware"
    assert transformed["case_dashboard"]["dashboard_format"] == "refreshable_case_dashboard"
    assert transformed["case_dashboard"]["cards"]["main_claims_or_issues"] == []
    assert transformed["case_dashboard"]["bilingual_rendering"]["output_language"] == "en"
    assert transformed["deadline_warnings"]["summary"]["warning_count"] >= 1
    assert transformed["case_dashboard"]["cards"]["timing_warnings"]
    assert transformed["document_request_checklist"]["deadline_warnings"]["summary"]["warning_count"] >= 1
    assert transformed["cross_output_consistency"]["summary"]["check_count"] >= 1
    assert transformed["cross_output_consistency"]["overall_status"] in {"consistent", "review_required"}
    assert transformed["skeptical_employer_review"]["summary"]["weakness_count"] >= 1
    assert transformed["document_request_checklist"]["group_count"] >= 1
    warnings = transformed["case_scope_quality"]["warnings"]
    assert any(item["code"] == "power_focused_review_without_org_context" for item in warnings)
    assert [item["field"] for item in transformed["case_scope_quality"]["recommended_next_inputs"]] == [
        "trigger_events",
        "alleged_adverse_actions",
        "org_context",
        "context_notes",
    ]
    assert transformed["message_appendix"]["included"] is True
    assert transformed["message_appendix"]["review_table_version"] == "2"
    assert transformed["message_appendix"]["row_count"] == 1
    assert transformed["message_appendix"]["rows"][0]["tone_summary"] == "Tense and directive."
    assert transformed["message_appendix"]["rows"][0]["communication_classification"]["primary_class"] == "controlling"
    assert transformed["message_appendix"]["rows"][0]["relevant_wording"][0]["text"] == "Please comply today."
    assert transformed["message_appendix"]["rows"][0]["excluded_actors"] == ["SBV"]


def test_transform_case_analysis_payload_preserves_matter_ingestion_report_and_review_mode() -> None:
    payload = _case_payload()
    payload["review_mode"] = "exhaustive_matter_review"
    payload["matter_manifest"] = {
        "manifest_id": "matter-1",
        "artifacts": [
            {
                "source_id": "manifest:personnel:1",
                "source_class": "personnel_file_record",
                "title": "Personnel file excerpt",
                "date": "2025-03-10",
                "filename": "personnel-file.pdf",
                "mime_type": "application/pdf",
                "text": "Personnel record excerpt.",
                "review_status": "parsed",
            }
        ],
    }
    params = EmailCaseAnalysisInput.model_validate(payload)
    answer_payload = {
        "search": {},
        "case_bundle": {"bundle_id": "case-123"},
        "multi_source_case_bundle": {
            "summary": {"missing_source_types": []},
            "sources": [
                {
                    "source_id": "manifest:personnel:1",
                    "source_type": "formal_document",
                    "document_kind": "personnel_file_record",
                    "title": "Personnel file excerpt",
                    "date": "2025-03-10",
                    "snippet": "Personnel record excerpt.",
                    "source_weighting": {"text_available": True, "can_corroborate_or_contradict": True},
                    "source_reliability": {"level": "high", "basis": "matter_manifest_parsed"},
                    "chronology_anchor": {"date": "2025-03-10"},
                }
            ],
            "source_links": [],
            "source_type_profiles": [],
            "chronology_anchors": [
                {
                    "source_id": "manifest:personnel:1",
                    "source_type": "formal_document",
                    "document_kind": "personnel_file_record",
                    "date": "2025-03-10",
                    "title": "Personnel file excerpt",
                    "reliability_level": "high",
                }
            ],
        },
        "matter_ingestion_report": {
            "version": "1",
            "review_mode": "exhaustive_matter_review",
            "completeness_status": "complete",
            "summary": {"total_supplied_artifacts": 1},
            "artifacts": [
                {
                    "artifact_id": "manifest:personnel:1",
                    "source_id": "manifest:personnel:1",
                    "review_status": "parsed",
                    "accounting_status": "included_in_case_bundle",
                }
            ],
        },
        "power_context": {},
        "case_patterns": {},
        "retaliation_analysis": {},
        "comparative_treatment": {},
        "communication_graph": {},
        "actor_identity_graph": {"actors": []},
        "finding_evidence_index": {"findings": []},
        "evidence_table": {"row_count": 0},
        "behavioral_strength_rubric": {"version": "1"},
        "investigation_report": {
            "summary": {"section_count": 1, "supported_section_count": 1, "insufficient_section_count": 0},
            "sections": {
                "missing_information": {
                    "section_id": "missing_information",
                    "title": "Missing Information / Further Evidence Needed",
                    "status": "supported",
                    "entries": [],
                    "insufficiency_reason": "",
                }
            },
        },
        "candidates": [],
    }

    transformed = transform_case_analysis_payload(answer_payload, params)

    assert transformed["review_mode"] == "exhaustive_matter_review"
    assert transformed["review_classification"]["classification"] == "counsel_grade_exhaustive_review"
    assert transformed["review_classification"]["may_be_presented_as_full_matter_review"] is True
    assert transformed["matter_ingestion_report"]["completeness_status"] == "complete"
    assert transformed["analysis_limits"]["review_mode"] == "exhaustive_matter_review"
    assert transformed["analysis_limits"]["completeness_status"] == "complete"


def test_transform_case_analysis_payload_downgrades_compacted_exhaustive_runs_and_preserves_telemetry() -> None:
    payload = _case_payload()
    payload["review_mode"] = "exhaustive_matter_review"
    payload["matter_manifest"] = {
        "manifest_id": "matter-2",
        "artifacts": [
            {
                "source_id": "manifest:email:1",
                "source_class": "formal_document",
                "title": "Case summary",
                "date": "2025-03-10",
                "text": "Document text.",
            }
        ],
    }
    params = EmailCaseAnalysisInput.model_validate(payload)
    answer_payload = {
        "search": {},
        "case_bundle": {"bundle_id": "case-123"},
        "multi_source_case_bundle": {
            "summary": {"missing_source_types": []},
            "sources": [],
            "source_links": [],
            "source_type_profiles": [],
        },
        "matter_ingestion_report": {
            "version": "1",
            "review_mode": "exhaustive_matter_review",
            "completeness_status": "complete",
            "summary": {"total_supplied_artifacts": 1},
            "artifacts": [
                {
                    "artifact_id": "manifest:email:1",
                    "source_id": "manifest:email:1",
                    "review_status": "parsed",
                    "accounting_status": "included_in_case_bundle",
                }
            ],
        },
        "power_context": {},
        "case_patterns": None,
        "retaliation_analysis": {},
        "comparative_treatment": {},
        "communication_graph": {},
        "actor_identity_graph": {"actors": []},
        "finding_evidence_index": None,
        "evidence_table": {"row_count": 0},
        "behavioral_strength_rubric": {"version": "1"},
        "investigation_report": None,
        "candidates": [
            {
                "uid": "uid-1",
                "date": "2025-03-15T10:00:00",
                "sender_name": "Erika Beispiel",
                "sender_email": "erika@example.org",
                "subject": "Privileged follow-up",
                "snippet": "A single surviving candidate remains visible after packing.",
            }
        ],
        "_packed": {
            "applied": True,
            "budget_chars": 16000,
            "estimated_chars_before": 780000,
            "estimated_chars_after": 17000,
            "truncated": {"body_candidates": 7},
            "deduplicated": {"body_candidates": 1},
        },
        "_case_surface_compaction": {
            "removed_count": 3,
            "removed": ["case_patterns", "finding_evidence_index", "investigation_report"],
        },
    }

    transformed = transform_case_analysis_payload(answer_payload, params)

    assert transformed["review_classification"]["classification"] == (
        "compacted_exhaustive_review_with_omitted_critical_surfaces"
    )
    assert transformed["review_classification"]["may_be_presented_as_full_matter_review"] is False
    assert transformed["_packed"]["applied"] is True
    assert transformed["_case_surface_compaction"]["removed_count"] == 3
    assert transformed["analysis_limits"]["packing"]["applied"] is True
    assert transformed["analysis_limits"]["case_surface_compaction"]["removed"] == [
        "case_patterns",
        "finding_evidence_index",
        "investigation_report",
    ]
    assert transformed["analysis_limits"]["omitted_case_analysis_surfaces"] == [
        "case_patterns",
        "finding_evidence_index",
        "investigation_report",
    ]
    assert transformed["analysis_limits"]["prompt_complete_behavioral_review"] is False


def test_transform_case_analysis_payload_applies_privacy_mode_redactions() -> None:
    payload = _case_payload()
    payload["privacy_mode"] = "witness_sharing"
    params = EmailCaseAnalysisInput.model_validate(payload)
    answer_payload = {
        "search": {},
        "case_bundle": {"bundle_id": "case-123"},
        "multi_source_case_bundle": None,
        "power_context": {},
        "case_patterns": {},
        "retaliation_analysis": {},
        "comparative_treatment": {},
        "communication_graph": {},
        "actor_identity_graph": {
            "actors": [
                {
                    "actor_id": "actor-1",
                    "primary_email": "erika@example.org",
                    "display_names": ["Erika Beispiel"],
                }
            ]
        },
        "finding_evidence_index": {"findings": []},
        "evidence_table": {"row_count": 0},
        "behavioral_strength_rubric": {"version": "1"},
        "investigation_report": {
            "summary": {"section_count": 1, "supported_section_count": 1, "insufficient_section_count": 0},
            "sections": {
                "overall_assessment": {
                    "section_id": "overall_assessment",
                    "title": "Overall Assessment",
                    "status": "supported",
                    "entries": [
                        {
                            "entry_id": "oa-1",
                            "statement": "Medical diagnosis from the physician was ignored after counsel review.",
                        }
                    ],
                },
                "missing_information": {
                    "section_id": "missing_information",
                    "title": "Missing Information / Further Evidence Needed",
                    "status": "supported",
                    "entries": [],
                },
            },
        },
        "candidates": [
            {
                "uid": "uid-1",
                "date": "2025-03-15T10:00:00",
                "sender_name": "Erika Beispiel",
                "sender_email": "erika@example.org",
                "subject": "Privileged follow-up",
                "snippet": "Medical diagnosis from the physician was shared with erika@example.org.",
            }
        ],
    }

    transformed = transform_case_analysis_payload(answer_payload, params)

    assert transformed["privacy_guardrails"]["privacy_mode"] == "witness_sharing"
    assert transformed["actor_identity_graph"]["actors"][0]["primary_email"] == "[REDACTED: participant_identity]"
    assert transformed["actor_identity_graph"]["actors"][0]["display_names"][0] == "[REDACTED: participant_identity]"
    assert transformed["investigation_report"]["sections"]["overall_assessment"]["entries"][0]["statement"] == (
        "[REDACTED: sensitive_medical_content]"
    )


def test_derive_case_analysis_query_uses_case_scope_context() -> None:
    payload = _case_payload()
    assert isinstance(payload["case_scope"], dict)
    payload["case_scope"]["comparator_actors"] = [{"name": "Pat Peer", "email": "pat@example.org", "role_hint": "employee"}]
    payload["case_scope"]["trigger_events"] = [{"trigger_type": "complaint", "date": "2025-02-02"}]
    payload["case_scope"]["employment_issue_tracks"] = ["participation_duty_gap"]
    params = EmailCaseAnalysisInput.model_validate(payload)
    query = derive_case_analysis_query(params)
    assert "workplace case analysis" in query
    assert "Max Mustermann" in query
    assert "retaliation, exclusion" in query
    assert "comparators Pat Peer pat@example.org" in query
    assert "trigger events complaint" in query
    assert "issue tracks participation_duty_gap" in query


def test_transform_case_analysis_payload_compacts_case_evidence_when_requested() -> None:
    payload = _case_payload()
    payload["compact_case_evidence"] = True
    params = EmailCaseAnalysisInput.model_validate(payload)
    answer_payload = {
        "finding_evidence_index": {
            "findings": [
                {"finding_id": "f-1"},
                {"finding_id": "f-2"},
                {"finding_id": "f-3"},
                {"finding_id": "f-4"},
            ]
        },
        "evidence_table": {"row_count": 8},
        "candidates": [
            {
                "uid": f"uid-{idx}",
                "date": "2025-03-15T10:00:00",
                "sender_name": "Erika Beispiel",
                "sender_email": "erika@example.org",
                "subject": f"Status {idx}",
                "snippet": "Please just comply with the updated process.",
                "language_rhetoric": {"authored_text": {"signal_count": 0, "signals": []}},
                "message_findings": {"authored_text": {"behavior_candidates": [], "counter_indicators": []}},
            }
            for idx in range(1, 8)
        ],
    }

    transformed = transform_case_analysis_payload(answer_payload, params)
    assert transformed["finding_evidence_index"]["summary"]["finding_count"] == 4
    assert transformed["evidence_table"]["summary"]["row_count"] == 8
    assert transformed["message_appendix"]["shown_row_count"] == 5
    assert transformed["message_appendix"]["_truncated"] == 2


def test_transform_case_analysis_payload_preserves_email_source_metadata_in_compacted_bundle() -> None:
    payload = _case_payload()
    payload["review_mode"] = "exhaustive_matter_review"
    payload["source_scope"] = "mixed_case_file"
    payload["matter_manifest"] = {
        "manifest_id": "matter-compact-1",
        "artifacts": [
            {
                "source_id": "manifest:note:1",
                "source_class": "formal_document",
                "title": "Meeting note",
                "date": "2025-03-10",
                "text": "Meeting note text.",
            }
        ],
    }
    payload["chat_log_entries"] = [
        {
            "platform": "Teams",
            "participants": ["max@example.org", "erika@example.org"],
            "date": "2025-03-11T10:00:00Z",
            "text": "Please keep this off email for now.",
        }
    ]
    params = EmailCaseAnalysisInput.model_validate(payload)
    answer_payload = {
        "search": {},
        "case_bundle": {
            "scope": {
                "case_label": "Case A",
                "target_person": {"name": "Max Mustermann", "email": "max@example.org"},
                "analysis_goal": "lawyer_briefing",
                "allegation_focus": ["retaliation"],
                "date_from": "2025-01-01",
                "date_to": "2025-06-30",
            }
        },
        "multi_source_case_bundle": {
            "summary": {"source_type_counts": {"email": 1}},
            "sources": [
                {
                    "source_id": "email:uid-1",
                    "source_type": "email",
                    "document_kind": "email_body",
                    "uid": "uid-1",
                    "title": "Status update",
                    "date": "2025-03-15T10:00:00",
                    "snippet": "Please comply with the updated process.",
                    "sender_name": "Erika Beispiel",
                    "sender_email": "erika@example.org",
                    "to": ["max@example.org"],
                    "cc": ["sbv@example.org"],
                    "source_reliability": {"level": "high", "basis": "authored_email_body"},
                    "document_locator": {"evidence_handle": "email:uid-1", "chunk_id": "chunk-1"},
                }
            ],
            "source_links": [],
            "source_type_profiles": [],
        },
        "matter_ingestion_report": {
            "version": "1",
            "review_mode": "exhaustive_matter_review",
            "completeness_status": "complete",
            "summary": {"total_supplied_artifacts": 1},
        },
        "power_context": {},
        "case_patterns": {},
        "retaliation_analysis": {},
        "comparative_treatment": {},
        "communication_graph": {},
        "actor_identity_graph": {"actors": []},
        "finding_evidence_index": {"findings": []},
        "evidence_table": {"row_count": 0},
        "behavioral_strength_rubric": {"version": "1"},
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
        "candidates": [
            {
                "uid": "uid-1",
                "date": "2025-03-15T10:00:00",
                "sender_name": "Erika Beispiel",
                "sender_email": "erika@example.org",
                "subject": "Status update",
                "snippet": "Please comply with the updated process.",
            }
        ],
    }

    transformed = transform_case_analysis_payload(answer_payload, params)

    evidence_row = transformed["matter_evidence_index"]["rows"][0]
    assert evidence_row["date"] == "2025-03-15T10:00:00"
    assert evidence_row["sender_or_author"] == "Erika Beispiel"
    assert evidence_row["recipients"] == ["max@example.org", "sbv@example.org"]
    assert evidence_row["short_description"] == "Status update: Please comply with the updated process."
    assert evidence_row["key_quoted_passage"] == "Please comply with the updated process."


def test_message_appendix_rows_are_sorted_by_date_then_uid() -> None:
    params = EmailCaseAnalysisInput.model_validate(_case_payload())
    transformed = transform_case_analysis_payload(
        {
            "finding_evidence_index": {"findings": []},
            "candidates": [
                {
                    "uid": "uid-b",
                    "date": "2025-03-15T10:00:00",
                    "sender_name": "Sender B",
                    "sender_email": "b@example.org",
                    "subject": "Second",
                    "snippet": "Second snippet",
                    "language_rhetoric": {"authored_text": {"signal_count": 0, "signals": []}},
                    "message_findings": {"authored_text": {"behavior_candidates": [], "counter_indicators": []}},
                },
                {
                    "uid": "uid-a",
                    "date": "2025-03-15T10:00:00",
                    "sender_name": "Sender A",
                    "sender_email": "a@example.org",
                    "subject": "First",
                    "snippet": "First snippet",
                    "language_rhetoric": {"authored_text": {"signal_count": 0, "signals": []}},
                    "message_findings": {"authored_text": {"behavior_candidates": [], "counter_indicators": []}},
                },
                {
                    "uid": "uid-c",
                    "date": "2025-03-16T10:00:00",
                    "sender_name": "Sender C",
                    "sender_email": "c@example.org",
                    "subject": "Third",
                    "snippet": "Third snippet",
                    "language_rhetoric": {"authored_text": {"signal_count": 0, "signals": []}},
                    "message_findings": {"authored_text": {"behavior_candidates": [], "counter_indicators": []}},
                },
            ],
        },
        params,
    )
    rows = transformed["message_appendix"]["rows"]
    assert [row["uid"] for row in rows] == ["uid-a", "uid-b", "uid-c"]


def test_transform_case_analysis_payload_adds_mixed_scope_warning() -> None:
    payload = _case_payload()
    payload["source_scope"] = "mixed_case_file"
    payload["chat_log_entries"] = [
        {
            "source_id": "chat-1",
            "platform": "Teams",
            "text": "Please keep this off email for now.",
        }
    ]
    params = EmailCaseAnalysisInput.model_validate(payload)
    transformed = transform_case_analysis_payload({}, params)
    assert "mixed_case_file_declared_without_native_chat_support" not in transformed["case_scope_quality"]["downgrade_reasons"]
    assert all(
        item["code"] != "mixed_case_file_declared_without_native_chat_support"
        for item in transformed["analysis_limits"]["scope_warnings"]
    )
