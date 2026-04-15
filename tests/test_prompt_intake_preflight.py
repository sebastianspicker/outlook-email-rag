from __future__ import annotations

from pathlib import Path

from src.case_prompt_intake import build_case_prompt_preflight
from src.mcp_models import EmailCasePromptPreflightInput, EmailLegalSupportInput

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "docs" / "agent"


def test_prompt_preflight_builds_conservative_scaffold_without_fabricating_retaliation_fields() -> None:
    params = EmailCasePromptPreflightInput.model_validate(
        {
            "prompt_text": (
                "Claimant: Max Mustermann. Manager: Erika Beispiel. "
                "Create a lawyer briefing on disability disadvantage and retaliation from November 2023 to present "
                "based on the email corpus and written communications."
            ),
            "today": "2026-04-13",
            "output_language": "en",
        }
    )

    payload = build_case_prompt_preflight(params)

    assert payload["workflow"] == "case_prompt_preflight"
    assert payload["analysis_goal"] == "lawyer_briefing"
    assert payload["recommended_source_scope"] == "emails_and_attachments"
    assert payload["draft_case_scope"]["target_person"]["name"] == "Max Mustermann"
    assert payload["draft_case_scope"]["suspected_actors"][0]["name"] == "Erika Beispiel"
    assert payload["draft_case_scope"]["date_from"] == "2023-11-01"
    assert payload["draft_case_scope"]["date_to"] == "2026-04-13"
    assert "trigger_events" not in payload["draft_case_scope"]
    assert "alleged_adverse_actions" not in payload["draft_case_scope"]
    assert payload["ready_for_case_analysis"] is False
    assert payload["supports_exhaustive_legal_support"] is False
    assert payload["extraction_summary"]["used_today_for_open_ended_range"] is True

    missing_fields = {item["field"] for item in payload["missing_required_inputs"]}
    assert "case_scope.trigger_events" in missing_fields
    assert "case_scope.alleged_adverse_actions" in missing_fields


def test_prompt_preflight_flags_missing_comparators_without_inventing_them() -> None:
    params = EmailCasePromptPreflightInput.model_validate(
        {
            "prompt_text": (
                "Employee: Max Mustermann. Review unequal treatment and discrimination from 2025-01-01 to 2025-03-31. "
                "Compare the claimant to colleagues for mobile-work restrictions and control intensity."
            ),
            "today": "2026-04-13",
        }
    )

    payload = build_case_prompt_preflight(params)

    assert payload["draft_case_scope"]["comparator_actors"] == []
    missing_fields = {item["field"] for item in payload["missing_required_inputs"]}
    assert "case_scope.comparator_actors" in missing_fields
    assert payload["ready_for_case_analysis"] is False


def test_prompt_preflight_emits_review_facing_candidate_structures_without_promoting_them() -> None:
    params = EmailCasePromptPreflightInput.model_validate(
        {
            "prompt_text": (
                "Claimant: Max Mustermann. Complaint on 2025-03-01 to HR after disability disclosure. "
                "Task withdrawal on 2025-03-05 and compare treatment with colleague: Pat Vergleich. "
                "Need SBV and NovaTime records."
            ),
            "today": "2026-04-13",
        }
    )

    payload = build_case_prompt_preflight(params)

    candidates = payload["candidate_structures"]
    assert candidates["summary"]["trigger_event_candidate_count"] >= 1
    assert candidates["summary"]["adverse_action_candidate_count"] >= 1
    assert candidates["summary"]["comparator_candidate_count"] == 1
    assert candidates["summary"]["protected_context_candidate_count"] >= 1
    assert candidates["summary"]["missing_record_candidate_count"] >= 1
    trigger_candidate = candidates["trigger_event_candidates"][0]
    adverse_candidate = candidates["adverse_action_candidates"][0]
    assert trigger_candidate["requires_operator_confirmation"] is True
    assert adverse_candidate["requires_operator_confirmation"] is True
    assert trigger_candidate["candidate_value"]["date"] == "2025-03-01"
    assert trigger_candidate["candidate_value"]["date_confidence"] == "exact"
    assert adverse_candidate["candidate_value"]["date"] == "2025-03-05"
    assert "trigger_events" not in payload["draft_case_scope"]
    assert "alleged_adverse_actions" not in payload["draft_case_scope"]


def test_email_legal_support_input_stays_strict_for_exhaustive_tools() -> None:
    try:
        EmailLegalSupportInput.model_validate(
            {
                "case_scope": {
                    "target_person": {"name": "Max Mustermann"},
                    "analysis_goal": "lawyer_briefing",
                    "date_from": "2025-01-01",
                    "date_to": "2025-03-31",
                    "allegation_focus": ["retaliation"],
                },
                "source_scope": "emails_and_attachments",
                "review_mode": "exhaustive_matter_review",
            }
        )
    except ValueError as exc:
        assert "matter_manifest" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Expected exhaustive legal-support input to reject missing matter_manifest.")


def test_prompt_family_fixtures_emit_expected_candidate_shapes_and_blockers() -> None:
    cases = [
        {
            "fixture": "prompt_fixture.retaliation_heavy.md",
            "source_scope": "mixed_case_file",
            "blocked_field": "case_scope.trigger_events",
            "candidate_count_key": "trigger_event_candidate_count",
            "expect_override_suggestion": False,
        },
        {
            "fixture": "prompt_fixture.comparator_heavy.md",
            "source_scope": "mixed_case_file",
            "blocked_field": None,
            "candidate_count_key": "comparator_candidate_count",
            "expect_override_suggestion": True,
        },
        {
            "fixture": "prompt_fixture.prevention_participation_heavy.md",
            "source_scope": "mixed_case_file",
            "blocked_field": None,
            "candidate_count_key": "missing_record_candidate_count",
            "expect_override_suggestion": False,
        },
        {
            "fixture": "prompt_fixture.eingruppierung_heavy.md",
            "source_scope": "mixed_case_file",
            "blocked_field": None,
            "candidate_count_key": "adverse_action_candidate_count",
            "expect_override_suggestion": False,
        },
        {
            "fixture": "prompt_fixture.mixed_source_documentary.md",
            "source_scope": "mixed_case_file",
            "blocked_field": "case_scope.trigger_events",
            "candidate_count_key": "missing_record_candidate_count",
            "expect_override_suggestion": False,
        },
    ]

    for case in cases:
        prompt_text = (FIXTURE_DIR / case["fixture"]).read_text(encoding="utf-8")
        payload = build_case_prompt_preflight(
            EmailCasePromptPreflightInput.model_validate({"prompt_text": prompt_text, "today": "2026-04-13"})
        )

        assert payload["recommended_source_scope"] == case["source_scope"]
        assert payload["candidate_structures"]["summary"][case["candidate_count_key"]] >= 1
        missing_fields = {item["field"] for item in payload["missing_required_inputs"]}
        if case["blocked_field"] is not None:
            assert case["blocked_field"] in missing_fields
        if case["expect_override_suggestion"]:
            recommendations = {item["field"] for item in payload["recommended_next_inputs"]}
            assert "case_scope.comparator_equivalence_notes" in recommendations


def test_prompt_preflight_does_not_promote_instruction_only_prompt_text_into_case_scope() -> None:
    params = EmailCasePromptPreflightInput.model_validate(
        {
            "prompt_text": (
                "You are an evidence-focused legal-support and case-analysis agent working on a German "
                "employment-related matter.\n\n"
                "Core rules:\n"
                "- Work only from the provided materials.\n"
                "- Preserve chronology.\n"
                "- Quote or precisely reference the source for every important finding.\n\n"
                "Output style:\n"
                "- concise but rigorous\n"
                "- neutral tone suitable for lawyer review\n\n"
                "Always ask yourself:\n"
                "- What is proven?\n"
                "- What is only alleged?\n"
            ),
            "today": "2026-04-15",
            "output_language": "en",
        }
    )

    payload = build_case_prompt_preflight(params)

    assert payload["draft_case_scope"]["context_notes"] == ""
    assert payload["draft_case_scope"]["allegation_focus"] == []
    assert payload["draft_case_scope"]["employment_issue_tracks"] == []
    assert payload["candidate_structures"]["summary"] == {
        "trigger_event_candidate_count": 0,
        "adverse_action_candidate_count": 0,
        "comparator_candidate_count": 0,
        "protected_context_candidate_count": 0,
        "missing_record_candidate_count": 0,
    }
