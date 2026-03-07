"""Tests for auto-tagging."""

from src.auto_tagger import auto_tag


def test_meeting_tag():
    tags = auto_tag("Team Meeting Agenda", "Please review the meeting schedule for next week.")
    assert "meeting" in tags


def test_finance_tag():
    tags = auto_tag("Invoice #1234", "Please process this payment for the budget allocation.")
    assert "finance" in tags


def test_project_tag():
    tags = auto_tag("Project Update", "The milestone deadline has been moved to next sprint.")
    assert "project" in tags


def test_no_tag_insufficient_matches():
    tags = auto_tag("Hello", "Just a regular email with no special keywords.")
    assert tags == []


def test_multiple_tags():
    tags = auto_tag(
        "Project Meeting Schedule",
        "The project milestone meeting is scheduled for Friday. Please update the agenda.",
    )
    assert "meeting" in tags
    assert "project" in tags


def test_empty_input():
    tags = auto_tag("", "")
    assert tags == []


def test_security_tag():
    tags = auto_tag("Security Alert", "Potential phishing vulnerability detected in authentication system.")
    assert "security" in tags


def test_tags_are_sorted():
    tags = auto_tag(
        "Project Meeting",
        "The project milestone meeting is scheduled. Please review the meeting agenda.",
    )
    assert tags == sorted(tags)


def test_hr_tag():
    tags = auto_tag("New Employee Onboarding", "Welcome to the team. Your salary details and leave policy are attached.")
    assert "hr" in tags


def test_legal_tag():
    tags = auto_tag("Contract Review", "Please review the NDA agreement and compliance terms.")
    assert "legal" in tags
