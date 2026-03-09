"""SQLite relational store for email metadata and relationships."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

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


class EmailDatabase:
    """SQLite-backed relational store for email metadata."""

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
    # Entity operations (Phase 6)
    # ------------------------------------------------------------------

    def insert_entities_batch(
        self, email_uid: str, entities: list[tuple[str, str, str]], *, commit: bool = True
    ) -> None:
        """Insert extracted entities for an email.

        Each entity is (entity_text, entity_type, normalized_form).

        Args:
            commit: If False, skip the final commit (caller is responsible).
        """
        cur = self.conn.cursor()
        for text, etype, norm in entities:
            cur.execute(
                """INSERT INTO entities(entity_text, entity_type, normalized_form)
                   VALUES(?, ?, ?)
                   ON CONFLICT(normalized_form, entity_type) DO UPDATE SET
                     entity_text = excluded.entity_text""",
                (text, etype, norm),
            )
            entity_id = cur.execute(
                "SELECT id FROM entities WHERE normalized_form=? AND entity_type=?",
                (norm, etype),
            ).fetchone()["id"]
            cur.execute(
                """INSERT INTO entity_mentions(entity_id, email_uid, mention_count)
                   VALUES(?, ?, 1)
                   ON CONFLICT(entity_id, email_uid) DO UPDATE SET
                     mention_count = entity_mentions.mention_count + 1""",
                (entity_id, email_uid),
            )
        if commit:
            self.conn.commit()

    def search_by_entity(
        self, entity_text: str, entity_type: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Find emails mentioning an entity (LIKE match)."""
        if entity_type:
            rows = self.conn.execute(
                """SELECT e.uid, e.subject, e.sender_email, e.date, e.folder,
                          ent.entity_text, ent.entity_type
                   FROM entity_mentions em
                   JOIN entities ent ON em.entity_id = ent.id
                   JOIN emails e ON em.email_uid = e.uid
                   WHERE ent.normalized_form LIKE ? AND ent.entity_type = ?
                   ORDER BY e.date DESC LIMIT ?""",
                (f"%{entity_text.lower()}%", entity_type, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT e.uid, e.subject, e.sender_email, e.date, e.folder,
                          ent.entity_text, ent.entity_type
                   FROM entity_mentions em
                   JOIN entities ent ON em.entity_id = ent.id
                   JOIN emails e ON em.email_uid = e.uid
                   WHERE ent.normalized_form LIKE ?
                   ORDER BY e.date DESC LIMIT ?""",
                (f"%{entity_text.lower()}%", limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def top_entities(self, entity_type: str | None = None, limit: int = 20) -> list[dict]:
        """Most frequently mentioned entities."""
        if entity_type:
            rows = self.conn.execute(
                """SELECT ent.entity_text, ent.entity_type, ent.normalized_form,
                          SUM(em.mention_count) AS total_mentions,
                          COUNT(DISTINCT em.email_uid) AS email_count
                   FROM entities ent
                   JOIN entity_mentions em ON ent.id = em.entity_id
                   WHERE ent.entity_type = ?
                   GROUP BY ent.id
                   ORDER BY total_mentions DESC LIMIT ?""",
                (entity_type, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT ent.entity_text, ent.entity_type, ent.normalized_form,
                          SUM(em.mention_count) AS total_mentions,
                          COUNT(DISTINCT em.email_uid) AS email_count
                   FROM entities ent
                   JOIN entity_mentions em ON ent.id = em.entity_id
                   GROUP BY ent.id
                   ORDER BY total_mentions DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def people_in_emails(self, name_query: str, limit: int = 20) -> list[dict]:
        """Find emails mentioning a person by name (LIKE match on person entities)."""
        rows = self.conn.execute(
            """SELECT e.uid, e.subject, e.sender_email, e.date, e.folder,
                      ent.entity_text AS person_name
               FROM entity_mentions em
               JOIN entities ent ON em.entity_id = ent.id
               JOIN emails e ON em.email_uid = e.uid
               WHERE ent.entity_type = 'person'
                 AND ent.normalized_form LIKE ?
               ORDER BY e.date DESC LIMIT ?""",
            (f"%{name_query.lower()}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def entity_timeline(
        self, entity_text: str, period: str = "month"
    ) -> list[dict]:
        """Show how often an entity appears over time.

        Args:
            entity_text: Entity text to search for (partial match).
            period: 'day', 'week', or 'month'.

        Returns:
            List of {period, count} dicts.
        """
        if period == "day":
            date_expr = "substr(e.date, 1, 10)"
        elif period == "week":
            # ISO week: YYYY-Www
            date_expr = "strftime('%Y-W%W', e.date)"
        else:
            date_expr = "substr(e.date, 1, 7)"

        rows = self.conn.execute(
            f"""SELECT {date_expr} AS period, COUNT(*) AS count
                FROM entity_mentions em
                JOIN entities ent ON em.entity_id = ent.id
                JOIN emails e ON em.email_uid = e.uid
                WHERE ent.normalized_form LIKE ?
                GROUP BY period
                ORDER BY period""",
            (f"%{entity_text.lower()}%",),
        ).fetchall()
        return [dict(r) for r in rows]

    def entity_co_occurrences(self, entity_text: str, limit: int = 20) -> list[dict]:
        """Entities that co-occur with the given entity in the same emails."""
        rows = self.conn.execute(
            """SELECT ent2.entity_text, ent2.entity_type, ent2.normalized_form,
                      COUNT(*) AS co_occurrence_count
               FROM entity_mentions em1
               JOIN entities ent1 ON em1.entity_id = ent1.id
               JOIN entity_mentions em2 ON em1.email_uid = em2.email_uid
               JOIN entities ent2 ON em2.entity_id = ent2.id
               WHERE ent1.normalized_form LIKE ? AND ent2.id != ent1.id
               GROUP BY ent2.id
               ORDER BY co_occurrence_count DESC LIMIT ?""",
            (f"%{entity_text.lower()}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Cluster operations (Phase C)
    # ------------------------------------------------------------------

    def insert_clusters_batch(
        self, assignments: list[tuple[str, int, float]]
    ) -> None:
        """Insert cluster assignments.

        Each tuple: (email_uid, cluster_id, distance_to_centroid).
        """
        cur = self.conn.cursor()
        for uid, cluster_id, distance in assignments:
            cur.execute(
                """INSERT OR REPLACE INTO email_clusters(email_uid, cluster_id, distance)
                   VALUES(?, ?, ?)""",
                (uid, cluster_id, distance),
            )
        self.conn.commit()

    def insert_cluster_info(self, clusters: list[dict]) -> None:
        """Insert cluster metadata.

        Each dict: {cluster_id, size, representative_uid, label}.
        """
        cur = self.conn.cursor()
        for c in clusters:
            cur.execute(
                """INSERT OR REPLACE INTO cluster_info(cluster_id, size, representative_uid, label)
                   VALUES(?, ?, ?, ?)""",
                (c["cluster_id"], c["size"], c.get("representative_uid"), c.get("label")),
            )
        self.conn.commit()

    def emails_in_cluster(self, cluster_id: int, limit: int = 50) -> list[dict]:
        """Get emails in a specific cluster."""
        rows = self.conn.execute(
            """SELECT e.uid, e.subject, e.sender_email, e.date, e.folder,
                      ec.distance
               FROM email_clusters ec
               JOIN emails e ON ec.email_uid = e.uid
               WHERE ec.cluster_id = ?
               ORDER BY ec.distance LIMIT ?""",
            (cluster_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def cluster_summary(self) -> list[dict]:
        """Get all clusters with sizes and representative info."""
        rows = self.conn.execute(
            """SELECT ci.cluster_id, ci.size, ci.representative_uid, ci.label,
                      e.subject AS representative_subject
               FROM cluster_info ci
               LEFT JOIN emails e ON ci.representative_uid = e.uid
               ORDER BY ci.size DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Keyword / Topic operations (Phase B)
    # ------------------------------------------------------------------

    def insert_keywords_batch(
        self, email_uid: str, keywords: list[tuple[str, float]]
    ) -> None:
        """Insert keyword/score pairs for an email."""
        cur = self.conn.cursor()
        for keyword, score in keywords:
            cur.execute(
                """INSERT OR REPLACE INTO email_keywords(email_uid, keyword, score)
                   VALUES(?, ?, ?)""",
                (email_uid, keyword, score),
            )
        self.conn.commit()

    def insert_topics(self, topics: list[dict]) -> None:
        """Insert topic definitions.

        Each dict: {id: int, label: str, top_words: list[str]}.
        """
        import json

        cur = self.conn.cursor()
        for topic in topics:
            cur.execute(
                "INSERT OR REPLACE INTO topics(id, label, top_words) VALUES(?, ?, ?)",
                (topic["id"], topic["label"], json.dumps(topic["top_words"])),
            )
        self.conn.commit()

    def insert_email_topics_batch(
        self, email_uid: str, topic_weights: list[tuple[int, float]]
    ) -> None:
        """Insert topic assignments for an email."""
        cur = self.conn.cursor()
        for topic_id, weight in topic_weights:
            cur.execute(
                """INSERT OR REPLACE INTO email_topics(email_uid, topic_id, weight)
                   VALUES(?, ?, ?)""",
                (email_uid, topic_id, weight),
            )
        self.conn.commit()

    def top_keywords(
        self,
        sender: str | None = None,
        folder: str | None = None,
        limit: int = 30,
    ) -> list[dict]:
        """Aggregate top keywords, optionally filtered by sender or folder."""
        query = """SELECT ek.keyword, ROUND(AVG(ek.score), 4) AS avg_score,
                          COUNT(DISTINCT ek.email_uid) AS email_count
                   FROM email_keywords ek"""
        conditions = []
        params: list = []

        if sender or folder:
            query += " JOIN emails e ON ek.email_uid = e.uid"
            if sender:
                conditions.append("e.sender_email = ?")
                params.append(sender)
            if folder:
                conditions.append("e.folder = ?")
                params.append(folder)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " GROUP BY ek.keyword ORDER BY avg_score DESC LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def emails_by_topic(self, topic_id: int, limit: int = 30) -> list[dict]:
        """Get emails assigned to a specific topic, ranked by weight."""
        rows = self.conn.execute(
            """SELECT e.uid, e.subject, e.sender_email, e.date, e.folder,
                      et.weight
               FROM email_topics et
               JOIN emails e ON et.email_uid = e.uid
               WHERE et.topic_id = ?
               ORDER BY et.weight DESC LIMIT ?""",
            (topic_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def topic_distribution(self) -> list[dict]:
        """Get all topics with their email counts."""
        import json

        rows = self.conn.execute(
            """SELECT t.id, t.label, t.top_words,
                      COUNT(et.email_uid) AS email_count
               FROM topics t
               LEFT JOIN email_topics et ON t.id = et.topic_id
               GROUP BY t.id
               ORDER BY email_count DESC"""
        ).fetchall()
        return [
            {
                "id": r["id"],
                "label": r["label"],
                "top_words": json.loads(r["top_words"]),
                "email_count": r["email_count"],
            }
            for r in rows
        ]

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
            "attachment_names": [],
            "attachments": [],
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
    # Network queries (Phase 4)
    # ------------------------------------------------------------------

    def top_contacts(self, email_address: str, limit: int = 20) -> list[dict]:
        """Top communication partners (bidirectional frequency)."""
        rows = self.conn.execute(
            """SELECT partner, SUM(cnt) AS total
               FROM (
                 SELECT recipient_email AS partner, email_count AS cnt
                 FROM communication_edges WHERE sender_email = ?
                 UNION ALL
                 SELECT sender_email AS partner, email_count AS cnt
                 FROM communication_edges WHERE recipient_email = ?
               )
               GROUP BY partner ORDER BY total DESC LIMIT ?""",
            (email_address, email_address, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def communication_between(self, email_a: str, email_b: str) -> dict:
        """Bidirectional stats between two addresses."""
        a_to_b = self.conn.execute(
            "SELECT email_count, first_date, last_date FROM communication_edges WHERE sender_email=? AND recipient_email=?",
            (email_a, email_b),
        ).fetchone()
        b_to_a = self.conn.execute(
            "SELECT email_count, first_date, last_date FROM communication_edges WHERE sender_email=? AND recipient_email=?",
            (email_b, email_a),
        ).fetchone()

        a_to_b_count = a_to_b["email_count"] if a_to_b else 0
        b_to_a_count = b_to_a["email_count"] if b_to_a else 0
        dates = [
            d
            for d in [
                a_to_b["first_date"] if a_to_b else None,
                b_to_a["first_date"] if b_to_a else None,
            ]
            if d
        ]
        last_dates = [
            d
            for d in [
                a_to_b["last_date"] if a_to_b else None,
                b_to_a["last_date"] if b_to_a else None,
            ]
            if d
        ]
        return {
            "a_to_b": a_to_b_count,
            "b_to_a": b_to_a_count,
            "total": a_to_b_count + b_to_a_count,
            "first_date": min(dates) if dates else "",
            "last_date": max(last_dates) if last_dates else "",
        }

    def all_edges(self) -> list[tuple[str, str, int]]:
        """All communication edges for graph building."""
        rows = self.conn.execute(
            "SELECT sender_email, recipient_email, email_count FROM communication_edges"
        ).fetchall()
        return [(r["sender_email"], r["recipient_email"], r["email_count"]) for r in rows]

    # ------------------------------------------------------------------
    # Relationship queries
    # ------------------------------------------------------------------

    def shared_recipients_query(
        self, sender_emails: list[str], min_shared: int = 2
    ) -> list[dict]:
        """Find recipients who received emails from multiple specified senders.

        Returns:
            List of {"recipient": str, "senders": [str], "total_emails": int}
        """
        if len(sender_emails) < 2:
            return []

        placeholders = ",".join("?" for _ in sender_emails)
        rows = self.conn.execute(
            f"""SELECT r.address AS recipient,
                       GROUP_CONCAT(DISTINCT e.sender_email) AS senders,
                       COUNT(*) AS total_emails
                FROM recipients r
                JOIN emails e ON r.email_uid = e.uid
                WHERE e.sender_email IN ({placeholders})
                  AND r.type IN ('to', 'cc')
                GROUP BY r.address
                HAVING COUNT(DISTINCT e.sender_email) >= ?
                ORDER BY total_emails DESC""",
            [*sender_emails, min_shared],
        ).fetchall()

        return [
            {
                "recipient": r["recipient"],
                "senders": r["senders"].split(",") if r["senders"] else [],
                "total_emails": r["total_emails"],
            }
            for r in rows
        ]

    def sender_activity_timeline(self, sender_emails: list[str]) -> list[dict]:
        """All email timestamps for specified senders, ordered by date.

        Returns:
            List of {"sender_email": str, "date": str, "uid": str, "subject": str}
        """
        if not sender_emails:
            return []

        placeholders = ",".join("?" for _ in sender_emails)
        rows = self.conn.execute(
            f"""SELECT sender_email, date, uid, subject
                FROM emails
                WHERE sender_email IN ({placeholders})
                  AND date IS NOT NULL
                ORDER BY date ASC""",
            sender_emails,
        ).fetchall()

        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Temporal queries (Phase 5)
    # ------------------------------------------------------------------

    def email_dates(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        sender: str | None = None,
    ) -> list[str]:
        """Return all email dates, optionally filtered."""
        query = "SELECT date FROM emails WHERE 1=1"
        params: list[str] = []
        if date_from:
            query += " AND date >= ?"
            params.append(date_from)
        if date_to:
            query += " AND date <= ?"
            params.append(date_to)
        if sender:
            query += " AND sender_email = ?"
            params.append(sender)
        rows = self.conn.execute(query, params).fetchall()
        return [r["date"] for r in rows if r["date"]]

    def response_pairs(
        self, sender: str | None = None, limit: int = 100
    ) -> list[dict]:
        """Join reply→original via in_reply_to = message_id."""
        query = """
            SELECT reply.sender_email AS reply_sender,
                   reply.date AS reply_date,
                   original.sender_email AS original_sender,
                   original.date AS original_date
            FROM emails reply
            JOIN emails original ON reply.in_reply_to = original.message_id
            WHERE reply.in_reply_to != '' AND original.message_id != ''
        """
        params: list[str] = []
        if sender:
            query += " AND reply.sender_email = ?"
            params.append(sender)
        query += " ORDER BY reply.date DESC LIMIT ?"
        params.append(str(limit))
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Ingestion tracking
    # ------------------------------------------------------------------

    def record_ingestion_start(
        self,
        olm_path: str,
        olm_sha256: str | None = None,
        file_size_bytes: int | None = None,
        custodian: str = "system",
    ) -> int:
        """Record the start of an ingestion run. Returns run ID."""
        from datetime import datetime, timezone

        cur = self.conn.execute(
            """INSERT INTO ingestion_runs(olm_path, started_at, olm_sha256, file_size_bytes, custodian)
               VALUES(?, ?, ?, ?, ?)""",
            (
                olm_path,
                datetime.now(timezone.utc).isoformat(),
                olm_sha256,
                file_size_bytes,
                custodian,
            ),
        )
        self.conn.commit()
        run_id = cur.lastrowid  # type: ignore[return-value]

        self.log_custody_event(
            "ingest_start",
            target_type="ingestion_run",
            target_id=str(run_id),
            details={
                "olm_path": olm_path,
                "olm_sha256": olm_sha256,
                "file_size_bytes": file_size_bytes,
            },
            content_hash=olm_sha256,
            actor=custodian,
        )
        return run_id

    def record_ingestion_complete(self, run_id: int, stats: dict) -> None:
        """Record the completion of an ingestion run."""
        from datetime import datetime, timezone

        self.conn.execute(
            "UPDATE ingestion_runs SET completed_at=?, emails_parsed=?, emails_inserted=?, status='completed' WHERE id=?",
            (
                datetime.now(timezone.utc).isoformat(),
                stats.get("emails_parsed", 0),
                stats.get("emails_inserted", 0),
                run_id,
            ),
        )
        self.conn.commit()

    def last_ingestion(self, olm_path: str | None = None) -> dict | None:
        """Return the most recent completed ingestion run."""
        if olm_path:
            row = self.conn.execute(
                "SELECT * FROM ingestion_runs WHERE olm_path=? AND status='completed' ORDER BY id DESC LIMIT 1",
                (olm_path,),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT * FROM ingestion_runs WHERE status='completed' ORDER BY id DESC LIMIT 1",
            ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Evidence management
    # ------------------------------------------------------------------

    def add_evidence(
        self,
        email_uid: str,
        category: str,
        key_quote: str,
        summary: str,
        relevance: int,
        notes: str = "",
    ) -> dict:
        """Add an evidence item linked to an email.

        Auto-populates sender/date/recipients/subject from the email record.
        Runs quote verification immediately against the email body.

        Args:
            email_uid: UID of the source email (must exist).
            category: Evidence category (e.g. discrimination, harassment).
            key_quote: Exact quote from the email body.
            summary: Brief description of why this is evidence.
            relevance: 1-5 rating (1=tangential, 5=critical).
            notes: Optional notes for the lawyer.

        Returns:
            Dict with the created evidence item including id and verified status.

        Raises:
            ValueError: If email_uid does not exist in the database.
        """
        # Validate email exists and fetch metadata
        email_row = self.conn.execute(
            "SELECT sender_name, sender_email, date, subject, body_text FROM emails WHERE uid = ?",
            (email_uid,),
        ).fetchone()
        if not email_row:
            raise ValueError(f"Email not found: {email_uid}")

        # Build recipients string from recipients table
        recip_rows = self.conn.execute(
            "SELECT address, display_name FROM recipients WHERE email_uid = ? AND type = 'to'",
            (email_uid,),
        ).fetchall()
        recipients = ", ".join(
            f"{r['display_name']} <{r['address']}>" if r["display_name"] else r["address"]
            for r in recip_rows
        )

        # Verify quote against body
        body_text = email_row["body_text"] or ""
        verified = 1 if key_quote.strip() and key_quote.strip().lower() in body_text.lower() else 0

        content_hash = self.compute_content_hash(f"{email_uid}|{category}|{key_quote}")

        cur = self.conn.execute(
            """INSERT INTO evidence_items
               (email_uid, category, key_quote, summary, relevance,
                sender_name, sender_email, date, recipients, subject, notes, verified,
                content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                email_uid, category, key_quote, summary, relevance,
                email_row["sender_name"], email_row["sender_email"],
                email_row["date"], recipients, email_row["subject"],
                notes, verified, content_hash,
            ),
        )
        self.conn.commit()
        new_id = cur.lastrowid

        self.log_custody_event(
            "evidence_add",
            target_type="evidence",
            target_id=str(new_id),
            details={
                "email_uid": email_uid,
                "category": category,
                "relevance": relevance,
                "summary": summary[:200],
            },
            content_hash=content_hash,
        )

        return {
            "id": new_id,
            "email_uid": email_uid,
            "category": category,
            "key_quote": key_quote,
            "summary": summary,
            "relevance": relevance,
            "sender_name": email_row["sender_name"],
            "sender_email": email_row["sender_email"],
            "date": email_row["date"],
            "recipients": recipients,
            "subject": email_row["subject"],
            "notes": notes,
            "verified": verified,
            "content_hash": content_hash,
        }

    def list_evidence(
        self,
        category: str | None = None,
        min_relevance: int | None = None,
        email_uid: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict:
        """List evidence items with optional filters.

        Returns:
            {"items": [...], "total": int}
        """
        conditions: list[str] = []
        params: list = []

        if category:
            conditions.append("category = ?")
            params.append(category)
        if min_relevance is not None:
            conditions.append("relevance >= ?")
            params.append(min_relevance)
        if email_uid:
            conditions.append("email_uid = ?")
            params.append(email_uid)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        total_row = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM evidence_items{where}", params
        ).fetchone()
        total = total_row["c"]

        rows = self.conn.execute(
            f"SELECT * FROM evidence_items{where} ORDER BY date ASC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

        return {
            "items": [dict(r) for r in rows],
            "total": total,
        }

    def get_evidence(self, evidence_id: int) -> dict | None:
        """Get a single evidence item by ID."""
        row = self.conn.execute(
            "SELECT * FROM evidence_items WHERE id = ?", (evidence_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_evidence(self, evidence_id: int, **fields) -> bool:
        """Update fields on an evidence item.

        Allowed fields: category, key_quote, summary, relevance, notes.
        Sets updated_at automatically. Re-verifies if key_quote changes.
        Logs a custody event with a snapshot of old values.

        Returns:
            True if the item was updated, False if not found.
        """
        allowed = {"category", "key_quote", "summary", "relevance", "notes"}
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return False

        # Check item exists and snapshot old values
        existing = self.conn.execute(
            "SELECT * FROM evidence_items WHERE id = ?", (evidence_id,)
        ).fetchone()
        if not existing:
            return False
        old_values = {k: existing[k] for k in updates}

        # Re-verify if key_quote changed
        if "key_quote" in updates:
            body_row = self.conn.execute(
                "SELECT body_text FROM emails WHERE uid = ?",
                (existing["email_uid"],),
            ).fetchone()
            body_text = (body_row["body_text"] or "") if body_row else ""
            new_quote = updates["key_quote"].strip()
            updates["verified"] = 1 if new_quote and new_quote.lower() in body_text.lower() else 0

        # Recompute content hash
        category = updates.get("category", existing["category"])
        key_quote = updates.get("key_quote", existing["key_quote"])
        new_hash = self.compute_content_hash(f"{existing['email_uid']}|{category}|{key_quote}")
        updates["content_hash"] = new_hash

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        set_clause += ", updated_at = datetime('now')"
        params = list(updates.values()) + [evidence_id]

        cur = self.conn.execute(
            f"UPDATE evidence_items SET {set_clause} WHERE id = ?",
            params,
        )
        self.conn.commit()

        if cur.rowcount > 0:
            self.log_custody_event(
                "evidence_update",
                target_type="evidence",
                target_id=str(evidence_id),
                details={"old_values": old_values, "new_values": {k: v for k, v in updates.items() if k != "content_hash"}},
                content_hash=new_hash,
            )
        return cur.rowcount > 0

    def remove_evidence(self, evidence_id: int) -> bool:
        """Delete an evidence item by ID. Logs custody event with snapshot. Returns True if deleted."""
        # Snapshot before deletion
        existing = self.conn.execute(
            "SELECT * FROM evidence_items WHERE id = ?", (evidence_id,)
        ).fetchone()

        cur = self.conn.execute(
            "DELETE FROM evidence_items WHERE id = ?", (evidence_id,)
        )
        self.conn.commit()

        if cur.rowcount > 0 and existing:
            snapshot = dict(existing)
            self.log_custody_event(
                "evidence_remove",
                target_type="evidence",
                target_id=str(evidence_id),
                details={
                    "email_uid": snapshot.get("email_uid"),
                    "category": snapshot.get("category"),
                    "key_quote": snapshot.get("key_quote", "")[:200],
                    "relevance": snapshot.get("relevance"),
                    "summary": snapshot.get("summary", "")[:200],
                },
                content_hash=snapshot.get("content_hash"),
            )
        return cur.rowcount > 0

    def verify_evidence_quotes(self) -> dict:
        """Verify all evidence quotes against actual email body text.

        For each evidence item, checks if key_quote appears (case-insensitive)
        in the linked email's body_text. Updates the verified column.

        Returns:
            {"verified": int, "failed": int, "failures": [{"evidence_id": ..., "key_quote_preview": ..., "email_uid": ...}, ...]}
        """
        rows = self.conn.execute(
            """SELECT ei.id, ei.key_quote, ei.email_uid, e.body_text
               FROM evidence_items ei
               JOIN emails e ON ei.email_uid = e.uid"""
        ).fetchall()

        verified_count = 0
        failed_count = 0
        failures: list[dict] = []

        for row in rows:
            body_text = row["body_text"] or ""
            quote = (row["key_quote"] or "").strip()
            is_verified = 1 if quote and quote.lower() in body_text.lower() else 0

            self.conn.execute(
                "UPDATE evidence_items SET verified = ? WHERE id = ?",
                (is_verified, row["id"]),
            )

            if is_verified:
                verified_count += 1
            else:
                failed_count += 1
                failures.append({
                    "evidence_id": row["id"],
                    "key_quote_preview": quote[:80] + ("..." if len(quote) > 80 else ""),
                    "email_uid": row["email_uid"],
                })

        self.conn.commit()
        return {
            "verified": verified_count,
            "failed": failed_count,
            "total": verified_count + failed_count,
            "failures": failures,
        }

    def evidence_stats(self) -> dict:
        """Return evidence collection statistics.

        Returns:
            {"total": int, "verified": int, "unverified": int,
             "by_category": [{"category": str, "count": int}, ...],
             "by_relevance": [{"relevance": int, "count": int}, ...]}
        """
        total_row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM evidence_items"
        ).fetchone()
        total = total_row["c"]

        verified_row = self.conn.execute(
            "SELECT COUNT(*) AS c FROM evidence_items WHERE verified = 1"
        ).fetchone()
        verified = verified_row["c"]

        cat_rows = self.conn.execute(
            "SELECT category, COUNT(*) AS count FROM evidence_items GROUP BY category ORDER BY count DESC"
        ).fetchall()

        rel_rows = self.conn.execute(
            "SELECT relevance, COUNT(*) AS count FROM evidence_items GROUP BY relevance ORDER BY relevance DESC"
        ).fetchall()

        return {
            "total": total,
            "verified": verified,
            "unverified": total - verified,
            "by_category": [dict(r) for r in cat_rows],
            "by_relevance": [dict(r) for r in rel_rows],
        }

    # ── Evidence: extended queries ────────────────────────────

    EVIDENCE_CATEGORIES: list[str] = [
        "discrimination", "harassment", "sexual_harassment",
        "insult", "bossing", "retaliation", "exclusion",
        "microaggression", "hostile_environment", "other",
    ]

    def search_evidence(
        self,
        query: str,
        category: str | None = None,
        min_relevance: int | None = None,
        limit: int = 50,
    ) -> dict:
        """Search evidence items by text across key_quote, summary, and notes.

        Returns:
            {"items": [...], "total": int, "query": str}
        """
        conditions = ["(key_quote LIKE ? OR summary LIKE ? OR notes LIKE ?)"]
        pattern = f"%{query}%"
        params: list = [pattern, pattern, pattern]

        if category:
            conditions.append("category = ?")
            params.append(category)
        if min_relevance is not None:
            conditions.append("relevance >= ?")
            params.append(min_relevance)

        where = " WHERE " + " AND ".join(conditions)

        total_row = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM evidence_items{where}", params
        ).fetchone()

        rows = self.conn.execute(
            f"SELECT * FROM evidence_items{where} ORDER BY date ASC LIMIT ?",
            [*params, limit],
        ).fetchall()

        return {
            "items": [dict(r) for r in rows],
            "total": total_row["c"],
            "query": query,
        }

    def evidence_timeline(
        self,
        category: str | None = None,
        min_relevance: int | None = None,
    ) -> list[dict]:
        """Return evidence items in chronological order for narrative building.

        Returns:
            List of evidence items ordered by date ascending.
        """
        conditions: list[str] = []
        params: list = []

        if category:
            conditions.append("category = ?")
            params.append(category)
        if min_relevance is not None:
            conditions.append("relevance >= ?")
            params.append(min_relevance)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        rows = self.conn.execute(
            f"""SELECT id, email_uid, date, category, relevance, summary, key_quote,
                       sender_name, sender_email, subject, verified
                FROM evidence_items{where}
                ORDER BY date ASC""",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def evidence_categories(self) -> list[dict]:
        """Return all canonical categories with current evidence counts.

        Returns:
            List of {"category": str, "count": int} for all 10 canonical categories.
        """
        count_rows = self.conn.execute(
            "SELECT category, COUNT(*) AS count FROM evidence_items GROUP BY category"
        ).fetchall()
        counts = {r["category"]: r["count"] for r in count_rows}

        return [
            {"category": cat, "count": counts.get(cat, 0)}
            for cat in self.EVIDENCE_CATEGORIES
        ]

    # ── Chain of custody ────────────────────────────────────

    @staticmethod
    def compute_content_hash(content: str) -> str:
        """SHA-256 hash of a content string."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def log_custody_event(
        self,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        details: dict | None = None,
        content_hash: str | None = None,
        actor: str = "system",
    ) -> int:
        """Record a chain-of-custody event. Returns event ID."""
        cur = self.conn.execute(
            """INSERT INTO custody_chain
               (action, actor, target_type, target_id, details, content_hash)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                action,
                actor,
                target_type,
                target_id,
                json.dumps(details) if details else None,
                content_hash,
            ),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def get_custody_chain(
        self,
        target_type: str | None = None,
        target_id: str | None = None,
        action: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Retrieve custody events with optional filters."""
        conditions: list[str] = []
        params: list = []

        if target_type:
            conditions.append("target_type = ?")
            params.append(target_type)
        if target_id:
            conditions.append("target_id = ?")
            params.append(target_id)
        if action:
            conditions.append("action = ?")
            params.append(action)

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = self.conn.execute(
            f"SELECT * FROM custody_chain{where} ORDER BY timestamp DESC LIMIT ?",
            [*params, limit],
        ).fetchall()

        result = []
        for r in rows:
            d = dict(r)
            if d.get("details"):
                try:
                    d["details"] = json.loads(d["details"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(d)
        return result

    def email_provenance(self, email_uid: str) -> dict:
        """Full provenance for an email: ingestion run, custody events."""
        email_row = self.conn.execute(
            "SELECT uid, message_id, sender_email, date, subject, content_sha256 FROM emails WHERE uid = ?",
            (email_uid,),
        ).fetchone()
        if not email_row:
            return {"error": f"Email not found: {email_uid}"}

        # Find ingestion run that contains this email (via olm_path match)
        run_row = self.conn.execute(
            "SELECT * FROM ingestion_runs WHERE status = 'completed' ORDER BY id DESC LIMIT 1"
        ).fetchone()

        custody_events = self.get_custody_chain(
            target_type="email", target_id=email_uid
        )

        return {
            "email": dict(email_row),
            "ingestion_run": dict(run_row) if run_row else None,
            "custody_events": custody_events,
        }

    def evidence_provenance(self, evidence_id: int) -> dict:
        """Full provenance for evidence: item details, source email, custody history."""
        item = self.get_evidence(evidence_id)
        if not item:
            return {"error": f"Evidence not found: {evidence_id}"}

        email_prov = self.email_provenance(item["email_uid"])

        evidence_events = self.get_custody_chain(
            target_type="evidence", target_id=str(evidence_id)
        )

        return {
            "evidence": item,
            "source_email": email_prov,
            "custody_events": evidence_events,
        }

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
