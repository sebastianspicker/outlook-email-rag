from __future__ import annotations

from src.multi_source_case_bundle import append_manifest_sources, build_multi_source_case_bundle


def test_build_multi_source_case_bundle_preserves_source_types_links_and_reliability():
    case_bundle = {
        "scope": {
            "case_label": "case-a",
        }
    }
    candidates = [
        {
            "uid": "uid-1",
            "sender_actor_id": "actor-manager",
            "subject": "Re: Process",
            "date": "2026-02-12T10:00:00",
            "snippet": "Please see the attached policy update.",
            "verification_status": "forensic_exact",
            "provenance": {"evidence_handle": "email:uid-1"},
            "follow_up": {"tool": "email_deep_context", "uid": "uid-1"},
        }
    ]
    attachment_candidates = [
        {
            "uid": "uid-1",
            "sender_actor_id": "actor-manager",
            "date": "2026-02-12T10:00:00",
            "snippet": "Policy section 4 requires written approval.",
            "provenance": {"evidence_handle": "attachment:uid-1:policy.pdf"},
            "follow_up": {"tool": "email_deep_context", "uid": "uid-1"},
            "attachment": {
                "filename": "policy.pdf",
                "mime_type": "application/pdf",
                "text_available": True,
                "evidence_strength": "strong_text",
                "extraction_state": "text_extracted",
            },
        }
    ]
    full_map = {
        "uid-1": {
            "uid": "uid-1",
            "subject": "Re: Process",
            "date": "2026-02-12T10:00:00",
            "meeting_data": {
                "OPFMeetingLocation": "Room A",
                "OPFMeetingStartDate": "2026-02-12T09:00:00",
            },
        }
    }

    payload = build_multi_source_case_bundle(
        case_bundle=case_bundle,
        candidates=candidates,
        attachment_candidates=attachment_candidates,
        full_map=full_map,
    )

    assert payload is not None
    assert payload["version"] == "1"
    assert payload["summary"]["source_type_counts"] == {
        "email": 1,
        "formal_document": 1,
        "meeting_note": 1,
    }
    assert payload["summary"]["missing_source_types"] == [
        "attachment",
        "chat_log",
        "note_record",
        "time_record",
        "participation_record",
    ]
    assert payload["summary"]["contradiction_ready_source_count"] == 2
    assert payload["summary"]["documentary_source_count"] == 2
    assert payload["summary"]["weak_extraction_source_count"] == 0
    assert payload["summary"]["ocr_source_count"] == 0
    assert payload["summary"]["unsupported_format_source_count"] == 0
    assert payload["summary"]["lossy_extraction_source_count"] == 0
    assert payload["summary"]["source_format_matrix_version"] == "1"
    assert payload["summary"]["chronology_anchor_count"] == 3
    source_types = {source["source_type"] for source in payload["sources"]}
    assert source_types == {"email", "formal_document", "meeting_note"}
    formal_document = next(source for source in payload["sources"] if source["source_type"] == "formal_document")
    assert formal_document["source_reliability"]["level"] == "high"
    assert formal_document["source_weighting"]["can_corroborate_or_contradict"] is True
    assert formal_document["documentary_support"]["extraction_state"] == "text_extracted"
    assert formal_document["documentary_support"]["format_profile"]["format_id"] == "pdf_document"
    assert formal_document["documentary_support"]["extraction_quality"]["quality_label"] == "native_text_extracted"
    assert formal_document["documentary_support"]["review_recommendation"].startswith("Native extracted")
    assert formal_document["document_locator"]["evidence_handle"] == "attachment:uid-1:policy.pdf"
    assert formal_document["chronology_anchor"]["date_origin"] == "source_timestamp"
    assert formal_document["chronology_anchor"]["source_type"] == "formal_document"
    link_types = {link["link_type"] for link in payload["source_links"]}
    assert link_types == {"attached_to_email", "extracted_from_email"}
    assert any(link["relationship"] == "can_corroborate_or_contradict_message" for link in payload["source_links"])
    formal_profile = next(profile for profile in payload["source_type_profiles"] if profile["source_type"] == "formal_document")
    assert formal_profile["direct_text_count"] == 1
    assert formal_profile["weak_extraction_count"] == 0
    assert formal_profile["reliability_counts"] == {"high": 1}
    assert formal_profile["format_support_counts"] == {"supported": 1}
    assert formal_profile["extraction_quality_counts"] == {"native_text_extracted": 1}


def test_build_multi_source_case_bundle_adds_operator_chat_logs() -> None:
    payload = build_multi_source_case_bundle(
        case_bundle={"scope": {"case_label": "case-a"}},
        candidates=[
            {
                "uid": "uid-1",
                "sender_actor_id": "actor-manager",
                "subject": "Re: Process",
                "date": "2026-02-12T10:00:00",
                "snippet": "Please see the attached policy update.",
                "verification_status": "forensic_exact",
                "provenance": {"evidence_handle": "email:uid-1"},
            }
        ],
        attachment_candidates=[],
        full_map={},
        chat_log_entries=[
            {
                "source_id": "chat-1",
                "platform": "Teams",
                "title": "Teams follow-up",
                "date": "2026-02-12T11:00:00",
                "participants": ["alex@example.com", "manager@example.com"],
                "text": "Please keep this off email for now.",
                "related_email_uid": "uid-1",
            }
        ],
    )

    assert payload is not None
    assert payload["summary"]["source_type_counts"] == {"chat_log": 1, "email": 1}
    assert payload["summary"]["missing_source_types"] == [
        "attachment",
        "meeting_note",
        "formal_document",
        "note_record",
        "time_record",
        "participation_record",
    ]
    assert payload["summary"]["documentary_source_count"] == 1
    assert payload["summary"]["chronology_anchor_count"] == 2
    chat_log = next(source for source in payload["sources"] if source["source_type"] == "chat_log")
    assert chat_log["source_reliability"]["level"] == "medium"
    assert chat_log["participants"] == ["alex@example.com", "manager@example.com"]
    assert chat_log["chronology_anchor"]["source_type"] == "chat_log"
    assert any(link["link_type"] == "related_to_email" for link in payload["source_links"])


def test_build_multi_source_case_bundle_surfaces_ocr_and_weak_documentary_states() -> None:
    payload = build_multi_source_case_bundle(
        case_bundle={"scope": {"case_label": "case-b"}},
        candidates=[],
        attachment_candidates=[
            {
                "uid": "uid-ocr-1",
                "sender_actor_id": "actor-1",
                "date": "2026-02-15T10:00:00",
                "snippet": "Scanned grievance document excerpt.",
                "provenance": {
                    "evidence_handle": "attachment:uid-ocr-1:grievance.pdf",
                    "chunk_id": "chunk-grievance-1",
                    "snippet_start": 0,
                    "snippet_end": 32,
                },
                "attachment": {
                    "filename": "grievance.pdf",
                    "mime_type": "application/pdf",
                    "text_available": True,
                    "evidence_strength": "strong_text",
                    "extraction_state": "ocr_text_extracted",
                    "ocr_used": True,
                    "text_preview": "Scanned grievance document excerpt.",
                },
            },
            {
                "uid": "uid-ocr-2",
                "sender_actor_id": "actor-2",
                "date": "2026-02-16T10:00:00",
                "snippet": "[Attachment: note.png from email ...]",
                "provenance": {
                    "evidence_handle": "attachment:uid-ocr-2:note.png",
                    "chunk_id": "chunk-note-1",
                },
                "attachment": {
                    "filename": "note.png",
                    "mime_type": "image/png",
                    "text_available": False,
                    "evidence_strength": "weak_reference",
                    "extraction_state": "ocr_failed",
                    "ocr_used": True,
                    "failure_reason": "ocr_failed",
                },
            },
        ],
        full_map={},
    )

    assert payload is not None
    assert payload["summary"]["source_type_counts"] == {"attachment": 1, "formal_document": 1}
    assert payload["summary"]["weak_extraction_source_count"] == 1
    assert payload["summary"]["ocr_source_count"] == 2
    assert payload["summary"]["unsupported_format_source_count"] == 0
    assert payload["summary"]["lossy_extraction_source_count"] == 2
    formal_document = next(source for source in payload["sources"] if source["source_type"] == "formal_document")
    weak_attachment = next(source for source in payload["sources"] if source["source_type"] == "attachment")
    assert formal_document["source_reliability"]["level"] == "medium"
    assert formal_document["source_reliability"]["basis"] == "formal_document_ocr_text_extracted"
    assert formal_document["documentary_support"]["format_profile"]["format_id"] == "scanned_pdf"
    assert formal_document["documentary_support"]["extraction_quality"]["quality_label"] == "ocr_text_recovered"
    assert formal_document["documentary_support"]["review_recommendation"].startswith("OCR-recovered text")
    assert weak_attachment["source_reliability"]["basis"] == "attachment_ocr_failed"
    assert weak_attachment["documentary_support"]["failure_reason"] == "ocr_failed"
    assert weak_attachment["documentary_support"]["format_profile"]["format_id"] == "image_only_exhibit"
    assert weak_attachment["documentary_support"]["extraction_quality"]["quality_label"] == "ocr_failed"
    assert weak_attachment["documentary_support"]["review_recommendation"].startswith("Treat this source as a weak")
    attachment_profile = next(profile for profile in payload["source_type_profiles"] if profile["source_type"] == "attachment")
    assert attachment_profile["weak_extraction_count"] == 1
    assert attachment_profile["ocr_source_count"] == 1
    assert attachment_profile["format_support_counts"] == {"reference_only": 1}
    assert attachment_profile["extraction_quality_counts"] == {"ocr_failed": 1}


def test_build_multi_source_case_bundle_classifies_notes_time_and_participation_records() -> None:
    payload = build_multi_source_case_bundle(
        case_bundle={"scope": {"case_label": "case-c"}},
        candidates=[],
        attachment_candidates=[
            {
                "uid": "uid-note-1",
                "sender_actor_id": "actor-1",
                "date": "2026-02-17T08:00:00",
                "snippet": "Gedächtnisprotokoll after the meeting with HR.",
                "provenance": {"evidence_handle": "attachment:uid-note-1:gedaechtnisprotokoll.txt"},
                "attachment": {
                    "filename": "gedaechtnisprotokoll.txt",
                    "mime_type": "text/plain",
                    "text_available": True,
                    "evidence_strength": "strong_text",
                    "extraction_state": "text_extracted",
                },
            },
            {
                "uid": "uid-time-1",
                "sender_actor_id": "actor-2",
                "date": "2026-02-17T09:00:00",
                "snippet": "Arbeitszeitnachweis for March with attendance corrections.",
                "provenance": {"evidence_handle": "attachment:uid-time-1:arbeitszeitnachweis.xlsx"},
                "attachment": {
                    "filename": "arbeitszeitnachweis.xlsx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "text_available": True,
                    "evidence_strength": "strong_text",
                    "extraction_state": "text_extracted",
                },
            },
            {
                "uid": "uid-participation-1",
                "sender_actor_id": "actor-3",
                "date": "2026-02-17T10:00:00",
                "snippet": "SBV consultation and Personalrat participation request.",
                "provenance": {"evidence_handle": "attachment:uid-participation-1:sbv-consultation.pdf"},
                "attachment": {
                    "filename": "sbv-consultation.pdf",
                    "mime_type": "application/pdf",
                    "text_available": True,
                    "evidence_strength": "strong_text",
                    "extraction_state": "text_extracted",
                },
            },
        ],
        full_map={},
    )

    assert payload is not None
    assert payload["summary"]["source_type_counts"] == {
        "note_record": 1,
        "participation_record": 1,
        "time_record": 1,
    }
    assert payload["summary"]["documentary_source_count"] == 3
    assert payload["summary"]["chronology_anchor_count"] == 3

    by_type = {source["source_type"]: source for source in payload["sources"]}
    assert by_type["note_record"]["document_kind"] == "attached_note_record"
    assert by_type["note_record"]["source_reliability"]["basis"] == "note_record_text_extracted"
    assert by_type["note_record"]["source_weighting"]["can_corroborate_or_contradict"] is True
    assert by_type["time_record"]["document_kind"] == "attached_time_record"
    assert by_type["time_record"]["source_reliability"]["basis"] == "time_record_text_extracted"
    assert by_type["time_record"]["spreadsheet_semantics"]["record_type"] == "attendance_export"
    assert by_type["time_record"]["spreadsheet_semantics"]["date_signal_strength"] == "weak"
    assert by_type["participation_record"]["document_kind"] == "attached_participation_record"
    assert by_type["participation_record"]["source_reliability"]["basis"] == "participation_record_text_extracted"

    profile_counts = {
        profile["source_type"]: profile["count"] for profile in payload["source_type_profiles"] if profile["available"] is True
    }
    assert profile_counts == {
        "note_record": 1,
        "time_record": 1,
        "participation_record": 1,
    }


def test_build_multi_source_case_bundle_extracts_document_event_dates_and_time_record_ranges() -> None:
    payload = build_multi_source_case_bundle(
        case_bundle={"scope": {"case_label": "case-d"}},
        candidates=[],
        attachment_candidates=[
            {
                "uid": "uid-note-2",
                "sender_actor_id": "actor-1",
                "date": "2026-03-20T11:00:00",
                "snippet": "Meeting summary for 2026-03-05 about the complaint follow-up.",
                "provenance": {"evidence_handle": "attachment:uid-note-2:meeting-summary.txt"},
                "attachment": {
                    "filename": "meeting-summary.txt",
                    "mime_type": "text/plain",
                    "text_available": True,
                    "evidence_strength": "strong_text",
                    "extraction_state": "text_extracted",
                    "text_preview": "Meeting summary for 2026-03-05 about the complaint follow-up.",
                },
            },
            {
                "uid": "uid-time-2",
                "sender_actor_id": "actor-2",
                "date": "2026-03-31T18:00:00",
                "snippet": "Attendance record covering 2026-03-01 to 2026-03-31.",
                "provenance": {"evidence_handle": "attachment:uid-time-2:attendance.xlsx"},
                "attachment": {
                    "filename": "attendance.xlsx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "text_available": True,
                    "evidence_strength": "strong_text",
                    "extraction_state": "text_extracted",
                    "text_preview": "[Sheet: March] Attendance record covering 2026-03-01 to 2026-03-31.",
                },
            },
        ],
        full_map={},
    )

    assert payload is not None
    note_record = next(source for source in payload["sources"] if source["source_type"] == "note_record")
    time_record = next(source for source in payload["sources"] if source["source_type"] == "time_record")
    assert note_record["chronology_anchor"]["date"] == "2026-03-05"
    assert note_record["chronology_anchor"]["date_origin"] == "document_text"
    assert time_record["chronology_anchor"]["date"] == "2026-03-01"
    assert time_record["chronology_anchor"]["date_origin"] == "time_record_range_start"
    assert time_record["chronology_anchor"]["date_range"] == {
        "start": "2026-03-01",
        "end": "2026-03-31",
    }
    assert time_record["spreadsheet_semantics"] == {
        "record_type": "attendance_export",
        "sheet_names": ["March"],
        "sheet_count": 1,
        "explicit_dates": ["2026-03-01", "2026-03-31"],
        "date_range": {"start": "2026-03-01", "end": "2026-03-31"},
        "month_labels": ["march"],
        "date_signal_strength": "range",
        "structure_signal": "sheeted",
    }


def test_build_multi_source_case_bundle_prefers_persisted_attachment_semantics_over_snippet_heuristics() -> None:
    payload = build_multi_source_case_bundle(
        case_bundle={"scope": {"case_label": "case-e2"}},
        candidates=[],
        attachment_candidates=[
            {
                "uid": "uid-time-persisted",
                "sender_actor_id": "actor-1",
                "date": "2026-03-31T18:00:00",
                "snippet": "Export attached.",
                "provenance": {"evidence_handle": "attachment:uid-time-persisted:times.xlsx"},
                "attachment": {
                    "filename": "times.xlsx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "text_available": True,
                    "evidence_strength": "strong_text",
                    "extraction_state": "text_extracted",
                    "source_type_hint": "time_record",
                    "format_profile": {"format_family": "spreadsheet", "format_id": "spreadsheet_export"},
                    "extraction_quality": {"quality_label": "native_text_extracted"},
                    "review_recommendation": (
                        "Extracted time-record text can support chronology and attendance follow-up directly."
                    ),
                    "spreadsheet_semantics": {
                        "record_type": "novatime_export",
                        "sheet_names": ["March"],
                        "sheet_count": 1,
                        "explicit_dates": ["2026-03-01", "2026-03-31"],
                        "date_range": {"start": "2026-03-01", "end": "2026-03-31"},
                        "month_labels": ["march"],
                        "date_signal_strength": "range",
                        "structure_signal": "sheeted",
                    },
                },
            }
        ],
        full_map={},
    )

    assert payload is not None
    source = payload["sources"][0]
    assert source["source_type"] == "time_record"
    assert source["spreadsheet_semantics"]["record_type"] == "novatime_export"
    assert source["chronology_anchor"]["date"] == "2026-03-01"
    assert source["chronology_anchor"]["date_origin"] == "time_record_range_start"


def test_build_multi_source_case_bundle_surfaces_unsupported_archives_and_lossy_calendar_exports() -> None:
    payload = build_multi_source_case_bundle(
        case_bundle={"scope": {"case_label": "case-e"}},
        candidates=[],
        attachment_candidates=[
            {
                "uid": "uid-archive-1",
                "sender_actor_id": "actor-1",
                "date": "2026-03-22T08:00:00",
                "snippet": "",
                "provenance": {"evidence_handle": "attachment:uid-archive-1:bundle.zip"},
                "attachment": {
                    "filename": "bundle.zip",
                    "mime_type": "application/zip",
                    "text_available": False,
                    "evidence_strength": "weak_reference",
                    "extraction_state": "binary_only",
                    "failure_reason": "archive_contents_not_extracted",
                },
            },
            {
                "uid": "uid-calendar-1",
                "sender_actor_id": "actor-2",
                "date": "2026-03-22T09:00:00",
                "snippet": (
                    "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:BEM review\nDTSTART:20260322T090000\n"
                    "DTEND:20260322T100000\nLOCATION:Room A\nATTENDEE:max@example.org\nEND:VEVENT\nEND:VCALENDAR"
                ),
                "provenance": {"evidence_handle": "attachment:uid-calendar-1:review.ics"},
                "attachment": {
                    "filename": "review.ics",
                    "mime_type": "text/calendar",
                    "text_available": True,
                    "evidence_strength": "strong_text",
                    "extraction_state": "text_extracted",
                    "text_preview": (
                        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:BEM review\nDTSTART:20260322T090000\n"
                        "DTEND:20260322T100000\nLOCATION:Room A\nATTENDEE:max@example.org\nEND:VEVENT\nEND:VCALENDAR"
                    ),
                },
            },
        ],
        full_map={},
    )

    assert payload is not None
    assert payload["summary"]["unsupported_format_source_count"] == 1
    assert payload["summary"]["lossy_extraction_source_count"] == 2
    archive = next(source for source in payload["sources"] if source["title"] == "bundle.zip")
    calendar = next(source for source in payload["sources"] if source["title"] == "review.ics")
    assert archive["documentary_support"]["format_profile"]["support_level"] == "unsupported"
    assert archive["documentary_support"]["review_recommendation"].startswith("Archive bundle is not currently supported")
    assert calendar["documentary_support"]["format_profile"]["format_id"] == "calendar_file"
    assert calendar["documentary_support"]["format_profile"]["lossiness"] == "medium"
    assert calendar["chronology_anchor"]["date"] == "2026-03-22T09:00:00"
    assert calendar["chronology_anchor"]["date_origin"] == "calendar_dtstart"
    assert calendar["calendar_semantics"] == {
        "calendar_summary": "BEM review",
        "dtstart": "2026-03-22T09:00:00",
        "dtend": "2026-03-22T10:00:00",
        "location": "Room A",
        "organizer": "",
        "attendees": ["max@example.org"],
        "attendee_count": 1,
        "field_count": 5,
    }


def test_append_manifest_sources_accounts_for_supplied_artifacts_and_source_classes() -> None:
    bundle = append_manifest_sources(
        {
            "version": "1",
            "summary": {},
            "sources": [
                {
                    "source_id": "email:uid-1",
                    "source_type": "email",
                    "document_kind": "email_body",
                    "uid": "uid-1",
                    "title": "Status",
                    "date": "2026-03-11T10:00:00",
                    "snippet": "Status email.",
                    "source_weighting": {"text_available": True, "can_corroborate_or_contradict": True},
                    "source_reliability": {"level": "high", "basis": "authored_email_body"},
                }
            ],
            "source_links": [],
            "source_type_profiles": [],
            "chronology_anchors": [],
        },
        matter_manifest={
            "manifest_id": "matter-1",
            "artifacts": [
                {
                    "source_id": "manifest:personnel:1",
                    "source_class": "personnel_file_record",
                    "title": "Personnel file excerpt",
                    "date": "2026-03-10",
                    "filename": "personnel-file.pdf",
                    "mime_type": "application/pdf",
                    "custodian": "HR",
                    "text": "Personnel record excerpt about assignment and complaints.",
                    "summary": "Operator summary that should not replace direct text.",
                    "author": "HR Department",
                    "recipients": ["alex@example.org"],
                    "date_start": "2026-03-01",
                    "date_end": "2026-03-31",
                    "date_is_approximate": True,
                    "text_source_path": "/tmp/personnel-file.pdf",
                    "text_locator": {"kind": "full_document_text", "page_hint": 2},
                    "review_status": "parsed",
                },
                {
                    "source_id": "manifest:chat:1",
                    "source_class": "chat_export",
                    "title": "Teams export",
                    "date": "2026-03-12",
                    "custodian": "IT",
                    "text": "Keep this off email for now.",
                    "participants": ["alex@example.org", "manager@example.org"],
                    "related_email_uid": "uid-1",
                    "review_status": "degraded",
                },
                {
                    "source_id": "manifest:image:1",
                    "source_class": "screenshot",
                    "title": "Screenshot with transcript",
                    "date": "2026-03-13",
                    "text": "Recovered screenshot text.",
                    "review_status": "degraded",
                    "weak_format_semantics": {
                        "recovery_mode": "sidecar_transcript",
                        "sidecar_source_path": "/tmp/screenshot.ocr.txt",
                        "original_format_family": "image",
                    },
                },
            ],
        },
    )

    assert bundle is not None
    assert bundle["summary"]["source_type_counts"]["email"] == 1
    assert bundle["summary"]["source_type_counts"]["formal_document"] == 1
    assert bundle["summary"]["source_type_counts"]["chat_log"] == 1
    assert bundle["summary"]["source_class_counts"]["personnel_file_record"] == 1
    assert bundle["summary"]["source_class_counts"]["chat_export"] == 1
    assert bundle["summary"]["chronology_anchor_count"] >= 3
    manifest_doc = next(source for source in bundle["sources"] if source["source_id"] == "manifest:personnel:1")
    manifest_chat = next(source for source in bundle["sources"] if source["source_id"] == "manifest:chat:1")
    manifest_image = next(source for source in bundle["sources"] if source["source_id"] == "manifest:image:1")
    assert manifest_doc["source_type"] == "formal_document"
    assert manifest_doc["document_kind"] == "personnel_file_record"
    assert manifest_doc["snippet"] == "Personnel record excerpt about assignment and complaints."
    assert manifest_doc["operator_summary"] == "Operator summary that should not replace direct text."
    assert manifest_doc["author"] == "HR Department"
    assert manifest_doc["recipients"] == ["alex@example.org"]
    assert manifest_doc["date_context"] == {
        "display_date": "2026-03-10",
        "date_start": "2026-03-01",
        "date_end": "2026-03-31",
        "is_approximate": True,
        "has_range": True,
    }
    assert manifest_doc["document_locator"]["text_source_path"] == "/tmp/personnel-file.pdf"
    assert manifest_doc["document_locator"]["text_locator"] == {"kind": "full_document_text", "page_hint": 2}
    assert manifest_doc["document_locator"]["snippet_locator"] == {
        "kind": "quoted_snippet",
        "char_start": 0,
        "char_end": len("Personnel record excerpt about assignment and complaints."),
        "line_start": 1,
        "line_end": 1,
    }
    assert manifest_chat["source_type"] == "chat_log"
    assert manifest_chat["participants"] == ["alex@example.org", "manager@example.org"]
    assert manifest_image["weak_format_semantics"]["recovery_mode"] == "sidecar_transcript"
    assert any(link["relationship"] == "matter_manifest_cross_reference" for link in bundle["source_links"])


def test_append_manifest_sources_keeps_operator_summary_separate_from_quoteable_snippet() -> None:
    bundle = append_manifest_sources(
        {"version": "1", "summary": {}, "sources": [], "source_links": [], "source_type_profiles": [], "chronology_anchors": []},
        matter_manifest={
            "manifest_id": "matter-2",
            "artifacts": [
                {
                    "source_id": "manifest:meeting:1",
                    "source_class": "meeting_note",
                    "title": "Meeting note",
                    "date": "2026-03-14",
                    "summary": "Operator summary only.",
                    "participants": ["alex@example.org", "manager@example.org"],
                    "review_status": "degraded",
                }
            ],
        },
    )

    assert bundle is not None
    source = bundle["sources"][0]
    assert source["snippet"] == ""
    assert source["operator_summary"] == "Operator summary only."
    assert source["source_weighting"]["text_available"] is False
