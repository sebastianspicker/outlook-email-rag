"""SQLite relational store for email metadata and relationships."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from .db_analytics import AnalyticsMixin
from .db_attachments import AttachmentMixin
from .db_custody import CustodyMixin
from .db_entities import EntityMixin
from .db_evidence import EvidenceMixin
from .db_schema import init_schema

if TYPE_CHECKING:
    from src.parse_olm import Email

logger = logging.getLogger(__name__)

_ADDR_RE = re.compile(r"^(.*?)\s*<([^>]+)>$")


def _safe_json_parse(raw: str | None, default=None):
    """Parse a JSON string, returning default (or []) on failure."""
    if not raw or not isinstance(raw, str):
        return default if default is not None else []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else []


def _format_recipient(name: str | None, addr: str) -> str:
    """Format a recipient as 'Name <addr>' or bare addr."""
    return f"{name} <{addr}>" if name else addr


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


class EmailDatabase(CustodyMixin, EvidenceMixin, EntityMixin, AnalyticsMixin, AttachmentMixin):
    """SQLite-backed relational store for email metadata.

    Method groups are organized into mixins for maintainability:
    - ``CustodyMixin`` — chain-of-custody audit trail and ingestion tracking
    - ``EvidenceMixin`` — evidence item CRUD, verification, search
    - ``EntityMixin`` — NLP entity insert, search, timeline
    - ``AnalyticsMixin`` — clusters, topics, keywords, contacts, relationships
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            if self._db_path != ":memory:":
                Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.row_factory = sqlite3.Row
            init_schema(self._conn)
        return self._conn

    def close(self) -> None:
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

        inserted = 0
        for cid, sv in zip(chunk_ids, sparse_vectors):
            if not sv:
                continue
            token_ids = sorted(sv.keys())
            weights = [sv[tid] for tid in token_ids]

            token_blob = struct.pack(f"<{len(token_ids)}i", *token_ids)
            weight_blob = struct.pack(f"<{len(weights)}f", *weights)

            self.conn.execute(
                "INSERT OR REPLACE INTO sparse_vectors(chunk_id, token_ids, weights, num_tokens) "
                "VALUES(?, ?, ?, ?)",
                (cid, token_blob, weight_blob, len(token_ids)),
            )
            inserted += 1
        self.conn.commit()
        return inserted

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
        return dict(zip(token_ids, weights))

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
            result[row["chunk_id"]] = dict(zip(token_ids, weights))
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

    def insert_email(self, email: Email) -> bool:
        """Insert a single email and update contacts/edges.

        Returns False if uid already exists (duplicate).
        """
        cur = self.conn.cursor()
        categories_json = json.dumps(getattr(email, "categories", []) or [])
        references_json = json.dumps(getattr(email, "references", []) or [])
        try:
            cur.execute(
                """INSERT INTO emails (uid, message_id, subject, sender_name,
                   sender_email, date, folder, email_type, has_attachments,
                   attachment_count, priority, is_read, conversation_id,
                   in_reply_to, base_subject, body_length, body_text, body_html,
                   categories, thread_topic, inference_classification,
                   is_calendar_message, references_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    len(email.clean_body),
                    email.clean_body,
                    email.body_html,
                    categories_json,
                    getattr(email, "thread_topic", "") or "",
                    getattr(email, "inference_classification", "") or "",
                    int(getattr(email, "is_calendar_message", False)),
                    references_json,
                ),
            )
        except sqlite3.IntegrityError:
            return False

        # Categories (normalized table)
        for cat in getattr(email, "categories", []) or []:
            cur.execute(
                "INSERT OR IGNORE INTO email_categories(email_uid, category) VALUES(?,?)",
                (email.uid, cat),
            )

        # Attachments table
        for att in getattr(email, "attachments", []) or []:
            cur.execute(
                "INSERT INTO attachments(email_uid, name, mime_type, size, content_id, is_inline) "
                "VALUES(?,?,?,?,?,?)",
                (
                    email.uid,
                    att.get("name", ""),
                    att.get("mime_type", ""),
                    att.get("size", 0),
                    att.get("content_id", ""),
                    int(att.get("is_inline", False)),
                ),
            )

        # Recipients
        all_recipients: list[tuple[str, str]] = []
        for addr in email.to:
            name, em = _parse_address(addr)
            cur.execute(
                "INSERT INTO recipients(email_uid, address, display_name, type) VALUES(?,?,?,?)",
                (email.uid, em or addr, name, "to"),
            )
            all_recipients.append((name, em or addr))
        for addr in email.cc:
            name, em = _parse_address(addr)
            cur.execute(
                "INSERT INTO recipients(email_uid, address, display_name, type) VALUES(?,?,?,?)",
                (email.uid, em or addr, name, "cc"),
            )
            all_recipients.append((name, em or addr))
        for addr in email.bcc:
            name, em = _parse_address(addr)
            cur.execute(
                "INSERT INTO recipients(email_uid, address, display_name, type) VALUES(?,?,?,?)",
                (email.uid, em or addr, name, "bcc"),
            )
            all_recipients.append((name, em or addr))

        # Upsert contacts
        if email.sender_email:
            self._upsert_contact(
                cur, email.sender_email, email.sender_name, email.date, "sender"
            )
        for name, em in all_recipients:
            if em:
                self._upsert_contact(cur, em, name, email.date, "recipient")

        # Communication edges
        if email.sender_email:
            for _, em in all_recipients:
                if em:
                    self._upsert_communication_edge(
                        cur, email.sender_email, em, email.date
                    )

        self.conn.commit()
        return True

    def insert_emails_batch(self, emails: list[Email]) -> int:
        """Insert a batch of emails in a single transaction. Returns count inserted.

        Uses batched parameter collection for recipients, categories,
        attachments, contacts, and communication edges to reduce per-row
        execute() overhead.
        """
        inserted = 0
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
                       inference_classification, is_calendar_message, references_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                        len(email.clean_body),
                        email.clean_body,
                        email.body_html,
                        content_sha256,
                        categories_json,
                        getattr(email, "thread_topic", "") or "",
                        getattr(email, "inference_classification", "") or "",
                        int(getattr(email, "is_calendar_message", False)),
                        references_json,
                    ),
                )
                if cur.rowcount == 0:
                    continue

                # Collect categories for batch insert
                for cat in getattr(email, "categories", []) or []:
                    category_rows.append((email.uid, cat))

                # Collect attachments for batch insert
                for att in getattr(email, "attachments", []) or []:
                    attachment_rows.append((
                        email.uid,
                        att.get("name", ""),
                        att.get("mime_type", ""),
                        att.get("size", 0),
                        att.get("content_id", ""),
                        int(att.get("is_inline", False)),
                    ))

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
                    contact_rows.append((
                        email.sender_email, email.sender_name,
                        email.date, email.date, 1, 0,
                    ))
                for name, em in all_recipients:
                    if em:
                        contact_rows.append((em, name, email.date, email.date, 0, 1))

                # Collect edges for batch upsert
                if email.sender_email:
                    for _, em in all_recipients:
                        if em:
                            edge_rows.append((email.sender_email, em, email.date, email.date))

                inserted += 1

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
                    "INSERT INTO attachments(email_uid, name, mime_type, size, content_id, is_inline) "
                    "VALUES(?,?,?,?,?,?)",
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
        return inserted

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
    # Read operations
    # ------------------------------------------------------------------

    def email_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM emails").fetchone()
        return row["c"]

    def unique_sender_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(DISTINCT sender_email) AS c FROM emails"
        ).fetchone()
        return row["c"]

    def date_range(self) -> tuple[str, str]:
        row = self.conn.execute(
            "SELECT MIN(date) AS min_d, MAX(date) AS max_d FROM emails"
        ).fetchone()
        return (row["min_d"] or "", row["max_d"] or "")

    def folder_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT folder, COUNT(*) AS c FROM emails GROUP BY folder ORDER BY c DESC"
        ).fetchall()
        return {r["folder"]: r["c"] for r in rows}

    def top_senders(self, limit: int = 30) -> list[dict]:
        rows = self.conn.execute(
            """SELECT sender_email, sender_name, COUNT(*) AS message_count
               FROM emails
               GROUP BY sender_email
               ORDER BY message_count DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def email_exists(self, uid: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM emails WHERE uid = ?", (uid,)
        ).fetchone()
        return row is not None

    def emails_by_sender(self, sender_email: str, limit: int = 100) -> list[dict]:
        """Get emails from a specific sender.

        Args:
            sender_email: Sender's email address (partial match).
            limit: Maximum emails to return.

        Returns:
            List of email dicts with uid, subject, body_text, date.
        """
        rows = self.conn.execute(
            """SELECT uid, subject, body_text, date, sender_name, sender_email
               FROM emails
               WHERE sender_email LIKE ?
               ORDER BY date DESC LIMIT ?""",
            (f"%{sender_email}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Category / Calendar / Attachment queries (schema v7)
    # ------------------------------------------------------------------

    def emails_by_category(self, category: str, limit: int = 50) -> list[dict]:
        """Get emails with a specific category."""
        rows = self.conn.execute(
            """SELECT e.uid, e.subject, e.sender_email, e.date, e.folder
               FROM email_categories ec
               JOIN emails e ON ec.email_uid = e.uid
               WHERE ec.category = ?
               ORDER BY e.date DESC LIMIT ?""",
            (category, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def category_counts(self) -> list[dict]:
        """Get category names with email counts."""
        rows = self.conn.execute(
            """SELECT category, COUNT(*) AS count
               FROM email_categories
               GROUP BY category
               ORDER BY count DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def calendar_emails(
        self, date_from: str | None = None, date_to: str | None = None, limit: int = 50,
    ) -> list[dict]:
        """Get calendar/meeting emails, optionally filtered by date."""
        query = "SELECT uid, subject, sender_email, date, folder FROM emails WHERE is_calendar_message = 1"
        params: list = []
        if date_from:
            query += " AND date >= ?"
            params.append(date_from)
        if date_to:
            query += " AND date <= ?"
            params.append(date_to)
        query += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def thread_by_references(self, message_id: str, limit: int = 50) -> list[dict]:
        """Find emails whose references_json contains a given message-id."""
        rows = self.conn.execute(
            """SELECT uid, subject, sender_email, date, folder
               FROM emails
               WHERE references_json LIKE ?
               ORDER BY date ASC LIMIT ?""",
            (f"%{message_id}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def thread_by_topic(self, thread_topic: str, limit: int = 50) -> list[dict]:
        """Find all emails sharing a thread topic."""
        rows = self.conn.execute(
            """SELECT uid, subject, sender_email, date, folder
               FROM emails
               WHERE thread_topic = ?
               ORDER BY date ASC LIMIT ?""",
            (thread_topic, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Full-body retrieval (Phase: export & browse)
    # ------------------------------------------------------------------

    def _recipients_for_uid(self, uid: str) -> dict[str, list[str]]:
        """Return {to: [...], cc: [...], bcc: [...]} for a single email."""
        rows = self.conn.execute(
            "SELECT address, display_name, type FROM recipients WHERE email_uid = ?",
            (uid,),
        ).fetchall()
        result: dict[str, list[str]] = {"to": [], "cc": [], "bcc": []}
        for r in rows:
            result[r["type"]].append(_format_recipient(r["display_name"], r["address"]))
        return result

    def _recipients_for_uids(self, uids: list[str]) -> dict[str, dict[str, list[str]]]:
        """Return {uid: {to: [...], cc: [...], bcc: [...]}} for multiple emails in one query."""
        if not uids:
            return {}
        placeholders = ",".join("?" * len(uids))
        rows = self.conn.execute(
            f"SELECT address, display_name, type, email_uid FROM recipients WHERE email_uid IN ({placeholders})",
            uids,
        ).fetchall()
        result: dict[str, dict[str, list[str]]] = {}
        for r in rows:
            uid = r["email_uid"]
            if uid not in result:
                result[uid] = {"to": [], "cc": [], "bcc": []}
            result[uid][r["type"]].append(_format_recipient(r["display_name"], r["address"]))
        return result

    def get_email_full(self, uid: str) -> dict | None:
        """Get a single email with full body text by UID."""
        row = self.conn.execute(
            "SELECT * FROM emails WHERE uid = ?", (uid,)
        ).fetchone()
        if not row:
            return None
        email = dict(row)
        recipients = self._recipients_for_uid(uid)
        email["to"] = recipients["to"]
        email["cc"] = recipients["cc"]
        email["bcc"] = recipients["bcc"]
        email["categories"] = _safe_json_parse(email.get("categories"))
        email["references"] = _safe_json_parse(email.get("references_json"))
        # Attachments from normalized table
        email["attachments"] = self.attachments_for_email(uid)
        return email

    def get_emails_full_batch(self, uids: list[str]) -> dict[str, dict]:
        """Get multiple emails with full body, recipients, and attachments.

        Returns {uid: email_dict} — 3 queries regardless of N.
        """
        if not uids:
            return {}
        placeholders = ",".join("?" * len(uids))
        # 1) Emails
        rows = self.conn.execute(
            f"SELECT * FROM emails WHERE uid IN ({placeholders})", uids,
        ).fetchall()
        # 2) Recipients (batch)
        all_recipients = self._recipients_for_uids(uids)
        # 3) Attachments (batch)
        att_rows = self.conn.execute(
            "SELECT name, mime_type, size, content_id, is_inline, email_uid"
            f" FROM attachments WHERE email_uid IN ({placeholders})",
            uids,
        ).fetchall()
        attachments_by_uid: dict[str, list[dict]] = {}
        for a in att_rows:
            a_uid = a["email_uid"]
            if a_uid not in attachments_by_uid:
                attachments_by_uid[a_uid] = []
            attachments_by_uid[a_uid].append({
                "name": a["name"], "mime_type": a["mime_type"],
                "size": a["size"], "content_id": a["content_id"],
                "is_inline": a["is_inline"],
            })

        result: dict[str, dict] = {}
        for row in rows:
            email = dict(row)
            uid = email["uid"]
            recips = all_recipients.get(uid, {"to": [], "cc": [], "bcc": []})
            email["to"] = recips["to"]
            email["cc"] = recips["cc"]
            email["bcc"] = recips["bcc"]
            email["categories"] = _safe_json_parse(email.get("categories"))
            email["references"] = _safe_json_parse(email.get("references_json"))
            email["attachments"] = attachments_by_uid.get(uid, [])
            result[uid] = email
        return result

    def get_thread_emails(self, conversation_id: str) -> list[dict]:
        """Get all emails in a conversation thread, sorted by date ASC."""
        if not conversation_id:
            return []
        rows = self.conn.execute(
            "SELECT * FROM emails WHERE conversation_id = ? ORDER BY date ASC",
            (conversation_id,),
        ).fetchall()
        uids = [row["uid"] for row in rows]
        all_recipients = self._recipients_for_uids(uids) if uids else {}
        result = []
        for row in rows:
            email = dict(row)
            recips = all_recipients.get(email["uid"], {"to": [], "cc": [], "bcc": []})
            email["to"] = recips["to"]
            email["cc"] = recips["cc"]
            email["bcc"] = recips["bcc"]
            result.append(email)
        return result

    def list_emails_paginated(
        self,
        offset: int = 0,
        limit: int = 20,
        sort_by: str = "date",
        sort_order: str = "DESC",
        folder: str | None = None,
        sender: str | None = None,
        category: str | None = None,
    ) -> dict:
        """Return a page of emails with metadata for browsing.

        Args:
            offset: Starting position.
            limit: Emails per page.
            sort_by: Column to sort by (date, subject, sender_email).
            sort_order: ASC or DESC.
            folder: Optional folder filter (exact match).
            sender: Optional sender filter (LIKE match).
            category: Optional category filter (exact match via JOIN).

        Returns:
            {"emails": [...], "total": int, "offset": int, "limit": int}
        """
        allowed_sort = {"date", "subject", "sender_email", "folder"}
        if sort_by not in allowed_sort:
            sort_by = "date"
        sort_order = "ASC" if sort_order.upper() == "ASC" else "DESC"

        join = ""
        conditions = []
        params: list = []
        if category:
            join = " JOIN email_categories ec ON emails.uid = ec.email_uid"
            conditions.append("ec.category = ?")
            params.append(category)
        if folder:
            conditions.append("folder = ?")
            params.append(folder)
        if sender:
            conditions.append("sender_email LIKE ?")
            params.append(f"%{sender}%")

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        total_row = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM emails{join}{where}", params
        ).fetchone()
        total = total_row["c"]

        rows = self.conn.execute(
            f"""SELECT emails.uid, subject, sender_name, sender_email, date, folder,
                       email_type, has_attachments, attachment_count, body_length,
                       conversation_id
                FROM emails{join}{where}
                ORDER BY {sort_by} {sort_order}
                LIMIT ? OFFSET ?""",
            [*params, limit, offset],
        ).fetchall()

        return {
            "emails": [dict(r) for r in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def update_body_text(self, uid: str, body_text: str, body_html: str) -> bool:
        """Update body_text and body_html for an existing email.

        Only overwrites body_html if the new value is non-empty, to avoid
        losing good HTML content when the re-parsed email lacks an HTML body.
        Returns True if updated.
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
    ) -> bool:
        """Update decoded header fields for an existing email.

        Fixes MIME encoded-word subjects and sender names that were stored
        without decoding during earlier ingestions.  Returns True if updated.
        """
        cur = self.conn.execute(
            """UPDATE emails
               SET subject = ?, sender_name = ?, sender_email = ?,
                   base_subject = ?, email_type = ?
             WHERE uid = ?""",
            (subject, sender_name, sender_email, base_subject, email_type, uid),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def update_v7_metadata(self, email: Email) -> bool:
        """Update schema-v7 metadata fields for an existing email.

        Populates categories, thread_topic, inference_classification,
        is_calendar_message, references_json, and related tables
        (email_categories, attachments). Returns True if updated.
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
        for cat in getattr(email, "categories", []) or []:
            cur.execute(
                "INSERT OR IGNORE INTO email_categories(email_uid, category) VALUES(?,?)",
                (email.uid, cat),
            )

        # Upsert attachments
        cur.execute("DELETE FROM attachments WHERE email_uid = ?", (email.uid,))
        for att in getattr(email, "attachments", []) or []:
            cur.execute(
                "INSERT INTO attachments(email_uid, name, mime_type, size, content_id, is_inline) "
                "VALUES(?,?,?,?,?,?)",
                (
                    email.uid,
                    att.get("name", ""),
                    att.get("mime_type", ""),
                    att.get("size", 0),
                    att.get("content_id", ""),
                    int(bool(att.get("content_id"))),
                ),
            )

        self.conn.commit()
        return True

    def all_uids(self) -> set[str]:
        """Return all UIDs in the database."""
        rows = self.conn.execute("SELECT uid FROM emails").fetchall()
        return {r["uid"] for r in rows}

    def uids_missing_body(self) -> set[str]:
        """Return UIDs of emails where body_text is NULL."""
        rows = self.conn.execute(
            "SELECT uid FROM emails WHERE body_text IS NULL"
        ).fetchall()
        return {r["uid"] for r in rows}

    def delete_sparse_by_uid(self, uid: str) -> int:
        """Delete sparse vectors for all chunks of an email. Returns count deleted."""
        cur = self.conn.execute(
            "DELETE FROM sparse_vectors WHERE chunk_id LIKE ?",
            (f"{uid}__%",),
        )
        self.conn.commit()
        return cur.rowcount

    def get_email_for_reembed(self, uid: str) -> dict | None:
        """Read an email from SQLite in the dict format expected by chunk_email().

        Returns None if the UID is not found or body_text is empty.
        """
        full = self.get_email_full(uid)
        if not full:
            return None
        body = full.get("body_text") or ""
        if not body.strip():
            return None
        return {
            "uid": full["uid"],
            "message_id": full.get("message_id", ""),
            "subject": full.get("subject", ""),
            "sender_name": full.get("sender_name", ""),
            "sender_email": full.get("sender_email", ""),
            "to": full.get("to", []),
            "cc": full.get("cc", []),
            "bcc": full.get("bcc", []),
            "date": full.get("date", ""),
            "body": body,
            "folder": full.get("folder", ""),
            "has_attachments": bool(full.get("has_attachments") or full.get("attachment_count")),
            "attachment_names": [a["name"] for a in full.get("attachments", []) if a.get("name")],
            "attachments": full.get("attachments", []),
            "attachment_count": full.get("attachment_count", 0),
            "conversation_id": full.get("conversation_id", ""),
            "in_reply_to": full.get("in_reply_to", ""),
            "references": full.get("references", []),
            "priority": full.get("priority", 0),
            "is_read": bool(full.get("is_read", True)),
            "email_type": full.get("email_type", "original"),
            "base_subject": full.get("base_subject", ""),
            "categories": full.get("categories", []) if isinstance(full.get("categories"), list) else [],
            "thread_topic": full.get("thread_topic", ""),
            "inference_classification": full.get("inference_classification", ""),
            "is_calendar_message": bool(full.get("is_calendar_message")),
        }

    # ------------------------------------------------------------------
    # Evidence management (see EvidenceMixin)
    # Ingestion tracking (see CustodyMixin)
    # Chain of custody (see CustodyMixin)
    # ------------------------------------------------------------------

    def emails_by_base_subject(
        self, min_group_size: int = 2
    ) -> list[tuple[str, list[tuple[str, str]]]]:
        """Group emails by base_subject for dedup comparison.

        Returns:
            List of (base_subject, [(uid, body_text), ...]) tuples,
            only groups with >= min_group_size emails.
        """
        # Get base_subjects with enough emails
        cursor = self.conn.execute(
            """
            SELECT base_subject, COUNT(*) as cnt
            FROM emails
            WHERE base_subject IS NOT NULL AND base_subject != ''
            GROUP BY base_subject
            HAVING cnt >= ?
            ORDER BY cnt DESC
            LIMIT 500
            """,
            (min_group_size,),
        )
        subjects = [row["base_subject"] for row in cursor]

        results = []
        for subject in subjects:
            rows = self.conn.execute(
                "SELECT uid, body_length FROM emails WHERE base_subject = ?",
                (subject,),
            ).fetchall()
            # We don't store body in SQLite, but we can return UIDs
            # The caller will need to use the body from elsewhere
            # For simplicity, return empty bodies — the dedup detector
            # will need to fetch bodies from ChromaDB or other source
            emails = [(row["uid"], "") for row in rows]
            results.append((subject, emails))

        return results
