from pathlib import Path

from src.parse_olm import (
    _extract_email_from_header,
    _extract_folder,
    _extract_header,
    _extract_name_from_header,
    _html_to_text,
    _parse_address_list,
    _parse_email_xml,
)


def test_html_to_text_strips_tags_and_scripts():
    html = "<html><body><script>alert(1)</script><p>Hello<br>World</p></body></html>"
    text = _html_to_text(html)
    assert "Hello" in text
    assert "World" in text
    assert "alert" not in text


def test_extract_folder_from_path():
    path = "Accounts/user/com.microsoft.__Messages/Inbox/msg.xml"
    assert _extract_folder(path) == "Inbox"


def test_extract_folder_preserves_nested_path():
    path = "Accounts/user/com.microsoft.__Messages/Inbox/Finance/Budgets/msg.xml"
    assert _extract_folder(path) == "Inbox/Finance/Budgets"


def test_parse_email_xml_falls_back_to_no_subject():
    xml = b'''<?xml version="1.0"?>
<email xmlns="http://schemas.microsoft.com/outlook/mac/2011">
  <OPFMessageCopySenderAddress>a@example.com</OPFMessageCopySenderAddress>
  <OPFMessageCopySentTime>2023-01-01T00:00:00Z</OPFMessageCopySentTime>
</email>
'''

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.subject == "(no subject)"
    assert parsed.sender_email == "a@example.com"


def test_parse_email_xml_does_not_resolve_external_entities(tmp_path: Path):
    secret_file = tmp_path / "secret.txt"
    secret_file.write_text("super-secret-token", encoding="utf-8")

    xml = f"""<?xml version="1.0"?>
<!DOCTYPE email [
<!ENTITY xxe SYSTEM "file://{secret_file}">
]>
<email xmlns="http://schemas.microsoft.com/outlook/mac/2011">
  <OPFMessageCopySubject>&xxe;</OPFMessageCopySubject>
  <OPFMessageCopySenderAddress>a@example.com</OPFMessageCopySenderAddress>
  <OPFMessageCopySentTime>2023-01-01T00:00:00Z</OPFMessageCopySentTime>
</email>
""".encode("utf-8")

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.subject != "super-secret-token"


# ── Non-namespaced OLM format ───────────────────────────────────


def test_parse_email_xml_non_namespaced_format():
    """Newer Outlook for Mac OLM exports use plain element names without namespace."""
    xml = b"""<?xml version="1.0"?>
<emails xml:space="preserve" elementCount="1"><email xml:space="preserve">
  <OPFMessageCopyMessageID>abc123@example.com</OPFMessageCopyMessageID>
  <OPFMessageCopySubject>Test Subject</OPFMessageCopySubject>
  <OPFMessageCopySenderAddress>sender@example.com</OPFMessageCopySenderAddress>
  <OPFMessageCopySenderName>Sender Name</OPFMessageCopySenderName>
  <OPFMessageCopySentTime>2025-06-25T08:52:47</OPFMessageCopySentTime>
  <OPFMessageCopyBody>Hello, this is the body text.</OPFMessageCopyBody>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.message_id == "abc123@example.com"
    assert parsed.subject == "Test Subject"
    assert parsed.sender_email == "sender@example.com"
    assert parsed.sender_name == "Sender Name"
    assert parsed.date == "2025-06-25T08:52:47"
    assert parsed.body_text == "Hello, this is the body text."


def test_parse_email_xml_non_namespaced_addresses():
    """Non-namespaced OLM format with structured To/CC/From address elements."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Addr test</OPFMessageCopySubject>
  <OPFMessageCopyFromAddresses>
    <emailAddress><OPFContactEmailAddressAddress>from@example.com</OPFContactEmailAddressAddress></emailAddress>
  </OPFMessageCopyFromAddresses>
  <OPFMessageCopyToAddresses>
    <emailAddress><OPFContactEmailAddressAddress>to@example.com</OPFContactEmailAddressAddress></emailAddress>
  </OPFMessageCopyToAddresses>
  <OPFMessageCopyCCAddresses>
    <emailAddress><OPFContactEmailAddressAddress>cc@example.com</OPFContactEmailAddressAddress></emailAddress>
  </OPFMessageCopyCCAddresses>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Sent Items/msg.xml")
    assert parsed is not None
    assert parsed.sender_email == "from@example.com"
    assert parsed.to == ["to@example.com"]
    assert parsed.cc == ["cc@example.com"]


# ── Fallback: OPFMessageCopySource header parsing ──────────────


def test_parse_email_xml_extracts_from_source_headers():
    """When structured XML fields are missing, extract from raw RFC 2822 source."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-06-25T08:52:47</OPFMessageCopySentTime>
  <OPFMessageCopySource>From: "John, Petra" &lt;petra@example.com&gt;
To: "Spicker, Sebastian" &lt;sebastian@example.com&gt;
CC: "Admin Team" &lt;admin@example.com&gt;
Subject: AW: Lucom Dokumentation Zugriff
Date: Wed, 25 Jun 2025 10:52:47 +0200
Message-ID: &lt;470bdd8b@example.com&gt;

Body text here.</OPFMessageCopySource>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.message_id == "470bdd8b@example.com"
    assert parsed.subject == "AW: Lucom Dokumentation Zugriff"
    assert parsed.sender_email == "petra@example.com"
    assert parsed.sender_name == "John, Petra"
    assert parsed.to == ["sebastian@example.com"]
    assert parsed.cc == ["admin@example.com"]


def test_parse_email_xml_preview_fallback_for_body():
    """When no Body or HTMLBody, fall back to OPFMessageCopyPreview."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Preview test</OPFMessageCopySubject>
  <OPFMessageCopyPreview>This is the preview text of the email.</OPFMessageCopyPreview>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.body_text == "This is the preview text of the email."


# ── Header parsing utilities ────────────────────────────────────


def test_extract_header_simple():
    source = "Subject: Hello World\nFrom: test@example.com\n\nBody"
    assert _extract_header(source, "Subject") == "Hello World"


def test_extract_header_with_continuation():
    source = "Subject: This is\n a long subject\nFrom: test@example.com\n\nBody"
    assert _extract_header(source, "Subject") == "This is a long subject"


def test_extract_email_from_header_angle_brackets():
    source = 'From: "Alice Bob" <alice@example.com>\nSubject: Test\n\nBody'
    assert _extract_email_from_header(source, "From") == "alice@example.com"


def test_extract_email_from_header_html_encoded():
    source = 'From: "Alice" &lt;alice@example.com&gt;\nSubject: Test\n\nBody'
    assert _extract_email_from_header(source, "From") == "alice@example.com"


def test_extract_name_from_header_quoted():
    source = 'From: "John, Petra" <petra@example.com>\nSubject: Test\n\nBody'
    assert _extract_name_from_header(source, "From") == "John, Petra"


def test_parse_address_list():
    raw = '"Alice" <alice@a.com>, bob@b.com, "Carol D" <carol@c.com>'
    result = _parse_address_list(raw)
    assert result == ["alice@a.com", "bob@b.com", "carol@c.com"]
