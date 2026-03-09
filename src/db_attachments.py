"""Attachment query mixin for EmailDatabase."""

from __future__ import annotations


def _attachment_filter_conditions(
    filename: str | None,
    extension: str | None,
    mime_type: str | None,
) -> tuple[list[str], list]:
    """Build WHERE conditions and params for attachment filename/extension/mime filters."""
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
    return conditions, params


class AttachmentMixin:
    """Attachment queries: per-email, stats, browse, and search."""

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
        conditions, params = _attachment_filter_conditions(filename, extension, mime_type)
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
        conditions, params = _attachment_filter_conditions(filename, extension, mime_type)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " GROUP BY e.uid ORDER BY e.date DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
