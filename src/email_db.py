"""SQLite relational store for email metadata and relationships."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from .db_analytics import AnalyticsMixin
from .db_attachments import AttachmentMixin
from .db_custody import CustodyMixin
from .db_entities import EntityMixin
from .db_evidence import EvidenceMixin
from .db_queries import QueryMixin
from .db_schema import _escape_like, init_schema

if TYPE_CHECKING:
    from src.parse_olm import Email

logger = logging.getLogger(__name__)

_ADDR_RE = re.compile(r"^(.*?)\s*<([^>]+)>$")


def _parse_address(raw: str) -> tuple[str, str]:
    """Parse 'Display Name <email>' into (name, email).

    Handles bare email addresses and name-only strings.
    """
    raw = raw.strip()
    if not raw:
        return ("", "")
    m = _ADDR_RE.match(raw)
    if m:
        return (m.group(1).strip().strip('"'), m.group(2).strip())
    if "@" in raw:
        return ("", raw)
    return (raw, "")


class EmailDatabase(CustodyMixin, EvidenceMixin, EntityMixin, AnalyticsMixin, AttachmentMixin, QueryMixin):
    """SQLite-backed relational store for email metadata.

    Method groups are organized into mixins for maintainability:
    - ``CustodyMixin`` — chain-of-custody audit trail and ingestion tracking
    - ``EvidenceMixin`` — evidence item CRUD, verification, search
    - ``EntityMixin`` — NLP entity insert, search, timeline
    - ``AnalyticsMixin`` — clusters, topics, keywords, contacts, relationships
    - ``QueryMixin`` — read queries, full-body retrieval, browsing, consistency
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._conn_lock = threading.Lock()

    @property
    def conn(self) -> sqlite3.Connection:  # type: ignore[override]  # mixin stubs declare as attribute
        # Fast path: connection already initialized (no lock needed).
        if self._conn is not None:
            return self._conn
        # Slow path: first access — serialize with lock to prevent two
        # threads from racing to create the connection.  Only contends once.
        with self._conn_lock:
            if self._conn is None:
                if self._db_path != ":memory:":
                    Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute("PRAGMA busy_timeout = 5000")
                self._conn.execute("PRAGMA foreign_keys=ON")
                self._conn.row_factory = sqlite3.Row
                init_schema(self._conn)
        return self._conn

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Sparse vector storage

    def insert_sparse_batch(
        self,
        chunk_ids: list[str],
        sparse_vectors: list[dict[int, float]],
    ) -> int:
        """Insert sparse vectors for chunks. Returns count of inserted rows."""
        import struct

        if len(chunk_ids) != len(sparse_vectors):
            raise ValueError("chunk_ids and sparse_vectors must have same length")

        rows: list[tuple] = []
        for cid, sv in zip(chunk_ids, sparse_vectors, strict=True):
            if not sv:
                continue
            token_ids = sorted(sv.keys())
            weights = [sv[tid] for tid in token_ids]

            token_blob = struct.pack(f"<{len(token_ids)}i", *token_ids)
            weight_blob = struct.pack(f"<{len(weights)}f", *weights)

            rows.append((cid, token_blob, weight_blob, len(token_ids)))

        if rows:
            self.conn.executemany(
                "INSERT OR REPLACE INTO sparse_vectors(chunk_id, token_ids, weights, num_tokens) VALUES(?, ?, ?, ?)",
                rows,
            )
            self.conn.commit()
        return len(rows)

    def get_sparse_vector(self, chunk_id: str) -> dict[int, float] | None:
        """Retrieve a single sparse vector by chunk_id."""
        import struct

        row = self.conn.execute(
            "SELECT token_ids, weights, num_tokens FROM sparse_vectors WHERE chunk_id = ?",
            (chunk_id,),
        ).fetchone()
        if not row:
            return None

        n = row["num_tokens"]
        token_ids = list(struct.unpack(f"<{n}i", row["token_ids"]))
        weights = list(struct.unpack(f"<{n}f", row["weights"]))
        return dict(zip(token_ids, weights, strict=True))

    def sparse_vector_count(self) -> int:
        """Count of stored sparse vectors."""
        row = self.conn.execute("SELECT COUNT(*) AS c FROM sparse_vectors").fetchone()
        return row["c"] if row else 0

    def all_sparse_vectors(self) -> dict[str, dict[int, float]]:
        """Load all sparse vectors into memory (for building inverted index)."""
        import struct

        result = {}
        for row in self.conn.execute("SELECT chunk_id, token_ids, weights, num_tokens FROM sparse_vectors"):
            n = row["num_tokens"]
            token_ids = list(struct.unpack(f"<{n}i", row["token_ids"]))
            weights = list(struct.unpack(f"<{n}f", row["weights"]))
            result[row["chunk_id"]] = dict(zip(token_ids, weights, strict=True))
        return result

    # ------------------------------------------------------------------
    # Analytics batch update
    # ------------------------------------------------------------------

    def update_analytics_batch(
        self,
        rows: list[tuple[str | None, str | None, float | None, str]],
    ) -> int:
        """Batch-update detected_language, sentiment_label, sentiment_score by uid.

        Each tuple: (detected_language, sentiment_label, sentiment_score, uid).
        Returns number of rows updated.
        """
        self.conn.executemany(
            "UPDATE emails SET detected_language=?, sentiment_label=?, sentiment_score=? WHERE uid=?",
            rows,
        )
        self.conn.commit()
        return len(rows)

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def insert_email(self, email: Email, *, ingestion_run_id: int | None = None) -> bool:
        """Insert a single email and update contacts/edges.

        Returns False if uid already exists (duplicate).
        """
        cur = self.conn.cursor()
        content_sha256 = self.compute_content_hash(email.clean_body) if email.clean_body else None
        categories_json = json.dumps(getattr(email, "categories", []) or [])
        references_json = json.dumps(getattr(email, "references", []) or [])
        try:
            cur.execute(
                """INSERT INTO emails (uid, message_id, subject, sender_name,
                   sender_email, date, folder, email_type, has_attachments,
                   attachment_count, priority, is_read, conversation_id,
                   in_reply_to, base_subject, body_length, body_text, body_html,
                   content_sha256, categories, thread_topic, inference_classification,
                   is_calendar_message, references_json, ingestion_run_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    email.uid,
                    email.message_id,
                    email.subject,
                    email.sender_name,
                    email.sender_email,
                    email.date,
                    email.folder,
                    email.email_type,
                    int(email.has_attachments),
                    len(email.attachment_names),
                    email.priority,
                    int(email.is_read),
                    email.conversation_id,
                    email.in_reply_to,
                    email.base_subject,
                    len(email.clean_body) if email.clean_body else 0,
                    email.clean_body,
                    email.body_html,
                    content_sha256,
                    categories_json,
                    getattr(email, "thread_topic", "") or "",
                    getattr(email, "inference_classification", "") or "",
                    int(getattr(email, "is_calendar_message", False)),
                    references_json,
                    ingestion_run_id,
                ),
            )
        except sqlite3.IntegrityError:
            return False

        try:
            # Categories (normalized table)
            cats = getattr(email, "categories", []) or []
            if cats:
                cur.executemany(
                    "INSERT OR IGNORE INTO email_categories(email_uid, category) VALUES(?,?)",
                    [(email.uid, cat) for cat in cats],
                )

            # Attachments table
            atts = getattr(email, "attachments", []) or []
            if atts:
                cur.executemany(
                    "INSERT INTO attachments(email_uid, name, mime_type, size, content_id, is_inline) VALUES(?,?,?,?,?,?)",
                    [
                        (
                            email.uid,
                            att.get("name", ""),
                            att.get("mime_type", ""),
                            att.get("size", 0),
                            att.get("content_id", ""),
                            int(att.get("is_inline", False)),
                        )
                        for att in atts
                    ],
                )

            # Recipients (to + cc + bcc in one batch)
            all_recipients: list[tuple[str, str]] = []
            recipient_rows: list[tuple] = []
            for addr, rtype in (
                *[(a, "to") for a in email.to],
                *[(a, "cc") for a in email.cc],
                *[(a, "bcc") for a in email.bcc],
            ):
                name, em = _parse_address(addr)
                recipient_rows.append((email.uid, em or addr, name, rtype))
                all_recipients.append((name, em or addr))
            if recipient_rows:
                cur.executemany(
                    "INSERT INTO recipients(email_uid, address, display_name, type) VALUES(?,?,?,?)",
                    recipient_rows,
                )

            # Upsert contacts
            if email.sender_email:
                self._upsert_contact(cur, email.sender_email, email.sender_name, email.date, "sender")
            for name, em in all_recipients:
                if em:
                    self._upsert_contact(cur, em, name, email.date, "recipient")

            # Communication edges
            if email.sender_email:
                for _, em in all_recipients:
                    if em:
                        self._upsert_communication_edge(cur, email.sender_email, em, email.date)

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return True

    def insert_emails_batch(
        self,
        emails: list[Email],
        ingestion_run_id: int | None = None,
    ) -> set[str]:
        """Insert a batch of emails in a single transaction.

        Returns the set of UIDs that were actually inserted (new emails).
        The set is truthy/has ``len()`` so callers that only need the count
        still work via ``len(result)`` or ``bool(result)``.

        Uses batched parameter collection for recipients, categories,
        attachments, contacts, and communication edges to reduce per-row
        execute() overhead.
        """
        inserted_uids: set[str] = set()
        cur = self.conn.cursor()

        # Collect rows for executemany() across the whole batch
        recipient_rows: list[tuple] = []
        category_rows: list[tuple] = []
        attachment_rows: list[tuple] = []
        contact_rows: list[tuple] = []
        edge_rows: list[tuple] = []

        try:
            for email in emails:
                content_sha256 = self.compute_content_hash(email.clean_body) if email.clean_body else None
                categories_json = json.dumps(getattr(email, "categories", []) or [])
                references_json = json.dumps(getattr(email, "references", []) or [])
                cur.execute(
                    """INSERT OR IGNORE INTO emails (uid, message_id, subject, sender_name,
                       sender_email, date, folder, email_type, has_attachments,
                       attachment_count, priority, is_read, conversation_id,
                       in_reply_to, base_subject, body_length, body_text, body_html,
                       content_sha256, categories, thread_topic,
                       inference_classification, is_calendar_message, references_json,
                       ingestion_run_id)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        email.uid,
                        email.message_id,
                        email.subject,
                        email.sender_name,
                        email.sender_email,
                        email.date,
                        email.folder,
                        email.email_type,
                        int(email.has_attachments),
                        len(email.attachment_names),
                        email.priority,
                        int(email.is_read),
                        email.conversation_id,
                        email.in_reply_to,
                        email.base_subject,
                        len(email.clean_body) if email.clean_body else 0,
                        email.clean_body,
                        email.body_html,
                        content_sha256,
                        categories_json,
                        getattr(email, "thread_topic", "") or "",
                        getattr(email, "inference_classification", "") or "",
                        int(getattr(email, "is_calendar_message", False)),
                        references_json,
                        ingestion_run_id,
                    ),
                )
                if cur.rowcount == 0:
                    continue

                # Collect categories for batch insert
                for cat in getattr(email, "categories", []) or []:
                    category_rows.append((email.uid, cat))

                # Collect attachments for batch insert
                for att in getattr(email, "attachments", []) or []:
                    attachment_rows.append(
                        (
                            email.uid,
                            att.get("name", ""),
                            att.get("mime_type", ""),
                            att.get("size", 0),
                            att.get("content_id", ""),
                            int(att.get("is_inline", False)),
                        )
                    )

                # Collect recipients for batch insert
                all_recipients: list[tuple[str, str]] = []
                for addr in email.to:
                    name, em = _parse_address(addr)
                    recipient_rows.append((email.uid, em or addr, name, "to"))
                    all_recipients.append((name, em or addr))
                for addr in email.cc:
                    name, em = _parse_address(addr)
                    recipient_rows.append((email.uid, em or addr, name, "cc"))
                    all_recipients.append((name, em or addr))
                for addr in email.bcc:
                    name, em = _parse_address(addr)
                    recipient_rows.append((email.uid, em or addr, name, "bcc"))
                    all_recipients.append((name, em or addr))

                # Collect contacts for batch upsert
                if email.sender_email:
                    contact_rows.append(
                        (
                            email.sender_email,
                            email.sender_name,
                            email.date,
                            email.date,
                            1,
                            0,
                        )
                    )
                for name, em in all_recipients:
                    if em:
                        contact_rows.append((em, name, email.date, email.date, 0, 1))

                # Collect edges for batch upsert
                if email.sender_email:
                    for _, em in all_recipients:
                        if em:
                            edge_rows.append((email.sender_email, em, email.date, email.date))

                inserted_uids.add(email.uid)

            # Batch insert collected rows
            if recipient_rows:
                cur.executemany(
                    "INSERT INTO recipients(email_uid, address, display_name, type) VALUES(?,?,?,?)",
                    recipient_rows,
                )
            if category_rows:
                cur.executemany(
                    "INSERT OR IGNORE INTO email_categories(email_uid, category) VALUES(?,?)",
                    category_rows,
                )
            if attachment_rows:
                cur.executemany(
                    "INSERT INTO attachments(email_uid, name, mime_type, size, content_id, is_inline) VALUES(?,?,?,?,?,?)",
                    attachment_rows,
                )
            if contact_rows:
                cur.executemany(
                    """INSERT INTO contacts(email_address, display_name, first_seen, last_seen,
                       sent_count, received_count)
                       VALUES(?, ?, ?, ?, ?, ?)
                       ON CONFLICT(email_address) DO UPDATE SET
                         display_name = COALESCE(NULLIF(excluded.display_name, ''), contacts.display_name),
                         first_seen = MIN(contacts.first_seen, excluded.first_seen),
                         last_seen = MAX(contacts.last_seen, excluded.last_seen),
                         sent_count = contacts.sent_count + excluded.sent_count,
                         received_count = contacts.received_count + excluded.received_count
                    """,
                    contact_rows,
                )
            if edge_rows:
                cur.executemany(
                    """INSERT INTO communication_edges(sender_email, recipient_email,
                       email_count, first_date, last_date)
                       VALUES(?, ?, 1, ?, ?)
                       ON CONFLICT(sender_email, recipient_email) DO UPDATE SET
                         email_count = communication_edges.email_count + 1,
                         first_date = MIN(communication_edges.first_date, excluded.first_date),
                         last_date = MAX(communication_edges.last_date, excluded.last_date)
                    """,
                    edge_rows,
                )

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return inserted_uids

    def _upsert_contact(
        self,
        cur: sqlite3.Cursor,
        email_address: str,
        display_name: str,
        date: str,
        role: str,
    ) -> None:
        cur.execute(
            """INSERT INTO contacts(email_address, display_name, first_seen, last_seen,
               sent_count, received_count)
               VALUES(?, ?, ?, ?, ?, ?)
               ON CONFLICT(email_address) DO UPDATE SET
                 display_name = COALESCE(NULLIF(excluded.display_name, ''), contacts.display_name),
                 first_seen = MIN(contacts.first_seen, excluded.first_seen),
                 last_seen = MAX(contacts.last_seen, excluded.last_seen),
                 sent_count = contacts.sent_count + excluded.sent_count,
                 received_count = contacts.received_count + excluded.received_count
            """,
            (
                email_address,
                display_name,
                date,
                date,
                1 if role == "sender" else 0,
                1 if role == "recipient" else 0,
            ),
        )

    def _upsert_communication_edge(
        self,
        cur: sqlite3.Cursor,
        sender: str,
        recipient: str,
        date: str,
    ) -> None:
        cur.execute(
            """INSERT INTO communication_edges(sender_email, recipient_email,
               email_count, first_date, last_date)
               VALUES(?, ?, 1, ?, ?)
               ON CONFLICT(sender_email, recipient_email) DO UPDATE SET
                 email_count = communication_edges.email_count + 1,
                 first_date = MIN(communication_edges.first_date, excluded.first_date),
                 last_date = MAX(communication_edges.last_date, excluded.last_date)
            """,
            (sender, recipient, date, date),
        )

    # ------------------------------------------------------------------
    # Read operations — see QueryMixin (db_queries.py)
    # ------------------------------------------------------------------

    def update_body_text(self, uid: str, body_text: str, body_html: str, *, commit: bool = True) -> bool:
        """Update body_text and body_html for an existing email.

        Only overwrites body_html if the new value is non-empty, to avoid
        losing good HTML content when the re-parsed email lacks an HTML body.
        Returns True if updated.  Pass ``commit=False`` to defer the commit
        (caller is responsible for calling ``self.conn.commit()``).
        """
        if body_html:
            cur = self.conn.execute(
                "UPDATE emails SET body_text = ?, body_html = ? WHERE uid = ?",
                (body_text, body_html, uid),
            )
        else:
            cur = self.conn.execute(
                "UPDATE emails SET body_text = ? WHERE uid = ?",
                (body_text, uid),
            )
        if commit:
            self.conn.commit()
        return cur.rowcount > 0

    def update_headers(
        self,
        uid: str,
        subject: str,
        sender_name: str,
        sender_email: str,
        base_subject: str,
        email_type: str,
        *,
        commit: bool = True,
    ) -> bool:
        """Update decoded header fields for an existing email.

        Fixes MIME encoded-word subjects and sender names that were stored
        without decoding during earlier ingestions.  Returns True if updated.
        Pass ``commit=False`` to defer the commit.
        """
        cur = self.conn.execute(
            """UPDATE emails
               SET subject = ?, sender_name = ?, sender_email = ?,
                   base_subject = ?, email_type = ?
             WHERE uid = ?""",
            (subject, sender_name, sender_email, base_subject, email_type, uid),
        )
        if commit:
            self.conn.commit()
        return cur.rowcount > 0

    def update_v7_metadata(self, email: Email, *, commit: bool = True) -> bool:
        """Update schema-v7 metadata fields for an existing email.

        Populates categories, thread_topic, inference_classification,
        is_calendar_message, references_json, and related tables
        (email_categories, attachments). Returns True if updated.
        Pass ``commit=False`` to defer the commit.
        """
        categories_json = json.dumps(getattr(email, "categories", []) or [])
        references_json = json.dumps(getattr(email, "references", []) or [])

        cur = self.conn.cursor()
        cur.execute(
            """UPDATE emails
               SET categories = ?, thread_topic = ?, inference_classification = ?,
                   is_calendar_message = ?, references_json = ?
             WHERE uid = ?""",
            (
                categories_json,
                getattr(email, "thread_topic", "") or "",
                getattr(email, "inference_classification", "") or "",
                int(getattr(email, "is_calendar_message", False)),
                references_json,
                email.uid,
            ),
        )
        if cur.rowcount == 0:
            return False

        # Upsert categories
        cats = getattr(email, "categories", []) or []
        if cats:
            cur.executemany(
                "INSERT OR IGNORE INTO email_categories(email_uid, category) VALUES(?,?)",
                [(email.uid, cat) for cat in cats],
            )

        # Upsert attachments
        cur.execute("DELETE FROM attachments WHERE email_uid = ?", (email.uid,))
        atts = getattr(email, "attachments", []) or []
        if atts:
            cur.executemany(
                "INSERT INTO attachments(email_uid, name, mime_type, size, content_id, is_inline) VALUES(?,?,?,?,?,?)",
                [
                    (
                        email.uid,
                        att.get("name", ""),
                        att.get("mime_type", ""),
                        att.get("size", 0),
                        att.get("content_id", ""),
                        int(att.get("is_inline", False)),
                    )
                    for att in atts
                ],
            )

        if commit:
            self.conn.commit()
        return True

    def all_uids(self) -> set[str]:
        """Return all UIDs in the database."""
        rows = self.conn.execute("SELECT uid FROM emails").fetchall()
        return {r["uid"] for r in rows}

    def uids_missing_body(self) -> set[str]:
        """Return UIDs of emails where body_text is NULL."""
        rows = self.conn.execute("SELECT uid FROM emails WHERE body_text IS NULL").fetchall()
        return {r["uid"] for r in rows}

    def delete_sparse_by_uid(self, uid: str) -> int:
        """Delete sparse vectors for all chunks of an email. Returns count deleted."""
        cur = self.conn.execute(
            "DELETE FROM sparse_vectors WHERE chunk_id LIKE ? ESCAPE '\\'",
            (f"{_escape_like(uid)}\\_\\_%",),
        )
        self.conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # Evidence management (see EvidenceMixin)
    # Ingestion tracking (see CustodyMixin)
    # Chain of custody (see CustodyMixin)
    # Re-embed, grouping, consistency — see QueryMixin (db_queries.py)
    # ------------------------------------------------------------------
