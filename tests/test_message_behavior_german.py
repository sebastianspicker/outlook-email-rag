from __future__ import annotations

from src.language_rhetoric import analyze_message_rhetoric
from src.message_behavior import analyze_message_behavior


def test_analyze_message_behavior_maps_german_narrative_framing_to_blame_shifting() -> None:
    text = (
        "Aufgrund Ihrer Verzögerung ist der Vorgang ins Stocken geraten. "
        "Nach hiesigem Kenntnisstand entsteht der Eindruck, dass dies erneut von Ihnen ausgeht."
    )
    rhetoric = analyze_message_rhetoric(text, text_scope="authored_text")

    findings = analyze_message_behavior(
        text,
        text_scope="authored_text",
        rhetoric=rhetoric,
    )

    behavior_ids = [candidate["behavior_id"] for candidate in findings["behavior_candidates"]]

    assert "blame_shifting" in behavior_ids


def test_analyze_message_behavior_keeps_neutral_formal_german_from_becoming_behavior_by_default() -> None:
    text = (
        "Bitte nehmen Sie zur Kenntnis, dass die Unterlagen im Portal bereitstehen. Für Rückfragen stehe ich gern zur Verfügung."
    )
    rhetoric = analyze_message_rhetoric(text, text_scope="authored_text")

    findings = analyze_message_behavior(
        text,
        text_scope="authored_text",
        rhetoric=rhetoric,
        recipient_count=1,
        visible_recipient_emails=["alex@example.com"],
        case_target_email="alex@example.com",
        case_target_name="Alex Example",
    )

    assert findings["behavior_candidates"] == []
