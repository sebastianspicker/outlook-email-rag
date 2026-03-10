"""Entity management mixin for EmailDatabase."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING


def _escape_like(text: str) -> str:
    """Escape SQL LIKE wildcards (``%``, ``_``, ``\\``)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class EntityMixin:
    """NLP entity insert, search, timeline, and co-occurrence queries."""

    if TYPE_CHECKING:
        conn: sqlite3.Connection

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
            row = cur.execute(
                """INSERT INTO entities(entity_text, entity_type, normalized_form)
                   VALUES(?, ?, ?)
                   ON CONFLICT(normalized_form, entity_type) DO UPDATE SET
                     entity_text = excluded.entity_text
                   RETURNING id""",
                (text, etype, norm),
            ).fetchone()
            entity_id = row[0]
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
        escaped = f"%{_escape_like(entity_text.lower())}%"
        if entity_type:
            rows = self.conn.execute(
                """SELECT e.uid, e.subject, e.sender_email, e.date, e.folder,
                          ent.entity_text, ent.entity_type
                   FROM entity_mentions em
                   JOIN entities ent ON em.entity_id = ent.id
                   JOIN emails e ON em.email_uid = e.uid
                   WHERE ent.normalized_form LIKE ? ESCAPE '\\' AND ent.entity_type = ?
                   ORDER BY e.date DESC LIMIT ?""",
                (escaped, entity_type, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT e.uid, e.subject, e.sender_email, e.date, e.folder,
                          ent.entity_text, ent.entity_type
                   FROM entity_mentions em
                   JOIN entities ent ON em.entity_id = ent.id
                   JOIN emails e ON em.email_uid = e.uid
                   WHERE ent.normalized_form LIKE ? ESCAPE '\\'
                   ORDER BY e.date DESC LIMIT ?""",
                (escaped, limit),
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
                 AND ent.normalized_form LIKE ? ESCAPE '\\'
               ORDER BY e.date DESC LIMIT ?""",
            (f"%{_escape_like(name_query.lower())}%", limit),
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
                WHERE ent.normalized_form LIKE ? ESCAPE '\\'
                GROUP BY period
                ORDER BY period""",
            (f"%{_escape_like(entity_text.lower())}%",),
        ).fetchall()
        return [dict(r) for r in rows]

    def entity_co_occurrences(self, entity_text: str, limit: int = 20) -> list[dict]:
        """Entities that co-occur with the given entity in the same emails."""
        rows = self.conn.execute(
            """SELECT ent2.entity_text, ent2.entity_type, ent2.normalized_form,
                      COUNT(DISTINCT em1.email_uid) AS co_occurrence_count
               FROM entity_mentions em1
               JOIN entities ent1 ON em1.entity_id = ent1.id
               JOIN entity_mentions em2 ON em1.email_uid = em2.email_uid
               JOIN entities ent2 ON em2.entity_id = ent2.id
               WHERE ent1.normalized_form LIKE ? ESCAPE '\\' AND ent2.id != ent1.id
               GROUP BY ent2.id
               ORDER BY co_occurrence_count DESC LIMIT ?""",
            (f"%{_escape_like(entity_text.lower())}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]
