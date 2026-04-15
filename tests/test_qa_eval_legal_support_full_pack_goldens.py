from __future__ import annotations

import json
from pathlib import Path


def test_legal_support_full_pack_golden_files_exist_and_cover_key_products() -> None:
    retaliation_path = Path("docs/agent/legal_support_full_pack_golden.retaliation_rights_assertion.json")
    comparator_path = Path("docs/agent/legal_support_full_pack_golden.comparator_unequal_treatment.json")

    retaliation = json.loads(retaliation_path.read_text(encoding="utf-8"))
    comparator = json.loads(comparator_path.read_text(encoding="utf-8"))

    assert retaliation["scenario"] == "legal_support_full_pack_retaliation"
    assert retaliation["projection"]["evidence_rows"]
    assert retaliation["projection"]["chronology_entries"]
    assert retaliation["projection"]["issue_rows"]
    assert retaliation["projection"]["memo_sections"]["executive_summary"]
    assert retaliation["projection"]["draft_preflight"]
    assert retaliation["projection"]["dashboard_cards"]["main_claims_or_issues"]
    assert retaliation["projection"]["retaliation_points"]
    assert retaliation["projection"]["provenance_examples"]
    assert retaliation["projection"]["cross_output_checks"]

    assert comparator["scenario"] == "legal_support_full_pack_comparator"
    assert comparator["projection"]["comparator_points"]
    assert comparator["projection"]["issue_rows"]
    assert comparator["projection"]["dashboard_cards"]["main_claims_or_issues"]


def test_refresh_captured_reports_check_mode_includes_full_pack_goldens() -> None:
    from scripts import refresh_qa_eval_captured_reports as runner
    from src.legal_support_acceptance_goldens import FULL_PACK_GOLDEN_ALIAS
    from src.qa_eval_captured_artifacts import CAPTURED_EVAL_SCENARIOS

    listed = json.loads(_capture_stdout(lambda: runner.main(["--list"])))
    assert FULL_PACK_GOLDEN_ALIAS in listed
    assert {scenario.name for scenario in CAPTURED_EVAL_SCENARIOS} <= set(listed)

    refresh_exit = runner.main(["--scenario", FULL_PACK_GOLDEN_ALIAS])
    assert refresh_exit == 0
    check_exit = runner.main(["--check", "--scenario", FULL_PACK_GOLDEN_ALIAS])
    assert check_exit == 0


def _capture_stdout(fn) -> str:
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        fn()
    return buffer.getvalue()
