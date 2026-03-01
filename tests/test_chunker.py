import threading

from src.chunker import _split_text, chunk_email


def test_long_header_does_not_break_chunking():
    email = {
        "uid": "123",
        "message_id": "m",
        "subject": "Subject " + ("x" * 2000),
        "sender_name": "Sender",
        "sender_email": "sender@example.com",
        "to": ["to@example.com"],
        "cc": [],
        "date": "2023-01-01",
        "body": "Body paragraph. " * 200,
        "folder": "Inbox",
        "has_attachments": False,
    }

    chunks = chunk_email(email)
    assert len(chunks) >= 1
    assert all(chunk.text for chunk in chunks)


def test_splitter_makes_progress_for_boundary_breaks():
    # Boundary at half-window can cause start pointer stagnation if progress isn't guarded.
    text = ("x" * 150) + "\n\n" + ("y" * 600)
    result = {"chunks": None}

    def _run() -> None:
        result["chunks"] = _split_text(text, max_len=300, overlap=200)

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    worker.join(0.5)

    assert not worker.is_alive()
    assert result["chunks"] is not None
    assert len(result["chunks"]) >= 2


def test_short_email_stays_single_chunk():
    email = {
        "uid": "1",
        "message_id": "m1",
        "subject": "Short",
        "sender_name": "S",
        "sender_email": "s@example.com",
        "to": [],
        "cc": [],
        "date": "2023-01-01",
        "body": "tiny body",
        "folder": "Inbox",
        "has_attachments": False,
    }

    chunks = chunk_email(email)
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "1__0"
