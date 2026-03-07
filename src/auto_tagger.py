"""Keyword-based email auto-tagging.

Zero dependencies — uses simple keyword matching to categorize emails.
Requires ≥2 keyword matches per category to assign a tag.
"""

from __future__ import annotations

import re

_TAG_KEYWORDS: dict[str, set[str]] = {
    "meeting": {"meeting", "agenda", "calendar", "invite", "schedule", "conference", "call"},
    "finance": {"invoice", "payment", "budget", "expense", "billing", "receipt", "financial"},
    "project": {"project", "milestone", "deadline", "deliverable", "roadmap", "sprint"},
    "hr": {"hiring", "onboarding", "resignation", "leave", "vacation", "employee", "salary"},
    "legal": {"contract", "agreement", "compliance", "legal", "terms", "nda", "amendment"},
    "newsletter": {"newsletter", "subscribe", "unsubscribe", "digest", "weekly", "update"},
    "security": {"security", "password", "authentication", "breach", "vulnerability", "phishing"},
}

_MIN_MATCHES = 2


def _tokenize(text: str) -> set[str]:
    """Simple word tokenizer returning a set of lowercase tokens."""
    return set(re.findall(r"[a-zA-Z]+", text.lower()))


def auto_tag(subject: str, body: str) -> list[str]:
    """Automatically categorize an email by subject and body keywords.

    Each category requires ≥2 keyword matches to be assigned.

    Args:
        subject: Email subject line.
        body: Email body text.

    Returns:
        List of tag strings (e.g., ["meeting", "project"]).
    """
    # Combine subject (weighted 2x by duplication) and body tokens
    combined = f"{subject} {subject} {body}"
    tokens = _tokenize(combined)

    tags = []
    for tag, keywords in _TAG_KEYWORDS.items():
        matches = len(tokens & keywords)
        if matches >= _MIN_MATCHES:
            tags.append(tag)

    return sorted(tags)
