import json
from pathlib import Path

import pytest


def test_run_evaluation_reports_source_counts(tmp_path: Path):
    from src.qa_eval import run_evaluation_sync

    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "fact-001",
                        "bucket": "fact_lookup",
                        "question": "Who asked for the updated budget?",
                        "expected_support_uids": ["uid-1"],
                        "expected_top_uid": "uid-1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps(
            {
                "fact-001": {
                    "count": 1,
                    "candidates": [{"uid": "uid-1", "score": 0.91}],
                    "attachment_candidates": [],
                    "answer_quality": {"top_candidate_uid": "uid-1", "confidence_label": "high", "ambiguity_reason": ""},
                }
            }
        ),
        encoding="utf-8",
    )

    report = run_evaluation_sync(questions_path=questions_path, results_path=results_path)

    assert report["source_counts"] == {"captured": 1}


@pytest.mark.asyncio
async def test_run_evaluation_uses_results_payloads(tmp_path: Path):
    from src.qa_eval import run_evaluation

    questions_path = tmp_path / "questions.json"
    results_path = tmp_path / "results.json"

    questions_path.write_text(
        json.dumps(
            {
                "version": 1,
                "cases": [
                    {
                        "id": "fact-001",
                        "bucket": "fact_lookup",
                        "question": "Who asked for the updated budget?",
                        "expected_support_uids": ["uid-1"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    results_path.write_text(
        json.dumps(
            {
                "fact-001": {
                    "count": 1,
                    "candidates": [{"uid": "uid-1", "score": 0.91}],
                    "attachment_candidates": [],
                    "answer_quality": {
                        "top_candidate_uid": "uid-1",
                        "confidence_label": "high",
                        "ambiguity_reason": None,
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    report = await run_evaluation(questions_path=questions_path, results_path=results_path)

    assert report["summary"]["total_cases"] == 1
    assert report["results"][0]["support_uid_hit"] is True
    assert report["results"][0]["source"] == "captured"
