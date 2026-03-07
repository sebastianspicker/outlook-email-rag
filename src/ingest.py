"""End-to-end ingestion pipeline."""

from __future__ import annotations

import argparse
import logging
import os
import time
import zipfile
from typing import Any

from dotenv import load_dotenv

from .chunker import chunk_attachment, chunk_email
from .config import configure_logging, get_settings
from .parse_olm import parse_olm
from .validation import positive_int as _shared_positive_int

logger = logging.getLogger(__name__)


def ingest(
    olm_path: str,
    chromadb_path: str | None = None,
    sqlite_path: str | None = None,
    batch_size: int = 500,
    max_emails: int | None = None,
    dry_run: bool = False,
    extract_attachments: bool = False,
    extract_entities: bool = False,
) -> dict:
    """Parse an OLM file and ingest all emails into the vector database."""
    settings = get_settings()
    start_time = time.time()

    logger.info("Email RAG ingestion started")
    logger.info("Source: %s", olm_path)
    logger.info("Dry run: %s", dry_run)

    embedder = None
    if not dry_run:
        try:
            from .embedder import EmailEmbedder
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Missing runtime dependency. Install project dependencies with "
                "'pip install -r requirements.txt'"
            ) from exc
        embedder = EmailEmbedder(chromadb_path=chromadb_path)

    if embedder:
        logger.info("Database: %s", embedder.chromadb_path)
        logger.info("Model: %s", embedder.model_name)
        logger.info("Existing chunks in DB: %s", embedder.count())
    else:
        logger.info("Database write disabled (dry-run)")
        logger.info("Configured default DB path: %s", chromadb_path or settings.chromadb_path)

    # SQLite relational store
    email_db = None
    if not dry_run:
        from .email_db import EmailDatabase

        resolved_sqlite = sqlite_path or settings.sqlite_path
        email_db = EmailDatabase(resolved_sqlite)
        logger.info("SQLite DB: %s", resolved_sqlite)

    # Entity extractor
    entity_extractor_fn = None
    if extract_entities and not dry_run:
        from .entity_extractor import extract_entities as _extract_entities

        entity_extractor_fn = _extract_entities

    attachment_extractor = None
    if extract_attachments:
        from .attachment_extractor import extract_text

        attachment_extractor = extract_text

    total_emails = 0
    total_chunks_created = 0
    total_chunks_added = 0
    total_batches_written = 0
    total_attachment_chunks = 0
    sqlite_inserted = 0
    pending_chunks = []
    pending_emails = []

    for email in parse_olm(olm_path, extract_attachments=extract_attachments):
        total_emails += 1
        email_dict = email.to_dict()
        chunks = chunk_email(email_dict)
        total_chunks_created += len(chunks)
        if embedder:
            pending_chunks.extend(chunks)

        # Buffer emails for SQLite batch write
        if email_db:
            pending_emails.append(email)

        # Process attachment contents if enabled
        if attachment_extractor and email.attachment_contents:
            for att_name, att_bytes in email.attachment_contents:
                att_text = attachment_extractor(att_name, att_bytes)
                if att_text:
                    att_chunks = chunk_attachment(
                        email_uid=email.uid,
                        filename=att_name,
                        text=att_text,
                        parent_metadata={
                            "uid": email.uid,
                            "subject": email_dict.get("subject", ""),
                            "sender_name": email_dict.get("sender_name", ""),
                            "sender_email": email_dict.get("sender_email", ""),
                            "date": email_dict.get("date", ""),
                            "folder": email_dict.get("folder", ""),
                        },
                    )
                    total_chunks_created += len(att_chunks)
                    total_attachment_chunks += len(att_chunks)
                    if embedder:
                        pending_chunks.extend(att_chunks)

        if total_emails % 100 == 0:
            logger.info("Parsed %s emails (%s chunks).", total_emails, total_chunks_created)

        if max_emails is not None and total_emails >= max_emails:
            logger.info("Reached --max-emails=%s; stopping parse loop.", max_emails)
            break

        if embedder and len(pending_chunks) >= batch_size:
            total_chunks_added += embedder.add_chunks(pending_chunks, batch_size=batch_size)
            total_batches_written += 1
            pending_chunks = []

        if email_db and len(pending_emails) >= batch_size:
            sqlite_inserted += email_db.insert_emails_batch(pending_emails)
            if entity_extractor_fn:
                for em in pending_emails:
                    entities = entity_extractor_fn(em.clean_body, em.sender_email)
                    if entities:
                        email_db.insert_entities_batch(
                            em.uid,
                            [(e.text, e.entity_type, e.normalized_form) for e in entities],
                        )
            pending_emails = []

    if embedder and pending_chunks:
        total_chunks_added += embedder.add_chunks(pending_chunks, batch_size=batch_size)
        total_batches_written += 1

    if email_db and pending_emails:
        sqlite_inserted += email_db.insert_emails_batch(pending_emails)
        if entity_extractor_fn:
            for em in pending_emails:
                entities = entity_extractor_fn(em.clean_body, em.sender_email)
                if entities:
                    email_db.insert_entities_batch(
                        em.uid,
                        [(e.text, e.entity_type, e.normalized_form) for e in entities],
                    )

    if email_db:
        email_db.close()

    elapsed = time.time() - start_time

    stats = {
        "emails_parsed": total_emails,
        "chunks_created": total_chunks_created,
        "attachment_chunks": total_attachment_chunks,
        "chunks_added": total_chunks_added,
        "chunks_skipped": (total_chunks_created - total_chunks_added) if embedder else 0,
        "batches_written": total_batches_written,
        "total_in_db": embedder.count() if embedder else None,
        "sqlite_inserted": sqlite_inserted,
        "dry_run": dry_run,
        "extract_attachments": extract_attachments,
        "extract_entities": extract_entities,
        "elapsed_seconds": round(elapsed, 1),
    }

    logger.info("Ingestion complete: %s", stats)
    return stats


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest Outlook .olm export into the email RAG database.")
    parser.add_argument("olm_path", help="Path to the .olm file to ingest.")
    parser.add_argument("--chromadb-path", default=None, help="Custom path for ChromaDB storage.")
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=500,
        help="Chunks per ingest write batch (default: 500).",
    )
    parser.add_argument(
        "--max-emails",
        type=_positive_int,
        default=None,
        help="Optional cap for number of emails to parse.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and chunk emails without writing embeddings to ChromaDB.",
    )
    parser.add_argument(
        "--extract-attachments",
        action="store_true",
        help="Extract and index text content from attachments (PDF, DOCX, XLSX, text).",
    )
    parser.add_argument(
        "--extract-entities",
        action="store_true",
        help="Extract entities (organizations, URLs, phones) and store in SQLite.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=None,
        help="Custom path for SQLite metadata database.",
    )
    parser.add_argument(
        "--reset-index",
        action="store_true",
        help="Delete ChromaDB collection and SQLite DB, then exit.",
    )
    parser.add_argument("--yes", action="store_true", help="Confirm destructive operations.")
    parser.add_argument(
        "--log-level",
        default=None,
        help="Logging level override (DEBUG, INFO, WARNING, ERROR).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parse_args(argv)
    configure_logging(args.log_level)

    if args.reset_index:
        if not args.yes:
            print("Refusing to reset index without --yes.")
            raise SystemExit(2)
        _reset_index(args)
        print("Index has been reset.")
        raise SystemExit(0)

    try:
        stats = ingest(
            args.olm_path,
            chromadb_path=args.chromadb_path,
            sqlite_path=args.sqlite_path,
            batch_size=args.batch_size,
            max_emails=args.max_emails,
            dry_run=args.dry_run,
            extract_attachments=args.extract_attachments,
            extract_entities=args.extract_entities,
        )
    except FileNotFoundError as exc:
        print(str(exc))
        raise SystemExit(2) from exc
    except zipfile.BadZipFile as exc:
        print(f"Invalid OLM archive: {args.olm_path} ({exc})")
        raise SystemExit(2) from exc
    except OSError as exc:
        print(f"Could not read OLM archive: {args.olm_path} ({exc})")
        raise SystemExit(2) from exc
    except RuntimeError as exc:
        print(str(exc))
        raise SystemExit(2) from exc

    summary_lines = format_ingestion_summary(stats)
    print("\n" + "\n".join(summary_lines))


def _reset_index(args: argparse.Namespace) -> None:
    """Delete ChromaDB collection and SQLite DB file."""
    settings = get_settings()
    sqlite_file = args.sqlite_path or settings.sqlite_path
    if os.path.exists(sqlite_file):
        os.remove(sqlite_file)
        print(f"Deleted SQLite DB: {sqlite_file}")
    chromadb_dir = args.chromadb_path or settings.chromadb_path
    if os.path.isdir(chromadb_dir):
        import shutil

        shutil.rmtree(chromadb_dir)
        print(f"Deleted ChromaDB: {chromadb_dir}")


def _positive_int(raw: str) -> int:
    try:
        return _shared_positive_int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def format_ingestion_summary(stats: dict[str, Any]) -> list[str]:
    lines = [
        "=== Ingestion Summary ===",
        f"Emails parsed: {stats['emails_parsed']}",
        f"Chunks created: {stats['chunks_created']}",
    ]

    if stats["dry_run"]:
        lines.append("Database write disabled (dry-run).")
    else:
        lines.extend(
            [
                f"Chunks added: {stats['chunks_added']}",
                f"Chunks skipped: {stats['chunks_skipped']}",
                f"Write batches: {stats['batches_written']}",
                f"Total in DB: {stats['total_in_db']}",
            ]
        )
        if "sqlite_inserted" in stats:
            lines.append(f"SQLite rows inserted: {stats['sqlite_inserted']}")

    lines.append(f"Elapsed: {stats['elapsed_seconds']}s")
    return lines


if __name__ == "__main__":
    main()
