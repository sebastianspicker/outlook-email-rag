"""HTML to plain-text conversion with semantic structure preservation."""

from __future__ import annotations

import re
from html import unescape

# Pre-compiled regexes for html_to_text() hot path
_RE_STYLE = re.compile(r"<style[^>]*>.*?</style>", re.DOTALL | re.IGNORECASE)
_RE_SCRIPT = re.compile(r"<script[^>]*>.*?</script>", re.DOTALL | re.IGNORECASE)
_RE_HEADINGS = {
    level: (
        re.compile(rf"<h{level}[^>]*>(.*?)</h{level}>", re.DOTALL | re.IGNORECASE),
        "#" * level + " ",
    )
    for level in range(1, 7)
}
_RE_BLOCKQUOTE = re.compile(r"<blockquote[^>]*>(.*?)</blockquote>", re.DOTALL | re.IGNORECASE)
_RE_LI_OPEN = re.compile(r"<li[^>]*>", re.IGNORECASE)
_RE_LI_CLOSE = re.compile(r"</li>", re.IGNORECASE)
_RE_LIST_TAG = re.compile(r"</?[ou]l[^>]*>", re.IGNORECASE)
_RE_TR_OPEN = re.compile(r"<tr[^>]*>", re.IGNORECASE)
_RE_TR_CLOSE = re.compile(r"</tr>", re.IGNORECASE)
_RE_TD_OPEN = re.compile(r"<t[dh][^>]*>", re.IGNORECASE)
_RE_TD_CLOSE = re.compile(r"</t[dh]>", re.IGNORECASE)
_RE_TABLE_TAG = re.compile(r"</?table[^>]*>", re.IGNORECASE)
_RE_LINK = re.compile(r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.DOTALL | re.IGNORECASE)
_RE_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)
_RE_P_CLOSE = re.compile(r"</p>", re.IGNORECASE)
_RE_DIV_CLOSE = re.compile(r"</div>", re.IGNORECASE)
_RE_COMMENT = re.compile(r"<!--[\s\S]*?-->")
_RE_ALL_TAGS = re.compile(r"<[^>]+>")
_RE_WHITESPACE_COLLAPSE = re.compile(r"\s+")


def looks_like_html(text: str) -> bool:
    """Detect whether a string contains HTML markup.

    OLM sometimes puts HTML content in the 'plain text' body field.
    This catches those cases so we can route them through html_to_text().
    """
    if not text:
        return False
    # Quick check for common HTML indicators
    lowered = text[:2000].lower()  # only check the beginning for speed
    html_indicators = (
        "<!doctype", "<html", "<head", "<body", "<div", "<table",
        "<style", "<p>", "<p ", "<br>", "<br/", "<br ", "<span",
    )
    return any(tag in lowered for tag in html_indicators)


def html_to_text(html: str) -> str:
    """Convert HTML to readable plain text, preserving semantic structure."""
    # Remove style and script blocks
    text = _RE_STYLE.sub("", html)
    text = _RE_SCRIPT.sub("", text)

    # Headings → markdown-style
    for _level, (pattern, prefix) in _RE_HEADINGS.items():
        _p = prefix  # bind for lambda closure
        text = pattern.sub(
            lambda m, p=_p: f"\n{p}{m.group(1).strip()}\n",  # type: ignore[misc]
            text,
        )

    # Blockquote
    text = _RE_BLOCKQUOTE.sub(
        lambda m: "\n" + "\n".join(f"> {line}" for line in m.group(1).strip().splitlines()) + "\n",
        text,
    )

    # Lists: <li> → bullet
    text = _RE_LI_OPEN.sub("\n- ", text)
    text = _RE_LI_CLOSE.sub("", text)
    text = _RE_LIST_TAG.sub("\n", text)

    # Tables: <tr> → newline, <td>/<th> → tab-separated
    text = _RE_TR_OPEN.sub("\n", text)
    text = _RE_TR_CLOSE.sub("", text)
    text = _RE_TD_OPEN.sub("\t", text)
    text = _RE_TD_CLOSE.sub("", text)
    text = _RE_TABLE_TAG.sub("\n", text)

    # Links: <a href="url">text</a> → text (url)
    text = _RE_LINK.sub(
        lambda m: f"{m.group(2).strip()} ({m.group(1)})" if m.group(1).strip() else m.group(2).strip(),
        text,
    )

    # <br> and block-level elements → newlines
    text = _RE_BR.sub("\n", text)
    text = _RE_P_CLOSE.sub("\n", text)
    text = _RE_DIV_CLOSE.sub("\n", text)

    # Strip HTML comments (before tag removal — comments contain '>')
    text = _RE_COMMENT.sub("", text)
    # Strip remaining tags
    text = _RE_ALL_TAGS.sub("", text)
    text = unescape(text)
    return clean_text(text)


def clean_text(text: str) -> str:
    """Normalize whitespace and collapse repeated blank lines.

    Preserves leading indentation (important for code blocks and lists)
    while stripping trailing whitespace from each line.
    """
    lines = text.splitlines()
    cleaned: list[str] = []
    blank_count = 0

    for line in lines:
        rstripped = line.rstrip()
        if not rstripped:
            blank_count += 1
            if blank_count <= 2:
                cleaned.append("")
        else:
            blank_count = 0
            cleaned.append(rstripped)

    return "\n".join(cleaned).strip()
