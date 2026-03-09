"""Schema DDL and migration logic for the email SQLite database."""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

_SCHEMA_VERSION = 8

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


def init_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and run pending migrations."""
    cur = conn.cursor()
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
    current = row[0] if row and row[0] else 0
    if current < 3:
        _migrate_to_v3(cur)
    if current < 4:
        _migrate_to_v4(cur)
    if current < 5:
        _migrate_to_v5(cur)
    if current < 6:
        _migrate_to_v6(cur)
    if current < 7:
        _migrate_to_v7(cur)
    if current < 8:
        _migrate_to_v8(cur)
    if current < _SCHEMA_VERSION:
        cur.execute(
            "INSERT OR REPLACE INTO schema_version(version) VALUES(?)",
            (_SCHEMA_VERSION,),
        )
    conn.commit()


def _migrate_to_v3(cur: sqlite3.Cursor) -> None:
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


def _migrate_to_v4(cur: sqlite3.Cursor) -> None:
    """Add chain-of-custody columns and tables (schema v4)."""
    ir_cols = {row[1] for row in cur.execute("PRAGMA table_info(ingestion_runs)").fetchall()}
    if "olm_sha256" not in ir_cols:
        cur.execute("ALTER TABLE ingestion_runs ADD COLUMN olm_sha256 TEXT")
        cur.execute("ALTER TABLE ingestion_runs ADD COLUMN file_size_bytes INTEGER")
        cur.execute("ALTER TABLE ingestion_runs ADD COLUMN custodian TEXT DEFAULT 'system'")
        logger.info("Schema migration v4: added ingestion_runs custody columns")

    em_cols = {row[1] for row in cur.execute("PRAGMA table_info(emails)").fetchall()}
    if "content_sha256" not in em_cols:
        cur.execute("ALTER TABLE emails ADD COLUMN content_sha256 TEXT")
        logger.info("Schema migration v4: added emails.content_sha256")

    ev_cols = {row[1] for row in cur.execute("PRAGMA table_info(evidence_items)").fetchall()}
    if "content_hash" not in ev_cols:
        cur.execute("ALTER TABLE evidence_items ADD COLUMN content_hash TEXT")
        cur.execute("ALTER TABLE evidence_items ADD COLUMN ingestion_run_id INTEGER")
        logger.info("Schema migration v4: added evidence_items custody columns")


def _migrate_to_v5(cur: sqlite3.Cursor) -> None:
    """Add sparse_vectors table (schema v5)."""
    cur.executescript(_SPARSE_SCHEMA_SQL)
    logger.info("Schema migration v5: created sparse_vectors table")


def _migrate_to_v6(cur: sqlite3.Cursor) -> None:
    """Add composite indexes for common query patterns (schema v6)."""
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_emails_sender_date ON emails(sender_email, date)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_emails_folder_date ON emails(folder, date)"
    )
    logger.info("Schema migration v6: added composite indexes (sender_date, folder_date)")


def _migrate_to_v7(cur: sqlite3.Cursor) -> None:
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
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_emails_calendar "
        "ON emails(is_calendar_message, date)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_emails_inference "
        "ON emails(inference_classification)"
    )
    logger.info("Schema migration v7: added categories, calendar, thread_topic, references_json columns + tables")


def _migrate_to_v8(cur: sqlite3.Cursor) -> None:
    """Add language detection and sentiment analysis columns (schema v8)."""
    existing = {
        row[1]
        for row in cur.execute("PRAGMA table_info(emails)").fetchall()
    }
    new_cols = {
        "detected_language": "TEXT",
        "sentiment_label": "TEXT",
        "sentiment_score": "REAL",
    }
    for col, col_type in new_cols.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE emails ADD COLUMN {col} {col_type}")
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_emails_language ON emails(detected_language)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_emails_sentiment ON emails(sentiment_label)"
    )
    logger.info("Schema migration v8: added detected_language, sentiment_label, sentiment_score columns")
