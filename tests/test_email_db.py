"""Tests for the SQLite EmailDatabase."""

from src.email_db import EmailDatabase, _parse_address
from src.parse_olm import Email


def _make_email(**overrides) -> Email:
    defaults = {
        "message_id": "<msg1@example.com>",
        "subject": "Hello",
        "sender_name": "Alice",
        "sender_email": "alice@example.com",
        "to": ["Bob <bob@example.com>"],
        "cc": [],
        "bcc": [],
        "date": "2024-01-15T10:30:00",
        "body_text": "Test body",
        "body_html": "",
        "folder": "Inbox",
        "has_attachments": False,
    }
    defaults.update(overrides)
    return Email(**defaults)


class TestParseAddress:
    def test_name_and_email(self):
        assert _parse_address("Alice <alice@example.com>") == ("Alice", "alice@example.com")

    def test_quoted_name(self):
        assert _parse_address('"Alice B" <alice@example.com>') == ("Alice B", "alice@example.com")

    def test_bare_email(self):
        assert _parse_address("alice@example.com") == ("", "alice@example.com")

    def test_name_only(self):
        assert _parse_address("Alice") == ("Alice", "")

    def test_empty(self):
        assert _parse_address("") == ("", "")


class TestEmailDatabase:
    def test_schema_created(self):
        db = EmailDatabase(":memory:")
        tables = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in tables}
        assert "emails" in names
        assert "recipients" in names
        assert "contacts" in names
        assert "communication_edges" in names
        assert "schema_version" in names
        db.close()

    def test_insert_email(self):
        db = EmailDatabase(":memory:")
        email = _make_email()
        assert db.insert_email(email) is True
        assert db.email_count() == 1
        db.close()

    def test_insert_duplicate(self):
        db = EmailDatabase(":memory:")
        email = _make_email()
        assert db.insert_email(email) is True
        assert db.insert_email(email) is False
        assert db.email_count() == 1
        db.close()

    def test_recipients_populated(self):
        db = EmailDatabase(":memory:")
        email = _make_email(
            to=["Bob <bob@example.com>"],
            cc=["Carol <carol@example.com>"],
            bcc=["dave@example.com"],
        )
        db.insert_email(email)
        rows = db.conn.execute(
            "SELECT address, type FROM recipients ORDER BY type"
        ).fetchall()
        result = [(r["address"], r["type"]) for r in rows]
        assert ("dave@example.com", "bcc") in result
        assert ("carol@example.com", "cc") in result
        assert ("bob@example.com", "to") in result
        db.close()

    def test_contacts_upserted(self):
        db = EmailDatabase(":memory:")
        email = _make_email()
        db.insert_email(email)

        sender = db.conn.execute(
            "SELECT * FROM contacts WHERE email_address = 'alice@example.com'"
        ).fetchone()
        assert sender is not None
        assert sender["sent_count"] == 1
        assert sender["received_count"] == 0

        recipient = db.conn.execute(
            "SELECT * FROM contacts WHERE email_address = 'bob@example.com'"
        ).fetchone()
        assert recipient is not None
        assert recipient["received_count"] == 1
        assert recipient["sent_count"] == 0
        db.close()

    def test_contacts_accumulate(self):
        db = EmailDatabase(":memory:")
        db.insert_email(_make_email(message_id="<m1@ex.com>", date="2024-01-01T00:00:00"))
        db.insert_email(_make_email(message_id="<m2@ex.com>", date="2024-06-01T00:00:00"))

        sender = db.conn.execute(
            "SELECT * FROM contacts WHERE email_address = 'alice@example.com'"
        ).fetchone()
        assert sender["sent_count"] == 2
        assert sender["first_seen"] == "2024-01-01T00:00:00"
        assert sender["last_seen"] == "2024-06-01T00:00:00"
        db.close()

    def test_communication_edges(self):
        db = EmailDatabase(":memory:")
        db.insert_email(_make_email(message_id="<m1@ex.com>"))
        db.insert_email(_make_email(message_id="<m2@ex.com>"))

        edge = db.conn.execute(
            "SELECT * FROM communication_edges WHERE sender_email='alice@example.com'"
        ).fetchone()
        assert edge is not None
        assert edge["email_count"] == 2
        assert edge["recipient_email"] == "bob@example.com"
        db.close()

    def test_email_count(self):
        db = EmailDatabase(":memory:")
        assert db.email_count() == 0
        db.insert_email(_make_email())
        assert db.email_count() == 1
        db.close()

    def test_top_senders(self):
        db = EmailDatabase(":memory:")
        db.insert_email(_make_email(message_id="<m1@ex.com>"))
        db.insert_email(_make_email(message_id="<m2@ex.com>"))
        db.insert_email(
            _make_email(
                message_id="<m3@ex.com>",
                sender_email="eve@example.com",
                sender_name="Eve",
            )
        )
        senders = db.top_senders(limit=10)
        assert senders[0]["sender_email"] == "alice@example.com"
        assert senders[0]["message_count"] == 2
        assert senders[1]["sender_email"] == "eve@example.com"
        db.close()

    def test_date_range(self):
        db = EmailDatabase(":memory:")
        db.insert_email(
            _make_email(message_id="<m1@ex.com>", date="2023-01-01T00:00:00")
        )
        db.insert_email(
            _make_email(message_id="<m2@ex.com>", date="2024-12-31T23:59:59")
        )
        min_d, max_d = db.date_range()
        assert min_d == "2023-01-01T00:00:00"
        assert max_d == "2024-12-31T23:59:59"
        db.close()

    def test_folder_counts(self):
        db = EmailDatabase(":memory:")
        db.insert_email(_make_email(message_id="<m1@ex.com>", folder="Inbox"))
        db.insert_email(_make_email(message_id="<m2@ex.com>", folder="Inbox"))
        db.insert_email(_make_email(message_id="<m3@ex.com>", folder="Sent"))
        counts = db.folder_counts()
        assert counts["Inbox"] == 2
        assert counts["Sent"] == 1
        db.close()

    def test_email_exists(self):
        db = EmailDatabase(":memory:")
        email = _make_email()
        db.insert_email(email)
        assert db.email_exists(email.uid) is True
        assert db.email_exists("nonexistent") is False
        db.close()

    def test_batch_insert(self):
        db = EmailDatabase(":memory:")
        emails = [
            _make_email(message_id="<m1@ex.com>"),
            _make_email(message_id="<m2@ex.com>"),
            _make_email(message_id="<m3@ex.com>"),
        ]
        inserted = db.insert_emails_batch(emails)
        assert inserted == 3
        assert db.email_count() == 3
        db.close()

    def test_batch_skips_duplicates(self):
        db = EmailDatabase(":memory:")
        db.insert_email(_make_email(message_id="<m1@ex.com>"))
        emails = [
            _make_email(message_id="<m1@ex.com>"),  # duplicate
            _make_email(message_id="<m2@ex.com>"),
        ]
        inserted = db.insert_emails_batch(emails)
        assert inserted == 1
        assert db.email_count() == 2
        db.close()

    def test_unique_sender_count(self):
        db = EmailDatabase(":memory:")
        db.insert_email(_make_email(message_id="<m1@ex.com>"))
        db.insert_email(
            _make_email(message_id="<m2@ex.com>", sender_email="bob@example.com")
        )
        assert db.unique_sender_count() == 2
        db.close()


    def test_migration_v5_to_v6_creates_composite_indexes(self):
        db = EmailDatabase(":memory:")
        indexes = db.conn.execute("PRAGMA index_list(emails)").fetchall()
        index_names = {row["name"] for row in indexes}
        assert "idx_emails_sender_date" in index_names
        assert "idx_emails_folder_date" in index_names
        db.close()


class TestNetworkQueries:
    def test_top_contacts(self):
        db = EmailDatabase(":memory:")
        db.insert_email(
            _make_email(message_id="<m1@ex.com>", to=["Bob <bob@example.com>"])
        )
        db.insert_email(
            _make_email(message_id="<m2@ex.com>", to=["Bob <bob@example.com>"])
        )
        db.insert_email(
            _make_email(message_id="<m3@ex.com>", to=["Carol <carol@example.com>"])
        )
        contacts = db.top_contacts("alice@example.com", limit=10)
        assert contacts[0]["partner"] == "bob@example.com"
        assert contacts[0]["total"] == 2
        assert contacts[1]["partner"] == "carol@example.com"
        db.close()

    def test_top_contacts_empty(self):
        db = EmailDatabase(":memory:")
        assert db.top_contacts("nobody@example.com") == []
        db.close()

    def test_communication_between(self):
        db = EmailDatabase(":memory:")
        db.insert_email(
            _make_email(message_id="<m1@ex.com>", to=["Bob <bob@example.com>"])
        )
        db.insert_email(
            _make_email(
                message_id="<m2@ex.com>",
                sender_email="bob@example.com",
                sender_name="Bob",
                to=["Alice <alice@example.com>"],
            )
        )
        result = db.communication_between("alice@example.com", "bob@example.com")
        assert result["a_to_b"] == 1
        assert result["b_to_a"] == 1
        assert result["total"] == 2
        db.close()

    def test_communication_between_no_relationship(self):
        db = EmailDatabase(":memory:")
        result = db.communication_between("a@example.com", "b@example.com")
        assert result["total"] == 0
        db.close()

    def test_all_edges(self):
        db = EmailDatabase(":memory:")
        db.insert_email(
            _make_email(message_id="<m1@ex.com>", to=["Bob <bob@example.com>"])
        )
        edges = db.all_edges()
        assert len(edges) == 1
        assert edges[0] == ("alice@example.com", "bob@example.com", 1)
        db.close()


class TestTemporalQueries:
    def test_email_dates(self):
        db = EmailDatabase(":memory:")
        db.insert_email(_make_email(message_id="<m1@ex.com>", date="2024-01-01T00:00:00"))
        db.insert_email(_make_email(message_id="<m2@ex.com>", date="2024-06-01T00:00:00"))
        dates = db.email_dates()
        assert len(dates) == 2
        db.close()

    def test_email_dates_filtered(self):
        db = EmailDatabase(":memory:")
        db.insert_email(_make_email(message_id="<m1@ex.com>", date="2024-01-01T00:00:00"))
        db.insert_email(_make_email(message_id="<m2@ex.com>", date="2024-06-01T00:00:00"))
        dates = db.email_dates(date_from="2024-03-01")
        assert len(dates) == 1
        db.close()

    def test_response_pairs(self):
        db = EmailDatabase(":memory:")
        db.insert_email(
            _make_email(
                message_id="<orig@ex.com>",
                date="2024-01-01T10:00:00",
            )
        )
        db.insert_email(
            _make_email(
                message_id="<reply@ex.com>",
                subject="RE: Hello",
                sender_email="bob@example.com",
                sender_name="Bob",
                to=["Alice <alice@example.com>"],
                in_reply_to="<orig@ex.com>",
                date="2024-01-01T11:00:00",
            )
        )
        pairs = db.response_pairs()
        assert len(pairs) == 1
        assert pairs[0]["reply_sender"] == "bob@example.com"
        assert pairs[0]["original_sender"] == "alice@example.com"
        db.close()


class TestEntityOperations:
    def test_insert_and_search(self):
        db = EmailDatabase(":memory:")
        db.insert_email(_make_email())
        email = _make_email()
        entities = [
            ("acme.com", "organization", "acme.com"),
            ("https://example.com", "url", "https://example.com"),
        ]
        db.insert_entities_batch(email.uid, entities)

        results = db.search_by_entity("acme", entity_type="organization")
        assert len(results) == 1
        assert results[0]["entity_text"] == "acme.com"

    def test_top_entities(self):
        db = EmailDatabase(":memory:")
        e1 = _make_email(message_id="<m1@ex.com>")
        e2 = _make_email(message_id="<m2@ex.com>")
        db.insert_email(e1)
        db.insert_email(e2)
        db.insert_entities_batch(e1.uid, [("acme.com", "organization", "acme.com")])
        db.insert_entities_batch(e2.uid, [("acme.com", "organization", "acme.com")])

        top = db.top_entities(entity_type="organization")
        assert top[0]["entity_text"] == "acme.com"
        assert top[0]["email_count"] == 2

    def test_co_occurrences(self):
        db = EmailDatabase(":memory:")
        email = _make_email()
        db.insert_email(email)
        db.insert_entities_batch(
            email.uid,
            [
                ("acme.com", "organization", "acme.com"),
                ("https://example.org/page", "url", "https://example.org/page"),
            ],
        )
        co = db.entity_co_occurrences("acme.com")
        assert len(co) == 1
        assert co[0]["entity_type"] == "url"
        db.close()
