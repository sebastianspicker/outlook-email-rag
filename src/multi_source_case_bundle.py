"""Multi-source case-evidence fusion for behavioural-analysis cases."""

from __future__ import annotations

from typing import Any, cast

from .matter_ingestion import source_from_manifest_artifact
from .multi_source_case_bundle_helpers import (
    MULTI_SOURCE_CASE_BUNDLE_VERSION,
    _attachment_document_kind,
    _attachment_source_type,
    _calendar_semantics,
    _chat_log_sources,
    _chronology_anchor_for_source,
    _document_locator,
    _documentary_support_payload,
    _meeting_note_sources,
    _source_reliability_for_attachment,
    _source_reliability_for_email,
    _spreadsheet_semantics,
    _string_list,
    _weighting_metadata,
)
from .multi_source_case_bundle_summary import _rebuild_bundle_summary


def append_chat_log_sources(
    bundle: dict[str, Any] | None,
    *,
    chat_log_entries: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    if not isinstance(bundle, dict):
        return bundle
    bundle_copy = {
        **bundle,
        "summary": dict(bundle.get("summary") or {}),
        "sources": [source for source in bundle.get("sources", []) if isinstance(source, dict)],
        "source_links": [link for link in bundle.get("source_links", []) if isinstance(link, dict)],
        "source_type_profiles": [profile for profile in bundle.get("source_type_profiles", []) if isinstance(profile, dict)],
    }
    existing_source_ids = {str(source.get("source_id") or "") for source in bundle_copy["sources"]}
    email_source_ids_by_uid = {
        str(source.get("uid") or ""): str(source.get("source_id") or "")
        for source in bundle_copy["sources"]
        if str(source.get("source_type") or "") == "email" and str(source.get("uid") or "")
    }
    new_sources, new_links, _new_counts = _chat_log_sources(chat_log_entries, email_source_ids_by_uid=email_source_ids_by_uid)
    for source in new_sources:
        if str(source.get("source_id") or "") in existing_source_ids:
            continue
        bundle_copy["sources"].append(source)
        existing_source_ids.add(str(source.get("source_id") or ""))
    bundle_copy["source_links"].extend(new_links)
    return _rebuild_bundle_summary(bundle_copy)


def append_manifest_sources(
    bundle: dict[str, Any] | None,
    *,
    matter_manifest: dict[str, Any] | None,
) -> dict[str, Any] | None:
    manifest = matter_manifest if isinstance(matter_manifest, dict) else {}
    artifacts = [item for item in manifest.get("artifacts", []) if isinstance(item, dict)]
    if not artifacts:
        return bundle
    bundle_copy = {
        **(bundle or {}),
        "summary": dict((bundle or {}).get("summary") or {}),
        "sources": [source for source in (bundle or {}).get("sources", []) if isinstance(source, dict)],
        "source_links": [link for link in (bundle or {}).get("source_links", []) if isinstance(link, dict)],
        "source_type_profiles": [
            profile for profile in (bundle or {}).get("source_type_profiles", []) if isinstance(profile, dict)
        ],
        "chronology_anchors": [anchor for anchor in (bundle or {}).get("chronology_anchors", []) if isinstance(anchor, dict)],
    }
    existing_source_ids = {str(source.get("source_id") or "") for source in bundle_copy["sources"]}
    email_source_ids_by_uid = {
        str(source.get("uid") or ""): str(source.get("source_id") or "")
        for source in bundle_copy["sources"]
        if str(source.get("source_type") or "") == "email" and str(source.get("uid") or "")
    }
    for index, artifact in enumerate(artifacts, start=1):
        source = source_from_manifest_artifact(artifact, index=index)
        source_id = str(source.get("source_id") or "")
        if source_id in existing_source_ids:
            continue
        bundle_copy["sources"].append(source)
        existing_source_ids.add(source_id)
        uid = str(source.get("uid") or "")
        if uid and str(source.get("source_type") or "") == "email":
            email_source_ids_by_uid[uid] = source_id
        if uid and str(source.get("source_type") or "") != "email":
            related_email_source_id = email_source_ids_by_uid.get(uid)
            if related_email_source_id:
                bundle_copy["source_links"].append(
                    {
                        "from_source_id": source_id,
                        "to_source_id": related_email_source_id,
                        "link_type": "declared_related_record",
                        "relationship": "matter_manifest_cross_reference",
                    }
                )
    return _rebuild_bundle_summary(bundle_copy)


def compact_multi_source_case_bundle(bundle: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(bundle, dict):
        return bundle
    compact_sources: list[dict[str, Any]] = []
    for source in bundle.get("sources", []):
        if not isinstance(source, dict):
            continue
        compact_source = {
            "source_id": str(source.get("source_id") or ""),
            "source_type": str(source.get("source_type") or ""),
            "document_kind": str(source.get("document_kind") or ""),
            "source_reliability": dict(source.get("source_reliability") or {}),
        }
        for key in (
            "uid",
            "actor_id",
            "title",
            "date",
            "snippet",
            "sender_name",
            "sender_email",
            "author",
            "date_context",
            "operator_summary",
        ):
            value = source.get(key)
            if value not in (None, "", []):
                compact_source[key] = value
        for key in ("to", "cc", "bcc", "recipients", "participants"):
            values = [str(item) for item in source.get(key, []) if item]
            if values:
                compact_source[key] = values
        if source.get("chronology_anchor"):
            compact_source["chronology_anchor"] = dict(source.get("chronology_anchor") or {})
        if source.get("provenance"):
            provenance = dict(source.get("provenance") or {})
            compact_source["provenance"] = {
                "evidence_handle": str(provenance.get("evidence_handle") or ""),
                "chunk_id": str(provenance.get("chunk_id") or ""),
                "snippet_start": provenance.get("snippet_start"),
                "snippet_end": provenance.get("snippet_end"),
            }
        if source.get("documentary_support"):
            documentary_support = dict(source.get("documentary_support") or {})
            compact_source["documentary_support"] = {
                "extraction_state": str(documentary_support.get("extraction_state") or ""),
                "evidence_strength": str(documentary_support.get("evidence_strength") or ""),
                "ocr_used": bool(documentary_support.get("ocr_used")),
                "failure_reason": str(documentary_support.get("failure_reason") or ""),
                "text_preview": str(documentary_support.get("text_preview") or ""),
                "format_profile": dict(documentary_support.get("format_profile") or {}),
                "extraction_quality": dict(documentary_support.get("extraction_quality") or {}),
            }
        if source.get("document_locator"):
            locator = dict(source.get("document_locator") or {})
            compact_source["document_locator"] = {
                "evidence_handle": str(locator.get("evidence_handle") or ""),
                "chunk_id": str(locator.get("chunk_id") or ""),
            }
        compact_sources.append(compact_source)
    compact_profiles = [
        dict(profile)
        for profile in bundle.get("source_type_profiles", [])
        if isinstance(profile, dict) and bool(profile.get("available"))
    ]
    return {
        "version": str(bundle.get("version") or ""),
        "summary": dict(bundle.get("summary") or {}),
        "sources": compact_sources,
        "source_links": [
            {
                "from_source_id": str(link.get("from_source_id") or ""),
                "to_source_id": str(link.get("to_source_id") or ""),
                "link_type": str(link.get("link_type") or ""),
                "relationship": str(link.get("relationship") or ""),
            }
            for link in bundle.get("source_links", [])
            if isinstance(link, dict)
        ],
        "source_type_profiles": compact_profiles,
    }


def build_multi_source_case_bundle(
    *,
    case_bundle: dict[str, Any] | None,
    candidates: list[dict[str, Any]],
    attachment_candidates: list[dict[str, Any]],
    full_map: dict[str, Any],
    chat_log_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    scope = (case_bundle or {}).get("scope") if isinstance(case_bundle, dict) else None
    if not isinstance(scope, dict):
        return None
    sources: list[dict[str, Any]] = []
    source_links: list[dict[str, Any]] = []
    email_source_ids_by_uid: dict[str, str] = {}
    for candidate in candidates:
        uid = str(candidate.get("uid") or "")
        if not uid:
            continue
        source_id = f"email:{uid}"
        email_source_ids_by_uid[uid] = source_id
        reliability = _source_reliability_for_email(candidate)
        full_email = full_map.get(uid) if isinstance(full_map, dict) else None
        source = {
            "source_id": source_id,
            "source_type": "email",
            "document_kind": "email_body",
            "uid": uid,
            "actor_id": str(candidate.get("sender_actor_id") or ""),
            "title": str(candidate.get("subject") or ""),
            "date": str(candidate.get("date") or ""),
            "snippet": str(candidate.get("snippet") or ""),
            "sender_name": str((full_email or {}).get("sender_name") or candidate.get("sender_name") or ""),
            "sender_email": str((full_email or {}).get("sender_email") or candidate.get("sender_email") or ""),
            "to": _string_list((full_email or {}).get("to")),
            "cc": _string_list((full_email or {}).get("cc")),
            "bcc": _string_list((full_email or {}).get("bcc")),
            "provenance": dict(candidate.get("provenance") or {}),
            "follow_up": dict(candidate.get("follow_up") or {}),
            "source_reliability": reliability,
            "source_weighting": _weighting_metadata(
                source_type="email",
                reliability_level=str(reliability["level"]),
                text_available=bool(str(candidate.get("snippet") or "").strip()),
            ),
        }
        chronology_anchor = _chronology_anchor_for_source(source)
        if chronology_anchor is not None:
            source["chronology_anchor"] = chronology_anchor
        spreadsheet_semantics = _spreadsheet_semantics(source)
        if spreadsheet_semantics is not None:
            source["spreadsheet_semantics"] = spreadsheet_semantics
        calendar_semantics = _calendar_semantics(source)
        if calendar_semantics is not None:
            source["calendar_semantics"] = calendar_semantics
        sources.append(source)
        for note in _meeting_note_sources(uid, full_map.get(uid) if isinstance(full_map, dict) else None):
            note.pop("_extracted_from", None)
            sources.append(note)
            source_links.append(
                {
                    "from_source_id": note["source_id"],
                    "to_source_id": source_id,
                    "link_type": "extracted_from_email",
                    "relationship": "contextual_metadata",
                }
            )
    for candidate in attachment_candidates:
        uid = str(candidate.get("uid") or "")
        attachment = cast(dict[str, Any], candidate.get("attachment")) if isinstance(candidate.get("attachment"), dict) else {}
        source_type = _attachment_source_type(candidate, attachment)
        filename = str(attachment.get("filename") or "attachment")
        source_id = f"{source_type}:{uid}:{filename}"
        reliability = _source_reliability_for_attachment(candidate, source_type=source_type)
        source = {
            "source_id": source_id,
            "source_type": source_type,
            "document_kind": _attachment_document_kind(source_type),
            "uid": uid,
            "actor_id": str(candidate.get("sender_actor_id") or ""),
            "title": filename,
            "date": str(candidate.get("date") or ""),
            "snippet": str(candidate.get("snippet") or ""),
            "provenance": dict(candidate.get("provenance") or {}),
            "attachment": dict(attachment),
            "document_locator": _document_locator(candidate),
            "documentary_support": _documentary_support_payload(candidate, source_type=source_type) or {},
            "follow_up": dict(candidate.get("follow_up") or {}),
            "source_reliability": reliability,
            "source_weighting": _weighting_metadata(
                source_type=source_type,
                reliability_level=str(reliability["level"]),
                text_available=bool(attachment.get("text_available")),
            ),
        }
        if isinstance(attachment.get("spreadsheet_semantics"), dict) and attachment.get("spreadsheet_semantics"):
            source["spreadsheet_semantics"] = dict(attachment.get("spreadsheet_semantics") or {})
        if isinstance(attachment.get("calendar_semantics"), dict) and attachment.get("calendar_semantics"):
            source["calendar_semantics"] = dict(attachment.get("calendar_semantics") or {})
        if isinstance(attachment.get("weak_format_semantics"), dict) and attachment.get("weak_format_semantics"):
            source["weak_format_semantics"] = dict(attachment.get("weak_format_semantics") or {})
        chronology_anchor = _chronology_anchor_for_source(source)
        if chronology_anchor is not None:
            source["chronology_anchor"] = chronology_anchor
        spreadsheet_semantics = _spreadsheet_semantics(source)
        if spreadsheet_semantics is not None:
            source["spreadsheet_semantics"] = spreadsheet_semantics
        calendar_semantics = _calendar_semantics(source)
        if calendar_semantics is not None:
            source["calendar_semantics"] = calendar_semantics
        sources.append(source)
        parent_source_id = email_source_ids_by_uid.get(uid)
        weighting = source["source_weighting"]
        can_corroborate_or_contradict = isinstance(weighting, dict) and bool(weighting.get("can_corroborate_or_contradict"))
        if parent_source_id:
            source_links.append(
                {
                    "from_source_id": source_id,
                    "to_source_id": parent_source_id,
                    "link_type": "attached_to_email",
                    "relationship": "can_corroborate_or_contradict_message"
                    if can_corroborate_or_contradict
                    else "reference_only_attachment",
                }
            )
    chat_sources, chat_links, _chat_counts = _chat_log_sources(chat_log_entries, email_source_ids_by_uid=email_source_ids_by_uid)
    sources.extend(chat_sources)
    source_links.extend(chat_links)
    return _rebuild_bundle_summary(
        {
            "version": MULTI_SOURCE_CASE_BUNDLE_VERSION,
            "summary": {},
            "sources": sources,
            "source_links": source_links,
            "source_type_profiles": [],
            "chronology_anchors": [],
        }
    )
