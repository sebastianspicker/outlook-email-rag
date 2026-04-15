"""Matter-manifest normalization and completeness-ledger helpers."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from .attachment_extractor import attachment_format_profile, extraction_quality_profile

MATTER_INGESTION_REPORT_VERSION = "1"
_MANIFEST_SUBJECT_RE = re.compile(r"(?im)^subject:\s*(.+)$")
_MANIFEST_FROM_RE = re.compile(r"(?im)^from:\s*(.+)$")
_MANIFEST_TO_RE = re.compile(r"(?im)^to:\s*(.+)$")
_MANIFEST_CC_RE = re.compile(r"(?im)^cc:\s*(.+)$")
_MANIFEST_BCC_RE = re.compile(r"(?im)^bcc:\s*(.+)$")
_MANIFEST_DATE_RE = re.compile(r"(?im)^date:\s*([^\n\r]+)$")
_MANIFEST_HEADING_RE = re.compile(r"(?m)^#\s+(.+)$")
_PARTY_WITH_EMAIL_RE = re.compile(r"[^<>\n]+<[^>]+>")

_SOURCE_CLASS_TO_TYPE: dict[str, tuple[str, str]] = {
    "email": ("email", "email_body"),
    "attachment": ("attachment", "attachment"),
    "formal_document": ("formal_document", "attached_document"),
    "personnel_file_record": ("formal_document", "personnel_file_record"),
    "job_evaluation_record": ("formal_document", "job_evaluation_record"),
    "prevention_record": ("formal_document", "prevention_record"),
    "medical_record": ("formal_document", "medical_record"),
    "meeting_note": ("meeting_note", "meeting_note"),
    "calendar_export": ("meeting_note", "calendar_export"),
    "note_record": ("note_record", "attached_note_record"),
    "time_record": ("time_record", "attached_time_record"),
    "attendance_export": ("time_record", "attendance_export"),
    "participation_record": ("participation_record", "attached_participation_record"),
    "chat_log": ("chat_log", "operator_supplied_chat_log"),
    "chat_export": ("chat_log", "chat_export"),
    "archive_bundle": ("attachment", "archive_bundle"),
    "screenshot": ("attachment", "image_attachment"),
    "other": ("attachment", "attachment"),
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _compact_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _preview(text: str, *, max_chars: int = 280) -> str:
    compact = _compact_text(text)
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _split_party_list(value: str) -> list[str]:
    compact_value = _compact_text(value)
    if not compact_value:
        return []
    matches = [_compact_text(match.group(0).lstrip(", ")) for match in _PARTY_WITH_EMAIL_RE.finditer(compact_value)]
    if matches:
        return [match for match in matches if match]
    parts = re.split(r",\s*(?=[^,]+@)", compact_value)
    return [item for item in (_compact_text(part) for part in parts) if item]


def _manifest_text_metadata(artifact: dict[str, Any], *, title: str, text: str) -> dict[str, Any]:
    if _compact_text(artifact.get("source_class")) != "formal_document" or not text:
        return {}
    heading_match = _MANIFEST_HEADING_RE.search(text)
    subject_match = _MANIFEST_SUBJECT_RE.search(text)
    from_match = _MANIFEST_FROM_RE.search(text)
    to_match = _MANIFEST_TO_RE.search(text)
    cc_match = _MANIFEST_CC_RE.search(text)
    bcc_match = _MANIFEST_BCC_RE.search(text)
    date_match = _MANIFEST_DATE_RE.search(text)
    filename = _compact_text(artifact.get("filename"))
    inferred_title = _compact_text(subject_match.group(1) if subject_match else "")
    if not inferred_title:
        heading = _compact_text(heading_match.group(1) if heading_match else "")
        if heading and heading != filename:
            inferred_title = heading
    return {
        "title": inferred_title or title,
        "author": _compact_text(from_match.group(1) if from_match else ""),
        "recipients": _split_party_list(to_match.group(1) if to_match else ""),
        "cc_recipients": _split_party_list(cc_match.group(1) if cc_match else ""),
        "bcc_recipients": _split_party_list(bcc_match.group(1) if bcc_match else ""),
        "date": _compact_text(date_match.group(1) if date_match else ""),
    }


def _line_number_for_offset(text: str, offset: int) -> int:
    return text[: max(offset, 0)].count("\n") + 1


def _snippet_locator(*, text: str, snippet: str, text_locator: dict[str, Any]) -> dict[str, Any]:
    compact_snippet = _compact_text(snippet)
    if not compact_snippet or not isinstance(text_locator, dict):
        return {}
    raw_text = str(text or "")
    if not raw_text:
        return {}
    start = raw_text.find(compact_snippet)
    if start < 0:
        return {}
    end = start + len(compact_snippet)
    locator = {
        "kind": "quoted_snippet",
        "char_start": int(text_locator.get("char_start") or 0) + start,
        "char_end": int(text_locator.get("char_start") or 0) + end,
        "line_start": _line_number_for_offset(raw_text, start),
        "line_end": _line_number_for_offset(raw_text, end),
    }
    if text_locator.get("source_path"):
        locator["source_path"] = text_locator.get("source_path")
    if text_locator.get("content_sha256"):
        locator["content_sha256"] = text_locator.get("content_sha256")
    return locator


def normalized_source_mapping(source_class: str) -> tuple[str, str]:
    """Return normalized downstream source typing for one manifest source class."""
    return _SOURCE_CLASS_TO_TYPE.get(str(source_class or "").strip(), ("attachment", "attachment"))


def source_review_status(artifact: dict[str, Any]) -> str:
    """Return the effective review status for one manifest artifact."""
    review_status = _compact_text(artifact.get("review_status"))
    if review_status:
        return review_status
    extraction_state = _compact_text(artifact.get("extraction_state")).lower()
    evidence_strength = _compact_text(artifact.get("evidence_strength")).lower()
    if extraction_state in {"excluded"}:
        return "excluded"
    if extraction_state in {"not_reviewed", "not_yet_reviewed"}:
        return "not_yet_reviewed"
    if extraction_state in {
        "binary_only",
        "image_embedding_only",
        "ocr_failed",
        "extraction_failed",
        "archive_inventory_extracted",
        "sidecar_text_extracted",
    }:
        return "degraded"
    if evidence_strength == "weak_reference":
        return "degraded"
    if extraction_state in {"unsupported"}:
        return "unsupported"
    return "parsed"


def documentary_support_for_manifest_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    """Return documentary support metadata for one manifest artifact."""
    filename = _compact_text(artifact.get("filename"))
    mime_type = _compact_text(artifact.get("mime_type")).lower()
    extraction_state = _compact_text(artifact.get("extraction_state")) or (
        "text_extracted" if _compact_text(artifact.get("text")) else "not_reviewed"
    )
    evidence_strength = _compact_text(artifact.get("evidence_strength")) or (
        "strong_text" if _compact_text(artifact.get("text")) else "weak_reference"
    )
    ocr_used = bool(artifact.get("ocr_used"))
    format_profile = attachment_format_profile(
        filename=filename,
        mime_type=mime_type,
        extraction_state=extraction_state,
        evidence_strength=evidence_strength,
        ocr_used=ocr_used,
        text_available=bool(_compact_text(artifact.get("text"))),
    )
    extraction_quality = extraction_quality_profile(
        extraction_state=extraction_state,
        evidence_strength=evidence_strength,
        ocr_used=ocr_used,
        format_profile=format_profile,
    )
    return {
        "filename": filename,
        "mime_type": mime_type,
        "text_available": bool(_compact_text(artifact.get("text"))),
        "evidence_strength": evidence_strength,
        "extraction_state": extraction_state,
        "ocr_used": ocr_used,
        "failure_reason": _compact_text(artifact.get("failure_reason") or artifact.get("excluded_reason")),
        "text_preview": _preview(str(artifact.get("text") or "")),
        "format_profile": format_profile,
        "extraction_quality": extraction_quality,
    }


def source_from_manifest_artifact(artifact: dict[str, Any], *, index: int) -> dict[str, Any]:
    """Return one normalized mixed-source entry for a manifest artifact."""
    source_class = _compact_text(artifact.get("source_class")) or "other"
    source_type, document_kind = normalized_source_mapping(source_class)
    documentary_support = documentary_support_for_manifest_artifact(artifact)
    review_status = source_review_status(artifact)
    reliability_level = "high"
    if review_status in {"degraded"} or bool(documentary_support.get("ocr_used")):
        reliability_level = "medium"
    if (
        review_status in {"unsupported", "excluded", "not_yet_reviewed"}
        or str(documentary_support.get("evidence_strength") or "") == "weak_reference"
    ):
        reliability_level = "low"
    source_id = _compact_text(artifact.get("source_id")) or f"manifest:{index}:{source_class}"
    title = _compact_text(artifact.get("title")) or _compact_text(artifact.get("filename")) or source_id
    raw_text = str(artifact.get("text") or "")
    text = _compact_text(raw_text)
    summary = _compact_text(artifact.get("summary"))
    text_metadata = _manifest_text_metadata(artifact, title=title, text=raw_text)
    title = _compact_text(text_metadata.get("title")) or title
    author = _compact_text(artifact.get("author")) or _compact_text(text_metadata.get("author"))
    recipients = [str(item) for item in _as_list(artifact.get("recipients")) if _compact_text(item)] or [
        str(item) for item in _as_list(text_metadata.get("recipients")) if _compact_text(item)
    ]
    cc_recipients = [str(item) for item in _as_list(artifact.get("cc_recipients")) if _compact_text(item)] or [
        str(item) for item in _as_list(text_metadata.get("cc_recipients")) if _compact_text(item)
    ]
    bcc_recipients = [str(item) for item in _as_list(artifact.get("bcc_recipients")) if _compact_text(item)] or [
        str(item) for item in _as_list(text_metadata.get("bcc_recipients")) if _compact_text(item)
    ]
    participants = [str(item) for item in _as_list(artifact.get("participants")) if _compact_text(item)]
    date_value = _compact_text(artifact.get("date")) or _compact_text(text_metadata.get("date"))
    date_start = _compact_text(artifact.get("date_start"))
    date_end = _compact_text(artifact.get("date_end"))
    date_is_approximate = bool(artifact.get("date_is_approximate"))
    text_source_path = _compact_text(artifact.get("text_source_path"))
    text_locator = _as_dict(artifact.get("text_locator"))
    snippet_locator = _snippet_locator(text=text, snippet=text, text_locator=text_locator) if text else {}
    source: dict[str, Any] = {
        "source_id": source_id,
        "source_type": source_type,
        "source_class": source_class,
        "document_kind": document_kind,
        "uid": _compact_text(artifact.get("related_email_uid")),
        "title": title,
        "date": date_value,
        "snippet": text,
        "operator_summary": summary,
        "author": author,
        "recipients": recipients,
        "cc_recipients": cc_recipients,
        "bcc_recipients": bcc_recipients,
        "participants": participants,
        "source_roles": {
            "author": author,
            "recipients": recipients,
            "cc_recipients": cc_recipients,
            "bcc_recipients": bcc_recipients,
            "participants": participants,
        },
        "date_context": {
            "display_date": date_value,
            "date_start": date_start,
            "date_end": date_end,
            "is_approximate": date_is_approximate,
            "has_range": bool(date_start and date_end),
        },
        "provenance": {
            "source_kind": "matter_manifest",
            "custodian": _compact_text(artifact.get("custodian")),
            "acquisition_date": _compact_text(artifact.get("acquisition_date")),
            "filename": _compact_text(artifact.get("filename")),
            "source_path": _compact_text(artifact.get("source_path")),
            "content_sha256": _compact_text(artifact.get("content_sha256")),
            "file_size_bytes": int(artifact.get("file_size_bytes") or 0),
            "related_email_uid": _compact_text(artifact.get("related_email_uid")),
        },
        "document_locator": {
            "evidence_handle": source_id,
            "source_path": _compact_text(artifact.get("source_path")),
            "filename": _compact_text(artifact.get("filename")),
            "content_sha256": _compact_text(artifact.get("content_sha256")),
            "text_source_path": text_source_path,
            "text_locator": text_locator,
            "snippet_locator": snippet_locator,
        },
        "documentary_support": documentary_support,
        "source_reliability": {
            "level": reliability_level,
            "basis": f"matter_manifest_{review_status or 'parsed'}",
            "caveats": ["This source entered through the operator-supplied matter manifest."]
            if source_class in {"chat_log", "chat_export"}
            else [],
        },
        "source_weighting": {
            "weight_label": reliability_level,
            "base_weight": 1.0 if reliability_level == "high" else 0.7 if reliability_level == "medium" else 0.4,
            "text_available": bool(text),
            "can_corroborate_or_contradict": bool(text)
            and source_type in {"email", "attachment", "formal_document", "note_record", "time_record", "participation_record"},
        },
        "source_manifest_entry": {
            "artifact_id": source_id,
            "review_status": review_status,
            "custodian": _compact_text(artifact.get("custodian")),
            "expected_collection": _compact_text(artifact.get("expected_collection")),
        },
    }
    weak_format_semantics = _as_dict(artifact.get("weak_format_semantics"))
    if weak_format_semantics:
        source["weak_format_semantics"] = weak_format_semantics
    return source


def build_matter_ingestion_report(
    *,
    review_mode: str,
    matter_manifest: dict[str, Any] | None,
    multi_source_case_bundle: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a completeness ledger for the supplied matter manifest."""
    manifest = _as_dict(matter_manifest)
    artifacts = [item for item in _as_list(manifest.get("artifacts")) if isinstance(item, dict)]
    sources = {
        str(source.get("source_id") or "")
        for source in _as_list(_as_dict(multi_source_case_bundle).get("sources"))
        if isinstance(source, dict) and str(source.get("source_id") or "")
    }
    review_status_counts: Counter[str] = Counter()
    source_class_counts: Counter[str] = Counter()
    custodian_counts: Counter[str] = Counter()
    artifact_rows: list[dict[str, Any]] = []
    for index, artifact in enumerate(artifacts, start=1):
        source_class = _compact_text(artifact.get("source_class")) or "other"
        source_id = _compact_text(artifact.get("source_id")) or f"manifest:{index}:{source_class}"
        review_status = source_review_status(artifact)
        review_status_counts[review_status] += 1
        source_class_counts[source_class] += 1
        custodian = _compact_text(artifact.get("custodian"))
        if custodian:
            custodian_counts[custodian] += 1
        documentary_support = documentary_support_for_manifest_artifact(artifact)
        artifact_rows.append(
            {
                "artifact_id": source_id,
                "source_id": source_id,
                "title": _compact_text(artifact.get("title")) or _compact_text(artifact.get("filename")) or source_id,
                "source_class": source_class,
                "normalized_source_type": normalized_source_mapping(source_class)[0],
                "date": _compact_text(artifact.get("date")),
                "custodian": custodian,
                "review_status": review_status,
                "accounting_status": "included_in_case_bundle" if source_id in sources else "not_in_case_bundle",
                "extraction_state": str(documentary_support.get("extraction_state") or ""),
                "evidence_strength": str(documentary_support.get("evidence_strength") or ""),
                "format_support_level": str(_as_dict(documentary_support.get("format_profile")).get("support_level") or ""),
                "related_email_uid": _compact_text(artifact.get("related_email_uid")),
                "source_path": _compact_text(artifact.get("source_path")),
                "file_size_bytes": int(artifact.get("file_size_bytes") or 0),
                "content_sha256": _compact_text(artifact.get("content_sha256")),
                "excluded_reason": _compact_text(artifact.get("excluded_reason")),
            }
        )
    summary = {
        "total_supplied_artifacts": len(artifacts),
        "parsed_artifacts": int(review_status_counts.get("parsed", 0)),
        "degraded_artifacts": int(review_status_counts.get("degraded", 0)),
        "unsupported_artifacts": int(review_status_counts.get("unsupported", 0)),
        "excluded_artifacts": int(review_status_counts.get("excluded", 0)),
        "not_yet_reviewed_artifacts": int(review_status_counts.get("not_yet_reviewed", 0)),
        "accounted_artifacts": sum(1 for row in artifact_rows if row["accounting_status"] == "included_in_case_bundle"),
        "unaccounted_artifacts": sum(1 for row in artifact_rows if row["accounting_status"] != "included_in_case_bundle"),
        "source_class_counts": dict(source_class_counts),
        "custodian_counts": dict(custodian_counts),
        "review_status_counts": dict(review_status_counts),
    }
    if review_mode == "retrieval_only" and not artifacts:
        completeness_status = "retrieval_only_no_manifest"
    elif summary["unaccounted_artifacts"] == 0:
        completeness_status = "complete"
    else:
        completeness_status = "incomplete"
    return {
        "version": MATTER_INGESTION_REPORT_VERSION,
        "review_mode": review_mode,
        "manifest_id": _compact_text(manifest.get("manifest_id")),
        "summary": summary,
        "completeness_status": completeness_status,
        "is_exhaustive_review": review_mode == "exhaustive_matter_review",
        "artifacts": artifact_rows,
    }
