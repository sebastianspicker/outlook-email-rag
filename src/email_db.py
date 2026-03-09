"""SQLite relational store for email metadata and relationships."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from .db_analytics import AnalyticsMixin
from .db_custody import CustodyMixin
from .db_entities import EntityMixin
from .db_evidence import EvidenceMixin

if TYPE_CHECKING:
    from src.parse_olm import Email

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 7

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS schema_version (
    version   INTEGER PRIMARY KEY,
    applied_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS emails (
    uid              TEXT PRIMARY KEY,
    message_id       TEXT,
    subject          TEXT,
    sender_name      TEXT,
    sender_email     TEXT,
    date             TEXT,
    folder           TEXT,
    email_type       TEXT,
    has_attachments  INTEGER,
    attachment_count INTEGER,
    priority         INTEGER,
    is_read          INTEGER,
    conversation_id  TEXT,
    in_reply_to      TEXT,
    base_subject     TEXT,
    body_length      INTEGER,
    body_text        TEXT,
    body_html        TEXT
);

CREATE INDEX IF NOT EXISTS idx_emails_sender ON emails(sender_email);
CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date);
CREATE INDEX IF NOT EXISTS idx_emails_folder ON emails(folder);
CREATE INDEX IF NOT EXISTS idx_emails_conversation ON emails(conversation_id);

CREATE TABLE IF NOT EXISTS recipients (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    email_uid TEXT NOT NULL REFERENCES emails(uid),
    address   TEXT NOT NULL,
    display_name TEXT,
    type      TEXT CHECK(type IN ('to', 'cc', 'bcc'))
);

CREATE INDEX IF NOT EXISTS idx_recipients_address ON recipients(address);
CREATE INDEX IF NOT EXISTS idx_recipients_uid ON recipients(email_uid);

CREATE TABLE IF NOT EXISTS contacts (
    email_address  TEXT PRIMARY KEY,
    display_name   TEXT,
    first_seen     TEXT,
    last_seen      TEXT,
    sent_count     INTEGER DEFAULT 0,
    received_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS communication_edges (
    sender_email    TEXT NOT NULL,
    recipient_email TEXT NOT NULL,
    email_count     INTEGER DEFAULT 1,
    first_date      TEXT,
    last_date       TEXT,
    PRIMARY KEY (sender_email, recipient_email)
);

CREATE INDEX IF NOT EXISTS idx_edges_sender ON communication_edges(sender_email);
CREATE INDEX IF NOT EXISTS idx_edges_recipient ON communication_edges(recipient_email);
"""

# Entity tables added in Phase 6 but created upfront for schema simplicity
_ENTITY_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS entities (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_text     TEXT NOT NULL,
    entity_type     TEXT NOT NULL,
    normalized_form TEXT NOT NULL,
    UNIQUE(normalized_form, entity_type)
);

CREATE TABLE IF NOT EXISTS entity_mentions (
    entity_id INTEGER NOT NULL REFERENCES entities(id),
    email_uid TEXT NOT NULL REFERENCES emails(uid),
    mention_count INTEGER DEFAULT 1,
    PRIMARY KEY (entity_id, email_uid)
);

CREATE INDEX IF NOT EXISTS idx_entity_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entity_mentions_uid ON entity_mentions(email_uid);
"""

_KEYWORDS_TOPICS_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS email_keywords (
    email_uid TEXT NOT NULL REFERENCES emails(uid),
    keyword   TEXT NOT NULL,
    score     REAL NOT NULL,
    PRIMARY KEY (email_uid, keyword)
);
CREATE INDEX IF NOT EXISTS idx_keywords_keyword ON email_keywords(keyword);

CREATE TABLE IF NOT EXISTS topics (
    id        INTEGER PRIMARY KEY,
    label     TEXT NOT NULL,
    top_words TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS email_topics (
    email_uid TEXT NOT NULL REFERENCES emails(uid),
    topic_id  INTEGER NOT NULL REFERENCES topics(id),
    weight    REAL NOT NULL,
    PRIMARY KEY (email_uid, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_email_topics_topic ON email_topics(topic_id);
"""

_CLUSTER_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS email_clusters (
    email_uid    TEXT PRIMARY KEY REFERENCES emails(uid),
    cluster_id   INTEGER NOT NULL,
    distance     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clusters_id ON email_clusters(cluster_id);

CREATE TABLE IF NOT EXISTS cluster_info (
    cluster_id        INTEGER PRIMARY KEY,
    size              INTEGER NOT NULL,
    representative_uid TEXT,
    label             TEXT
);
"""

_INGESTION_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS ingestion_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    olm_path        TEXT NOT NULL,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    emails_parsed   INTEGER,
    emails_inserted INTEGER,
    status          TEXT DEFAULT 'running'
);
"""

_EVIDENCE_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS evidence_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    email_uid       TEXT NOT NULL REFERENCES emails(uid),
    category        TEXT NOT NULL,
    key_quote       TEXT NOT NULL,
    summary         TEXT NOT NULL,
    relevance       INTEGER NOT NULL CHECK(relevance BETWEEN 1 AND 5),
    sender_name     TEXT,
    sender_email    TEXT,
    date            TEXT,
    recipients      TEXT,
    subject         TEXT,
    notes           TEXT DEFAULT '',
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now')),
    verified        INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_evidence_email ON evidence_items(email_uid);
CREATE INDEX IF NOT EXISTS idx_evidence_category ON evidence_items(category);
CREATE INDEX IF NOT EXISTS idx_evidence_relevance ON evidence_items(relevance);
"""

_CUSTODY_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS custody_chain (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    action       TEXT NOT NULL,
    timestamp    TEXT NOT NULL DEFAULT (datetime('now')),
    actor        TEXT DEFAULT 'system',
    target_type  TEXT,
    target_id    TEXT,
    details      TEXT,
    content_hash TEXT
);

CREATE INDEX IF NOT EXISTS idx_custody_action ON custody_chain(action);
CREATE INDEX IF NOT EXISTS idx_custody_target ON custody_chain(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_custody_timestamp ON custody_chain(timestamp);
"""

_SPARSE_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS sparse_vectors (
    chunk_id   TEXT PRIMARY KEY,
    token_ids  BLOB NOT NULL,
    weights    BLOB NOT NULL,
    num_tokens INTEGER NOT NULL
);
"""

_ATTACHMENTS_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS attachments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email_uid TEXT NOT NULL REFERENCES emails(uid),
    name TEXT NOT NULL,
    mime_type TEXT,
    size INTEGER DEFAULT 0,
    content_id TEXT,
    is_inline INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_attachments_uid ON attachments(email_uid);
CREATE INDEX IF NOT EXISTS idx_attachments_inline ON attachments(is_inline);
CREATE INDEX IF NOT EXISTS idx_attachments_name ON attachments(name);
"""

_CATEGORIES_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS email_categories (
    email_uid TEXT NOT NULL REFERENCES emails(uid),
    category TEXT NOT NULL,
    PRIMARY KEY (email_uid, category)
);
CREATE INDEX IF NOT EXISTS idx_categories_name ON email_categories(category);
"""

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


class EmailDatabase(CustodyMixin, EvidenceMixin, EntityMixin, AnalyticsMixin):
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
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(_SCHEMA_SQL)
        cur.executescript(_ENTITY_SCHEMA_SQL)
        cur.executescript(_KEYWORDS_TOPICS_SCHEMA_SQL)
        cur.executescript(_CLUSTER_SCHEMA_SQL)
        cur.executescript(_INGESTION_SCHEMA_SQL)
        cur.executescript(_EVIDENCE_SCHEMA_SQL)
        cur.executescript(_CUSTODY_SCHEMA_SQL)
        cur.executescript(_SPARSE_SCHEMA_SQL)
        cur.executescript(_ATTACHMENTS_SCHEMA_SQL)
        cur.executescript(_CATEGORIES_SCHEMA_SQL)
        row = cur.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        current = row["v"] if row and row["v"] else 0
        if current < 3:
            self._migrate_to_v3(cur)
        if current < 4:
            self._migrate_to_v4(cur)
        if current < 5:
            self._migrate_to_v5(cur)
        if current < 6:
            self._migrate_to_v6(cur)
        if current < 7:
            self._migrate_to_v7(cur)
        if current < _SCHEMA_VERSION:
            cur.execute(
                "INSERT OR REPLACE INTO schema_version(version) VALUES(?)",
                (_SCHEMA_VERSION,),
            )
        self.conn.commit()

    def _migrate_to_v3(self, cur: sqlite3.Cursor) -> None:
        """Add body_text and body_html columns (schema v3)."""
        existing = {
            row[1]
            for row in cur.execute("PRAGMA table_info(emails)").fetchall()
        }
        if "body_text" not in existing:
            cur.execute("ALTER TABLE emails ADD COLUMN body_text TEXT")
            logger.info("Schema migration v3: added body_text column")
        if "body_html" not in existing:
            cur.execute("ALTER TABLE emails ADD COLUMN body_html TEXT")
            logger.info("Schema migration v3: added body_html column")

    def _migrate_to_v4(self, cur: sqlite3.Cursor) -> None:
        """Add chain-of-custody columns and tables (schema v4)."""
        # Extend ingestion_runs
        ir_cols = {row[1] for row in cur.execute("PRAGMA table_info(ingestion_runs)").fetchall()}
        if "olm_sha256" not in ir_cols:
            cur.execute("ALTER TABLE ingestion_runs ADD COLUMN olm_sha256 TEXT")
            cur.execute("ALTER TABLE ingestion_runs ADD COLUMN file_size_bytes INTEGER")
            cur.execute("ALTER TABLE ingestion_runs ADD COLUMN custodian TEXT DEFAULT 'system'")
            logger.info("Schema migration v4: added ingestion_runs custody columns")

        # Extend emails
        em_cols = {row[1] for row in cur.execute("PRAGMA table_info(emails)").fetchall()}
        if "content_sha256" not in em_cols:
            cur.execute("ALTER TABLE emails ADD COLUMN content_sha256 TEXT")
            logger.info("Schema migration v4: added emails.content_sha256")

        # Extend evidence_items
        ev_cols = {row[1] for row in cur.execute("PRAGMA table_info(evidence_items)").fetchall()}
        if "content_hash" not in ev_cols:
            cur.execute("ALTER TABLE evidence_items ADD COLUMN content_hash TEXT")
            cur.execute("ALTER TABLE evidence_items ADD COLUMN ingestion_run_id INTEGER")
            logger.info("Schema migration v4: added evidence_items custody columns")

    def _migrate_to_v5(self, cur: sqlite3.Cursor) -> None:
        """Add sparse_vectors table (schema v5)."""
        cur.executescript(_SPARSE_SCHEMA_SQL)
        logger.info("Schema migration v5: created sparse_vectors table")

    def _migrate_to_v6(self, cur: sqlite3.Cursor) -> None:
        """Add composite indexes for common query patterns (schema v6)."""
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_emails_sender_date ON emails(sender_email, date)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_emails_folder_date ON emails(folder, date)"
        )
        logger.info("Schema migration v6: added composite indexes (sender_date, folder_date)")

    def _migrate_to_v7(self, cur: sqlite3.Cursor) -> None:
        """Add categories, calendar, thread_topic, references_json columns + tables (schema v7)."""
        existing = {
            row[1]
            for row in cur.execute("PRAGMA table_info(emails)").fetchall()
        }
        new_cols = {
            "categories": "TEXT",
            "thread_topic": "TEXT",
            "inference_classification": "TEXT",
            "is_calendar_message": "INTEGER DEFAULT 0",
            "references_json": "TEXT",
        }
        for col, col_type in new_cols.items():
            if col not in existing:
                cur.execute(f"ALTER TABLE emails ADD COLUMN {col} {col_type}")
        # Tables created by executescript above (IF NOT EXISTS), just add indexes
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_emails_calendar "
            "ON emails(is_calendar_message, date)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_emails_inference "
            "ON emails(inference_classification)"
        )
        logger.info("Schema migration v7: added categories, calendar, thread_topic, references_json columns + tables")

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

        Uses batched parameter collection for recipients, categories, and
        attachments to reduce per-row execute() overhead.
        """
        inserted = 0
        cur = self.conn.cursor()

        # Collect rows for executemany() across the whole batch
        recipient_rows: list[tuple] = []
        category_rows: list[tuple] = []
        attachment_rows: list[tuple] = []

        try:
            for email in emails:
                try:
                    content_sha256 = self.compute_content_hash(email.clean_body) if email.clean_body else None
                    categories_json = json.dumps(getattr(email, "categories", []) or [])
                    references_json = json.dumps(getattr(email, "references", []) or [])
                    cur.execute(
                        """INSERT INTO emails (uid, message_id, subject, sender_name,
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
                except sqlite3.IntegrityError:
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

                # Contacts and edges still need per-row upsert (ON CONFLICT with min/max)
                if email.sender_email:
                    self._upsert_contact(
                        cur, email.sender_email, email.sender_name, email.date, "sender"
                    )
                for name, em in all_recipients:
                    if em:
                        self._upsert_contact(cur, em, name, email.date, "recipient")

                if email.sender_email:
                    for _, em in all_recipients:
                        if em:
                            self._upsert_communication_edge(
                                cur, email.sender_email, em, email.date
                            )

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

    def attachments_for_email(self, uid: str) -> list[dict]:
        """Get all attachments for a specific email."""
        rows = self.conn.execute(
            "SELECT name, mime_type, size, content_id, is_inline FROM attachments WHERE email_uid = ?",
            (uid,),
        ).fetchall()
        return [dict(r) for r in rows]

    def attachment_stats(self) -> dict:
        """Aggregate attachment statistics: counts, sizes, type distribution."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(size), 0) AS total_size FROM attachments"
        ).fetchone()
        total_attachments = row["total"]
        total_size = row["total_size"]

        emails_with = self.conn.execute(
            "SELECT COUNT(DISTINCT email_uid) AS cnt FROM attachments"
        ).fetchone()["cnt"]

        # Extension distribution
        ext_rows = self.conn.execute(
            """SELECT
                   CASE WHEN INSTR(name, '.') > 0
                        THEN LOWER(SUBSTR(name, INSTR(name, '.') - LENGTH(name)))
                        ELSE '' END AS ext,
                   COUNT(*) AS cnt,
                   COALESCE(SUM(size), 0) AS total_size
               FROM attachments
               GROUP BY ext ORDER BY cnt DESC LIMIT 30"""
        ).fetchall()
        by_extension = [
            {"extension": r["ext"], "count": r["cnt"], "total_size": r["total_size"]}
            for r in ext_rows
        ]

        # Top filenames
        top_rows = self.conn.execute(
            "SELECT name, COUNT(*) AS cnt FROM attachments GROUP BY name ORDER BY cnt DESC LIMIT 20"
        ).fetchall()
        top_filenames = [{"name": r["name"], "count": r["cnt"]} for r in top_rows]

        return {
            "total_attachments": total_attachments,
            "total_size_bytes": total_size,
            "emails_with_attachments": emails_with,
            "by_extension": by_extension,
            "top_filenames": top_filenames,
        }

    def list_attachments(
        self,
        *,
        filename: str | None = None,
        extension: str | None = None,
        mime_type: str | None = None,
        sender: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict:
        """Browse attachments with optional filters. Joins with emails table."""
        query = (
            "SELECT a.name, a.mime_type, a.size, a.is_inline,"
            " a.email_uid, e.subject, e.sender_email, e.date"
            " FROM attachments a JOIN emails e ON a.email_uid = e.uid"
        )
        conditions: list[str] = []
        params: list = []
        if filename:
            conditions.append("a.name LIKE ?")
            params.append(f"%{filename}%")
        if extension:
            ext = extension if extension.startswith(".") else f".{extension}"
            conditions.append("LOWER(a.name) LIKE ?")
            params.append(f"%{ext.lower()}")
        if mime_type:
            conditions.append("a.mime_type LIKE ?")
            params.append(f"%{mime_type}%")
        if sender:
            conditions.append("(e.sender_email LIKE ? OR e.sender_name LIKE ?)")
            params.extend([f"%{sender}%", f"%{sender}%"])
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        # Get total count
        count_query = query.replace(
            "SELECT a.name, a.mime_type, a.size, a.is_inline,"
            " a.email_uid, e.subject, e.sender_email, e.date",
            "SELECT COUNT(*) AS cnt",
        )
        total = self.conn.execute(count_query, params).fetchone()["cnt"]

        query += " ORDER BY e.date DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.conn.execute(query, params).fetchall()
        return {
            "attachments": [dict(r) for r in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    def search_emails_by_attachment(
        self,
        *,
        filename: str | None = None,
        extension: str | None = None,
        mime_type: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Find emails with matching attachments. Returns email rows + matching_attachments."""
        query = (
            "SELECT e.uid, e.subject, e.sender_email, e.sender_name, e.date, e.folder,"
            " GROUP_CONCAT(a.name, ', ') AS matching_attachments,"
            " COUNT(a.id) AS match_count"
            " FROM emails e JOIN attachments a ON e.uid = a.email_uid"
        )
        conditions: list[str] = []
        params: list = []
        if filename:
            conditions.append("a.name LIKE ?")
            params.append(f"%{filename}%")
        if extension:
            ext = extension if extension.startswith(".") else f".{extension}"
            conditions.append("LOWER(a.name) LIKE ?")
            params.append(f"%{ext.lower()}")
        if mime_type:
            conditions.append("a.mime_type LIKE ?")
            params.append(f"%{mime_type}%")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " GROUP BY e.uid ORDER BY e.date DESC LIMIT ?"
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
            addr = r["address"]
            name = r["display_name"]
            formatted = f"{name} <{addr}>" if name else addr
            result[r["type"]].append(formatted)
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
        # Parse JSON fields
        cat_raw = email.get("categories")
        if cat_raw and isinstance(cat_raw, str):
            try:
                email["categories"] = json.loads(cat_raw)
            except (json.JSONDecodeError, TypeError):
                email["categories"] = []
        refs_raw = email.get("references_json")
        if refs_raw and isinstance(refs_raw, str):
            try:
                email["references"] = json.loads(refs_raw)
            except (json.JSONDecodeError, TypeError):
                email["references"] = []
        # Attachments from normalized table
        email["attachments"] = self.attachments_for_email(uid)
        return email

    def get_thread_emails(self, conversation_id: str) -> list[dict]:
        """Get all emails in a conversation thread, sorted by date ASC."""
        if not conversation_id:
            return []
        rows = self.conn.execute(
            "SELECT * FROM emails WHERE conversation_id = ? ORDER BY date ASC",
            (conversation_id,),
        ).fetchall()
        result = []
        for row in rows:
            email = dict(row)
            recipients = self._recipients_for_uid(email["uid"])
            email["to"] = recipients["to"]
            email["cc"] = recipients["cc"]
            email["bcc"] = recipients["bcc"]
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
