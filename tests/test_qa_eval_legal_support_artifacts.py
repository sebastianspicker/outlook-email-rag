import json
from pathlib import Path


def test_legal_support_question_set_is_labeled_and_scored():
    from src.qa_eval import load_question_cases

    path = Path("docs/agent/qa_eval_questions.legal_support.captured.json")
    cases = load_question_cases(path)

    assert len(cases) >= 5
    assert all(case.status == "labeled" for case in cases)
    assert any(case.expected_comparator_issue_ids for case in cases)
    assert any(case.expected_dashboard_cards for case in cases)
    assert any(case.expected_actor_ids for case in cases)
    assert any(case.expected_draft_ceiling_level for case in cases)


def test_saved_legal_support_report_has_expected_contract():
    questions_path = Path("docs/agent/qa_eval_questions.legal_support.captured.json")
    report_path = Path("docs/agent/qa_eval_report.legal_support.captured.json")

    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    cases = questions["cases"]
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert report["questions_path"].endswith("docs/agent/qa_eval_questions.legal_support.captured.json")
    assert report["total_cases"] == len(cases)
    assert report["summary"]["total_cases"] == len(cases)
    assert report["source_counts"] == {"captured": len(cases)}
    assert report["summary"]["legal_support_product_completeness"]["scorable"] >= 5
    assert report["summary"]["comparator_matrix_coverage"]["scorable"] >= 1
    assert report["summary"]["dashboard_card_coverage"]["scorable"] >= 1
    assert report["summary"]["actor_map_coverage"]["scorable"] >= 1
    assert report["summary"]["checklist_group_coverage"]["scorable"] >= 1
    assert report["summary"]["drafting_ceiling_match"]["scorable"] >= 1
    assert report["summary"]["draft_section_completeness"]["scorable"] >= 1
    assert "failure_taxonomy" in report
    assert "ranked_categories" in report["failure_taxonomy"]
