"""Shared text sanitization helpers for untrusted output."""

from __future__ import annotations

import re

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")
OSC_ESCAPE_RE = re.compile(r"\x1B\][^\x07\x1B]*(?:\x07|\x1B\\)")
DISALLOWED_BIDI_CODEPOINTS = {
    0x061C,
    0x200E,
    0x200F,
    0x202A,
    0x202B,
    0x202C,
    0x202D,
    0x202E,
    0x2066,
    0x2067,
    0x2068,
    0x2069,
}


def sanitize_untrusted_text(value: str) -> str:
    """Strip ANSI/OSC escapes and unsafe control chars from untrusted text."""
    no_osc = OSC_ESCAPE_RE.sub("", value)
    no_ansi = ANSI_ESCAPE_RE.sub("", no_osc)
    no_esc = no_ansi.replace("\x1b", "")
    return "".join(
        ch
        for ch in no_esc
        if (ch in "\n\t" or (0x20 <= ord(ch) <= 0x7E) or ord(ch) >= 0xA0)
        and ord(ch) not in DISALLOWED_BIDI_CODEPOINTS
    )
