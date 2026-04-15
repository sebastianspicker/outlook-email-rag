import pytest


def test_infer_quoted_speaker_prefers_quoted_header_context():
    from src.tools.search_answer_context import _infer_quoted_speaker

    speaker_email, source, confidence = _infer_quoted_speaker(
        full_email=None,
        authored_email="alice@example.com",
        conversation_context={"participants": ["alice@example.com", "bob@example.com"]},
        segment_text=(
            "From: Bob Example <bob@example.com>\n"
            "Sent: Tuesday, April 1, 2025 09:00\n"
            "To: Alice Example <alice@example.com>\n"
            "Subject: Figures\n\n"
            "Can you send the figures?"
        ),
    )

    assert speaker_email == "bob@example.com"
    assert source == "quoted_from_header"
    assert confidence == pytest.approx(0.85)


def test_infer_quoted_speaker_uses_single_email_in_quoted_block():
    from src.tools.search_answer_context import _infer_quoted_speaker

    speaker_email, source, confidence = _infer_quoted_speaker(
        full_email=None,
        authored_email="alice@example.com",
        conversation_context={"participants": ["alice@example.com", "bob@example.com", "carol@example.com"]},
        segment_text="On Tue, Bob Example <bob@example.com> wrote:",
    )

    assert speaker_email == "bob@example.com"
    assert source == "quoted_block_email"
    assert confidence == pytest.approx(0.6)


def test_infer_quoted_speaker_keeps_multi_party_case_unresolved():
    from src.tools.search_answer_context import _infer_quoted_speaker

    speaker_email, source, confidence = _infer_quoted_speaker(
        full_email=None,
        authored_email="alice@example.com",
        conversation_context={"participants": ["alice@example.com", "bob@example.com", "carol@example.com"]},
        segment_text="Bob Example <bob@example.com>\nCc: Carol Example <carol@example.com>",
    )

    assert speaker_email == ""
    assert source == "unresolved"
    assert confidence == pytest.approx(0.0)


def test_infer_quoted_speaker_prefers_from_header_in_multi_party_case():
    from src.tools.search_answer_context import _infer_quoted_speaker

    speaker_email, source, confidence = _infer_quoted_speaker(
        full_email=None,
        authored_email="alice@example.com",
        conversation_context={"participants": ["alice@example.com", "bob@example.com", "carol@example.com"]},
        segment_text=(
            "From: Bob Example <bob@example.com>\n"
            "Cc: Carol Example <carol@example.com>\n"
            "To: Alice Example <alice@example.com>\n\n"
            "Can you send the figures?"
        ),
    )

    assert speaker_email == "bob@example.com"
    assert source == "quoted_from_header"
    assert confidence == pytest.approx(0.85)


def test_infer_quoted_speaker_does_not_overclaim_from_reply_context_only():
    from src.tools.search_answer_context import _infer_quoted_speaker

    speaker_email, source, confidence = _infer_quoted_speaker(
        full_email={
            "reply_context_from": "bob@example.com",
            "reply_context_to": ["alice@example.com", "carol@example.com"],
        },
        authored_email="alice@example.com",
        conversation_context={"participants": ["alice@example.com", "bob@example.com", "carol@example.com"]},
        segment_text="Bob Example <bob@example.com>\nCc: Carol Example <carol@example.com>",
    )

    assert speaker_email == ""
    assert source == "unresolved"
    assert confidence == pytest.approx(0.0)
