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

_SCHEMA_VERSION = 1

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
    body_length      INTEGER
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
        row = cur.execute(
            "SELECT MAX(version) AS v FROM schema_version"
        ).fetchone()
        current = row["v"] if row and row["v"] else 0
        if current < _SCHEMA_VERSION:
            cur.execute(
                "INSERT OR REPLACE INTO schema_version(version) VALUES(?)",
                (_SCHEMA_VERSION,),
            )
        self.conn.commit()

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
                   in_reply_to, base_subject, body_length)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                           in_reply_to, base_subject, body_length)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
