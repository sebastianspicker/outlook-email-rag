"""
End-to-end ingestion pipeline.

Usage:
    python -m src.ingest data/your-export.olm
    python -m src.ingest data/your-export.olm --chromadb-path data/chromadb
"""

import argparse
import time
import sys

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
