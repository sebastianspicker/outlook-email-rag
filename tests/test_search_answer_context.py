def test_weak_message_semantics_describes_source_shell_message():
    from src.formatting import weak_message_semantics

    weak_message = weak_message_semantics(
        {
            "body_kind": "content",
            "body_empty_reason": "source_shell_only",
            "recovery_strategy": "source_shell_summary",
            "recovery_confidence": 0.2,
        }
    )

    assert weak_message is not None
    assert weak_message["code"] == "source_shell_only"
    assert weak_message["label"] == "Source-shell message"
    assert "visible authored text" in weak_message["explanation"]
