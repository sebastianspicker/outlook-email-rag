"""Reingest, reembed, and reset helpers for the ingestion CLI."""

from __future__ import annotations

import argparse
import os
import shutil
from typing import Any

from .config import get_settings


def reingest_bodies_impl(
    olm_path: str,
    sqlite_path: str | None = None,
    force: bool = False,
    parse_olm_fn=None,
) -> dict[str, Any]:
    """Backfill body_text/body_html for emails missing them in SQLite."""
    settings = get_settings()
    from .email_db import EmailDatabase

    resolved_sqlite = sqlite_path or settings.sqlite_path
    email_db = EmailDatabase(resolved_sqlite)

    parser = parse_olm_fn

    if force:
        all_uids = email_db.all_uids()
        if not all_uids:
            email_db.close()
            return {"updated": 0, "total": 0, "message": "No emails in database."}
        updated = 0
        batch_size = 200
        for email in parser(olm_path):
            if email.uid in all_uids:
                email_db.update_body_text(
                    email.uid,
                    email.clean_body,
                    email.body_html,
                    normalized_body_source=email.clean_body_source,
                    body_normalization_version=email.body_normalization_version,
                    commit=False,
                )
                email_db.update_headers(
                    email.uid,
                    subject=email.subject,
                    sender_name=email.sender_name,
                    sender_email=email.sender_email,
                    base_subject=email.base_subject,
                    email_type=email.email_type,
                    commit=False,
                )
                updated += 1
                if updated % batch_size == 0:
                    email_db.conn.commit()
        email_db.conn.commit()
        email_db.close()
        return {
            "updated": updated,
            "total": len(all_uids),
            "message": f"Force-updated {updated} of {len(all_uids)} emails (bodies + headers).",
        }

    missing_uids = email_db.uids_missing_body()

    if not missing_uids:
        email_db.close()
        return {"updated": 0, "total_missing": 0, "message": "All emails already have body text."}

    updated = 0
    batch_size = 200
    for email in parser(olm_path):
        if email.uid in missing_uids:
            email_db.update_body_text(
                email.uid,
                email.clean_body,
                email.body_html,
                normalized_body_source=email.clean_body_source,
                body_normalization_version=email.body_normalization_version,
                commit=False,
            )
            updated += 1
            if updated % batch_size == 0:
                email_db.conn.commit()

    email_db.conn.commit()
    email_db.close()
    return {
        "updated": updated,
        "total_missing": len(missing_uids),
        "message": f"Updated {updated} of {len(missing_uids)} emails with body text.",
    }


def reingest_metadata_impl(
    olm_path: str,
    sqlite_path: str | None = None,
    exchange_entities_from_email=None,
    parse_olm_fn=None,
) -> dict[str, Any]:
    """Backfill schema-v7 metadata for existing emails in SQLite."""
    settings = get_settings()
    from .email_db import EmailDatabase

    resolved_sqlite = sqlite_path or settings.sqlite_path
    email_db = EmailDatabase(resolved_sqlite)

    all_uids = email_db.all_uids()
    if not all_uids:
        email_db.close()
        return {"updated": 0, "total": 0, "message": "No emails in database."}

    updated = 0
    exchange_entities_inserted = 0
    batch_size = 200
    rows_since_commit = 0

    extractor = exchange_entities_from_email
    parser = parse_olm_fn
    for email in parser(olm_path):
        if email.uid not in all_uids:
            continue

        if email_db.update_v7_metadata(email, commit=False):
            updated += 1

        exchange_entities = extractor(email) if extractor else []
        if exchange_entities:
            email_db.insert_entities_batch(email.uid, exchange_entities, commit=False)
            exchange_entities_inserted += len(exchange_entities)

        rows_since_commit += 1
        if rows_since_commit >= batch_size:
            email_db.conn.commit()
            rows_since_commit = 0

    email_db.conn.commit()
    email_db.close()
    return {
        "updated": updated,
        "total": len(all_uids),
        "exchange_entities_inserted": exchange_entities_inserted,
        "message": (
            f"Updated {updated} of {len(all_uids)} emails with v7 metadata. "
            f"{exchange_entities_inserted} Exchange entities inserted."
        ),
    }


def reingest_analytics_impl(sqlite_path: str | None = None) -> dict[str, Any]:
    """Backfill detected_language and sentiment for emails missing analytics."""
    settings = get_settings()
    from .email_db import EmailDatabase
    from .language_detector import detect_language
    from .sentiment_analyzer import analyze as analyze_sentiment

    resolved_sqlite = sqlite_path or settings.sqlite_path
    email_db = EmailDatabase(resolved_sqlite)

    rows = email_db.conn.execute(
        "SELECT uid, body_text FROM emails "
        "WHERE (detected_language IS NULL OR sentiment_label IS NULL) "
        "AND body_text IS NOT NULL AND LENGTH(TRIM(body_text)) >= 20"
    ).fetchall()

    total_missing = len(rows)
    if not total_missing:
        email_db.close()
        return {"updated": 0, "total_missing": 0, "message": "All emails already have analytics data."}

    batch: list[tuple[str | None, str | None, float | None, str]] = []
    for row in rows:
        body = row["body_text"]
        lang = detect_language(body)
        sent = analyze_sentiment(body)
        batch.append((lang if lang != "unknown" else None, sent.sentiment, sent.score, row["uid"]))

    updated = email_db.update_analytics_batch(batch)
    email_db.close()
    return {
        "updated": updated,
        "total_missing": total_missing,
        "message": f"Computed language and sentiment for {updated} emails.",
    }


def reextract_entities_impl(
    *,
    sqlite_path: str | None = None,
    entity_extractor_fn=None,
    extractor_key: str = "",
    extraction_version: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Backfill or rebuild entity mentions from stored email bodies."""
    settings = get_settings()
    from .email_db import EmailDatabase

    if entity_extractor_fn is None:
        return {"updated": 0, "total_candidates": 0, "message": "Entity extraction is unavailable."}

    resolved_sqlite = sqlite_path or settings.sqlite_path
    email_db = EmailDatabase(resolved_sqlite)
    query = (
        "SELECT uid, body_text, sender_email FROM emails "
        "WHERE body_text IS NOT NULL AND LENGTH(TRIM(body_text)) > 0"
    )
    if not force:
        query += (
            " AND ("
            " uid NOT IN (SELECT DISTINCT email_uid FROM entity_mentions)"
            " OR uid IN (SELECT DISTINCT email_uid FROM entity_mentions WHERE COALESCE(extractor_key, '') = '')"
            " )"
        )
    rows = email_db.conn.execute(query).fetchall()
    total_candidates = len(rows)
    if not rows:
        email_db.close()
        return {
            "updated": 0,
            "total_candidates": 0,
            "message": "All emails already have entity provenance metadata.",
        }

    updated = 0
    inserted_mentions = 0
    for row in rows:
        uid = str(row["uid"])
        body_text = str(row["body_text"] or "")
        sender_email = str(row["sender_email"] or "")
        entities = entity_extractor_fn(body_text, sender_email)
        email_db.delete_entity_mentions_for_email(uid, commit=False)
        if entities:
            email_db.insert_entities_batch(
                uid,
                entities,
                extractor_key=extractor_key,
                extraction_version=extraction_version,
                commit=False,
            )
            inserted_mentions += len(entities)
        updated += 1
    email_db.conn.commit()
    email_db.close()
    return {
        "updated": updated,
        "total_candidates": total_candidates,
        "inserted_mentions": inserted_mentions,
        "extractor_key": extractor_key,
        "extraction_version": extraction_version,
        "message": (
            f"Re-extracted entities for {updated} emails using {extractor_key or 'unknown'} "
            f"(version {extraction_version or 'unknown'})."
        ),
    }


def reprocess_degraded_attachments_impl(
    olm_path: str,
    *,
    chromadb_path: str | None = None,
    sqlite_path: str | None = None,
    batch_size: int = 100,
    force: bool = False,
    parse_olm_fn=None,
    chunk_attachment_fn=None,
    attachment_text_extractor=None,
    attachment_ocr_extractor=None,
) -> dict[str, Any]:
    """Re-parse degraded mailbox attachments and attempt OCR recovery for image attachments."""
    settings = get_settings()
    from .attachment_extractor import image_ocr_available
    from .email_db import EmailDatabase
    from .embedder import EmailEmbedder
    from .ingest_embed_pipeline import _attachment_completion_status
    from .ingest_pipeline import (
        _attachment_text_preview,
        _mailbox_attachment_locator,
        _set_attachment_evidence,
        _textless_attachment_state_with_ocr,
    )

    if parse_olm_fn is None or chunk_attachment_fn is None or attachment_text_extractor is None:
        return {"updated": 0, "total_candidates": 0, "message": "Attachment reprocessing dependencies are unavailable."}

    resolved_sqlite = sqlite_path or settings.sqlite_path
    email_db = EmailDatabase(resolved_sqlite)
    embedder = EmailEmbedder(chromadb_path=chromadb_path)
    embedder.set_sparse_db(email_db)

    if force:
        target_rows = email_db.conn.execute(
            "SELECT DISTINCT email_uid FROM attachments"
        ).fetchall()
    else:
        target_rows = email_db.conn.execute(
            "SELECT email_uid FROM email_ingest_state WHERE attachment_status IN ('degraded', 'unsupported')"
        ).fetchall()
    target_uids = {str(row["email_uid"]) for row in target_rows if str(row["email_uid"] or "")}
    if not target_uids:
        close_embedder = getattr(embedder, "close", None)
        if callable(close_embedder):
            close_embedder()
        email_db.close()
        return {"updated": 0, "total_candidates": 0, "message": "No degraded attachment rows require reprocessing."}

    updated = 0
    ocr_recovered = 0
    chunks_added = 0
    ocr_available = image_ocr_available() if attachment_ocr_extractor else False
    recovered_attachments = 0
    parser = parse_olm_fn(olm_path, extract_attachments=True)
    for email in parser:
        if email.uid not in target_uids:
            continue
        attachment_chunks = []
        for att_i, (att_name, att_bytes) in enumerate(getattr(email, "attachment_contents", []) or []):
            attachments = getattr(email, "attachments", None) or []
            mime_type = str((attachments[att_i] or {}).get("mime_type") or "") if 0 <= att_i < len(attachments) else ""
            att_text = attachment_text_extractor(att_name, att_bytes, mime_type=mime_type)
            ocr_used = False
            extraction_state = "text_extracted"
            failure_reason = None
            if not att_text:
                ocr_text = attachment_ocr_extractor(att_name, att_bytes) if attachment_ocr_extractor else None
                if ocr_text:
                    att_text = ocr_text
                    ocr_used = True
                    extraction_state = "ocr_text_extracted"
                else:
                    extraction_state, failure_reason = _textless_attachment_state_with_ocr(
                        filename=att_name,
                        mime_type=mime_type,
                        ocr_attempted=bool(attachment_ocr_extractor),
                        ocr_available=ocr_available,
                    )
            if att_text:
                locator = _mailbox_attachment_locator(
                    email_uid=email.uid,
                    att_index=att_i,
                    filename=att_name,
                    extraction_state=extraction_state,
                )
                _set_attachment_evidence(
                    email,
                    att_index=att_i,
                    extraction_state=extraction_state,
                    evidence_strength="strong_text",
                    ocr_used=ocr_used,
                    failure_reason=None,
                    text_preview=_attachment_text_preview(att_text),
                    extracted_text=att_text,
                    text_source_path=f"attachment://{email.uid}/{att_i}/{att_name}",
                    text_locator=locator,
                )
                attachment_chunks.extend(
                    chunk_attachment_fn(
                        email.uid,
                        att_name,
                        att_text,
                        email.to_dict(),
                        att_index=att_i,
                        extraction_state=extraction_state,
                        evidence_strength="strong_text",
                        ocr_used=ocr_used,
                    )
                )
                recovered_attachments += 1
                if ocr_used:
                    ocr_recovered += 1
            else:
                _set_attachment_evidence(
                    email,
                    att_index=att_i,
                    extraction_state=extraction_state,
                    evidence_strength="weak_reference",
                    ocr_used=False,
                    failure_reason=failure_reason,
                    text_preview="",
                    extracted_text="",
                    text_source_path="",
                    text_locator=_mailbox_attachment_locator(
                        email_uid=email.uid,
                        att_index=att_i,
                        filename=att_name,
                        extraction_state=extraction_state,
                    ),
                )
        email_db.update_v7_metadata(email, commit=False)
        if attachment_chunks:
            if hasattr(embedder, "upsert_chunks"):
                chunks_added += embedder.upsert_chunks(attachment_chunks, batch_size=batch_size)
            else:
                chunks_added += embedder.add_chunks(attachment_chunks, batch_size=batch_size)
        state_row = email_db.conn.execute(
            """SELECT body_chunk_count, image_chunk_count
               FROM email_ingest_state WHERE email_uid = ?""",
            (email.uid,),
        ).fetchone()
        body_chunk_count = int((state_row["body_chunk_count"] if state_row else 0) or 0)
        image_chunk_count = int((state_row["image_chunk_count"] if state_row else 0) or 0)
        attachment_chunk_count = len(attachment_chunks)
        email._ingest_body_chunk_count = body_chunk_count
        email._ingest_attachment_chunk_count = attachment_chunk_count
        email._ingest_image_chunk_count = image_chunk_count
        email._ingest_attachment_requested = True
        email._ingest_image_requested = bool(image_chunk_count)
        attachment_status = _attachment_completion_status(email)
        if attachment_status == "pending":
            attachment_status = "completed"
        email_db.mark_ingest_batch_completed(
            [
                {
                    "email_uid": email.uid,
                    "body_chunk_count": body_chunk_count,
                    "attachment_chunk_count": attachment_chunk_count,
                    "image_chunk_count": image_chunk_count,
                    "vector_chunk_count": body_chunk_count + attachment_chunk_count + image_chunk_count,
                    "attachment_status": attachment_status,
                    "image_status": "completed" if image_chunk_count else "not_requested",
                }
            ],
            commit=False,
        )
        updated += 1

    email_db.conn.commit()
    close_embedder = getattr(embedder, "close", None)
    if callable(close_embedder):
        close_embedder()
    email_db.close()
    return {
        "updated": updated,
        "total_candidates": len(target_uids),
        "recovered_attachments": recovered_attachments,
        "ocr_recovered": ocr_recovered,
        "chunks_added": chunks_added,
        "message": (
            f"Reprocessed degraded attachments for {updated} emails; "
            f"recovered {recovered_attachments} attachments ({ocr_recovered} via OCR)."
        ),
    }


def reembed_impl(
    chromadb_path: str | None = None,
    sqlite_path: str | None = None,
    batch_size: int = 100,
) -> dict[str, Any]:
    """Re-chunk and re-embed all emails from corrected SQLite body text."""
    from .chunker import chunk_email
    from .email_db import EmailDatabase
    from .embedder import EmailEmbedder

    settings = get_settings()
    resolved_sqlite = sqlite_path or settings.sqlite_path
    email_db = EmailDatabase(resolved_sqlite)
    embedder = EmailEmbedder(chromadb_path=chromadb_path)
    embedder.set_sparse_db(email_db)

    all_uids = email_db.all_uids()
    if not all_uids:
        email_db.close()
        return {"reembedded": 0, "total": 0, "message": "No emails in database."}

    reembedded = 0
    chunks_deleted = 0
    chunks_added = 0
    skipped_no_body = 0
    existing_ids = embedder.get_existing_ids(refresh=False)
    body_chunk_ids_by_uid: dict[str, list[str]] = {}
    for chunk_id in existing_ids:
        if "__att_" in chunk_id or "__img_" in chunk_id:
            continue
        uid = chunk_id.split("__", 1)[0]
        body_chunk_ids_by_uid.setdefault(uid, []).append(chunk_id)

    for uid in sorted(all_uids):
        email_dict = email_db.get_email_for_reembed(uid)
        if email_dict is None:
            skipped_no_body += 1
            continue

        body_chunk_ids = body_chunk_ids_by_uid.get(uid, [])
        if body_chunk_ids:
            embedder.collection.delete(ids=body_chunk_ids)
            existing_ids.difference_update(body_chunk_ids)
            chunks_deleted += len(body_chunk_ids)
        email_db.delete_sparse_by_uid(uid)

        chunks = chunk_email(email_dict)
        added = embedder.upsert_chunks(chunks, batch_size=batch_size)
        chunks_added += added
        reembedded += 1

    embedder.close()
    email_db.close()
    return {
        "reembedded": reembedded,
        "total": len(all_uids),
        "chunks_deleted": chunks_deleted,
        "chunks_added": chunks_added,
        "skipped_no_body": skipped_no_body,
        "message": (
            f"Re-embedded {reembedded} of {len(all_uids)} emails "
            f"({chunks_added} chunks). {skipped_no_body} skipped (no body text)."
        ),
    }


def reset_index_impl(args: argparse.Namespace) -> None:
    """Delete ChromaDB collection and SQLite DB file."""
    settings = get_settings()
    sqlite_file = args.sqlite_path or settings.sqlite_path
    if os.path.exists(sqlite_file):
        os.remove(sqlite_file)
        print(f"Deleted SQLite DB: {sqlite_file}")
    chromadb_dir = args.chromadb_path or settings.chromadb_path
    if os.path.isdir(chromadb_dir):
        shutil.rmtree(chromadb_dir)
        print(f"Deleted ChromaDB: {chromadb_dir}")
