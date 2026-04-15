"""Shared chronology helper primitives and entry builders."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from .behavioral_taxonomy import issue_track_to_tag_ids, normalize_issue_tag_ids, text_to_issue_tag_ids

MASTER_CHRONOLOGY_VERSION = "1"
_EVENT_READ_IDS = (
    "disability_disadvantage",
    "retaliation_after_protected_event",
    "eingruppierung_dispute",
    "prevention_duty_gap",
    "participation_duty_gap",
    "ordinary_managerial_explanation",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _date_precision(date_value: str) -> str:
    """Return a conservative precision label for one chronology date."""
    value = str(date_value or "").strip()
    if not value:
        return "unknown"
    if len(value) == 4 and value[:4].isdigit():
        return "year"
    if len(value) == 7 and value[4] == "-" and value[:4].isdigit() and value[5:7].isdigit():
        return "month"
    if len(value) >= 10 and value[4] == "-" and value[7] == "-" and value[:4].isdigit():
        if "T" not in value:
            return "day"
        time_part = value.split("T", 1)[1]
        if len(time_part) >= 8 and time_part[2] == ":" and time_part[5] == ":":
            return "second"
        if len(time_part) >= 5 and time_part[2] == ":":
            return "minute"
        return "day"
    return "unknown"


def _date_only(value: str) -> date | None:
    """Return date-only precision for chronology-gap detection."""
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    date_part = text[:10]
    try:
        return date.fromisoformat(date_part)
    except ValueError:
        return None


def _citation_ids_by_uid(finding_evidence_index: dict[str, Any]) -> dict[str, list[str]]:
    """Return citation ids grouped by supporting uid."""
    by_uid: dict[str, list[str]] = {}
    for finding in _as_list(finding_evidence_index.get("findings")):
        if not isinstance(finding, dict):
            continue
        for citation in _as_list(finding.get("supporting_evidence")):
            if not isinstance(citation, dict):
                continue
            uid = str(citation.get("message_or_document_id") or "")
            citation_id = str(citation.get("citation_id") or "")
            if not uid or not citation_id:
                continue
            by_uid.setdefault(uid, [])
            if citation_id not in by_uid[uid]:
                by_uid[uid].append(citation_id)
    return by_uid


def _source_lookup(multi_source_case_bundle: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(source.get("source_id") or ""): source
        for source in _as_list(multi_source_case_bundle.get("sources"))
        if isinstance(source, dict) and str(source.get("source_id") or "")
    }


def _scope_context(case_bundle: dict[str, Any]) -> dict[str, Any]:
    scope = _as_dict(case_bundle.get("scope"))
    return {
        "employment_issue_tracks": [str(item) for item in _as_list(scope.get("employment_issue_tracks")) if str(item).strip()],
        "employment_issue_tags": normalize_issue_tag_ids(
            [str(item) for item in _as_list(scope.get("employment_issue_tags")) if str(item).strip()]
        ),
        "context_text": " ".join(str(scope.get("context_notes") or "").lower().split()),
        "has_trigger_event": bool(_as_list(scope.get("trigger_events"))),
        "has_vulnerability_context": any(
            str(_as_dict(item).get("context_type") or "") in {"disability", "illness"}
            for item in _as_list(_as_dict(scope.get("org_context")).get("vulnerability_contexts"))
        ),
    }


def _matrix_item(
    *,
    read_id: str,
    status: str,
    reason: str,
    linked_issue_tags: Sequence[str],
    selected_in_case_scope: bool,
) -> dict[str, Any]:
    return {
        "read_id": read_id,
        "status": status,
        "reason": reason,
        "linked_issue_tags": list(linked_issue_tags),
        "selected_in_case_scope": selected_in_case_scope,
    }


def _event_support_matrix(
    *,
    case_bundle: dict[str, Any],
    entry_type: str,
    title: str,
    description: str,
) -> dict[str, Any]:
    """Return neutral per-event timeline reads for one chronology entry."""
    scope = _scope_context(case_bundle)
    event_text = " ".join(part for part in [title, description] if part)
    direct_tags = normalize_issue_tag_ids(text_to_issue_tag_ids(event_text))
    selected_tracks = set(scope["employment_issue_tracks"])

    def _read_status(track_id: str, *, direct_tag_hits: Sequence[str], context_support: bool) -> tuple[str, str]:
        if direct_tag_hits:
            return (
                "direct_event_support",
                "Current event text directly contains issue-linked terms for this timeline read.",
            )
        if context_support:
            return (
                "contextual_support_only",
                "This event fits the selected case context, but the current event text does not directly prove the read.",
            )
        return (
            "not_supported_by_current_event",
            "The current event does not directly support this timeline read on its own.",
        )

    matrix: dict[str, Any] = {}
    for track_id in (
        "disability_disadvantage",
        "retaliation_after_protected_event",
        "eingruppierung_dispute",
        "prevention_duty_gap",
        "participation_duty_gap",
    ):
        track_tags = issue_track_to_tag_ids(track_id, context_text=scope["context_text"])
        direct_tag_hits = [tag for tag in track_tags if tag in direct_tags]
        context_support = track_id in selected_tracks
        if track_id == "retaliation_after_protected_event" and entry_type == "trigger_event" and scope["has_trigger_event"]:
            context_support = True
            if not direct_tag_hits:
                direct_tag_hits = ["retaliation_massregelung"]
        if track_id in {"disability_disadvantage", "prevention_duty_gap"} and scope["has_vulnerability_context"]:
            context_support = True
        status, reason = _read_status(track_id, direct_tag_hits=direct_tag_hits, context_support=context_support)
        matrix[track_id] = _matrix_item(
            read_id=track_id,
            status=status,
            reason=reason,
            linked_issue_tags=direct_tag_hits or list(track_tags),
            selected_in_case_scope=track_id in selected_tracks,
        )

    managerial_keywords = ("policy", "process", "meeting", "approval", "status", "workflow", "schedule")
    managerial_hit = any(keyword in event_text.lower() for keyword in managerial_keywords) or entry_type in {
        "timeline_event",
        "source_event",
    }
    managerial_status = "plausible_alternative" if managerial_hit else "not_obvious_from_current_event"
    managerial_reason = (
        "The current event can still fit an ordinary managerial, workflow, or process explanation."
        if managerial_hit
        else "The current event does not obviously suggest an ordinary managerial explanation by itself."
    )
    matrix["ordinary_managerial_explanation"] = _matrix_item(
        read_id="ordinary_managerial_explanation",
        status=managerial_status,
        reason=managerial_reason,
        linked_issue_tags=[],
        selected_in_case_scope=False,
    )
    return matrix


def _source_entry(
    anchor: dict[str, Any],
    source: dict[str, Any],
    *,
    case_bundle: dict[str, Any],
    citation_ids_by_uid: dict[str, list[str]],
) -> dict[str, Any]:
    """Return one chronology entry derived from a mixed-source anchor."""
    uid = str(source.get("uid") or "")
    provenance = _as_dict(source.get("provenance"))
    locator = _as_dict(source.get("document_locator"))
    source_type = str(anchor.get("source_type") or source.get("source_type") or "")
    title = str(anchor.get("title") or source.get("title") or source_type or "Source event")
    entry_date = str(anchor.get("date") or source.get("date") or "")
    date_origin = str(anchor.get("date_origin") or "source_timestamp")
    date_range = _as_dict(anchor.get("date_range"))
    description_bits = [part for part in [title, str(source.get("snippet") or "").strip()] if part]
    people_involved = [
        str(item)
        for item in dict.fromkeys(
            [
                str(source.get("sender_name") or "").strip(),
                str(source.get("author") or "").strip(),
                *[str(item).strip() for item in _as_list(source.get("participants"))],
                *[str(item).strip() for item in _as_list(source.get("to"))],
                *[str(item).strip() for item in _as_list(source.get("cc"))],
                *[str(item).strip() for item in _as_list(source.get("bcc"))],
                *[str(item).strip() for item in _as_list(source.get("recipients"))],
                *[str(item).strip() for item in _as_list(source.get("cc_recipients"))],
                *[str(item).strip() for item in _as_list(source.get("bcc_recipients"))],
            ]
        )
        if str(item).strip()
    ]
    entry = {
        "date": entry_date,
        "date_precision": _date_precision(entry_date),
        "date_origin": date_origin,
        "entry_type": "source_event",
        "provenance_class": "source_derived",
        "title": title,
        "description": ": ".join(description_bits[:2])[:220],
        "people_involved": people_involved[:8],
        "source_document": {
            "title": title,
            "source_id": str(anchor.get("source_id") or source.get("source_id") or ""),
            "source_type": source_type,
        },
        "event_support_matrix": _event_support_matrix(
            case_bundle=case_bundle,
            entry_type="source_event",
            title=title,
            description=": ".join(description_bits[:2])[:220],
        ),
        "source_linkage": {
            "source_ids": [str(anchor.get("source_id") or source.get("source_id") or "")],
            "source_types": [source_type] if source_type else [],
            "supporting_uids": [uid] if uid else [],
            "supporting_citation_ids": citation_ids_by_uid.get(uid, [])[:4],
            "evidence_handles": [str(provenance.get("evidence_handle") or "")] if provenance.get("evidence_handle") else [],
            "document_locators": [locator] if locator else [],
            "source_evidence_status": "linked_record",
        },
    }
    if date_range:
        entry["coverage_window"] = {
            "start": str(date_range.get("start") or ""),
            "end": str(date_range.get("end") or ""),
        }
    recorded_date = str(anchor.get("source_recorded_date") or "")
    if recorded_date:
        entry["source_recorded_date"] = recorded_date
    return entry


def _trigger_entry(trigger_event: dict[str, Any]) -> dict[str, Any]:
    """Return one chronology entry for a supplied trigger event."""
    trigger_type = str(trigger_event.get("trigger_type") or "trigger")
    entry_date = str(trigger_event.get("date") or "")
    description = f"Supplied {trigger_type.replace('_', ' ')} trigger event."
    return {
        "date": entry_date,
        "date_precision": _date_precision(entry_date),
        "entry_type": "trigger_event",
        "provenance_class": "scope_supplied",
        "title": trigger_type.replace("_", " ").capitalize(),
        "description": description,
        "source_linkage": {
            "source_ids": [],
            "source_types": [],
            "supporting_uids": [],
            "supporting_citation_ids": [],
            "evidence_handles": [],
            "document_locators": [],
            "source_evidence_status": "scope_only",
        },
    }


def _timeline_fallback_entry(
    event: dict[str, Any],
    *,
    case_bundle: dict[str, Any],
    citation_ids_by_uid: dict[str, list[str]],
) -> dict[str, Any]:
    """Return one chronology entry when no source object exists."""
    uid = str(event.get("uid") or "")
    synthetic_source_id = f"email:{uid}" if uid else ""
    title = str(event.get("subject") or event.get("sender_name") or "Timeline event")
    entry_date = str(event.get("date") or "")
    conversation_id = str(event.get("thread_group_id") or event.get("conversation_id") or "")
    description = str(event.get("summary") or event.get("snippet") or "") or (
        f"Fallback chronology event from thread {conversation_id or 'unknown'}."
    )
    people_involved = [
        str(item)
        for item in dict.fromkeys(
            [
                str(event.get("sender_name") or "").strip(),
                str(event.get("sender_email") or "").strip(),
                *[str(item).strip() for item in _as_list(event.get("participants"))],
                *[str(item).strip() for item in _as_list(event.get("to"))],
                *[str(item).strip() for item in _as_list(event.get("cc"))],
                *[str(item).strip() for item in _as_list(event.get("bcc"))],
            ]
        )
        if str(item).strip()
    ]
    return {
        "date": entry_date,
        "date_precision": _date_precision(entry_date),
        "entry_type": "timeline_event",
        "provenance_class": "timeline_fallback",
        "title": title,
        "description": description,
        "people_involved": people_involved[:8],
        "source_document": {
            "title": title,
            "source_id": synthetic_source_id,
            "source_type": "email" if synthetic_source_id else "",
        },
        "event_support_matrix": _event_support_matrix(
            case_bundle=case_bundle,
            entry_type="timeline_event",
            title=title,
            description=description,
        ),
        "source_linkage": {
            "source_ids": [synthetic_source_id] if synthetic_source_id else [],
            "source_types": ["email"] if synthetic_source_id else [],
            "supporting_uids": [uid] if uid else [],
            "supporting_citation_ids": citation_ids_by_uid.get(uid, [])[:4],
            "evidence_handles": [synthetic_source_id] if synthetic_source_id else [],
            "document_locators": [{"evidence_handle": synthetic_source_id}] if synthetic_source_id else [],
            "source_evidence_status": "timeline_only",
        },
    }


def _source_date_conflicts(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return chronology conflicts where extracted event date differs materially from source-recorded date."""
    conflicts: list[dict[str, Any]] = []
    for entry in entries:
        source_recorded_date = _date_only(str(entry.get("source_recorded_date") or ""))
        event_date = _date_only(str(entry.get("date") or ""))
        if source_recorded_date is None or event_date is None:
            continue
        delta_days = abs((source_recorded_date - event_date).days)
        if delta_days < 7:
            continue
        conflicts.append(
            {
                "conflict_id": f"SEQ-{len(conflicts) + 1:03d}",
                "chronology_id": str(entry.get("chronology_id") or ""),
                "source_recorded_date": str(entry.get("source_recorded_date") or ""),
                "event_date": str(entry.get("date") or ""),
                "delta_days": delta_days,
                "source_types": [
                    str(item) for item in _as_list(_as_dict(entry.get("source_linkage")).get("source_types")) if str(item).strip()
                ],
                "why_it_matters": (
                    "The extracted event date materially differs from the source-recorded date and should be reviewed "
                    "before relying on sequence assumptions."
                ),
            }
        )
    return conflicts


def _selected_issue_tracks(case_bundle: dict[str, Any]) -> list[str]:
    """Return selected issue tracks in stable priority order."""
    scope = _as_dict(case_bundle.get("scope"))
    selected = [str(item) for item in _as_list(scope.get("employment_issue_tracks")) if str(item).strip()]
    ordered = [track for track in _EVENT_READ_IDS if track != "ordinary_managerial_explanation" and track in selected]
    return ordered or [track for track in _EVENT_READ_IDS if track != "ordinary_managerial_explanation"]


def _best_supportive_read(entry: dict[str, Any], *, case_bundle: dict[str, Any]) -> tuple[str, dict[str, Any]] | tuple[str, None]:
    """Return the strongest non-managerial read for one entry."""
    matrix = _as_dict(entry.get("event_support_matrix"))
    selected_tracks = _selected_issue_tracks(case_bundle)
    for preferred_status in ("direct_event_support", "contextual_support_only"):
        for read_id in selected_tracks:
            payload = _as_dict(matrix.get(read_id))
            if str(payload.get("status") or "") == preferred_status:
                return read_id, payload
    return "", None
