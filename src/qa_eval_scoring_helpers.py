"""Helper metrics for QA evaluation payload scoring."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .qa_eval_cases import QuestionCase


def _normalize_eval_text(value: str) -> str:
    return " ".join((value or "").casefold().split())


def _candidate_uids(payload: dict[str, Any]) -> list[str]:
    uids: list[str] = []
    for key in ("candidates", "attachment_candidates"):
        for item in payload.get(key, []):
            uid = item.get("uid")
            if uid and uid not in uids:
                uids.append(str(uid))
    return uids


def _uids_for_key(payload: dict[str, Any], key: str) -> list[str]:
    uids: list[str] = []
    for item in payload.get(key, []):
        uid = item.get("uid")
        if uid and uid not in uids:
            uids.append(str(uid))
    return uids


def _strong_attachment_support_uid_hit(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if case.bucket != "attachment_lookup" or not case.expected_support_uids:
        return None
    for item in payload.get("attachment_candidates", []):
        uid = str(item.get("uid") or "")
        if uid not in case.expected_support_uids:
            continue
        attachment = item.get("attachment") or {}
        if not isinstance(attachment, dict):
            continue
        if str(attachment.get("evidence_strength") or "") == "strong_text":
            return True
    return False


def _strong_attachment_ocr_support_uid_hit(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if case.bucket != "attachment_lookup" or not case.expected_support_uids or "attachment_ocr" not in case.triage_tags:
        return None
    for item in payload.get("attachment_candidates", []):
        uid = str(item.get("uid") or "")
        if uid not in case.expected_support_uids:
            continue
        attachment = item.get("attachment") or {}
        if not isinstance(attachment, dict):
            continue
        if (
            str(attachment.get("evidence_strength") or "") == "strong_text"
            and bool(attachment.get("ocr_used"))
            and str(attachment.get("extraction_state") or "").strip().lower() == "ocr_text_extracted"
        ):
            return True
    return False


def _weak_evidence_explained(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if (case.expected_ambiguity or "").lower() != "insufficient":
        return None
    weak_reason_markers = {
        "weak_scan_body",
        "source_shell_only",
        "image_only",
        "metadata_only_reply",
        "true_blank",
        "attachment_only",
    }
    answer_quality = payload.get("answer_quality") or {}
    ambiguity_reason = str(answer_quality.get("ambiguity_reason") or "")
    if ambiguity_reason in weak_reason_markers:
        return True
    for key in ("candidates", "attachment_candidates"):
        for item in payload.get(key, []):
            weak_message = item.get("weak_message")
            if isinstance(weak_message, dict) and weak_message.get("code") in weak_reason_markers:
                return True
    return False


def _resolve_top_uid(payload: dict[str, Any]) -> str | None:
    answer_quality = payload.get("answer_quality") or {}
    top_uid = answer_quality.get("top_candidate_uid")
    if top_uid:
        return str(top_uid)
    for key in ("candidates", "attachment_candidates"):
        items = payload.get(key) or []
        if items:
            uid = items[0].get("uid")
            if uid:
                return str(uid)
    return None


def _long_thread_answer_present(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if "long_thread" not in case.triage_tags:
        return None
    final_answer = payload.get("final_answer")
    if not isinstance(final_answer, dict):
        return False
    return bool(str(final_answer.get("text") or "").strip())


def _long_thread_structure_preserved(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if "long_thread" not in case.triage_tags:
        return None
    conversation_groups = payload.get("conversation_groups")
    timeline = payload.get("timeline")
    timeline_events = timeline.get("events") if isinstance(timeline, dict) else None
    return bool(conversation_groups) and bool(timeline_events)


def _ambiguity_matches(expected: str | None, payload: dict[str, Any]) -> bool | None:
    if expected is None:
        return None
    answer_quality = payload.get("answer_quality") or {}
    label = str(answer_quality.get("confidence_label") or "").lower()
    reason = str(answer_quality.get("ambiguity_reason") or "").lower()
    count = int(payload.get("count") or 0)
    normalized = expected.lower()
    if normalized == "ambiguous":
        return label == "ambiguous" or bool(reason)
    if normalized == "clear":
        return label in {"high", "medium"} and not reason
    if normalized == "insufficient":
        return label == "low" or count == 0 or reason == "no_results"
    return None


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _average_metric(results: list[dict[str, Any]], metric: str) -> dict[str, float | int]:
    values = [float(result[metric]) for result in results if result.get(metric) is not None]
    if not values:
        return {"scorable": 0, "average": 0.0}
    return {"scorable": len(values), "average": sum(values) / len(values)}


def _observed_quoted_speaker_emails(payload: dict[str, Any]) -> list[str]:
    observed: list[str] = []
    for key in ("candidates", "attachment_candidates"):
        for item in payload.get(key, []):
            attribution = item.get("speaker_attribution")
            if not isinstance(attribution, dict):
                continue
            for block in attribution.get("quoted_blocks", []):
                if not isinstance(block, dict):
                    continue
                speaker_email = str(block.get("speaker_email") or "").strip().lower()
                if speaker_email and speaker_email not in observed:
                    observed.append(speaker_email)
    return observed


def _case_bundle_present(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if case.case_scope is None:
        return None
    return isinstance(payload.get("case_bundle"), dict)


def _investigation_blocks_present(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if case.case_scope is None:
        return None
    required_blocks = (
        "case_bundle",
        "actor_identity_graph",
        "case_patterns",
        "finding_evidence_index",
        "evidence_table",
        "quote_attribution_metrics",
    )
    return all(isinstance(payload.get(key), dict) for key in required_blocks)


def _case_bundle_support_uid_hit(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if case.case_scope is None or not case.expected_case_bundle_uids:
        return None
    candidate_uids = _candidate_uids(payload)
    return any(uid in candidate_uids for uid in case.expected_case_bundle_uids)


def _case_bundle_support_uid_recall(case: QuestionCase, payload: dict[str, Any]) -> float | None:
    if case.case_scope is None or not case.expected_case_bundle_uids:
        return None
    candidate_uids = _candidate_uids(payload)
    matched = [uid for uid in case.expected_case_bundle_uids if uid in candidate_uids]
    return _ratio(len(matched), len(case.expected_case_bundle_uids))


def _multi_source_source_types_match(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if case.case_scope is None or not case.expected_source_types:
        return None
    multi_source_case_bundle = payload.get("multi_source_case_bundle")
    if not isinstance(multi_source_case_bundle, dict):
        return False
    observed = {
        str(source.get("source_type") or "")
        for source in multi_source_case_bundle.get("sources", []) or []
        if isinstance(source, dict)
    }
    return set(case.expected_source_types).issubset(observed)


def _timeline_uids(payload: dict[str, Any]) -> list[str]:
    observed: list[str] = []
    timeline = payload.get("timeline")
    if not isinstance(timeline, dict):
        return observed
    for event in timeline.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        uid = str(event.get("uid") or "")
        if uid and uid not in observed:
            observed.append(uid)
    return observed


def _chronology_uid_hit(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if not case.expected_timeline_uids:
        return None
    observed = _timeline_uids(payload)
    return any(uid in observed for uid in case.expected_timeline_uids)


def _chronology_uid_recall(case: QuestionCase, payload: dict[str, Any]) -> float | None:
    if not case.expected_timeline_uids:
        return None
    observed = _timeline_uids(payload)
    matched = [uid for uid in case.expected_timeline_uids if uid in observed]
    return _ratio(len(matched), len(case.expected_timeline_uids))


def _observed_behavior_ids(payload: dict[str, Any]) -> list[str]:
    observed: list[str] = []
    for candidate in payload.get("candidates", []) or []:
        if not isinstance(candidate, dict):
            continue
        message_findings = candidate.get("message_findings")
        if not isinstance(message_findings, dict):
            continue
        authored_text = message_findings.get("authored_text")
        if isinstance(authored_text, dict):
            for behavior in authored_text.get("behavior_candidates", []) or []:
                if not isinstance(behavior, dict):
                    continue
                behavior_id = str(behavior.get("behavior_id") or "")
                if behavior_id and behavior_id not in observed:
                    observed.append(behavior_id)
        for block in message_findings.get("quoted_blocks", []) or []:
            if not isinstance(block, dict):
                continue
            analysis = block.get("analysis")
            if not isinstance(analysis, dict):
                continue
            for behavior in analysis.get("behavior_candidates", []) or []:
                if not isinstance(behavior, dict):
                    continue
                behavior_id = str(behavior.get("behavior_id") or "")
                if behavior_id and behavior_id not in observed:
                    observed.append(behavior_id)
    return observed


def _behavior_tag_coverage(case: QuestionCase, payload: dict[str, Any]) -> float | None:
    if not case.expected_behavior_ids:
        return None
    observed = _observed_behavior_ids(payload)
    matched = [behavior_id for behavior_id in case.expected_behavior_ids if behavior_id in observed]
    return _ratio(len(matched), len(case.expected_behavior_ids))


def _behavior_tag_precision(case: QuestionCase, payload: dict[str, Any]) -> float | None:
    if not case.expected_behavior_ids:
        return None
    observed = _observed_behavior_ids(payload)
    if not observed:
        return 0.0
    matched = [behavior_id for behavior_id in observed if behavior_id in case.expected_behavior_ids]
    return _ratio(len(matched), len(observed))


def _observed_counter_indicator_texts(payload: dict[str, Any]) -> list[str]:
    observed: list[str] = []

    def _append(value: str) -> None:
        normalized = _normalize_eval_text(value)
        if normalized and normalized not in observed:
            observed.append(normalized)

    for candidate in payload.get("candidates", []) or []:
        if not isinstance(candidate, dict):
            continue
        message_findings = candidate.get("message_findings")
        if not isinstance(message_findings, dict):
            continue
        authored_text = message_findings.get("authored_text")
        if isinstance(authored_text, dict):
            for item in authored_text.get("counter_indicators", []) or []:
                _append(str(item))
        for block in message_findings.get("quoted_blocks", []) or []:
            if not isinstance(block, dict):
                continue
            analysis = block.get("analysis")
            if not isinstance(analysis, dict):
                continue
            for item in analysis.get("counter_indicators", []) or []:
                _append(str(item))

    finding_index = payload.get("finding_evidence_index")
    if isinstance(finding_index, dict):
        for finding in finding_index.get("findings", []) or []:
            if not isinstance(finding, dict):
                continue
            for item in finding.get("counter_indicators", []) or []:
                _append(str(item))
            for item in finding.get("alternative_explanations", []) or []:
                _append(str(item))

    report = payload.get("investigation_report")
    if isinstance(report, dict):
        sections = report.get("sections")
        if isinstance(sections, dict):
            overall = sections.get("overall_assessment")
            if isinstance(overall, dict):
                for entry in overall.get("entries", []) or []:
                    if not isinstance(entry, dict):
                        continue
                    for item in entry.get("alternative_explanations", []) or []:
                        _append(str(item))
                    for item in entry.get("ambiguity_disclosures", []) or []:
                        _append(str(item))
            missing = sections.get("missing_information")
            if isinstance(missing, dict):
                for entry in missing.get("entries", []) or []:
                    if isinstance(entry, dict):
                        _append(str(entry.get("statement") or ""))
    return observed


def _counter_indicator_quality(case: QuestionCase, payload: dict[str, Any]) -> float | None:
    if not case.expected_counter_indicator_markers:
        return None
    observed = _observed_counter_indicator_texts(payload)
    matched = 0
    for marker in case.expected_counter_indicator_markers:
        normalized_marker = _normalize_eval_text(marker)
        if any(normalized_marker in item for item in observed):
            matched += 1
    return _ratio(matched, len(case.expected_counter_indicator_markers))


def _claim_level_rank(level: str | None) -> int:
    return {
        "insufficient_evidence": 1,
        "pattern_concern": 2,
        "observed_fact": 3,
        "stronger_interpretation": 4,
    }.get(str(level or ""), 0)


def _report_claim_levels(payload: dict[str, Any]) -> list[str]:
    report = payload.get("investigation_report")
    if not isinstance(report, dict):
        return []
    sections = report.get("sections")
    if not isinstance(sections, dict):
        return []
    levels: list[str] = []
    for section in sections.values():
        if not isinstance(section, dict):
            continue
        for entry in section.get("entries", []) or []:
            if not isinstance(entry, dict):
                continue
            level = str(entry.get("claim_level") or "")
            if level:
                levels.append(level)
    return levels


def _overclaim_guard_match(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if not case.expected_max_claim_level:
        return None
    observed_levels = _report_claim_levels(payload)
    if not observed_levels:
        return False
    max_observed = max(_claim_level_rank(level) for level in observed_levels)
    return max_observed <= _claim_level_rank(case.expected_max_claim_level)


def _report_completeness(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if not case.expected_report_sections:
        return None
    report = payload.get("investigation_report")
    if not isinstance(report, dict):
        return False
    sections = report.get("sections")
    if not isinstance(sections, dict):
        return False
    for section_id in case.expected_report_sections:
        section = sections.get(section_id)
        if not isinstance(section, dict):
            return False
        if str(section.get("status") or "") != "supported":
            return False
    return True


def _legal_support_product_completeness(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if not case.expected_legal_support_products:
        return None
    for product_id in case.expected_legal_support_products:
        product = payload.get(product_id)
        if not isinstance(product, dict):
            return False
    return True


def _observed_comparator_issue_ids(payload: dict[str, Any]) -> list[str]:
    observed: list[str] = []
    comparative_treatment = payload.get("comparative_treatment")
    if not isinstance(comparative_treatment, dict):
        return observed
    for summary in comparative_treatment.get("comparator_summaries", []) or []:
        if not isinstance(summary, dict):
            continue
        matrix = summary.get("comparator_matrix")
        if not isinstance(matrix, dict):
            continue
        for row in matrix.get("rows", []) or []:
            if not isinstance(row, dict):
                continue
            issue_id = str(row.get("issue_id") or "")
            if issue_id and issue_id not in observed:
                observed.append(issue_id)
    return observed


def _comparator_matrix_coverage(case: QuestionCase, payload: dict[str, Any]) -> float | None:
    if not case.expected_comparator_issue_ids:
        return None
    observed = _observed_comparator_issue_ids(payload)
    matched = [issue_id for issue_id in case.expected_comparator_issue_ids if issue_id in observed]
    return _ratio(len(matched), len(case.expected_comparator_issue_ids))


def _dashboard_card_coverage(case: QuestionCase, payload: dict[str, Any]) -> float | None:
    if not case.expected_dashboard_cards:
        return None
    dashboard = payload.get("case_dashboard")
    if not isinstance(dashboard, dict):
        return 0.0
    cards = dashboard.get("cards")
    if not isinstance(cards, dict):
        return 0.0
    matched = 0
    for card_id in case.expected_dashboard_cards:
        rows = cards.get(card_id)
        if isinstance(rows, list) and any(isinstance(item, dict) for item in rows):
            matched += 1
    return _ratio(matched, len(case.expected_dashboard_cards))


def _actor_map_coverage(case: QuestionCase, payload: dict[str, Any]) -> float | None:
    if not case.expected_actor_ids:
        return None
    actor_map = payload.get("actor_map")
    if not isinstance(actor_map, dict):
        return 0.0
    observed = {
        str(actor.get("actor_id") or "")
        for actor in actor_map.get("actors", []) or []
        if isinstance(actor, dict) and str(actor.get("actor_id") or "")
    }
    matched = [actor_id for actor_id in case.expected_actor_ids if actor_id in observed]
    return _ratio(len(matched), len(case.expected_actor_ids))


def _checklist_group_coverage(case: QuestionCase, payload: dict[str, Any]) -> float | None:
    if not case.expected_checklist_group_ids:
        return None
    checklist = payload.get("document_request_checklist")
    if not isinstance(checklist, dict):
        return 0.0
    observed = {
        str(group.get("group_id") or "")
        for group in checklist.get("groups", []) or []
        if isinstance(group, dict) and str(group.get("group_id") or "")
    }
    matched = [group_id for group_id in case.expected_checklist_group_ids if group_id in observed]
    return _ratio(len(matched), len(case.expected_checklist_group_ids))


def _drafting_ceiling_match(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if not case.expected_draft_ceiling_level:
        return None
    drafting = payload.get("controlled_factual_drafting")
    if not isinstance(drafting, dict):
        return False
    preflight = drafting.get("framing_preflight")
    if not isinstance(preflight, dict):
        return False
    allegation_ceiling = preflight.get("allegation_ceiling")
    if not isinstance(allegation_ceiling, dict):
        return False
    return str(allegation_ceiling.get("ceiling_level") or "") == case.expected_draft_ceiling_level


def _draft_section_completeness(case: QuestionCase, payload: dict[str, Any]) -> bool | None:
    if not case.expected_draft_sections:
        return None
    drafting = payload.get("controlled_factual_drafting")
    if not isinstance(drafting, dict):
        return False
    draft = drafting.get("controlled_draft")
    if not isinstance(draft, dict):
        return False
    sections = draft.get("sections")
    if not isinstance(sections, dict):
        return False
    for section_id in case.expected_draft_sections:
        rows = sections.get(section_id)
        if not isinstance(rows, list) or not any(isinstance(item, dict) for item in rows):
            return False
    return True


def summarize_evaluation(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize evaluation outcomes across all scored cases."""
    buckets = Counter(result["bucket"] for result in results)

    def _metric_summary(metric: str) -> dict[str, int]:
        scorable = [result for result in results if result.get(metric) is not None]
        passed = [result for result in scorable if result.get(metric) is True]
        return {
            "scorable": len(scorable),
            "passed": len(passed),
            "failed": len(scorable) - len(passed),
        }

    return {
        "total_cases": len(results),
        "bucket_counts": dict(sorted(buckets.items())),
        "top_1_correctness": _metric_summary("top_1_correctness"),
        "support_uid_hit": _metric_summary("support_uid_hit"),
        "support_uid_hit_top_3": _metric_summary("support_uid_hit_top_3"),
        "support_uid_recall": _average_metric(results, "support_uid_recall"),
        "evidence_precision": _average_metric(results, "evidence_precision"),
        "top_uid_match": _metric_summary("top_uid_match"),
        "ambiguity_match": _metric_summary("ambiguity_match"),
        "confidence_calibration_match": _metric_summary("confidence_calibration_match"),
        "attachment_support_uid_hit": _metric_summary("attachment_support_uid_hit"),
        "attachment_answer_success": _metric_summary("attachment_answer_success"),
        "attachment_text_evidence_success": _metric_summary("attachment_text_evidence_success"),
        "attachment_ocr_text_evidence_success": _metric_summary("attachment_ocr_text_evidence_success"),
        "weak_evidence_explained": _metric_summary("weak_evidence_explained"),
        "quote_attribution_precision": _average_metric(results, "quote_attribution_precision"),
        "quote_attribution_coverage": _average_metric(results, "quote_attribution_coverage"),
        "thread_group_id_match": _metric_summary("thread_group_id_match"),
        "thread_group_source_match": _metric_summary("thread_group_source_match"),
        "long_thread_answer_present": _metric_summary("long_thread_answer_present"),
        "long_thread_structure_preserved": _metric_summary("long_thread_structure_preserved"),
        "case_bundle_present": _metric_summary("case_bundle_present"),
        "investigation_blocks_present": _metric_summary("investigation_blocks_present"),
        "case_bundle_support_uid_hit": _metric_summary("case_bundle_support_uid_hit"),
        "case_bundle_support_uid_recall": _average_metric(results, "case_bundle_support_uid_recall"),
        "multi_source_source_types_match": _metric_summary("multi_source_source_types_match"),
        "chronology_uid_hit": _metric_summary("chronology_uid_hit"),
        "chronology_uid_recall": _average_metric(results, "chronology_uid_recall"),
        "behavior_tag_coverage": _average_metric(results, "behavior_tag_coverage"),
        "behavior_tag_precision": _average_metric(results, "behavior_tag_precision"),
        "counter_indicator_quality": _average_metric(results, "counter_indicator_quality"),
        "overclaim_guard_match": _metric_summary("overclaim_guard_match"),
        "report_completeness": _metric_summary("report_completeness"),
        "legal_support_product_completeness": _metric_summary("legal_support_product_completeness"),
        "comparator_matrix_coverage": _average_metric(results, "comparator_matrix_coverage"),
        "dashboard_card_coverage": _average_metric(results, "dashboard_card_coverage"),
        "actor_map_coverage": _average_metric(results, "actor_map_coverage"),
        "checklist_group_coverage": _average_metric(results, "checklist_group_coverage"),
        "drafting_ceiling_match": _metric_summary("drafting_ceiling_match"),
        "draft_section_completeness": _metric_summary("draft_section_completeness"),
    }
