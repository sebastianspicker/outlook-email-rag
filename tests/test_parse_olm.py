from pathlib import Path

from lxml import etree

from src.parse_olm import (
    Email,
    _clean_text,
    _extract_email_from_header,
    _extract_folder,
    _extract_header,
    _extract_html_body,
    _extract_name_from_header,
    _html_to_text,
    _normalize_date,
    _parse_address_element,
    _parse_address_list,
    _parse_email_xml,
    _parse_references,
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
    assert "to@example.com" in parsed.to[0]
    assert "cc@example.com" in parsed.cc[0]


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


# ── Fuzzy address extraction ─────────────────────────────────────


def test_parse_address_element_child_elements():
    """_parse_address_element handles child-element format (older OLM)."""
    xml = b"""<emailAddress>
      <OPFContactEmailAddressName>Alice</OPFContactEmailAddressName>
      <OPFContactEmailAddressAddress>alice@example.com</OPFContactEmailAddressAddress>
    </emailAddress>"""
    el = etree.fromstring(xml)
    name, email = _parse_address_element(el)
    assert name == "Alice"
    assert email == "alice@example.com"


def test_parse_address_element_attributes():
    """_parse_address_element handles attribute format (newer OLM / Sent Items)."""
    xml = b"""<emailAddress xml:space="preserve"
      OPFContactEmailAddressAddress="bob@example.com"
      OPFContactEmailAddressName="Bob Smith"
      OPFContactEmailAddressType="0">
    </emailAddress>"""
    el = etree.fromstring(xml)
    name, email = _parse_address_element(el)
    assert name == "Bob Smith"
    assert email == "bob@example.com"


def test_parse_address_element_fuzzy_child_tags():
    """Fuzzy matching works for non-standard child element names."""
    xml = b"""<recipient>
      <displayName>Charlie</displayName>
      <emailAddress>charlie@example.com</emailAddress>
    </recipient>"""
    el = etree.fromstring(xml)
    name, email = _parse_address_element(el)
    assert name == "Charlie"
    assert email == "charlie@example.com"


def test_extract_addresses_includes_display_name():
    """Addresses include display name in 'Name <email>' format."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Name test</OPFMessageCopySubject>
  <OPFMessageCopyToAddresses>
    <emailAddress>
      <OPFContactEmailAddressName>Alice Wonderland</OPFContactEmailAddressName>
      <OPFContactEmailAddressAddress>alice@example.com</OPFContactEmailAddressAddress>
    </emailAddress>
  </OPFMessageCopyToAddresses>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.to == ["Alice Wonderland <alice@example.com>"]


def test_extract_addresses_fuzzy_tags():
    """Address extraction works even with non-standard child element names."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Fuzzy test</OPFMessageCopySubject>
  <OPFMessageCopyToAddresses>
    <recipient>
      <recipientName>Charlie Brown</recipientName>
      <recipientAddress>charlie@example.com</recipientAddress>
    </recipient>
  </OPFMessageCopyToAddresses>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Sent Items/msg.xml")
    assert parsed is not None
    assert parsed.to == ["Charlie Brown <charlie@example.com>"]


def test_sent_items_sender_from_from_addresses():
    """Sent Items without SenderAddress/SenderName get sender from FromAddresses."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Sent item</OPFMessageCopySubject>
  <OPFMessageCopyFromAddresses>
    <emailAddress>
      <OPFContactEmailAddressName>Sebastian Spicker</OPFContactEmailAddressName>
      <OPFContactEmailAddressAddress>sebastian@example.com</OPFContactEmailAddressAddress>
    </emailAddress>
  </OPFMessageCopyFromAddresses>
  <OPFMessageCopyToAddresses>
    <emailAddress>
      <OPFContactEmailAddressName>Recipient</OPFContactEmailAddressName>
      <OPFContactEmailAddressAddress>recipient@example.com</OPFContactEmailAddressAddress>
    </emailAddress>
  </OPFMessageCopyToAddresses>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Sent Items/msg.xml")
    assert parsed is not None
    assert parsed.sender_name == "Sebastian Spicker"
    assert parsed.sender_email == "sebastian@example.com"
    assert parsed.to == ["Recipient <recipient@example.com>"]


def test_sent_items_attribute_format_addresses():
    """Sent Items with addresses in XML attributes (real-world OLM format)."""
    xml = b"""<?xml version="1.0"?>
<emails xml:space="preserve" elementCount="1"><email xml:space="preserve">
  <OPFMessageCopySentTime>2025-12-05T12:51:03</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Sprachkurs Lautstaerke</OPFMessageCopySubject>
  <OPFMessageCopyCCAddresses xml:space="preserve">
    <emailAddress xml:space="preserve"
      OPFContactEmailAddressAddress="claus@example.com"
      OPFContactEmailAddressName="Schwellenbach, Claus"
      OPFContactEmailAddressType="0"></emailAddress>
  </OPFMessageCopyCCAddresses>
  <OPFMessageCopyDisplayTo xml:space="preserve">Kobler, Sabrina</OPFMessageCopyDisplayTo>
  <OPFMessageCopyFromAddresses xml:space="preserve">
    <emailAddress xml:space="preserve"
      OPFContactEmailAddressAddress="sebastian@example.com"
      OPFContactEmailAddressName="Spicker, Sebastian"
      OPFContactEmailAddressType="0"></emailAddress>
  </OPFMessageCopyFromAddresses>
  <OPFMessageCopyToAddresses xml:space="preserve">
    <emailAddress xml:space="preserve"
      OPFContactEmailAddressAddress="sabrina@example.com"
      OPFContactEmailAddressName="Kobler, Sabrina"
      OPFContactEmailAddressType="0"></emailAddress>
  </OPFMessageCopyToAddresses>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Sent Items/msg.xml")
    assert parsed is not None
    assert parsed.sender_name == "Spicker, Sebastian"
    assert parsed.sender_email == "sebastian@example.com"
    assert parsed.to == ["Kobler, Sabrina <sabrina@example.com>"]
    assert parsed.cc == ["Schwellenbach, Claus <claus@example.com>"]


def test_display_to_fallback_when_no_to_addresses():
    """Falls back to OPFMessageCopyDisplayTo when ToAddresses is missing."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Display To test</OPFMessageCopySubject>
  <OPFMessageCopyDisplayTo>Mueller, Hans</OPFMessageCopyDisplayTo>
  <OPFMessageCopyFromAddresses xml:space="preserve">
    <emailAddress xml:space="preserve"
      OPFContactEmailAddressAddress="sender@example.com"
      OPFContactEmailAddressName="Sender Name"
      OPFContactEmailAddressType="0"></emailAddress>
  </OPFMessageCopyFromAddresses>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Sent Items/msg.xml")
    assert parsed is not None
    assert parsed.to == ["Mueller, Hans"]
    assert parsed.sender_email == "sender@example.com"


def test_display_to_fallback_multiple_recipients():
    """OPFMessageCopyDisplayTo with semicolon-separated names."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Multi recipient</OPFMessageCopySubject>
  <OPFMessageCopyDisplayTo>Alice; Bob; Carol</OPFMessageCopyDisplayTo>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Sent Items/msg.xml")
    assert parsed is not None
    assert parsed.to == ["Alice", "Bob", "Carol"]


# ── Phase 1: email_type and base_subject ────────────────────────


def test_email_type_original():
    email = Email(
        message_id="1", subject="Hello World", sender_name="", sender_email="a@b.com",
        to=[], cc=[], bcc=[], date="", body_text="", body_html="", folder="Inbox",
        has_attachments=False,
    )
    assert email.email_type == "original"
    assert email.base_subject == "Hello World"


def test_email_type_reply_re():
    email = Email(
        message_id="2", subject="RE: Hello World", sender_name="", sender_email="a@b.com",
        to=[], cc=[], bcc=[], date="", body_text="", body_html="", folder="Inbox",
        has_attachments=False,
    )
    assert email.email_type == "reply"
    assert email.base_subject == "Hello World"


def test_email_type_reply_aw():
    email = Email(
        message_id="3", subject="AW: AW: Betreff", sender_name="", sender_email="a@b.com",
        to=[], cc=[], bcc=[], date="", body_text="", body_html="", folder="Inbox",
        has_attachments=False,
    )
    assert email.email_type == "reply"
    assert email.base_subject == "Betreff"


def test_email_type_forward_fw():
    email = Email(
        message_id="4", subject="FW: Some Message", sender_name="", sender_email="a@b.com",
        to=[], cc=[], bcc=[], date="", body_text="", body_html="", folder="Inbox",
        has_attachments=False,
    )
    assert email.email_type == "forward"
    assert email.base_subject == "Some Message"


def test_email_type_forward_wg():
    email = Email(
        message_id="5", subject="WG: Weitergeleitete Nachricht", sender_name="", sender_email="a@b.com",
        to=[], cc=[], bcc=[], date="", body_text="", body_html="", folder="Inbox",
        has_attachments=False,
    )
    assert email.email_type == "forward"
    assert email.base_subject == "Weitergeleitete Nachricht"


def test_base_subject_strips_mixed_prefixes():
    email = Email(
        message_id="6", subject="RE: FW: AW: WG: Deep Thread", sender_name="", sender_email="a@b.com",
        to=[], cc=[], bcc=[], date="", body_text="", body_html="", folder="Inbox",
        has_attachments=False,
    )
    assert email.base_subject == "Deep Thread"


# ── Phase 1: to_dict includes new fields ────────────────────────


def test_to_dict_includes_new_fields():
    email = Email(
        message_id="7", subject="RE: Test", sender_name="Alice", sender_email="a@b.com",
        to=["b@c.com"], cc=[], bcc=[], date="2025-01-01", body_text="hi", body_html="",
        folder="Inbox", has_attachments=False,
        conversation_id="conv-123", in_reply_to="msg-456",
        references=["msg-111", "msg-222"], priority=2, is_read=False,
    )
    d = email.to_dict()
    assert d["conversation_id"] == "conv-123"
    assert d["in_reply_to"] == "msg-456"
    assert d["references"] == ["msg-111", "msg-222"]
    assert d["priority"] == 2
    assert d["is_read"] is False
    assert d["email_type"] == "reply"
    assert d["base_subject"] == "Test"


# ── Phase 1: conversation_id, in_reply_to, references, priority, is_read from XML ──


def test_parse_email_xml_threading_fields():
    """Parse conversation_id, in_reply_to, references, priority, is_read from XML."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>RE: Thread test</OPFMessageCopySubject>
  <OPFMessageCopyExchangeConversationId>AAQkADc1NjJk</OPFMessageCopyExchangeConversationId>
  <OPFMessageCopyInReplyTo>&lt;parent-msg-id@example.com&gt;</OPFMessageCopyInReplyTo>
  <OPFMessageCopyReferences>&lt;root@example.com&gt; &lt;parent@example.com&gt;</OPFMessageCopyReferences>
  <OPFMessageGetPriority>2</OPFMessageGetPriority>
  <OPFMessageGetIsRead>false</OPFMessageGetIsRead>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.conversation_id == "AAQkADc1NjJk"
    assert parsed.in_reply_to == "<parent-msg-id@example.com>"
    assert parsed.references == ["root@example.com", "parent@example.com"]
    assert parsed.priority == 2
    assert parsed.is_read is False
    assert parsed.email_type == "reply"
    assert parsed.base_subject == "Thread test"


def test_parse_email_xml_defaults_for_new_fields():
    """New fields default sensibly when not present in XML."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Simple email</OPFMessageCopySubject>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.conversation_id == ""
    assert parsed.in_reply_to == ""
    assert parsed.references == []
    assert parsed.priority == 0
    assert parsed.is_read is True
    assert parsed.email_type == "original"


# ── Phase 1: In-Reply-To / References from OPFMessageCopySource fallback ──


def test_parse_email_xml_threading_from_source_headers():
    """Extract In-Reply-To and References from raw RFC 2822 source as fallback."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySource>Subject: RE: From source
In-Reply-To: &lt;parent-id@example.com&gt;
References: &lt;root-id@example.com&gt; &lt;parent-id@example.com&gt;

Body text.</OPFMessageCopySource>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.in_reply_to == "parent-id@example.com"
    assert parsed.references == ["root-id@example.com", "parent-id@example.com"]


# ── Phase 1: _parse_references ──────────────────────────────────


def test_parse_references_angle_brackets():
    assert _parse_references("<a@b.com> <c@d.com>") == ["a@b.com", "c@d.com"]


def test_parse_references_empty():
    assert _parse_references("") == []
    assert _parse_references("   ") == []


# ── Phase 1: attachment attribute format ────────────────────────


def test_extract_attachments_attribute_format():
    """Attachments using XML attribute format (same pattern as addresses)."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Attachment test</OPFMessageCopySubject>
  <OPFMessageCopyAttachmentList>
    <messageAttachment OPFAttachmentName="report.pdf"
      OPFAttachmentContentType="application/pdf"
      OPFAttachmentContentFileSize="12345" />
    <messageAttachment OPFAttachmentName="image.png"
      OPFAttachmentContentType="image/png" />
  </OPFMessageCopyAttachmentList>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.has_attachments is True
    assert parsed.attachment_names == ["report.pdf", "image.png"]


def test_extract_attachments_child_element_format():
    """Attachments using child element format still work."""
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Attachment test</OPFMessageCopySubject>
  <OPFMessageCopyAttachmentList>
    <messageAttachment>
      <OPFAttachmentName>doc.docx</OPFAttachmentName>
    </messageAttachment>
  </OPFMessageCopyAttachmentList>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.has_attachments is True
    assert parsed.attachment_names == ["doc.docx"]


# ── Phase 2: BCC parsing ──────────────────────────────────────


def test_parse_bcc_from_xml():
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>BCC test</OPFMessageCopySubject>
  <OPFMessageCopyBCCAddresses>
    <emailAddress OPFContactEmailAddressAddress="secret@example.com"
                  OPFContactEmailAddressName="Secret User" />
  </OPFMessageCopyBCCAddresses>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.bcc == ["Secret User <secret@example.com>"]


def test_parse_bcc_from_source_header_fallback():
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySource>Subject: BCC test
BCC: hidden@example.com

Body text.</OPFMessageCopySource>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.bcc == ["hidden@example.com"]


def test_parse_bcc_defaults_empty():
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>No BCC</OPFMessageCopySubject>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert parsed.bcc == []


def test_to_dict_includes_bcc():
    email = Email(
        message_id="b1", subject="Test", sender_name="", sender_email="a@b.com",
        to=[], cc=[], bcc=["hidden@example.com"], date="", body_text="", body_html="",
        folder="Inbox", has_attachments=False,
    )
    d = email.to_dict()
    assert d["bcc"] == ["hidden@example.com"]


# ── Phase 2: Attachment metadata ──────────────────────────────


def test_attachment_metadata_extracted():
    xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Attachments</OPFMessageCopySubject>
  <OPFMessageCopyAttachmentList>
    <messageAttachment OPFAttachmentName="report.pdf"
      OPFAttachmentContentType="application/pdf"
      OPFAttachmentContentFileSize="12345" />
    <messageAttachment OPFAttachmentName="image.png"
      OPFAttachmentContentType="image/png" />
  </OPFMessageCopyAttachmentList>
</email></emails>"""

    parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
    assert parsed is not None
    assert len(parsed.attachments) == 2
    assert parsed.attachments[0] == {"name": "report.pdf", "mime_type": "application/pdf", "size": 12345}
    assert parsed.attachments[1] == {"name": "image.png", "mime_type": "image/png", "size": 0}


def test_to_dict_includes_attachment_count():
    email = Email(
        message_id="a1", subject="Test", sender_name="", sender_email="a@b.com",
        to=[], cc=[], bcc=[], date="", body_text="", body_html="",
        folder="Inbox", has_attachments=True, attachment_names=["a.pdf", "b.txt"],
    )
    d = email.to_dict()
    assert d["attachment_count"] == 2


# ── Phase 2: email_type uses in_reply_to fallback ─────────────


def test_email_type_reply_via_in_reply_to():
    """Email without RE: prefix but with in_reply_to is classified as reply."""
    email = Email(
        message_id="irt1", subject="Meeting notes", sender_name="", sender_email="a@b.com",
        to=[], cc=[], bcc=[], date="", body_text="", body_html="", folder="Inbox",
        has_attachments=False, in_reply_to="parent-msg-id@example.com",
    )
    assert email.email_type == "reply"


# ── Phase 2: HTML-to-text improvements ────────────────────────


def test_html_to_text_preserves_links():
    html = '<a href="https://example.com">Click here</a>'
    text = _html_to_text(html)
    assert "Click here (https://example.com)" in text


def test_html_to_text_preserves_lists():
    html = "<ul><li>First</li><li>Second</li></ul>"
    text = _html_to_text(html)
    assert "- First" in text
    assert "- Second" in text


def test_html_to_text_preserves_tables():
    html = "<table><tr><td>Name</td><td>Value</td></tr><tr><td>Alice</td><td>100</td></tr></table>"
    text = _html_to_text(html)
    assert "Name" in text
    assert "Alice" in text


def test_html_to_text_preserves_headings():
    html = "<h2>Important Section</h2><p>Content here.</p>"
    text = _html_to_text(html)
    assert "## Important Section" in text


def test_html_to_text_preserves_blockquote():
    html = "<blockquote>Quoted text here</blockquote>"
    text = _html_to_text(html)
    assert "> Quoted text here" in text


# ── _clean_text preserves indentation ─────────────────────────────


def test_clean_text_preserves_leading_indentation():
    text = "if True:\n    do_something()\n    more()"
    result = _clean_text(text)
    assert "    do_something()" in result
    assert "    more()" in result


def test_clean_text_strips_trailing_whitespace():
    text = "hello   \nworld   "
    result = _clean_text(text)
    assert result == "hello\nworld"


def test_clean_text_collapses_blank_lines():
    text = "a\n\n\n\n\nb"
    result = _clean_text(text)
    assert result == "a\n\n\nb"


# ── _normalize_date ───────────────────────────────────────────────


def test_normalize_date_iso_passthrough():
    assert _normalize_date("2025-06-25T08:52:47") == "2025-06-25T08:52:47"
    assert _normalize_date("2025-06-25T08:52:47Z") == "2025-06-25T08:52:47Z"


def test_normalize_date_rfc2822_to_iso():
    result = _normalize_date("Wed, 25 Jun 2025 10:52:47 +0200")
    assert result.startswith("2025-06-25T10:52:47")


def test_normalize_date_empty():
    assert _normalize_date("") == ""
    assert _normalize_date("   ") == "   "


def test_normalize_date_unparseable():
    assert _normalize_date("not a date") == "not a date"


# ── _parse_references mixed format ────────────────────────────────


def test_parse_references_mixed_bracketed_and_bare():
    raw = "<id1@host.com> bare@host.com <id3@host.com>"
    result = _parse_references(raw)
    assert "id1@host.com" in result
    assert "bare@host.com" in result
    assert "id3@host.com" in result
    assert len(result) == 3


def test_parse_references_bare_only():
    raw = "id1@host.com id2@host.com"
    result = _parse_references(raw)
    assert result == ["id1@host.com", "id2@host.com"]


def test_parse_references_no_duplicates():
    raw = "<id1@host.com> id1@host.com"
    result = _parse_references(raw)
    assert result == ["id1@host.com"]


# ── _extract_name_from_header edge cases ──────────────────────────


def test_extract_name_from_header_escaped_quotes():
    source = 'From: "John \\"Johnny\\" Smith" <john@example.com>\n\nBody'
    name = _extract_name_from_header(source, "From")
    assert "Johnny" in name
    assert "John" in name


def test_extract_name_from_header_unquoted():
    source = "From: John Smith <john@example.com>\n\nBody"
    name = _extract_name_from_header(source, "From")
    assert name == "John Smith"


# ── _extract_html_body ────────────────────────────────────────────


def test_extract_html_body_pure_text():
    el = etree.fromstring("<body>Hello world</body>")
    assert _extract_html_body(el) == "Hello world"


def test_extract_html_body_with_child_elements():
    el = etree.fromstring("<body><p>Para 1</p><p>Para 2</p></body>")
    result = _extract_html_body(el)
    assert "<p>" in result
    assert "Para 1" in result
    assert "Para 2" in result


def test_extract_html_body_mixed_content():
    el = etree.fromstring("<body>Before<br/>After</body>")
    result = _extract_html_body(el)
    assert "Before" in result
    assert "<br>" in result or "<br/>" in result
    assert "After" in result


# ── sender email normalization ────────────────────────────────────


def test_sender_email_normalized_lowercase():
    """Sender email from OLM XML should be lowercased."""
    xml = b"""<?xml version="1.0"?>
    <email>
      <OPFMessageCopySubject>Test</OPFMessageCopySubject>
      <OPFMessageCopySentTime>2024-01-01T00:00:00</OPFMessageCopySentTime>
      <OPFMessageCopySenderAddress>John.Smith@Example.COM</OPFMessageCopySenderAddress>
      <OPFMessageCopySenderName>John Smith</OPFMessageCopySenderName>
      <OPFMessageCopyBody>Body</OPFMessageCopyBody>
    </email>"""
    email = _parse_email_xml(xml, "test.xml")
    assert email.sender_email == "john.smith@example.com"
