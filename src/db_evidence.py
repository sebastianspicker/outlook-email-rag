"""Evidence management mixin for EmailDatabase."""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import TYPE_CHECKING, ClassVar

from .db_schema import _escape_like

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r"[\s\xa0]+")


def _normalize_ws(text: str) -> str:
    """Collapse all whitespace (including nbsp) to single spaces and lowercase."""
    return _WS_RE.sub(" ", text.strip()).lower()


class EvidenceMixin:
    """Evidence item CRUD, verification, search, and statistics."""

    if TYPE_CHECKING:
        conn: sqlite3.Connection

        @staticmethod
        def compute_content_hash(content: str) -> str: ...  # from CustodyMixin

        def log_custody_event(
            self,
            action: str,
            target_type: str | None = ...,
            target_id: str | None = ...,
            details: dict | None = ...,
            content_hash: str | None = ...,
            actor: str = ...,
            commit: bool = ...,
        ) -> int: ...  # from CustodyMixin

    EVIDENCE_CATEGORIES: ClassVar[list[str]] = [
        "bossing",
        "harassment",
        "discrimination",
        "retaliation",
        "hostile_environment",
        "micromanagement",
        "exclusion",
        "gaslighting",
        "workload",
        "general",
    ]

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
        relevance = max(1, min(5, int(relevance)))

        if category not in self.EVIDENCE_CATEGORIES:
            logger.warning(
                "Non-standard evidence category: %s (expected one of %s)",
                category,
                self.EVIDENCE_CATEGORIES,
            )

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
        recipients = ", ".join(f"{r['display_name']} <{r['address']}>" if r["display_name"] else r["address"] for r in recip_rows)

        # Verify quote against body
        body_text = email_row["body_text"] or ""
        verified = 1 if key_quote.strip() and _normalize_ws(key_quote) in _normalize_ws(body_text) else 0

        content_hash = self.compute_content_hash(f"{email_uid}|{category}|{key_quote}")

        try:
            cur = self.conn.execute(
                """INSERT INTO evidence_items
                   (email_uid, category, key_quote, summary, relevance,
                    sender_name, sender_email, date, recipients, subject, notes, verified,
                    content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    email_uid,
                    category,
                    key_quote,
                    summary,
                    relevance,
                    email_row["sender_name"],
                    email_row["sender_email"],
                    email_row["date"],
                    recipients,
                    email_row["subject"],
                    notes,
                    verified,
                    content_hash,
                ),
            )
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
                commit=False,
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

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
            f"SELECT COUNT(*) AS c FROM evidence_items{where}",  # nosec B608
            params,
        ).fetchone()
        total = total_row["c"]

        rows = self.conn.execute(
            f"SELECT * FROM evidence_items{where} ORDER BY date ASC LIMIT ? OFFSET ?",  # nosec B608
            [*params, limit, offset],
        ).fetchall()

        return {
            "items": [dict(r) for r in rows],
            "total": total,
        }

    def get_evidence(self, evidence_id: int) -> dict | None:
        """Get a single evidence item by ID."""
        row = self.conn.execute(
            "SELECT * FROM evidence_items WHERE id = ?",
            (evidence_id,),
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
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False

        if "relevance" in updates and updates["relevance"] is not None:
            updates["relevance"] = max(1, min(5, int(updates["relevance"])))

        # Check item exists and snapshot old values
        existing = self.conn.execute(
            "SELECT * FROM evidence_items WHERE id = ?",
            (evidence_id,),
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
            updates["verified"] = 1 if new_quote and _normalize_ws(new_quote) in _normalize_ws(body_text) else 0

        # Recompute content hash
        category = updates.get("category", existing["category"])
        key_quote = updates.get("key_quote", existing["key_quote"])
        new_hash = self.compute_content_hash(f"{existing['email_uid']}|{category}|{key_quote}")
        updates["content_hash"] = new_hash

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        set_clause += ", updated_at = datetime('now')"
        params = [*updates.values(), evidence_id]

        try:
            cur = self.conn.execute(
                f"UPDATE evidence_items SET {set_clause} WHERE id = ?",  # nosec B608
                params,
            )

            if cur.rowcount > 0:
                self.log_custody_event(
                    "evidence_update",
                    target_type="evidence",
                    target_id=str(evidence_id),
                    details={"old_values": old_values, "new_values": {k: v for k, v in updates.items() if k != "content_hash"}},
                    content_hash=new_hash,
                    commit=False,
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return cur.rowcount > 0

    def remove_evidence(self, evidence_id: int) -> bool:
        """Delete an evidence item by ID. Logs custody event with snapshot. Returns True if deleted."""
        # Snapshot before deletion
        existing = self.conn.execute(
            "SELECT * FROM evidence_items WHERE id = ?",
            (evidence_id,),
        ).fetchone()

        try:
            cur = self.conn.execute(
                "DELETE FROM evidence_items WHERE id = ?",
                (evidence_id,),
            )

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
                    commit=False,
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
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
               LEFT JOIN emails e ON ei.email_uid = e.uid"""
        ).fetchall()

        verified_count = 0
        failed_count = 0
        orphaned_count = 0
        failures: list[dict] = []
        verified_ids: list[tuple[int]] = []
        failed_ids: list[tuple[int]] = []

        for row in rows:
            body_text = row["body_text"]
            quote = (row["key_quote"] or "").strip()

            if body_text is None:
                # Orphaned evidence — source email missing
                orphaned_count += 1
                failed_ids.append((row["id"],))
                failures.append(
                    {
                        "evidence_id": row["id"],
                        "key_quote_preview": quote[:80] + ("..." if len(quote) > 80 else ""),
                        "email_uid": row["email_uid"],
                        "orphaned": True,
                    }
                )
                continue

            is_verified = 1 if quote and _normalize_ws(quote) in _normalize_ws(body_text) else 0

            if is_verified:
                verified_count += 1
                verified_ids.append((row["id"],))
            else:
                failed_count += 1
                failed_ids.append((row["id"],))
                failures.append(
                    {
                        "evidence_id": row["id"],
                        "key_quote_preview": quote[:80] + ("..." if len(quote) > 80 else ""),
                        "email_uid": row["email_uid"],
                    }
                )

        if verified_ids:
            self.conn.executemany(
                "UPDATE evidence_items SET verified = 1 WHERE id = ?",
                verified_ids,
            )
        if failed_ids:
            self.conn.executemany(
                "UPDATE evidence_items SET verified = 0 WHERE id = ?",
                failed_ids,
            )
        self.conn.commit()
        return {
            "verified": verified_count,
            "failed": failed_count,
            "orphaned": orphaned_count,
            "total": verified_count + failed_count + orphaned_count,
            "failures": failures,
        }

    def evidence_stats(
        self,
        category: str | None = None,
        min_relevance: int | None = None,
    ) -> dict:
        """Return evidence collection statistics, optionally filtered.

        Args:
            category: Only count items in this category.
            min_relevance: Only count items with relevance >= this value.

        Returns:
            {"total": int, "verified": int, "unverified": int,
             "by_category": [{"category": str, "count": int}, ...],
             "by_relevance": [{"relevance": int, "count": int}, ...]}
        """
        where_clauses: list[str] = []
        params: list[object] = []
        if category:
            where_clauses.append("category = ?")
            params.append(category)
        if min_relevance is not None:
            where_clauses.append("relevance >= ?")
            params.append(min_relevance)
        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        total_row = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM evidence_items{where_sql}",  # nosec B608
            params,
        ).fetchone()
        total = total_row["c"]

        verified_row = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM evidence_items{where_sql} {'AND' if where_clauses else 'WHERE'} verified = 1",  # nosec B608
            params,
        ).fetchone()
        verified = verified_row["c"]

        cat_rows = self.conn.execute(
            f"SELECT category, COUNT(*) AS count FROM evidence_items{where_sql} GROUP BY category ORDER BY count DESC",  # nosec B608
            params,
        ).fetchall()

        rel_rows = self.conn.execute(
            f"SELECT relevance, COUNT(*) AS count FROM evidence_items{where_sql} GROUP BY relevance ORDER BY relevance DESC",  # nosec B608
            params,
        ).fetchall()

        return {
            "total": total,
            "verified": verified,
            "unverified": total - verified,
            "by_category": [dict(r) for r in cat_rows],
            "by_relevance": [dict(r) for r in rel_rows],
        }

    # ── Evidence: extended queries ────────────────────────────

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
        conditions = ["(key_quote LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\')"]
        pattern = f"%{_escape_like(query)}%"
        params: list = [pattern, pattern, pattern]

        if category:
            conditions.append("category = ?")
            params.append(category)
        if min_relevance is not None:
            conditions.append("relevance >= ?")
            params.append(min_relevance)

        where = " WHERE " + " AND ".join(conditions)

        total_row = self.conn.execute(
            f"SELECT COUNT(*) AS c FROM evidence_items{where}",  # nosec B608
            params,
        ).fetchone()

        rows = self.conn.execute(
            f"SELECT * FROM evidence_items{where} ORDER BY date ASC LIMIT ?",  # nosec B608
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
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict]:
        """Return evidence items in chronological order for narrative building.

        Args:
            category: Filter by evidence category.
            min_relevance: Minimum relevance score.
            limit: Maximum items to return (None = unlimited).
            offset: Number of items to skip (for pagination).

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

        sql = f"SELECT * FROM evidence_items{where} ORDER BY date ASC"  # nosec B608
        if limit is not None and limit >= 0:
            sql += " LIMIT ?"
            params.append(limit)
        elif offset > 0:
            # OFFSET requires LIMIT in SQLite; use -1 for unlimited
            sql += " LIMIT -1"
        if offset > 0:
            sql += " OFFSET ?"
            params.append(offset)

        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def evidence_categories(self) -> list[dict]:
        """Return all canonical categories with current evidence counts.

        Returns:
            List of {"category": str, "count": int} for all 10 canonical categories.
        """
        count_rows = self.conn.execute("SELECT category, COUNT(*) AS count FROM evidence_items GROUP BY category").fetchall()
        counts = {r["category"]: r["count"] for r in count_rows}

        return [{"category": cat, "count": counts.get(cat, 0)} for cat in self.EVIDENCE_CATEGORIES]
