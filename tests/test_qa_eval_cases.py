import json
from pathlib import Path


def test_load_question_cases_reads_template_object(tmp_path: Path):
    from src.qa_eval import load_question_cases

    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [
                    {
                        "id": "fact-001",
                        "bucket": "fact_lookup",
                        "question": "Who asked for the updated budget?",
                        "expected_support_uids": ["uid-1"],
                        "triage_tags": ["retrieval_recall"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    cases = load_question_cases(path)

    assert len(cases) == 1
    assert cases[0].id == "fact-001"
    assert cases[0].expected_support_uids == ["uid-1"]
    assert cases[0].triage_tags == ["retrieval_recall"]


def test_load_question_cases_reads_case_scope_and_bundle_expectations(tmp_path: Path):
    from src.qa_eval import load_question_cases

    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [
                    {
                        "id": "investigation-001",
                        "bucket": "investigation_case",
                        "question": "Analyze the quick and dirty conversation.",
                        "case_scope": {
                            "target_person": {"name": "Alex Example"},
                            "suspected_actors": [{"name": "Morgan Manager", "email": "manager@example.com"}],
                            "allegation_focus": ["hostility", "exclusion"],
                            "analysis_goal": "internal_review",
                        },
                        "expected_case_bundle_uids": ["uid-1", "uid-2"],
                        "expected_source_types": ["email"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    cases = load_question_cases(path)

    assert len(cases) == 1
    assert cases[0].case_scope is not None
    assert cases[0].case_scope.analysis_goal == "internal_review"
    assert cases[0].expected_case_bundle_uids == ["uid-1", "uid-2"]
    assert cases[0].expected_source_types == ["email"]


def test_load_question_cases_reads_behavioral_analysis_expectations(tmp_path: Path):
    from src.qa_eval import load_question_cases

    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [
                    {
                        "id": "behavior-001",
                        "bucket": "explicit_hostility",
                        "question": "Assess the conduct in this message.",
                        "expected_timeline_uids": ["uid-1"],
                        "expected_behavior_ids": ["escalation", "public_correction"],
                        "expected_counter_indicator_markers": ["process friction"],
                        "expected_max_claim_level": "observed_fact",
                        "expected_report_sections": ["executive_summary", "overall_assessment"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    cases = load_question_cases(path)

    assert cases[0].expected_timeline_uids == ["uid-1"]
    assert cases[0].expected_behavior_ids == ["escalation", "public_correction"]
    assert cases[0].expected_counter_indicator_markers == ["process friction"]
    assert cases[0].expected_max_claim_level == "observed_fact"
    assert cases[0].expected_report_sections == ["executive_summary", "overall_assessment"]
