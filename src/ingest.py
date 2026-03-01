"""End-to-end ingestion pipeline."""

from __future__ import annotations

import argparse
import logging
import time
import zipfile

from dotenv import load_dotenv

from .chunker import chunk_email
from .config import configure_logging, get_settings
from .parse_olm import parse_olm

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
    pending_chunks = []

    for email in parse_olm(olm_path):
        total_emails += 1
        chunks = chunk_email(email.to_dict())
        total_chunks_created += len(chunks)
        if embedder:
            pending_chunks.extend(chunks)

        if total_emails % 500 == 0:
            logger.info("Parsed %s emails (%s chunks).", total_emails, total_chunks_created)

        if max_emails is not None and total_emails >= max_emails:
            logger.info("Reached --max-emails=%s; stopping parse loop.", max_emails)
            break

        if embedder and len(pending_chunks) >= batch_size:
            total_chunks_added += embedder.add_chunks(pending_chunks, batch_size=batch_size)
            pending_chunks = []

    if embedder and pending_chunks:
        total_chunks_added += embedder.add_chunks(pending_chunks, batch_size=batch_size)
"""
End-to-end ingestion pipeline.

Usage:
    python -m src.ingest data/your-export.olm
    python -m src.ingest data/your-export.olm --chromadb-path data/chromadb
"""

import argparse
import time

from .parse_olm import parse_olm
from .chunker import chunk_email
from .embedder import EmailEmbedder


def ingest(olm_path: str, chromadb_path: str | None = None) -> dict:
    """
    Parse an OLM file and ingest all emails into the vector database.

    Args:
        olm_path: Path to the .olm file.
        chromadb_path: Optional custom path for ChromaDB storage.

    Returns:
        Dict with ingestion stats.
    """
    print(f"\n📧 Email RAG — Ingestion Pipeline")
    print(f"{'='*50}")
    print(f"Source: {olm_path}")
    start_time = time.time()

    # Initialize embedder
    embedder = EmailEmbedder(chromadb_path=chromadb_path)
    print(f"Database: {embedder.chromadb_path}")
    print(f"Model: {embedder.model_name}")
    print(f"Existing chunks in DB: {embedder.count()}")
    print()

    # Parse and chunk
    print("Parsing OLM file...")
    total_emails = 0
    total_chunks_created = 0
    all_chunks = []
    batch_threshold = 500  # Embed in batches of 500 chunks

    for email in parse_olm(olm_path):
        total_emails += 1
        email_dict = email.to_dict()
        chunks = chunk_email(email_dict)
        total_chunks_created += len(chunks)
        all_chunks.extend(chunks)

        if total_emails % 500 == 0:
            print(f"  Parsed {total_emails} emails ({total_chunks_created} chunks)...")

        # Embed in batches to avoid memory issues
        if len(all_chunks) >= batch_threshold:
            embedder.add_chunks(all_chunks)
            all_chunks = []

    # Embed remaining chunks
    if all_chunks:
        embedder.add_chunks(all_chunks)

    elapsed = time.time() - start_time

    stats = {
        "emails_parsed": total_emails,
        "chunks_created": total_chunks_created,
        "chunks_added": total_chunks_added,
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

    print("\n=== Ingestion Summary ===")
    print(f"Emails parsed: {stats['emails_parsed']}")
    print(f"Chunks created: {stats['chunks_created']}")
    if not stats["dry_run"]:
        print(f"Chunks added: {stats['chunks_added']}")
        print(f"Total in DB: {stats['total_in_db']}")
    print(f"Elapsed: {stats['elapsed_seconds']}s")


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("Value must be a positive integer.")
    return value
        "total_in_db": embedder.count(),
        "elapsed_seconds": round(elapsed, 1),
    }

    print(f"\n{'='*50}")
    print(f"✅ Ingestion complete!")
    print(f"   Emails parsed: {stats['emails_parsed']}")
    print(f"   Chunks created: {stats['chunks_created']}")
    print(f"   Total in database: {stats['total_in_db']}")
    print(f"   Time: {stats['elapsed_seconds']}s")
    print()

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Ingest Outlook .olm export into the email RAG database."
    )
    parser.add_argument(
        "olm_path",
        help="Path to the .olm file to ingest.",
    )
    parser.add_argument(
        "--chromadb-path",
        default=None,
        help="Custom path for ChromaDB storage (default: data/chromadb).",
    )

    args = parser.parse_args()
    ingest(args.olm_path, chromadb_path=args.chromadb_path)


if __name__ == "__main__":
    main()
