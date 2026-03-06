"""End-to-end ingestion pipeline."""

from __future__ import annotations

import argparse
import logging
import time
import zipfile
from typing import Any

from dotenv import load_dotenv

from .chunker import chunk_email
from .config import configure_logging, get_settings
from .parse_olm import parse_olm
from .validation import positive_int as _shared_positive_int

logger = logging.getLogger(__name__)


def ingest(
    olm_path: str,
    chromadb_path: str | None = None,
    batch_size: int = 500,
    max_emails: int | None = None,
    dry_run: bool = False,
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

    total_emails = 0
    total_chunks_created = 0
    total_chunks_added = 0
    total_batches_written = 0
    pending_chunks = []

    for email in parse_olm(olm_path):
        total_emails += 1
        chunks = chunk_email(email.to_dict())
        total_chunks_created += len(chunks)
        if embedder:
            pending_chunks.extend(chunks)

        if total_emails % 100 == 0:
            logger.info("Parsed %s emails (%s chunks).", total_emails, total_chunks_created)

        if max_emails is not None and total_emails >= max_emails:
            logger.info("Reached --max-emails=%s; stopping parse loop.", max_emails)
            break

        if embedder and len(pending_chunks) >= batch_size:
            total_chunks_added += embedder.add_chunks(pending_chunks, batch_size=batch_size)
            total_batches_written += 1
            pending_chunks = []

    if embedder and pending_chunks:
        total_chunks_added += embedder.add_chunks(pending_chunks, batch_size=batch_size)
        total_batches_written += 1

    elapsed = time.time() - start_time

    stats = {
        "emails_parsed": total_emails,
        "chunks_created": total_chunks_created,
        "chunks_added": total_chunks_added,
        "chunks_skipped": (total_chunks_created - total_chunks_added) if embedder else 0,
        "batches_written": total_batches_written,
        "total_in_db": embedder.count() if embedder else None,
        "dry_run": dry_run,
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
        "--log-level",
        default=None,
        help="Logging level override (DEBUG, INFO, WARNING, ERROR).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = parse_args(argv)
    configure_logging(args.log_level)

    try:
        stats = ingest(
            args.olm_path,
            chromadb_path=args.chromadb_path,
            batch_size=args.batch_size,
            max_emails=args.max_emails,
            dry_run=args.dry_run,
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

    lines.append(f"Elapsed: {stats['elapsed_seconds']}s")
    return lines


if __name__ == "__main__":
    main()
