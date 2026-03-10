"""End-to-end ingestion pipeline."""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import queue
import threading
import time
import zipfile
from typing import Any

from dotenv import load_dotenv

from .chunker import EmailChunk, chunk_attachment, chunk_email
from .config import configure_logging, get_settings
from .parse_olm import parse_olm
from .validation import positive_int as _shared_positive_int

logger = logging.getLogger(__name__)

_SENTINEL = object()


class _EmbedPipeline:
    """Background thread that embeds and writes batches while parsing continues.

    Producer (main thread): calls ``submit(chunks, emails)`` to enqueue work.
    Consumer (background thread): embeds chunks and writes emails to SQLite.
    """

    def __init__(
        self,
        embedder: Any,
        email_db: Any,
        entity_extractor_fn: Any,
        batch_size: int,
        ingestion_run_id: int | None = None,
    ) -> None:
        self._embedder = embedder
        self._email_db = email_db
        self._entity_extractor_fn = entity_extractor_fn
        self._batch_size = batch_size
        self._ingestion_run_id = ingestion_run_id
        self._queue: queue.Queue = queue.Queue(maxsize=4)
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None

        self._detailed_timing = False

        # Accumulated stats (written only by consumer thread)
        self.chunks_added = 0
        self.sqlite_inserted = 0
        self.batches_written = 0
        self.embed_seconds = 0.0
        self.write_seconds = 0.0
        self.sqlite_seconds = 0.0
        self.entity_seconds = 0.0
        self.analytics_seconds = 0.0

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def submit(self, chunks: list, emails: list) -> None:
        """Enqueue a batch for the consumer. Blocks if queue is full (backpressure)."""
        if not chunks and not emails:
            return
        if self._error is not None:
            raise self._error
        self._queue.put((chunks, emails))

    def finish(self) -> None:
        """Signal end-of-input and wait for consumer to drain. Re-raises errors."""
        self._queue.put(_SENTINEL)
        if self._thread is not None:
            self._thread.join()
        if self._error is not None:
            raise self._error

    def _run(self) -> None:
        """Consumer loop — runs in background thread."""
        try:
            while True:
                item = self._queue.get()
                if item is _SENTINEL:
                    break
                chunks, emails = item
                self._process_batch(chunks, emails)
        except BaseException as exc:
            self._error = exc
            # Drain the queue so the producer never blocks on a full queue
            while True:
                try:
                    item = self._queue.get_nowait()
                    if item is _SENTINEL:
                        break
                except Exception:
                    break

    def _process_batch(self, chunks: list, emails: list) -> None:
        if self._embedder and chunks:
            t0 = time.monotonic()
            added = self._embedder.add_chunks(chunks, batch_size=self._batch_size)
            dt_embed = time.monotonic() - t0
            self.chunks_added += added
            self.batches_written += 1
            self.embed_seconds += dt_embed
            rate = len(chunks) / dt_embed if dt_embed > 0 else 0
            logger.info(
                "Batch %d: %d chunks embedded in %.1fs (%.0f chunks/s)",
                self.batches_written, len(chunks), dt_embed, rate,
            )

        if self._email_db and emails:
            t0 = time.monotonic()
            inserted_uids = self._email_db.insert_emails_batch(
                emails, ingestion_run_id=self._ingestion_run_id,
            )
            self.sqlite_inserted += len(inserted_uids)
            dt_sqlite = time.monotonic() - t0
            self.sqlite_seconds += dt_sqlite

            # Only run entity extraction + analytics for newly inserted emails
            new_emails = [em for em in emails if em.uid in inserted_uids]
            if len(new_emails) < len(emails):
                logger.debug(
                    "Skipped %d already-inserted emails for entity/analytics processing",
                    len(emails) - len(new_emails),
                )

            t1 = time.monotonic()
            has_entities = False
            if self._entity_extractor_fn:
                for em in new_emails:
                    if not em.clean_body:
                        continue
                    entities = self._entity_extractor_fn(em.clean_body, em.sender_email)
                    if entities:
                        self._email_db.insert_entities_batch(
                            em.uid,
                            [(e.text, e.entity_type, e.normalized_form) for e in entities],
                            commit=False,
                        )
                        has_entities = True
            # Insert Exchange-extracted entities (always available from XML)
            for em in new_emails:
                exchange_entities = _exchange_entities_from_email(em)
                if exchange_entities:
                    self._email_db.insert_entities_batch(em.uid, exchange_entities, commit=False)
                    has_entities = True
            # Single commit for all entity inserts in this batch
            if has_entities:
                self._email_db.conn.commit()
            dt_entity = time.monotonic() - t1
            self.entity_seconds += dt_entity

            # Compute language and sentiment analytics
            t2 = time.monotonic()
            self._compute_analytics(new_emails)
            dt_analytics = time.monotonic() - t2
            self.analytics_seconds += dt_analytics

            self.write_seconds += dt_sqlite + dt_entity + dt_analytics


    def _compute_analytics(self, emails: list) -> None:
        """Detect language and sentiment for emails in this batch."""
        if not self._email_db:
            return
        from .language_detector import detect_language
        from .sentiment_analyzer import analyze as analyze_sentiment

        rows = []
        for em in emails:
            body = em.clean_body
            if not body or len(body.strip()) < 20:
                continue
            lang = detect_language(body)
            sent = analyze_sentiment(body)
            rows.append((
                lang if lang != "unknown" else None,
                sent.sentiment,
                sent.score,
                em.uid,
            ))
        if rows:
            self._email_db.update_analytics_batch(rows)


def _exchange_entities_from_email(email: Any) -> list[tuple[str, str, str]]:
    """Extract entity tuples from Exchange-extracted fields on an Email object.

    Returns list of (entity_text, entity_type, normalized_form) tuples
    suitable for ``insert_entities_batch``.
    """
    entities: list[tuple[str, str, str]] = []

    for link in getattr(email, "exchange_extracted_links", []):
        url = link.get("url", "").strip()
        if url:
            entities.append((url, "url", url.lower()))

    for addr in getattr(email, "exchange_extracted_emails", []):
        addr = addr.strip()
        if addr:
            entities.append((addr, "email", addr.lower()))

    for contact in getattr(email, "exchange_extracted_contacts", []):
        contact = contact.strip()
        if contact:
            entities.append((contact, "person", contact.lower()))

    for meeting in getattr(email, "exchange_extracted_meetings", []):
        subject = meeting.get("subject", "").strip()
        if subject:
            entities.append((subject, "event", subject.lower()))

    return entities


def _hash_file_sha256(filepath: str) -> str:
    """Compute SHA-256 hash of a file using streaming reads."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


_SPACY_MODELS = ["en_core_web_sm", "de_core_news_sm"]


def _auto_download_spacy_models() -> None:
    """Download spaCy language models if not already installed."""
    if os.environ.get("SPACY_AUTO_DOWNLOAD", "1") == "0":
        logger.debug("spaCy auto-download disabled via SPACY_AUTO_DOWNLOAD=0")
        return

    try:
        import spacy  # noqa: F811
    except ImportError:
        logger.debug("spaCy not installed, skipping model download")
        return

    import subprocess
    import sys

    for model_name in _SPACY_MODELS:
        try:
            spacy.load(model_name)
            logger.debug("spaCy model already installed: %s", model_name)
        except OSError:
            logger.info("Downloading spaCy model: %s ...", model_name)
            try:
                subprocess.check_call(
                    [sys.executable, "-m", "spacy", "download", model_name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                logger.info("spaCy model installed: %s", model_name)
            except subprocess.CalledProcessError:
                logger.warning("Failed to download spaCy model: %s", model_name)


class _NoOpProgressBar:
    """Fallback when tqdm is not available."""

    def update(self, n: int = 1) -> None:
        pass

    def close(self) -> None:
        pass

    def set_postfix(self, **kwargs: Any) -> None:
        pass


def _make_progress_bar(total: int | None, desc: str = "", unit: str = "it") -> Any:
    """Create a tqdm progress bar if available, otherwise return a no-op."""
    try:
        from tqdm import tqdm

        return tqdm(total=total, desc=desc, unit=unit)
    except ImportError:
        return _NoOpProgressBar()


def ingest(
    olm_path: str,
    chromadb_path: str | None = None,
    sqlite_path: str | None = None,
    batch_size: int = 500,
    max_emails: int | None = None,
    dry_run: bool = False,
    extract_attachments: bool = False,
    extract_entities: bool = False,
    incremental: bool = False,
    embed_images: bool = False,
    timing: bool = False,
) -> dict:
    """Parse an OLM file and ingest all emails into the vector database."""
    settings = get_settings()
    start_time = time.time()

    # embed_images requires attachments to be extracted
    if embed_images:
        extract_attachments = True

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
        try:
            coll_meta = embedder.collection.metadata
            logger.info("HNSW config: %s", {k: v for k, v in coll_meta.items() if k.startswith("hnsw:")})
        except Exception:
            pass
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

    if embedder and email_db:
        embedder.set_sparse_db(email_db)

    # Entity extractor — prefer NLP (spaCy) if available, fall back to regex
    entity_extractor_fn = None
    if extract_entities and not dry_run:
        try:
            from .nlp_entity_extractor import extract_nlp_entities, is_spacy_available

            if not is_spacy_available():
                _auto_download_spacy_models()
                # Re-check after download attempt
                from .nlp_entity_extractor import reset_model_cache

                reset_model_cache()

            if is_spacy_available():
                entity_extractor_fn = extract_nlp_entities
                logger.info("Entity extraction: spaCy NLP + regex (enhanced)")
            else:
                from .entity_extractor import extract_entities as _extract_entities

                entity_extractor_fn = _extract_entities
                logger.info("Entity extraction: regex-only (spaCy models not available)")
        except ImportError:
            from .entity_extractor import extract_entities as _extract_entities

            entity_extractor_fn = _extract_entities
            logger.info("Entity extraction: regex-only")

    attachment_extractor = None
    if extract_attachments:
        from .attachment_extractor import extract_text

        attachment_extractor = extract_text

    # Image embedder (optional, requires Visualized-BGE-M3 weights)
    image_embedder_fn = None
    if embed_images and not dry_run:
        from .attachment_extractor import (
            _get_image_embedder,
            extract_image_embedding,
            is_image_attachment,
        )

        image_embedder_fn = extract_image_embedding
        # Eagerly initialise the singleton and check availability
        probe = _get_image_embedder()
        if not probe.is_available:
            logger.warning(
                "Image embedding requested but Visualized-BGE weights not found. "
                "Image attachments will be skipped."
            )
            image_embedder_fn = None

    # ── Model preload ──────────────────────────────────────────────
    # Force all model downloads and GPU loading before the ingestion
    # loop so that the pipeline has zero lazy-init overhead.
    t_preload = time.monotonic()

    if embedder:
        logger.info("Warming up embedding model …")
        embedder.warmup()

    if entity_extractor_fn:
        try:
            from .nlp_entity_extractor import preload as _preload_nlp

            _preload_nlp()
        except ImportError:
            pass

    dt_preload = time.monotonic() - t_preload
    if dt_preload > 0.1:
        logger.info("Model preload complete (%.1fs)", dt_preload)

    # Record ingestion start with OLM file hash for chain of custody
    ingestion_run_id = None
    olm_sha256 = None
    olm_file_size = None
    if email_db:
        if os.path.isfile(olm_path):
            olm_sha256 = _hash_file_sha256(olm_path)
            olm_file_size = os.path.getsize(olm_path)
        ingestion_run_id = email_db.record_ingestion_start(
            olm_path,
            olm_sha256=olm_sha256,
            file_size_bytes=olm_file_size,
        )

    total_emails = 0
    total_chunks_created = 0
    total_attachment_chunks = 0
    total_image_embeddings = 0
    total_skipped_incremental = 0
    pending_chunks: list = []
    pending_emails: list = []
    parse_seconds = 0.0
    queue_wait_seconds = 0.0

    # Set up pipeline for non-dry-run mode
    pipeline: _EmbedPipeline | None = None
    if not dry_run:
        pipeline = _EmbedPipeline(
            embedder=embedder,
            email_db=email_db,
            entity_extractor_fn=entity_extractor_fn,
            batch_size=batch_size,
            ingestion_run_id=ingestion_run_id,
        )
        pipeline.start()

    # Progress bar (tqdm if available, else no-op)
    progress = _make_progress_bar(max_emails, desc="Ingesting", unit="email")

    for email in parse_olm(olm_path, extract_attachments=extract_attachments):
        t_parse_start = time.monotonic()
        total_emails += 1

        # Incremental mode: skip already-ingested emails
        if incremental and email_db and email_db.email_exists(email.uid):
            total_skipped_incremental += 1
            progress.update(1)
            parse_seconds += time.monotonic() - t_parse_start
            continue
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
            for att_i, (att_name, att_bytes) in enumerate(email.attachment_contents):
                # Image embedding (separate path from text extraction)
                if image_embedder_fn and is_image_attachment(att_name):
                    img_embedding = image_embedder_fn(att_name, att_bytes)
                    if img_embedding and embedder:
                        img_chunk = EmailChunk(
                            uid=email.uid,
                            chunk_id=f"{email.uid}::img::{att_name}::{att_i}",
                            text=f"[Image attachment: {att_name}]",
                            metadata={
                                "uid": email.uid,
                                "subject": email_dict.get("subject", ""),
                                "sender_name": email_dict.get("sender_name", ""),
                                "sender_email": email_dict.get("sender_email", ""),
                                "date": email_dict.get("date", ""),
                                "folder": email_dict.get("folder", ""),
                                "chunk_type": "image",
                                "filename": att_name,
                            },
                            embedding=img_embedding,
                        )
                        pending_chunks.append(img_chunk)
                        total_chunks_created += 1
                        total_image_embeddings += 1
                    continue

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
                        att_index=att_i,
                    )
                    total_chunks_created += len(att_chunks)
                    total_attachment_chunks += len(att_chunks)
                    if embedder:
                        pending_chunks.extend(att_chunks)

        if total_emails % 100 == 0:
            logger.info("Parsed %s emails (%s chunks).", total_emails, total_chunks_created)

        progress.update(1)

        parse_seconds += time.monotonic() - t_parse_start

        if max_emails is not None and total_emails >= max_emails:
            logger.info("Reached --max-emails=%s; stopping parse loop.", max_emails)
            break

        if len(pending_chunks) >= batch_size or len(pending_emails) >= batch_size:
            if pipeline:
                t_q = time.monotonic()
                pipeline.submit(pending_chunks, pending_emails)
                queue_wait_seconds += time.monotonic() - t_q
                pending_chunks = []
                pending_emails = []

    # Flush remaining
    if pipeline:
        if pending_chunks or pending_emails:
            t_q = time.monotonic()
            pipeline.submit(pending_chunks, pending_emails)
            queue_wait_seconds += time.monotonic() - t_q
        pipeline.finish()

    progress.close()

    elapsed = time.time() - start_time

    total_chunks_added = pipeline.chunks_added if pipeline else 0
    total_batches_written = pipeline.batches_written if pipeline else 0
    sqlite_inserted = pipeline.sqlite_inserted if pipeline else 0

    timing_dict: dict[str, float] = {}
    if pipeline:
        timing_dict["embed_seconds"] = round(pipeline.embed_seconds, 1)
        timing_dict["write_seconds"] = round(pipeline.write_seconds, 1)
        if timing:
            timing_dict["parse_seconds"] = round(parse_seconds, 1)
            timing_dict["queue_wait_seconds"] = round(queue_wait_seconds, 1)
            timing_dict["sqlite_seconds"] = round(pipeline.sqlite_seconds, 1)
            timing_dict["entity_seconds"] = round(pipeline.entity_seconds, 1)
            timing_dict["analytics_seconds"] = round(pipeline.analytics_seconds, 1)

    stats: dict[str, Any] = {
        "emails_parsed": total_emails,
        "chunks_created": total_chunks_created,
        "attachment_chunks": total_attachment_chunks,
        "image_embeddings": total_image_embeddings,
        "chunks_added": total_chunks_added,
        "chunks_skipped": (total_chunks_created - total_chunks_added) if embedder else 0,
        "batches_written": total_batches_written,
        "total_in_db": embedder.count() if embedder else None,
        "sqlite_inserted": sqlite_inserted,
        "skipped_incremental": total_skipped_incremental,
        "dry_run": dry_run,
        "extract_attachments": extract_attachments,
        "extract_entities": extract_entities,
        "incremental": incremental,
        "elapsed_seconds": round(elapsed, 1),
        "timing": timing_dict,
    }

    # Record ingestion completion
    if email_db and ingestion_run_id is not None:
        email_db.record_ingestion_complete(ingestion_run_id, {
            "emails_parsed": total_emails,
            "emails_inserted": sqlite_inserted,
        })

    if email_db:
        email_db.close()

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
        "--embed-images",
        action="store_true",
        help="Embed image attachments (JPG, PNG, etc.) using Visualized-BGE-M3.",
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
        "--incremental",
        action="store_true",
        help="Skip emails already present in SQLite (saves embedding compute on re-runs).",
    )
    parser.add_argument(
        "--reset-index",
        action="store_true",
        help="Delete ChromaDB collection and SQLite DB, then exit.",
    )
    parser.add_argument(
        "--reingest-bodies",
        action="store_true",
        help="Re-parse OLM to backfill body_text/body_html. With --force, also updates subjects and sender names.",
    )
    parser.add_argument(
        "--reingest-metadata",
        action="store_true",
        help="Re-parse OLM to backfill v7 metadata (categories, thread_topic, calendar, references, attachments).",
    )
    parser.add_argument(
        "--reembed",
        action="store_true",
        help="Re-chunk and re-embed all emails from corrected SQLite body text into ChromaDB.",
    )
    parser.add_argument(
        "--reingest-analytics",
        action="store_true",
        help="Backfill language detection and sentiment analysis for emails missing analytics data.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-parse all emails (use with --reingest-bodies to overwrite existing body text and headers).",
    )
    parser.add_argument(
        "--timing",
        action="store_true",
        help="Show per-phase timing breakdown (parse, embed, sqlite, entities, analytics).",
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

    if args.reingest_bodies:
        result = reingest_bodies(args.olm_path, sqlite_path=args.sqlite_path, force=args.force)
        print(result["message"])
        raise SystemExit(0)

    if args.reingest_metadata:
        result = reingest_metadata(args.olm_path, sqlite_path=args.sqlite_path)
        print(result["message"])
        raise SystemExit(0)

    if args.reingest_analytics:
        result = reingest_analytics(sqlite_path=args.sqlite_path)
        print(result["message"])
        raise SystemExit(0)

    if args.reembed:
        result = reembed(
            chromadb_path=args.chromadb_path,
            sqlite_path=args.sqlite_path,
            batch_size=args.batch_size,
        )
        print(result["message"])
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
            incremental=args.incremental,
            embed_images=args.embed_images,
            timing=args.timing,
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


def reingest_bodies(
    olm_path: str,
    sqlite_path: str | None = None,
    force: bool = False,
) -> dict:
    """Backfill body_text/body_html for emails missing them in SQLite.

    This re-parses the OLM file and updates existing SQLite rows that have
    NULL body_text. Useful after upgrading from schema v2 to v3.

    With force=True, re-parses ALL emails and overwrites existing body text
    **and** header fields (subject, sender_name, sender_email, base_subject,
    email_type).  This fixes MIME encoded-word subjects and sender names that
    were stored without decoding during earlier ingestions.
    """
    settings = get_settings()
    from .email_db import EmailDatabase

    resolved_sqlite = sqlite_path or settings.sqlite_path
    email_db = EmailDatabase(resolved_sqlite)

    if force:
        all_uids = email_db.all_uids()
        if not all_uids:
            email_db.close()
            return {"updated": 0, "total": 0, "message": "No emails in database."}
        logger.info("Force re-ingesting bodies and headers for ALL %d emails", len(all_uids))
        updated = 0
        for email in parse_olm(olm_path):
            if email.uid in all_uids:
                email_db.update_body_text(email.uid, email.clean_body, email.body_html)
                email_db.update_headers(
                    email.uid,
                    subject=email.subject,
                    sender_name=email.sender_name,
                    sender_email=email.sender_email,
                    base_subject=email.base_subject,
                    email_type=email.email_type,
                )
                updated += 1
                if updated % 100 == 0:
                    logger.info("Updated %d / %d emails", updated, len(all_uids))
        email_db.close()
        logger.info("Force re-ingestion complete: %d updated", updated)
        return {
            "updated": updated,
            "total": len(all_uids),
            "message": f"Force-updated {updated} of {len(all_uids)} emails (bodies + headers).",
        }

    missing_uids = email_db.uids_missing_body()

    if not missing_uids:
        email_db.close()
        return {"updated": 0, "total_missing": 0, "message": "All emails already have body text."}

    logger.info("Re-ingesting bodies for %d emails", len(missing_uids))
    updated = 0

    for email in parse_olm(olm_path):
        if email.uid in missing_uids:
            email_db.update_body_text(email.uid, email.clean_body, email.body_html)
            updated += 1
            if updated % 100 == 0:
                logger.info("Updated %d / %d bodies", updated, len(missing_uids))

    email_db.close()
    logger.info("Body re-ingestion complete: %d updated", updated)
    return {
        "updated": updated,
        "total_missing": len(missing_uids),
        "message": f"Updated {updated} of {len(missing_uids)} emails with body text.",
    }


def reingest_metadata(
    olm_path: str,
    sqlite_path: str | None = None,
) -> dict:
    """Backfill schema-v7 metadata for existing emails in SQLite.

    Re-parses the OLM file and updates existing rows with categories,
    thread_topic, inference_classification, is_calendar_message,
    references_json, and populates email_categories + attachments tables.
    Also inserts Exchange-extracted entities. Does not re-embed.
    """
    settings = get_settings()
    from .email_db import EmailDatabase

    resolved_sqlite = sqlite_path or settings.sqlite_path
    email_db = EmailDatabase(resolved_sqlite)

    all_uids = email_db.all_uids()
    if not all_uids:
        email_db.close()
        return {"updated": 0, "total": 0, "message": "No emails in database."}

    logger.info("Re-ingesting v7 metadata for %d emails", len(all_uids))
    updated = 0
    exchange_entities_inserted = 0

    for email in parse_olm(olm_path):
        if email.uid not in all_uids:
            continue

        if email_db.update_v7_metadata(email):
            updated += 1

        # Insert Exchange-extracted entities
        exchange_entities = _exchange_entities_from_email(email)
        if exchange_entities:
            email_db.insert_entities_batch(email.uid, exchange_entities)
            exchange_entities_inserted += len(exchange_entities)

        if updated % 100 == 0 and updated > 0:
            logger.info("Updated %d / %d emails", updated, len(all_uids))

    email_db.close()
    logger.info("Metadata re-ingestion complete: %d updated, %d exchange entities", updated, exchange_entities_inserted)
    return {
        "updated": updated,
        "total": len(all_uids),
        "exchange_entities_inserted": exchange_entities_inserted,
        "message": (
            f"Updated {updated} of {len(all_uids)} emails with v7 metadata. "
            f"{exchange_entities_inserted} Exchange entities inserted."
        ),
    }


def reingest_analytics(
    sqlite_path: str | None = None,
) -> dict:
    """Backfill detected_language and sentiment for emails missing analytics.

    Scans all emails in SQLite where detected_language or sentiment_label
    is NULL, runs the zero-dependency language detector and sentiment
    analyzer, and batch-updates the rows.
    """
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

    logger.info("Computing analytics for %d emails", total_missing)
    batch: list[tuple[str | None, str | None, float | None, str]] = []
    for row in rows:
        body = row["body_text"]
        lang = detect_language(body)
        sent = analyze_sentiment(body)
        batch.append((
            lang if lang != "unknown" else None,
            sent.sentiment,
            sent.score,
            row["uid"],
        ))

    updated = email_db.update_analytics_batch(batch)
    email_db.close()
    logger.info("Analytics backfill complete: %d updated", updated)
    return {
        "updated": updated,
        "total_missing": total_missing,
        "message": f"Computed language and sentiment for {updated} emails.",
    }


def reembed(
    chromadb_path: str | None = None,
    sqlite_path: str | None = None,
    batch_size: int = 100,
) -> dict:
    """Re-chunk and re-embed all emails from corrected SQLite body text.

    Reads body_text from SQLite (already fixed by --reingest-bodies --force),
    re-chunks each email, and upserts new embeddings into ChromaDB.  Old chunks
    whose IDs no longer exist after re-chunking are deleted.  Sparse vectors
    are updated via INSERT OR REPLACE.

    This is the recommended way to rebuild search quality after parser fixes
    without a full reset-and-reingest cycle.
    """
    settings = get_settings()
    from .chunker import chunk_email
    from .email_db import EmailDatabase
    from .embedder import EmailEmbedder

    resolved_sqlite = sqlite_path or settings.sqlite_path
    email_db = EmailDatabase(resolved_sqlite)
    embedder = EmailEmbedder(chromadb_path=chromadb_path)
    embedder.set_sparse_db(email_db)

    all_uids = email_db.all_uids()
    if not all_uids:
        email_db.close()
        return {"reembedded": 0, "total": 0, "message": "No emails in database."}

    logger.info("Re-embedding %d emails from SQLite body text", len(all_uids))

    reembedded = 0
    chunks_deleted = 0
    chunks_added = 0
    skipped_no_body = 0

    for uid in sorted(all_uids):
        email_dict = email_db.get_email_for_reembed(uid)
        if email_dict is None:
            skipped_no_body += 1
            continue

        # Delete old chunks (ChromaDB + sparse)
        deleted = embedder.delete_chunks_by_uid(uid)
        email_db.delete_sparse_by_uid(uid)
        chunks_deleted += deleted

        # Re-chunk and upsert
        chunks = chunk_email(email_dict)
        added = embedder.upsert_chunks(chunks, batch_size=batch_size)
        chunks_added += added
        reembedded += 1

        if reembedded % 100 == 0:
            logger.info(
                "Re-embedded %d / %d emails (%d chunks)",
                reembedded, len(all_uids), chunks_added,
            )

    embedder.close()
    email_db.close()
    logger.info(
        "Re-embedding complete: %d emails, %d chunks deleted, %d chunks added, %d skipped (no body)",
        reembedded, chunks_deleted, chunks_added, skipped_no_body,
    )
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
        if stats.get("skipped_incremental", 0) > 0:
            lines.append(f"Skipped (incremental): {stats['skipped_incremental']}")

    timing_info = stats.get("timing")
    if timing_info:
        parts = []
        if timing_info.get("embed_seconds"):
            parts.append(f"embed={timing_info['embed_seconds']}s")
        if timing_info.get("write_seconds"):
            parts.append(f"write={timing_info['write_seconds']}s")
        if parts:
            lines.append(f"Timing: {', '.join(parts)}")
        # Detailed sub-phase breakdown (when --timing flag is used)
        detail_keys = [
            ("parse_seconds", "parse"),
            ("queue_wait_seconds", "queue_wait"),
            ("sqlite_seconds", "sqlite"),
            ("entity_seconds", "entities"),
            ("analytics_seconds", "analytics"),
        ]
        detail_parts = []
        for key, label in detail_keys:
            if key in timing_info:
                detail_parts.append(f"{label}={timing_info[key]}s")
        if detail_parts:
            lines.append(f"  Breakdown: {', '.join(detail_parts)}")

    lines.append(f"Elapsed: {stats['elapsed_seconds']}s")
    return lines


if __name__ == "__main__":
    main()
