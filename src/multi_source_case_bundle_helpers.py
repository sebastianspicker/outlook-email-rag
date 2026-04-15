"""Helper families for multi-source case-bundle assembly."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Any, cast

from .attachment_extractor import (
    attachment_format_profile,
    extraction_quality_profile,
)

MULTI_SOURCE_CASE_BUNDLE_VERSION = "1"
_DECLARED_SOURCE_TYPES = (
    "email",
    "attachment",
    "meeting_note",
    "chat_log",
    "formal_document",
    "note_record",
    "time_record",
    "participation_record",
)
_FORMAL_DOCUMENT_EXTENSIONS = {".doc", ".docx", ".md", ".odt", ".pdf", ".rtf", ".txt"}
_FORMAL_DOCUMENT_MIME_MARKERS = (
    "application/pdf",
    "application/msword",
    "application/rtf",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
    "text/rtf",
)
_NOTE_RECORD_KEYWORDS = (
    "notes",
    "memo",
    "minutes",
    "meeting summary",
    "protokoll",
    "gedächtnisprotokoll",
    "gedaechtnisprotokoll",
    "aktennotiz",
)
_TIME_RECORD_KEYWORDS = (
    "timesheet",
    "time sheet",
    "time record",
    "attendance",
    "arbeitszeit",
    "arbeitszeitnachweis",
    "zeiterfassung",
    "stundennachweis",
)
_PARTICIPATION_RECORD_KEYWORDS = (
    "sbv",
    "schwerbehindertenvertretung",
    "personalrat",
    "betriebsrat",
    "mitbestimmung",
    "consultation",
    "beteiligung",
    "anhoerung",
    "anhörung",
)
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_DATE_RANGE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\s*(?:to|through|until|bis|–|-)\s*(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE)
_SHEET_NAME_RE = re.compile(r"\[Sheet:\s*([^\]]+)\]")
_MONTH_LABEL_RE = re.compile(
    r"(?i)\b("
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"januar|februar|märz|maerz|april|mai|juni|juli|august|september|oktober|november|dezember"
    r")\b"
)
_ICAL_FIELD_RE = re.compile(r"(?im)^(SUMMARY|DTSTART|DTEND|LOCATION|ORGANIZER|ATTENDEE)[^:\n]*:(.+)$")
_ICAL_DATETIME_RE = re.compile(r"\b(20\d{2})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?)?")


def _normalized_text(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _attachment_source_type(candidate: dict[str, Any], attachment: dict[str, Any]) -> str:
    explicit_hint = str(attachment.get("source_type_hint") or "").strip()
    if explicit_hint in _DECLARED_SOURCE_TYPES:
        return explicit_hint
    classification_text = " ".join(
        part
        for part in (
            _normalized_text(attachment.get("filename")),
            _normalized_text(candidate.get("subject")),
            _normalized_text(candidate.get("snippet")),
        )
        if part
    )
    if any(keyword in classification_text for keyword in _TIME_RECORD_KEYWORDS):
        return "time_record"
    if any(keyword in classification_text for keyword in _PARTICIPATION_RECORD_KEYWORDS):
        return "participation_record"
    if any(keyword in classification_text for keyword in _NOTE_RECORD_KEYWORDS):
        return "note_record"
    if _is_formal_document(attachment):
        return "formal_document"
    return "attachment"


def _attachment_document_kind(source_type: str) -> str:
    if source_type == "attachment":
        return "attachment"
    if source_type == "formal_document":
        return "attached_document"
    return f"attached_{source_type}"


def _attachment_reliability_basis_prefix(source_type: str) -> str:
    return source_type


def _source_review_recommendation(
    *,
    extraction_state: str,
    evidence_strength: str,
    ocr_used: bool,
    source_type: str,
    format_profile: dict[str, Any] | None = None,
    extraction_quality: dict[str, Any] | None = None,
) -> str:
    format_profile = format_profile if isinstance(format_profile, dict) else {}
    extraction_quality = extraction_quality if isinstance(extraction_quality, dict) else {}
    format_label = str(format_profile.get("format_label") or "source").strip()
    handling_mode = str(format_profile.get("handling_mode") or "").strip()
    support_level = str(format_profile.get("support_level") or "").strip()
    if support_level == "unsupported":
        return (
            f"{format_label} is not currently supported for reliable extraction; keep it as a visible reference "
            "and review the original file directly."
        )
    if evidence_strength == "strong_text" and extraction_state == "text_extracted":
        if handling_mode == "flattened_tabular_text":
            return f"{format_label} text is usable, but sheet structure and formulas were flattened during extraction."
        if handling_mode == "calendar_text_flattened":
            return (
                f"{format_label} text is usable, but richer calendar structure was flattened and should be checked "
                "against the original file when timing detail matters."
            )
        if source_type == "note_record":
            return "Extracted note text can support chronology, summary comparison, and follow-up directly."
        if source_type == "time_record":
            return "Extracted time-record text can support chronology and attendance follow-up directly."
        if source_type == "participation_record":
            return "Extracted participation-record text can support process and consultation follow-up directly."
        if source_type == "formal_document":
            return "Native extracted document text can support chronology and exhibit follow-up directly."
        return "Extracted attachment text can support downstream follow-up directly."
    if evidence_strength == "strong_text" and ocr_used:
        return "OCR-recovered text is usable, but the original page image should be checked before relying on fine wording."
    if extraction_state in {"ocr_failed", "extraction_failed", "binary_only", "image_embedding_only"}:
        return "Treat this source as a weak documentary reference until the original file is reviewed manually."
    if extraction_quality.get("manual_review_required"):
        return "Manual review is still required before treating this source as strong documentary proof."
    return "Review the original file before treating this source as strong documentary proof."


def _source_reliability_for_chat_log(chat_log: dict[str, Any]) -> dict[str, Any]:
    participants = [str(item) for item in chat_log.get("participants", []) if str(item).strip()]
    parsed_messages = [item for item in chat_log.get("parsed_messages", []) if isinstance(item, dict)]
    if participants and parsed_messages:
        return {
            "level": "medium",
            "basis": "native_chat_export_with_parsed_messages",
            "caveats": [
                (
                    "Chat evidence is normalized from export text and should be checked against the "
                    "original export when fine timing or threading detail matters."
                )
            ],
        }
    if participants:
        return {
            "level": "medium",
            "basis": "operator_supplied_chat_log_with_participants",
            "caveats": ["Chat-log evidence is operator supplied and is not yet normalized into the behavioral-analysis layers."],
        }
    return {
        "level": "low",
        "basis": "operator_supplied_chat_log_excerpt",
        "caveats": [
            "Chat-log evidence is operator supplied and lacks structured participant context.",
            "Chat-log evidence is not yet normalized into the behavioral-analysis layers.",
        ],
    }


def _is_formal_document(attachment: dict[str, Any]) -> bool:
    filename = str(attachment.get("filename") or "").strip()
    mime_type = str(attachment.get("mime_type") or "").strip().lower()
    if Path(filename).suffix.lower() in _FORMAL_DOCUMENT_EXTENSIONS:
        return True
    return any(marker in mime_type for marker in _FORMAL_DOCUMENT_MIME_MARKERS)


def _source_reliability_for_email(candidate: dict[str, Any]) -> dict[str, Any]:
    weak_message = candidate.get("weak_message")
    verification_status = str(candidate.get("verification_status") or "")
    if weak_message:
        return {
            "level": "medium",
            "basis": "weak_message_semantics",
            "caveats": ["Email body is available, but the message was already classified as weak evidence."],
        }
    if "forensic" in verification_status:
        return {"level": "high", "basis": "forensic_body_verification", "caveats": []}
    return {"level": "high", "basis": "authored_email_body", "caveats": []}


def _source_reliability_for_attachment(candidate: dict[str, Any], *, source_type: str) -> dict[str, Any]:
    attachment = cast(dict[str, Any], candidate.get("attachment")) if isinstance(candidate.get("attachment"), dict) else {}
    evidence_strength = str(attachment.get("evidence_strength") or "")
    extraction_state = str(attachment.get("extraction_state") or "")
    ocr_used = bool(attachment.get("ocr_used"))
    basis_prefix = _attachment_reliability_basis_prefix(source_type)
    if evidence_strength == "strong_text":
        basis = f"{basis_prefix}_text_extracted"
        level = "high"
        caveats: list[str] = []
        if extraction_state == "ocr_text_extracted" or ocr_used:
            basis = f"{basis_prefix}_ocr_text_extracted"
            level = "medium"
            caveats = ["Text was recovered via OCR and should be checked against the original page or file."]
        return {"level": level, "basis": basis, "caveats": caveats}
    if extraction_state in {"ocr_failed", "ocr_failure"}:
        return {
            "level": "low",
            "basis": f"{basis_prefix}_ocr_failed",
            "caveats": ["OCR failed, so this source currently acts only as a weak documentary reference."],
        }
    if extraction_state in {"binary_only", "image_embedding_only"}:
        return {
            "level": "low",
            "basis": f"{basis_prefix}_binary_only",
            "caveats": ["No extracted text is available, so the original file must be reviewed directly."],
        }
    return {
        "level": "low",
        "basis": extraction_state or f"{basis_prefix}_reference_only",
        "caveats": ["Attachment is represented as a reference hit without extracted strong-text evidence."],
    }


def _source_reliability_for_meeting(note: dict[str, Any]) -> dict[str, Any]:
    extracted_from = str(note.get("_extracted_from") or "")
    if extracted_from == "meeting_data":
        return {"level": "high", "basis": "calendar_meeting_metadata", "caveats": []}
    return {
        "level": "medium",
        "basis": "exchange_extracted_meeting_reference",
        "caveats": ["Meeting context was extracted from Exchange metadata rather than authored narrative text."],
    }


def _weighting_metadata(*, source_type: str, reliability_level: str, text_available: bool) -> dict[str, Any]:
    base_weight = 0.4
    if reliability_level == "medium":
        base_weight = 0.7
    elif reliability_level == "high":
        base_weight = 1.0
    return {
        "weight_label": reliability_level,
        "base_weight": base_weight,
        "text_available": text_available,
        "can_corroborate_or_contradict": text_available
        and source_type in {"email", "attachment", "formal_document", "note_record", "time_record", "participation_record"},
    }


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []


def _documentary_support_payload(candidate: dict[str, Any], *, source_type: str) -> dict[str, Any] | None:
    attachment = candidate.get("attachment") if isinstance(candidate.get("attachment"), dict) else {}
    if not attachment:
        return None
    explicit_payload = attachment.get("documentary_support")
    if isinstance(explicit_payload, dict) and explicit_payload:
        return {
            "filename": str(explicit_payload.get("filename") or attachment.get("filename") or ""),
            "mime_type": str(explicit_payload.get("mime_type") or attachment.get("mime_type") or ""),
            "text_available": bool(explicit_payload.get("text_available")),
            "evidence_strength": str(explicit_payload.get("evidence_strength") or attachment.get("evidence_strength") or ""),
            "extraction_state": str(explicit_payload.get("extraction_state") or attachment.get("extraction_state") or ""),
            "ocr_used": bool(explicit_payload.get("ocr_used") if "ocr_used" in explicit_payload else attachment.get("ocr_used")),
            "failure_reason": str(explicit_payload.get("failure_reason") or attachment.get("failure_reason") or ""),
            "text_preview": str(explicit_payload.get("text_preview") or attachment.get("text_preview") or ""),
            "format_profile": dict(explicit_payload.get("format_profile") or attachment.get("format_profile") or {}),
            "extraction_quality": dict(explicit_payload.get("extraction_quality") or attachment.get("extraction_quality") or {}),
            "review_recommendation": str(
                explicit_payload.get("review_recommendation") or attachment.get("review_recommendation") or ""
            ),
        }
    extraction_state = str(attachment.get("extraction_state") or "")
    evidence_strength = str(attachment.get("evidence_strength") or "")
    ocr_used = bool(attachment.get("ocr_used"))
    format_profile = dict(attachment.get("format_profile") or {})
    if not format_profile:
        format_profile = attachment_format_profile(
            filename=str(attachment.get("filename") or ""),
            mime_type=str(attachment.get("mime_type") or ""),
            extraction_state=extraction_state,
            evidence_strength=evidence_strength,
            ocr_used=ocr_used,
            text_available=bool(attachment.get("text_available")),
        )
    extraction_quality = dict(attachment.get("extraction_quality") or {})
    if not extraction_quality:
        extraction_quality = extraction_quality_profile(
            extraction_state=extraction_state,
            evidence_strength=evidence_strength,
            ocr_used=ocr_used,
            format_profile=format_profile,
        )
    return {
        "filename": str(attachment.get("filename") or ""),
        "mime_type": str(attachment.get("mime_type") or ""),
        "text_available": bool(attachment.get("text_available")),
        "evidence_strength": evidence_strength,
        "extraction_state": extraction_state,
        "ocr_used": ocr_used,
        "failure_reason": str(attachment.get("failure_reason") or ""),
        "text_preview": str(attachment.get("text_preview") or ""),
        "format_profile": format_profile,
        "extraction_quality": extraction_quality,
        "review_recommendation": str(attachment.get("review_recommendation") or "")
        or _source_review_recommendation(
            extraction_state=extraction_state,
            evidence_strength=evidence_strength,
            ocr_used=ocr_used,
            source_type=source_type,
            format_profile=format_profile,
            extraction_quality=extraction_quality,
        ),
    }


def _spreadsheet_semantics(source: dict[str, Any]) -> dict[str, Any] | None:
    explicit = source.get("spreadsheet_semantics")
    if isinstance(explicit, dict) and explicit:
        return explicit
    if str(source.get("source_type") or "") != "time_record":
        return None
    documentary = (
        cast(dict[str, Any], source.get("documentary_support")) if isinstance(source.get("documentary_support"), dict) else {}
    )
    format_profile = documentary.get("format_profile")
    if not isinstance(format_profile, dict) or str(format_profile.get("format_family") or "") != "spreadsheet":
        return None
    chronology_text = _chronology_text(source)
    explicit_dates = [match.group(1) for match in _ISO_DATE_RE.finditer(chronology_text)]
    date_range = _date_range_from_text(chronology_text)
    month_labels = sorted({match.group(1).lower() for match in _MONTH_LABEL_RE.finditer(chronology_text)})
    sheet_names = [match.group(1).strip() for match in _SHEET_NAME_RE.finditer(chronology_text) if match.group(1).strip()]
    lower_text = chronology_text.lower()
    record_type = "generic_time_record"
    if any(token in lower_text for token in ("novatime", "nova time")):
        record_type = "novatime_export"
    elif "attendance" in lower_text:
        record_type = "attendance_export"
    elif any(token in lower_text for token in ("arbeitszeit", "timesheet", "time sheet", "zeiterfassung")):
        record_type = "time_tracking_export"
    return {
        "record_type": record_type,
        "sheet_names": sheet_names,
        "sheet_count": len(sheet_names),
        "explicit_dates": list(dict.fromkeys(explicit_dates)),
        "date_range": date_range,
        "month_labels": month_labels,
        "date_signal_strength": "range" if date_range else "dates" if explicit_dates else "weak",
        "structure_signal": "sheeted" if sheet_names else "flattened_rows_only",
    }


def _document_locator(candidate: dict[str, Any]) -> dict[str, Any]:
    provenance = dict(candidate.get("provenance") or {})
    return {
        "evidence_handle": str(provenance.get("evidence_handle") or ""),
        "chunk_id": str(provenance.get("chunk_id") or ""),
        "snippet_start": provenance.get("snippet_start"),
        "snippet_end": provenance.get("snippet_end"),
        "page_hint": provenance.get("page"),
        "section_hint": provenance.get("section"),
    }


def _chronology_text(source: dict[str, Any]) -> str:
    documentary = (
        cast(dict[str, Any], source.get("documentary_support")) if isinstance(source.get("documentary_support"), dict) else {}
    )
    return " ".join(
        part
        for part in (str(source.get("title") or ""), str(source.get("snippet") or ""), str(documentary.get("text_preview") or ""))
        if part
    )


def _date_range_from_text(text: str) -> dict[str, str] | None:
    match = _DATE_RANGE_RE.search(text)
    if not match:
        return None
    start, end = match.group(1), match.group(2)
    if start > end:
        start, end = end, start
    return {"start": start, "end": end}


def _event_date_from_text(text: str) -> str:
    match = _ISO_DATE_RE.search(text)
    return match.group(1) if match else ""


def _ical_to_iso(value: str) -> str:
    compact = " ".join(str(value or "").split()).strip()
    match = _ICAL_DATETIME_RE.search(compact)
    if not match:
        return ""
    year, month, day, hour, minute, second = match.groups()
    if hour and minute:
        return f"{year}-{month}-{day}T{hour}:{minute}:{second or '00'}"
    return f"{year}-{month}-{day}"


def _calendar_semantics(source: dict[str, Any]) -> dict[str, Any] | None:
    explicit = source.get("calendar_semantics")
    if isinstance(explicit, dict) and explicit:
        return explicit
    documentary = (
        cast(dict[str, Any], source.get("documentary_support")) if isinstance(source.get("documentary_support"), dict) else {}
    )
    format_profile = documentary.get("format_profile")
    if not isinstance(format_profile, dict) or str(format_profile.get("format_family") or "") != "calendar":
        return None
    chronology_text = _chronology_text(source)
    field_map: dict[str, list[str]] = {}
    for match in _ICAL_FIELD_RE.finditer(chronology_text):
        key = match.group(1).upper()
        value = " ".join(str(match.group(2) or "").split()).strip()
        field_map.setdefault(key, [])
        if value and value not in field_map[key]:
            field_map[key].append(value)
    attendees = list(dict.fromkeys(field_map.get("ATTENDEE", [])))
    dtstart = _ical_to_iso(field_map.get("DTSTART", [""])[0]) if field_map.get("DTSTART") else ""
    dtend = _ical_to_iso(field_map.get("DTEND", [""])[0]) if field_map.get("DTEND") else ""
    return {
        "calendar_summary": field_map.get("SUMMARY", [""])[0] if field_map.get("SUMMARY") else "",
        "dtstart": dtstart,
        "dtend": dtend,
        "location": field_map.get("LOCATION", [""])[0] if field_map.get("LOCATION") else "",
        "organizer": field_map.get("ORGANIZER", [""])[0] if field_map.get("ORGANIZER") else "",
        "attendees": attendees,
        "attendee_count": len(attendees),
        "field_count": sum(len(values) for values in field_map.values()),
    }


def _meeting_event_date(source: dict[str, Any]) -> str:
    provenance = cast(dict[str, Any], source.get("provenance")) if isinstance(source.get("provenance"), dict) else {}
    if str(provenance.get("meeting_source") or "") == "meeting_data":
        text = str(source.get("snippet") or "")
        for key in ("OPFMeetingStartDate", "startTime", "start"):
            marker = f"{key}="
            if marker in text:
                value = text.split(marker, 1)[1].split(";", 1)[0].strip()
                if value:
                    return value
    return ""


def _chronology_anchor_for_source(source: dict[str, Any]) -> dict[str, Any] | None:
    source_type = str(source.get("source_type") or "")
    source_date = str(source.get("date") or "").strip()
    chronology_text = _chronology_text(source)
    event_date = source_date
    date_origin = "source_timestamp"
    date_range = None
    spreadsheet = _spreadsheet_semantics(source)
    if source_type == "meeting_note":
        meeting_date = _meeting_event_date(source)
        if meeting_date:
            event_date = meeting_date
            date_origin = "meeting_metadata"
    calendar = _calendar_semantics(source)
    if calendar and str(calendar.get("dtstart") or ""):
        event_date = str(calendar.get("dtstart") or "")
        date_origin = "calendar_dtstart"
    if source_type in {"formal_document", "note_record", "time_record", "participation_record", "attachment"}:
        explicit_range = (
            cast(dict[str, Any], spreadsheet.get("date_range"))
            if source_type == "time_record" and isinstance(spreadsheet, dict) and isinstance(spreadsheet.get("date_range"), dict)
            else None
        )
        detected_range = explicit_range or _date_range_from_text(chronology_text)
        if detected_range is not None and source_type == "time_record":
            date_range = {"start": str(detected_range.get("start") or ""), "end": str(detected_range.get("end") or "")}
            event_date = date_range["start"]
            date_origin = "time_record_range_start"
        else:
            document_date = _event_date_from_text(chronology_text)
            if document_date:
                event_date = document_date
                date_origin = "document_text"
    if not event_date:
        return None
    anchor: dict[str, Any] = {
        "source_id": str(source.get("source_id") or ""),
        "source_type": source_type,
        "document_kind": str(source.get("document_kind") or ""),
        "date": event_date,
        "title": str(source.get("title") or ""),
        "reliability_level": str((source.get("source_reliability") or {}).get("level") or ""),
        "date_origin": date_origin,
    }
    if date_range is not None:
        anchor["date_range"] = date_range
    if source_date and source_date != event_date:
        anchor["source_recorded_date"] = source_date
    return anchor


def _meeting_note_sources(uid: str, full_email: dict[str, Any] | None) -> list[dict[str, Any]]:
    email = full_email or {}
    sources: list[dict[str, Any]] = []
    meeting_data = email.get("meeting_data")
    if isinstance(meeting_data, dict) and meeting_data:
        note = {
            "source_id": f"meeting:{uid}:meeting_data",
            "source_type": "meeting_note",
            "document_kind": "calendar_metadata",
            "uid": uid,
            "parent_source_id": f"email:{uid}",
            "title": str(email.get("subject") or meeting_data.get("subject") or ""),
            "snippet": "; ".join(f"{key}={value}" for key, value in sorted(meeting_data.items())[:3]),
            "date": str(email.get("date") or ""),
            "provenance": {"uid": uid, "meeting_source": "meeting_data"},
            "_extracted_from": "meeting_data",
        }
        reliability = _source_reliability_for_meeting(note)
        note["source_reliability"] = reliability
        note["source_weighting"] = _weighting_metadata(
            source_type="meeting_note", reliability_level=str(reliability["level"]), text_available=True
        )
        sources.append(note)
    exchange_meetings = email.get("exchange_extracted_meetings")
    if isinstance(exchange_meetings, list):
        for index, meeting in enumerate(exchange_meetings, start=1):
            if not isinstance(meeting, dict) or not meeting:
                continue
            note = {
                "source_id": f"meeting:{uid}:exchange:{index}",
                "source_type": "meeting_note",
                "document_kind": "exchange_meeting_reference",
                "uid": uid,
                "parent_source_id": f"email:{uid}",
                "title": str(meeting.get("subject") or email.get("subject") or ""),
                "snippet": "; ".join(f"{key}={value}" for key, value in sorted(meeting.items())[:3]),
                "date": str(email.get("date") or ""),
                "provenance": {"uid": uid, "meeting_source": "exchange_extracted_meetings", "index": index},
                "_extracted_from": "exchange_extracted_meetings",
            }
            reliability = _source_reliability_for_meeting(note)
            note["source_reliability"] = reliability
            note["source_weighting"] = _weighting_metadata(
                source_type="meeting_note", reliability_level=str(reliability["level"]), text_available=True
            )
            sources.append(note)
    return sources


def _chat_log_sources(
    chat_log_entries: list[dict[str, Any]] | None,
    *,
    email_source_ids_by_uid: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    chat_sources: list[dict[str, Any]] = []
    chat_links: list[dict[str, Any]] = []
    chat_counts: Counter[str] = Counter()
    for index, entry in enumerate(chat_log_entries or [], start=1):
        if not isinstance(entry, dict):
            continue
        source_id = str(entry.get("source_id") or f"chat:{index}")
        parsed_messages = [item for item in entry.get("parsed_messages", []) if isinstance(item, dict)]
        message_count = int(entry.get("chat_message_count") or entry.get("message_count") or len(parsed_messages) or 0)
        reliability = _source_reliability_for_chat_log(entry)
        source = {
            "source_id": source_id,
            "source_type": "chat_log",
            "document_kind": "operator_chat_log",
            "uid": str(entry.get("uid") or entry.get("related_email_uid") or ""),
            "title": str(entry.get("title") or "Chat export"),
            "date": str(entry.get("date") or ""),
            "snippet": str(entry.get("snippet") or entry.get("text") or ""),
            "participants": _string_list(entry.get("participants")),
            "parsed_messages": parsed_messages,
            "chat_message_units": parsed_messages,
            "message_count": message_count,
            "chat_message_count": message_count,
            "provenance": dict(entry.get("provenance") or {}),
            "source_reliability": reliability,
            "source_weighting": _weighting_metadata(
                source_type="chat_log",
                reliability_level=str(reliability["level"]),
                text_available=bool(str(entry.get("snippet") or entry.get("text") or "").strip()),
            ),
        }
        chronology_anchor = _chronology_anchor_for_source(source)
        if chronology_anchor is not None:
            source["chronology_anchor"] = chronology_anchor
        chat_sources.append(source)
        chat_counts["chat_log"] += 1
        uid = str(source.get("uid") or "")
        if uid and uid in email_source_ids_by_uid:
            chat_links.append(
                {
                    "from_source_id": source_id,
                    "to_source_id": email_source_ids_by_uid[uid],
                    "link_type": "related_to_email",
                    "relationship": "operator_supplied_parallel_record",
                }
            )
    return chat_sources, chat_links, chat_counts
