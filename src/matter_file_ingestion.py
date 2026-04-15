"""File-backed enrichment for operator-supplied matter manifests."""

from __future__ import annotations

import copy
import hashlib
import mimetypes
import tarfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .attachment_extractor import attachment_format_profile, extract_text, extraction_quality_profile

MATTER_FILE_INGESTION_VERSION = "1"
_TEXTLESS_EXTRACTION_STATES_BY_SUFFIX = {
    ".png": "image_embedding_only",
    ".jpg": "image_embedding_only",
    ".jpeg": "image_embedding_only",
    ".webp": "image_embedding_only",
    ".heic": "image_embedding_only",
    ".zip": "binary_only",
    ".gz": "binary_only",
    ".tar": "binary_only",
    ".rar": "binary_only",
    ".7z": "binary_only",
}
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".heic"}
_ARCHIVE_SUFFIXES = {".zip", ".gz", ".tar", ".rar", ".7z"}
_SIDECAR_SUFFIX_CANDIDATES = (".ocr.txt", ".ocr.md", ".txt", ".md")
_ENRICHMENT_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}
_FILE_BACKED_CACHE_FIELDS = {
    "filename",
    "mime_type",
    "source_path",
    "file_size_bytes",
    "content_sha256",
    "matter_file_ingestion_version",
    "text",
    "summary",
    "extraction_state",
    "evidence_strength",
    "ocr_used",
    "failure_reason",
    "text_source_path",
    "text_locator",
    "documentary_support",
    "ingestion_notes",
    "weak_format_semantics",
    "review_status",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _preview(text: str, *, max_chars: int = 500) -> str:
    compact = _compact(text)
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _text_locator_metrics(text: str) -> dict[str, Any]:
    raw = str(text or "")
    lines = raw.splitlines()
    page_breaks = raw.count("\f")
    headings = [
        line.strip()
        for line in lines
        if line.strip().startswith(("#", "##", "###")) or line.strip().lower().startswith(("section ", "abschnitt "))
    ]
    metrics: dict[str, Any] = {
        "char_start": 0,
        "char_end": len(raw),
        "line_start": 1,
        "line_end": len(lines) if lines else 1,
    }
    if raw:
        metrics["page_count_estimate"] = page_breaks + 1
    if headings:
        metrics["section_markers"] = headings[:5]
    return metrics


def _default_textless_state(path: Path) -> str:
    return _TEXTLESS_EXTRACTION_STATES_BY_SUFFIX.get(path.suffix.lower(), "binary_only")


def _cache_key(path: Path) -> tuple[str, int, int]:
    stat = path.stat()
    return (str(path.resolve()), int(stat.st_mtime_ns), int(stat.st_size))


def _review_status_for_support_level(*, support_level: str, text_available: bool) -> str:
    if text_available:
        return "parsed"
    if support_level == "unsupported":
        return "unsupported"
    return "degraded"


def _read_sidecar_text(path: Path) -> tuple[str, str] | None:
    """Return sidecar transcript text when a sibling text companion exists."""
    candidates = [path.with_name(f"{path.stem}{suffix}") for suffix in _SIDECAR_SUFFIX_CANDIDATES]
    for candidate in candidates:
        if candidate == path or not candidate.exists() or not candidate.is_file():
            continue
        try:
            content = candidate.read_bytes()
        except OSError:
            continue
        mime_type = str(mimetypes.guess_type(candidate.name)[0] or "")
        text = extract_text(candidate.name, content, mime_type=mime_type) or content.decode("utf-8", errors="ignore").strip()
        compact = _compact(text)
        if compact:
            return compact, str(candidate)
    return None


def _archive_inventory_text(path: Path) -> str | None:
    """Return a bounded archive member inventory when full extraction is unsupported."""
    names: list[str] = []
    try:
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as archive:
                names = [item.filename for item in archive.infolist() if item.filename][:20]
        elif path.suffix.lower() in {".tar", ".gz"}:
            with tarfile.open(path) as archive:
                names = [item.name for item in archive.getmembers() if item.name][:20]
    except (OSError, tarfile.TarError, zipfile.BadZipFile):
        return None
    if not names:
        return None
    return "Archive member inventory:\n" + "\n".join(f"- {name}" for name in names)


def _archive_inventory_semantics(text: str) -> dict[str, Any] | None:
    lines = [line.strip()[2:] for line in str(text or "").splitlines() if line.strip().startswith("- ")]
    if not lines:
        return None
    member_classes: set[str] = set()
    for name in lines:
        lowered = name.lower()
        if any(token in lowered for token in ("chat", "teams", "slack", "whatsapp")):
            member_classes.add("chat_export_like")
        if any(token in lowered for token in ("note", "summary", "protokoll", "memo")):
            member_classes.add("note_like")
        if lowered.endswith((".ics", ".ical", ".vcs")):
            member_classes.add("calendar_like")
        if lowered.endswith((".csv", ".tsv", ".xlsx", ".xlsm", ".ods")):
            member_classes.add("spreadsheet_like")
    return {
        "recovery_mode": "archive_member_inventory",
        "member_count": len(lines),
        "member_preview": lines[:10],
        "detected_member_classes": sorted(member_classes),
    }


def enrich_manifest_artifact(artifact: dict[str, Any]) -> dict[str, Any]:
    """Return one manifest artifact enriched from a local source file when available."""
    enriched = dict(artifact)
    source_path = _compact(enriched.get("source_path"))
    if not source_path:
        return enriched

    path = Path(source_path).expanduser()
    if not path.exists() or not path.is_file():
        notes = [str(item) for item in _as_list(enriched.get("ingestion_notes")) if _compact(item)]
        notes.append(f"Local source path could not be read: {source_path}")
        enriched["ingestion_notes"] = notes
        if not _compact(enriched.get("failure_reason")):
            enriched["failure_reason"] = "source_path_unreadable"
        if str(enriched.get("review_status") or "") == "parsed":
            enriched["review_status"] = "degraded"
        return enriched

    cache_key = _cache_key(path)
    cached = _ENRICHMENT_CACHE.get(cache_key)
    if cached is not None:
        merged = dict(enriched)
        merged.update(copy.deepcopy(cached))
        return merged

    content = path.read_bytes()
    filename = _compact(enriched.get("filename")) or path.name
    mime_type = _compact(enriched.get("mime_type")) or str(mimetypes.guess_type(path.name)[0] or "")
    provided_text = str(enriched.get("text") or "")
    extracted_text = extract_text(filename, content, mime_type=mime_type) or ""
    effective_text = provided_text or extracted_text
    sidecar_path = ""
    archive_inventory_used = False
    if not effective_text:
        sidecar = _read_sidecar_text(path)
        if sidecar is not None:
            effective_text, sidecar_path = sidecar
        elif path.suffix.lower() in _ARCHIVE_SUFFIXES:
            inventory_text = _archive_inventory_text(path)
            if inventory_text:
                effective_text = inventory_text
                archive_inventory_used = True

    extracted_state = _compact(enriched.get("extraction_state"))
    evidence_strength = _compact(enriched.get("evidence_strength"))
    ocr_used = bool(enriched.get("ocr_used"))
    if not extracted_state:
        if archive_inventory_used:
            extracted_state = "archive_inventory_extracted"
        elif sidecar_path:
            extracted_state = "sidecar_text_extracted"
        else:
            extracted_state = "text_extracted" if effective_text else _default_textless_state(path)
    if not evidence_strength:
        evidence_strength = "weak_reference" if archive_inventory_used else "strong_text" if effective_text else "weak_reference"

    format_profile = attachment_format_profile(
        filename=filename,
        mime_type=mime_type,
        extraction_state=extracted_state,
        evidence_strength=evidence_strength,
        ocr_used=ocr_used,
        text_available=bool(effective_text),
    )
    extraction_quality = extraction_quality_profile(
        extraction_state=extracted_state,
        evidence_strength=evidence_strength,
        ocr_used=ocr_used,
        format_profile=format_profile,
    )
    support_level = str(format_profile.get("support_level") or "")

    enriched["filename"] = filename
    enriched["mime_type"] = mime_type
    enriched["source_path"] = str(path)
    enriched["file_size_bytes"] = len(content)
    enriched["content_sha256"] = hashlib.sha256(content).hexdigest()
    enriched["matter_file_ingestion_version"] = MATTER_FILE_INGESTION_VERSION
    if effective_text and not provided_text:
        enriched["text"] = effective_text
    if not _compact(enriched.get("summary")) and effective_text:
        enriched["summary"] = _preview(effective_text)
    enriched["extraction_state"] = extracted_state
    enriched["evidence_strength"] = evidence_strength
    enriched["ocr_used"] = ocr_used
    if not _compact(enriched.get("failure_reason")) and not effective_text and support_level == "unsupported":
        enriched["failure_reason"] = str(format_profile.get("degrade_reason") or "unsupported_format")
    text_source_path = ""
    text_locator: dict[str, Any] = {}
    if effective_text:
        if sidecar_path:
            text_source_path = sidecar_path
            text_locator = {
                "kind": "sidecar_transcript",
                "source_path": sidecar_path,
                "related_source_path": str(path),
                "content_sha256": enriched["content_sha256"],
                **_text_locator_metrics(effective_text),
            }
        elif archive_inventory_used:
            text_source_path = str(path)
            text_locator = {
                "kind": "archive_member_inventory",
                "source_path": str(path),
                "content_sha256": enriched["content_sha256"],
                **_text_locator_metrics(effective_text),
            }
        else:
            text_source_path = str(path)
            text_locator = {
                "kind": "full_document_text",
                "source_path": str(path),
                "content_sha256": enriched["content_sha256"],
                **_text_locator_metrics(effective_text),
            }
    enriched["text_source_path"] = text_source_path
    enriched["text_locator"] = text_locator
    enriched["documentary_support"] = {
        "format_profile": format_profile,
        "extraction_quality": extraction_quality,
    }

    notes = [str(item) for item in _as_list(enriched.get("ingestion_notes")) if _compact(item)]
    notes.append(
        "File-backed enrichment loaded metadata and "
        + ("extracted text." if effective_text else "support-level information without recoverable text.")
    )
    if sidecar_path:
        notes.append(f"Recovered text from sidecar transcript: {sidecar_path}")
    if archive_inventory_used:
        notes.append("Recovered archive member inventory without unpacking file contents.")
    enriched["ingestion_notes"] = list(dict.fromkeys(notes))
    if sidecar_path:
        enriched["weak_format_semantics"] = {
            "recovery_mode": "sidecar_transcript",
            "sidecar_source_path": sidecar_path,
            "original_format_family": str(format_profile.get("format_family") or path.suffix.lower().lstrip(".") or "unknown"),
        }
    elif archive_inventory_used:
        enriched["weak_format_semantics"] = _archive_inventory_semantics(effective_text) or {
            "recovery_mode": "archive_member_inventory",
            "member_count": 0,
            "member_preview": [],
            "detected_member_classes": [],
        }

    if str(enriched.get("review_status") or "") == "parsed" and not effective_text:
        enriched["review_status"] = _review_status_for_support_level(
            support_level=support_level,
            text_available=False,
        )
    elif str(enriched.get("review_status") or "") == "parsed" and (
        archive_inventory_used or sidecar_path or path.suffix.lower() in _IMAGE_SUFFIXES
    ):
        enriched["review_status"] = "degraded"
    _ENRICHMENT_CACHE[cache_key] = {
        key: copy.deepcopy(value) for key, value in enriched.items() if key in _FILE_BACKED_CACHE_FIELDS
    }
    return enriched


def enrich_matter_manifest(matter_manifest: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a manifest enriched from file-backed artifacts when available."""
    manifest = _as_dict(matter_manifest)
    if not manifest:
        return matter_manifest
    raw_artifacts = [artifact for artifact in _as_list(manifest.get("artifacts")) if isinstance(artifact, dict)]
    max_workers = min(4, len(raw_artifacts))
    if max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            artifacts = list(executor.map(enrich_manifest_artifact, raw_artifacts))
    else:
        artifacts = [enrich_manifest_artifact(artifact) for artifact in raw_artifacts]
    return {
        **manifest,
        "artifacts": artifacts,
    }
