"""Query mixin for EmailDatabase: read operations, full-body retrieval, browsing."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

from .db_schema import _escape_like

if TYPE_CHECKING:
    pass  # conn declared below for mypy


def _safe_json_parse(raw: str | None, default: list | dict | None = None) -> list | dict:
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


class QueryMixin:
    """Read queries, full-body retrieval, browsing, and consistency checks."""

    if TYPE_CHECKING:
        conn: sqlite3.Connection

        def attachments_for_email(self, uid: str) -> list[dict]: ...
        def all_uids(self) -> set[str]: ...

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def email_count(self) -> int:
        """Return total number of emails in the database."""
        row = self.conn.execute("SELECT COUNT(*) AS c FROM emails").fetchone()
        return row["c"]

    def unique_sender_count(self) -> int:
        """Return count of distinct sender email addresses."""
        row = self.conn.execute("SELECT COUNT(DISTINCT sender_email) AS c FROM emails").fetchone()
        return row["c"]

    def date_range(self) -> tuple[str, str]:
        """Return (earliest_date, latest_date) across all emails."""
        row = self.conn.execute("SELECT MIN(NULLIF(date, '')) AS min_d, MAX(NULLIF(date, '')) AS max_d FROM emails").fetchone()
        return (row["min_d"] or "", row["max_d"] or "")

    def folder_counts(self) -> dict[str, int]:
        """Return {folder_name: email_count} for all folders."""
        rows = self.conn.execute("SELECT folder, COUNT(*) AS c FROM emails GROUP BY folder ORDER BY c DESC").fetchall()
        return {r["folder"]: r["c"] for r in rows}

    def top_senders(self, limit: int = 30) -> list[dict]:
        """Return senders ranked by message count."""
        rows = self.conn.execute(
            """SELECT sender_email, sender_name, COUNT(*) AS message_count
               FROM emails
               GROUP BY sender_email
               ORDER BY message_count DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def email_exists(self, uid: str) -> bool:
        """Check whether an email with the given UID exists."""
        row = self.conn.execute("SELECT 1 FROM emails WHERE uid = ?", (uid,)).fetchone()
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
               WHERE sender_email LIKE ? ESCAPE '\\'
               ORDER BY date DESC LIMIT ?""",
            (f"%{_escape_like(sender_email)}%", limit),
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
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Get calendar/meeting emails, optionally filtered by date."""
        query = "SELECT uid, subject, sender_email, date, folder FROM emails WHERE is_calendar_message = 1"
        params: list = []
        if date_from:
            query += " AND SUBSTR(date, 1, 10) >= ?"
            params.append(date_from[:10])
        if date_to:
            query += " AND SUBSTR(date, 1, 10) <= ?"
            params.append(date_to[:10])
        query += " ORDER BY date DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def thread_by_references(self, message_id: str, limit: int = 50) -> list[dict]:
        """Find emails whose references_json contains a given message-id."""
        rows = self.conn.execute(
            """SELECT uid, subject, sender_email, date, folder
               FROM emails
               WHERE references_json LIKE ? ESCAPE '\\'
               ORDER BY date ASC LIMIT ?""",
            (f"%{_escape_like(message_id)}%", limit),
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
            if r["type"] in result:
                result[r["type"]].append(_format_recipient(r["display_name"], r["address"]))
        return result

    def _recipients_for_uids(self, uids: list[str]) -> dict[str, dict[str, list[str]]]:
        """Return {uid: {to: [...], cc: [...], bcc: [...]}} for multiple emails in one query."""
        if not uids:
            return {}
        placeholders = ",".join("?" * len(uids))
        rows = self.conn.execute(
            f"SELECT address, display_name, type, email_uid FROM recipients WHERE email_uid IN ({placeholders})",  # nosec B608
            uids,
        ).fetchall()
        result: dict[str, dict[str, list[str]]] = {}
        for r in rows:
            uid = r["email_uid"]
            if uid not in result:
                result[uid] = {"to": [], "cc": [], "bcc": []}
            if r["type"] in result[uid]:
                result[uid][r["type"]].append(_format_recipient(r["display_name"], r["address"]))
        return result

    def get_email_full(self, uid: str) -> dict | None:
        """Get a single email with full body text by UID."""
        row = self.conn.execute("SELECT * FROM emails WHERE uid = ?", (uid,)).fetchone()
        if not row:
            return None
        email = dict(row)
        recipients = self._recipients_for_uid(uid)
        email["to"] = recipients["to"]
        email["cc"] = recipients["cc"]
        email["bcc"] = recipients["bcc"]
        email["categories"] = _safe_json_parse(email.pop("categories", None))
        email["references"] = _safe_json_parse(email.pop("references_json", None))
        # Attachments from normalized table
        email["attachments"] = self.attachments_for_email(uid)
        return email

    def get_emails_full_batch(self, uids: list[str]) -> dict[str, dict]:
        """Get multiple emails with full body, recipients, and attachments.

        Returns {uid: email_dict} -- 3 queries regardless of N.
        """
        if not uids:
            return {}
        placeholders = ",".join("?" * len(uids))
        # 1) Emails
        rows = self.conn.execute(
            f"SELECT * FROM emails WHERE uid IN ({placeholders})",  # nosec B608
            uids,
        ).fetchall()
        # 2) Recipients (batch)
        all_recipients = self._recipients_for_uids(uids)
        # 3) Attachments (batch)
        att_rows = self.conn.execute(
            "SELECT name, mime_type, size, content_id, is_inline, email_uid"  # nosec B608
            f" FROM attachments WHERE email_uid IN ({placeholders})",
            uids,
        ).fetchall()
        attachments_by_uid: dict[str, list[dict]] = {}
        for a in att_rows:
            attachments_by_uid.setdefault(a["email_uid"], []).append(
                {
                    "name": a["name"],
                    "mime_type": a["mime_type"],
                    "size": a["size"],
                    "content_id": a["content_id"],
                    "is_inline": a["is_inline"],
                }
            )

        result: dict[str, dict] = {}
        for row in rows:
            email = dict(row)
            uid = email["uid"]
            recips = all_recipients.get(uid, {"to": [], "cc": [], "bcc": []})
            email["to"] = recips["to"]
            email["cc"] = recips["cc"]
            email["bcc"] = recips["bcc"]
            email["categories"] = _safe_json_parse(email.pop("categories", None))
            email["references"] = _safe_json_parse(email.pop("references_json", None))
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
        # Batch-fetch attachments (same pattern as get_emails_full_batch)
        attachments_by_uid: dict[str, list[dict]] = {}
        if uids:
            placeholders = ",".join("?" * len(uids))
            att_rows = self.conn.execute(
                "SELECT name, mime_type, size, content_id, is_inline, email_uid"  # nosec B608
                f" FROM attachments WHERE email_uid IN ({placeholders})",
                uids,
            ).fetchall()
            for a in att_rows:
                attachments_by_uid.setdefault(a["email_uid"], []).append(
                    {
                        "name": a["name"],
                        "mime_type": a["mime_type"],
                        "size": a["size"],
                        "content_id": a["content_id"],
                        "is_inline": a["is_inline"],
                    }
                )
        result = []
        for row in rows:
            email = dict(row)
            uid = email["uid"]
            recips = all_recipients.get(uid, {"to": [], "cc": [], "bcc": []})
            email["to"] = recips["to"]
            email["cc"] = recips["cc"]
            email["bcc"] = recips["bcc"]
            email["categories"] = _safe_json_parse(email.pop("categories", None))
            email["references"] = _safe_json_parse(email.pop("references_json", None))
            email["attachments"] = attachments_by_uid.get(uid, [])
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
        date_from: str | None = None,
        date_to: str | None = None,
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
            date_from: Optional start date in YYYY-MM-DD format (inclusive).
            date_to: Optional end date in YYYY-MM-DD format (inclusive).

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
            conditions.append("sender_email LIKE ? ESCAPE '\\'")
            params.append(f"%{_escape_like(sender)}%")
        if date_from:
            conditions.append("SUBSTR(date, 1, 10) >= ?")
            params.append(date_from[:10])
        if date_to:
            conditions.append("SUBSTR(date, 1, 10) <= ?")
            params.append(date_to[:10])

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""

        total_row = self.conn.execute(f"SELECT COUNT(*) AS c FROM emails{join}{where}", params).fetchone()  # nosec B608
        total = total_row["c"]

        rows = self.conn.execute(
            f"SELECT emails.uid, subject, sender_name, sender_email, date, folder,"  # nosec B608
            f" email_type, has_attachments, attachment_count, body_length,"
            f" conversation_id"
            f" FROM emails{join}{where}"
            f" ORDER BY {sort_by} {sort_order}"
            f" LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

        return {
            "emails": [dict(r) for r in rows],
            "total": total,
            "offset": offset,
            "limit": limit,
        }

    # ------------------------------------------------------------------
    # Re-embed helper
    # ------------------------------------------------------------------

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
        # Use ``or`` instead of ``.get(key, default)`` for string/int fields
        # because dict(sqlite_row) sets keys to None for NULL columns and
        # ``.get("key", "")`` returns None (not "") when the key exists.
        return {
            "uid": full["uid"],
            "message_id": full.get("message_id") or "",
            "subject": full.get("subject") or "",
            "sender_name": full.get("sender_name") or "",
            "sender_email": full.get("sender_email") or "",
            "to": full.get("to") or [],
            "cc": full.get("cc") or [],
            "bcc": full.get("bcc") or [],
            "date": full.get("date") or "",
            "body": body,
            "folder": full.get("folder") or "",
            "has_attachments": bool(full.get("has_attachments") or full.get("attachment_count")),
            "attachment_names": [a["name"] for a in (full.get("attachments") or []) if a.get("name")],
            "attachments": full.get("attachments") or [],
            "attachment_count": full.get("attachment_count") or 0,
            "conversation_id": full.get("conversation_id") or "",
            "in_reply_to": full.get("in_reply_to") or "",
            "references": full.get("references") or [],
            "priority": full.get("priority") or 0,
            "is_read": bool(full.get("is_read", True)),
            "email_type": full.get("email_type") or "original",
            "base_subject": full.get("base_subject") or "",
            "categories": full.get("categories") if isinstance(full.get("categories"), list) else [],
            "thread_topic": full.get("thread_topic") or "",
            "inference_classification": full.get("inference_classification") or "",
            "is_calendar_message": bool(full.get("is_calendar_message")),
        }

    # ------------------------------------------------------------------
    # Grouping
    # ------------------------------------------------------------------

    def emails_by_base_subject(self, min_group_size: int = 2) -> list[tuple[str, list[tuple[str, str]]]]:
        """Group emails by base_subject for dedup comparison.

        Returns:
            List of (base_subject, [(uid, body_text), ...]) tuples,
            only groups with >= min_group_size emails.
        """
        # Single query: fetch all emails whose base_subject appears >= min_group_size times
        rows = self.conn.execute(
            """
            SELECT e.base_subject, e.uid, e.body_text
            FROM emails e
            JOIN (
                SELECT base_subject
                FROM emails
                WHERE base_subject IS NOT NULL AND base_subject != ''
                GROUP BY base_subject
                HAVING COUNT(*) >= ?
                ORDER BY COUNT(*) DESC
                LIMIT 500
            ) g ON e.base_subject = g.base_subject
            ORDER BY e.base_subject, e.date
            """,
            (min_group_size,),
        ).fetchall()

        # Group results in Python
        groups: dict[str, list[tuple[str, str]]] = {}
        for row in rows:
            groups.setdefault(row["base_subject"], []).append((row["uid"], row["body_text"] or ""))

        return list(groups.items())

    # ------------------------------------------------------------------
    # Consistency checks
    # ------------------------------------------------------------------

    def consistency_check(self, chromadb_uids: set[str]) -> dict:
        """Compare SQLite email UIDs against ChromaDB chunk UIDs.

        ChromaDB chunk IDs follow the pattern ``{uid}__{chunk_index}``, so
        the email UID is the prefix before ``__``.

        Args:
            chromadb_uids: Set of chunk IDs from ChromaDB (e.g. via
                ``iter_collection_ids()``).

        Returns:
            Dict with ``sqlite_only`` (UIDs in SQLite but no chunks in
            ChromaDB), ``chromadb_only`` (UIDs with chunks in ChromaDB but
            missing from SQLite), and counts.
        """
        sqlite_uids = self.all_uids()

        # Extract email UIDs from chunk IDs: "uid__0" -> "uid"
        chromadb_email_uids: set[str] = set()
        for chunk_id in chromadb_uids:
            parts = chunk_id.split("__", 1)
            if parts:
                chromadb_email_uids.add(parts[0])

        sqlite_only = sqlite_uids - chromadb_email_uids
        chromadb_only = chromadb_email_uids - sqlite_uids

        return {
            "sqlite_count": len(sqlite_uids),
            "chromadb_uid_count": len(chromadb_email_uids),
            "sqlite_only": sorted(sqlite_only),
            "chromadb_only": sorted(chromadb_only),
            "sqlite_only_count": len(sqlite_only),
            "chromadb_only_count": len(chromadb_only),
            "is_consistent": len(sqlite_only) == 0 and len(chromadb_only) == 0,
        }
