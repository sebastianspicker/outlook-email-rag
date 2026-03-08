"""SQLite relational store for email metadata and relationships."""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.parse_olm import Email

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 3

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
            self._conn = sqlite3.connect(self._db_path)
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
        row = cur.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        current = row["v"] if row and row["v"] else 0
        if current < 3:
            self._migrate_to_v3(cur)
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

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def insert_email(self, email: Email) -> bool:
        """Insert a single email and update contacts/edges.

        Returns False if uid already exists (duplicate).
        """
        cur = self.conn.cursor()
        try:
            cur.execute(
                """INSERT INTO emails (uid, message_id, subject, sender_name,
                   sender_email, date, folder, email_type, has_attachments,
                   attachment_count, priority, is_read, conversation_id,
                   in_reply_to, base_subject, body_length, body_text, body_html)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                ),
            )
        except sqlite3.IntegrityError:
            return False

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
        """Insert a batch of emails in a single transaction. Returns count inserted."""
        inserted = 0
        cur = self.conn.cursor()
        try:
            for email in emails:
                try:
                    cur.execute(
                        """INSERT INTO emails (uid, message_id, subject, sender_name,
                           sender_email, date, folder, email_type, has_attachments,
                           attachment_count, priority, is_read, conversation_id,
                           in_reply_to, base_subject, body_length, body_text, body_html)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                        ),
                    )
                except sqlite3.IntegrityError:
                    continue

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
        self, email_uid: str, entities: list[tuple[str, str, str]]
    ) -> None:
        """Insert extracted entities for an email.

        Each entity is (entity_text, entity_type, normalized_form).
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
    ) -> dict:
        """Return a page of emails with metadata for browsing.

        Args:
            offset: Starting position.
            limit: Emails per page.
            sort_by: Column to sort by (date, subject, sender_email).
            sort_order: ASC or DESC.
            folder: Optional folder filter (exact match).
            sender: Optional sender filter (LIKE match).

        Returns:
            {"emails": [...], "total": int, "offset": int, "limit": int}
        """
        allowed_sort = {"date", "subject", "sender_email", "folder"}
        if sort_by not in allowed_sort:
            sort_by = "date"
        sort_order = "ASC" if sort_order.upper() == "ASC" else "DESC"

        conditions = []
        params: list = []
        if folder:
            conditions.append("folder = ?")
            params.append(folder)
        if sender:
            conditions.append("sender_email LIKE ?")
            params.append(f"%{sender}%")

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        total_row = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM emails{where}", params
        ).fetchone()
        total = total_row["c"]

        rows = self.conn.execute(
            f"""SELECT uid, subject, sender_name, sender_email, date, folder,
                       email_type, has_attachments, attachment_count, body_length,
                       conversation_id
                FROM emails{where}
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
        """Update body_text and body_html for an existing email. Returns True if updated."""
        cur = self.conn.execute(
            "UPDATE emails SET body_text = ?, body_html = ? WHERE uid = ?",
            (body_text, body_html, uid),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def uids_missing_body(self) -> set[str]:
        """Return UIDs of emails where body_text is NULL."""
        rows = self.conn.execute(
            "SELECT uid FROM emails WHERE body_text IS NULL"
        ).fetchall()
        return {r["uid"] for r in rows}

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

    def record_ingestion_start(self, olm_path: str) -> int:
        """Record the start of an ingestion run. Returns run ID."""
        from datetime import datetime, timezone

        cur = self.conn.execute(
            "INSERT INTO ingestion_runs(olm_path, started_at) VALUES(?, ?)",
            (olm_path, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

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
