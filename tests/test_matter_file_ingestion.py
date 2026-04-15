from __future__ import annotations

import zipfile

from src.matter_file_ingestion import enrich_manifest_artifact, enrich_matter_manifest
from src.matter_ingestion import source_from_manifest_artifact


def test_enrich_manifest_artifact_reads_file_backed_text_and_metadata(tmp_path) -> None:
    long_text = "Meeting note. " * 1500
    path = tmp_path / "meeting-note.txt"
    path.write_text(long_text, encoding="utf-8")

    enriched = enrich_manifest_artifact(
        {
            "source_id": "artifact:1",
            "source_class": "meeting_note",
            "source_path": str(path),
            "review_status": "parsed",
        }
    )

    assert enriched["filename"] == "meeting-note.txt"
    assert enriched["source_path"] == str(path)
    assert enriched["text"].startswith("Meeting note.")
    assert len(enriched["text"]) > 12000
    assert enriched["summary"].startswith("Meeting note.")
    assert enriched["extraction_state"] == "text_extracted"
    assert enriched["evidence_strength"] == "strong_text"
    assert enriched["file_size_bytes"] == len(path.read_bytes())
    assert len(str(enriched["content_sha256"])) == 64
    assert enriched["text_source_path"] == str(path)
    assert enriched["text_locator"] == {
        "kind": "full_document_text",
        "source_path": str(path),
        "content_sha256": str(enriched["content_sha256"]),
        "char_start": 0,
        "char_end": len(enriched["text"]),
        "line_start": 1,
        "line_end": 1,
        "page_count_estimate": 1,
    }
    assert enriched["documentary_support"]["format_profile"]["support_level"] == "supported"


def test_enrich_manifest_artifact_marks_missing_paths_as_degraded() -> None:
    enriched = enrich_manifest_artifact(
        {
            "source_id": "artifact:missing",
            "source_class": "formal_document",
            "source_path": "/tmp/does-not-exist-codex-email-rag.txt",
            "review_status": "parsed",
        }
    )

    assert enriched["failure_reason"] == "source_path_unreadable"
    assert enriched["review_status"] == "degraded"
    assert "could not be read" in enriched["ingestion_notes"][0]


def test_enrich_matter_manifest_enriches_each_artifact(tmp_path) -> None:
    path = tmp_path / "record.txt"
    path.write_text("Timeline note", encoding="utf-8")

    manifest = enrich_matter_manifest(
        {
            "manifest_id": "matter-1",
            "artifacts": [
                {
                    "source_id": "artifact:1",
                    "source_class": "note_record",
                    "source_path": str(path),
                    "review_status": "parsed",
                }
            ],
        }
    )

    assert manifest is not None
    assert manifest["artifacts"][0]["text"] == "Timeline note"


def test_enrich_manifest_artifact_recovers_image_sidecar_transcript(tmp_path) -> None:
    image_path = tmp_path / "meeting-screenshot.png"
    image_path.write_bytes(b"not-a-real-png-but-good-enough-for-path-based-ingestion")
    sidecar_path = tmp_path / "meeting-screenshot.ocr.txt"
    sidecar_path.write_text("SBV participation was discussed in the screenshot.", encoding="utf-8")

    enriched = enrich_manifest_artifact(
        {
            "source_id": "artifact:image-sidecar",
            "source_class": "screenshot",
            "source_path": str(image_path),
            "review_status": "parsed",
        }
    )

    assert enriched["text"] == "SBV participation was discussed in the screenshot."
    assert enriched["extraction_state"] == "sidecar_text_extracted"
    assert enriched["review_status"] == "degraded"
    assert enriched["documentary_support"]["format_profile"]["format_id"] == "image_sidecar_transcript"
    assert enriched["documentary_support"]["extraction_quality"]["quality_label"] == "sidecar_text_recovered"
    assert enriched["weak_format_semantics"] == {
        "recovery_mode": "sidecar_transcript",
        "sidecar_source_path": str(sidecar_path),
        "original_format_family": "image",
    }
    assert enriched["text_source_path"] == str(sidecar_path)
    assert enriched["text_locator"] == {
        "kind": "sidecar_transcript",
        "source_path": str(sidecar_path),
        "related_source_path": str(image_path),
        "content_sha256": str(enriched["content_sha256"]),
        "char_start": 0,
        "char_end": len("SBV participation was discussed in the screenshot."),
        "line_start": 1,
        "line_end": 1,
        "page_count_estimate": 1,
    }
    assert any("sidecar transcript" in note.lower() for note in enriched["ingestion_notes"])


def test_enrich_manifest_artifact_recovers_archive_inventory(tmp_path) -> None:
    archive_path = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("chat/export.html", "<html><body><p>Export</p></body></html>")
        archive.writestr("notes/summary.txt", "Summary")

    enriched = enrich_manifest_artifact(
        {
            "source_id": "artifact:archive",
            "source_class": "archive_bundle",
            "source_path": str(archive_path),
            "review_status": "parsed",
        }
    )

    assert enriched["extraction_state"] == "archive_inventory_extracted"
    assert enriched["evidence_strength"] == "weak_reference"
    assert enriched["review_status"] == "degraded"
    assert "Archive member inventory" in enriched["text"]
    assert enriched["documentary_support"]["format_profile"]["format_id"] == "archive_inventory_bundle"
    assert enriched["documentary_support"]["extraction_quality"]["quality_label"] == "archive_inventory_extracted"
    assert enriched["text_source_path"] == str(archive_path)
    assert enriched["text_locator"] == {
        "kind": "archive_member_inventory",
        "source_path": str(archive_path),
        "content_sha256": str(enriched["content_sha256"]),
        "char_start": 0,
        "char_end": len(enriched["text"]),
        "line_start": 1,
        "line_end": 3,
        "page_count_estimate": 1,
    }
    assert enriched["weak_format_semantics"] == {
        "recovery_mode": "archive_member_inventory",
        "member_count": 2,
        "member_preview": ["chat/export.html", "notes/summary.txt"],
        "detected_member_classes": ["chat_export_like", "note_like"],
    }


def test_source_from_manifest_artifact_recovers_email_export_metadata_from_formal_document_text() -> None:
    source = source_from_manifest_artifact(
        {
            "source_id": "manifest:file:1",
            "source_class": "formal_document",
            "filename": "recent_email.html",
            "title": "recent_email.html",
            "text": (
                "# Arbeitsunfähigkeitsmeldung 1 email · 2026-03-06\n"
                "From: Target, Person <target.person@example.org>\n"
                "To: Manager, Two <manager.two@example.org>, "
                "Recipient, One <recipient.one@example.org>\n"
                "Date: 2026-03-06T10:57:24\n"
                "Subject: Arbeitsunfähigkeitsmeldung\n"
                "Lieber Claus, liebe Anabel, ich melde mich heute krank.\n"
            ),
            "summary": "Exported thread.",
        },
        index=1,
    )

    assert source["title"] == "Arbeitsunfähigkeitsmeldung"
    assert source["author"] == "Target, Person <target.person@example.org>"
    assert source["recipients"] == [
        "Manager, Two <manager.two@example.org>",
        "Recipient, One <recipient.one@example.org>",
    ]
    assert source["date"] == "2026-03-06T10:57:24"
