"""Extended tests for src/parse_olm.py — targeting uncovered lines."""

from __future__ import annotations

import base64
import zipfile
from pathlib import Path

import pytest
from lxml import etree

from src.parse_olm import (
    _NS_OUTLOOK,
    Email,
    _detect_namespace,
    _extract_attachment_contents,
    _extract_attachment_field,
    _extract_html_body,
    _find,
    _find_text,
    _parse_email_xml,
    parse_olm,
)

# ── Email.uid fallback (lines 98-99) ─────────────────────────


class TestEmailUidFallback:
    def test_uid_without_message_id_uses_subject_date_sender(self):
        """When message_id is empty, uid falls back to subject|date|sender."""
        email = Email(
            message_id="",
            subject="Test",
            sender_name="Alice",
            sender_email="alice@example.com",
            to=[],
            cc=[],
            bcc=[],
            date="2024-01-01",
            body_text="",
            body_html="",
            folder="Inbox",
            has_attachments=False,
        )
        uid = email.uid
        assert uid  # non-empty
        assert len(uid) == 32  # md5 hex

    def test_uid_with_message_id(self):
        email = Email(
            message_id="<abc@example.com>",
            subject="Test",
            sender_name="",
            sender_email="",
            to=[],
            cc=[],
            bcc=[],
            date="",
            body_text="",
            body_html="",
            folder="Inbox",
            has_attachments=False,
        )
        uid = email.uid
        assert uid
        assert len(uid) == 32

    def test_uid_fallback_deterministic(self):
        """Same subject/date/sender always produces same uid."""
        kwargs = {
            "message_id": "",
            "subject": "Test",
            "sender_name": "",
            "sender_email": "a@b.com",
            "to": [],
            "cc": [],
            "bcc": [],
            "date": "2024-01-01",
            "body_text": "",
            "body_html": "",
            "folder": "Inbox",
            "has_attachments": False,
        }
        e1 = Email(**kwargs)
        e2 = Email(**kwargs)
        assert e1.uid == e2.uid


# ── Email.clean_body with HTML in body_text (line 137) ────────


class TestCleanBodyHtmlInBodyText:
    def test_clean_body_html_in_body_text_field(self):
        """When body_text contains HTML, clean_body strips HTML tags."""
        email = Email(
            message_id="<m@test>",
            subject="Test",
            sender_name="",
            sender_email="",
            to=[],
            cc=[],
            bcc=[],
            date="",
            body_text="<html><body><p>Hello</p></body></html>",
            body_html="",
            folder="Inbox",
            has_attachments=False,
        )
        result = email.clean_body
        assert "Hello" in result
        assert "<html>" not in result
        assert "<p>" not in result


# ── Email.email_type implicit reply (line 116-117) ────────────


class TestEmailTypeImplicitReply:
    def test_email_type_implicit_reply_via_in_reply_to(self):
        """No RE: prefix but has in_reply_to -> classified as reply."""
        email = Email(
            message_id="<m@test>",
            subject="Project Discussion",
            sender_name="",
            sender_email="a@b.com",
            to=[],
            cc=[],
            bcc=[],
            date="",
            body_text="",
            body_html="",
            folder="Inbox",
            has_attachments=False,
            in_reply_to="<parent@test>",
        )
        assert email.email_type == "reply"


# ── parse_olm: oversized XML skip (line 209/227-232) ─────────


class TestParseOlmOversizedXml:
    def test_skip_oversized_xml_file(self, monkeypatch, tmp_path: Path):
        """Files exceeding MAX_XML_BYTES are skipped."""
        import src.parse_olm as mod

        archive = tmp_path / "oversized.olm"
        xml_content = (
            b'<?xml version="1.0"?>\n'
            b'<email xmlns="http://schemas.microsoft.com/outlook/mac/2011">\n'
            b"  <OPFMessageCopySenderAddress>a@example.com"
            b"</OPFMessageCopySenderAddress>\n"
            b"  <OPFMessageCopySentTime>2023-01-01T00:00:00Z"
            b"</OPFMessageCopySentTime>\n"
            b"</email>"
        )
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(
                "Accounts/a/com.microsoft.__Messages/Inbox/big.xml",
                xml_content,
            )

        # Set limit to less than the XML size
        monkeypatch.setattr(mod, "MAX_XML_BYTES", 10)
        parsed = list(mod.parse_olm(str(archive)))
        assert len(parsed) == 0

    def test_max_total_xml_bytes_mid_stream_break(self, monkeypatch, tmp_path: Path):
        """Stop parsing when cumulative bytes exceed MAX_TOTAL_XML_BYTES (mid-read)."""
        import src.parse_olm as mod

        archive = tmp_path / "total_limit.olm"
        xml_template = (
            b'<?xml version="1.0"?>\n'
            b'<email xmlns="http://schemas.microsoft.com/outlook/mac/2011">\n'
            b"  <OPFMessageCopySenderAddress>a@example.com"
            b"</OPFMessageCopySenderAddress>\n"
            b"  <OPFMessageCopySentTime>2023-01-01T00:00:00Z"
            b"</OPFMessageCopySentTime>\n"
            b"</email>"
        )
        with zipfile.ZipFile(archive, "w") as zf:
            for i in range(5):
                zf.writestr(
                    f"Accounts/a/com.microsoft.__Messages/Inbox/msg-{i}.xml",
                    xml_template,
                )

        # Allow individual files but limit total to roughly 1 file's worth
        monkeypatch.setattr(mod, "MAX_XML_BYTES", 50_000_000)
        monkeypatch.setattr(mod, "MAX_TOTAL_XML_BYTES", len(xml_template) + 10)
        parsed = list(mod.parse_olm(str(archive)))
        # Should parse just 1 file then stop on the second
        assert len(parsed) == 1


# ── parse_olm: file not found (line 197) ─────────────────────


class TestParseOlmFileNotFound:
    def test_raises_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            list(parse_olm("/nonexistent/archive.olm"))


# ── _detect_namespace (line 263) ──────────────────────────────


class TestDetectNamespace:
    def test_detect_namespace_via_subject_tag(self):
        """Detect namespace when Subject element is present but SentTime is not."""
        xml = b"""<?xml version="1.0"?>
<email xmlns="http://schemas.microsoft.com/outlook/mac/2011">
  <OPFMessageCopySubject>Test</OPFMessageCopySubject>
</email>"""
        root = etree.fromstring(xml)
        ns = _detect_namespace(root)
        assert ns == _NS_OUTLOOK

    def test_detect_no_namespace(self):
        xml = b"""<?xml version="1.0"?>
<email>
  <OPFMessageCopySubject>Test</OPFMessageCopySubject>
</email>"""
        root = etree.fromstring(xml)
        ns = _detect_namespace(root)
        assert ns == {}


# ── _extract_addresses: name-only entries (lines 511-512) ─────


class TestExtractAddressesNameOnly:
    def test_address_with_name_only_no_email(self):
        """When an address element has only a name, return name alone."""
        xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Name only test</OPFMessageCopySubject>
  <OPFMessageCopyToAddresses>
    <emailAddress>
      <OPFContactEmailAddressName>Just A Name</OPFContactEmailAddressName>
    </emailAddress>
  </OPFMessageCopyToAddresses>
</email></emails>"""
        parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
        assert parsed is not None
        assert "Just A Name" in parsed.to


# ── _extract_attachment_field with namespace (line 560) ───────


class TestExtractAttachmentFieldNamespaced:
    def test_attachment_field_namespaced_child(self):
        xml = b"""<messageAttachment xmlns="http://schemas.microsoft.com/outlook/mac/2011">
  <OPFAttachmentName>report.pdf</OPFAttachmentName>
  <OPFAttachmentContentType>application/pdf</OPFAttachmentContentType>
  <OPFAttachmentContentFileSize>1024</OPFAttachmentContentFileSize>
</messageAttachment>"""
        el = etree.fromstring(xml)
        ns = _NS_OUTLOOK
        name = _extract_attachment_field(el, ns, "OPFAttachmentName", attr_hint="name")
        assert name == "report.pdf"

    def test_attachment_field_attribute_fallback(self):
        """When child element is missing, fall back to attribute matching."""
        xml = b'<messageAttachment OPFAttachmentName="doc.docx"></messageAttachment>'
        el = etree.fromstring(xml)
        name = _extract_attachment_field(el, {}, "OPFAttachmentName", attr_hint="name")
        assert name == "doc.docx"


# ── _extract_attachments ──────────────────────────────────────


class TestExtractAttachments:
    def test_extracts_attachment_metadata(self):
        xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Attach test</OPFMessageCopySubject>
  <OPFMessageCopyAttachmentList>
    <messageAttachment>
      <OPFAttachmentName>file.pdf</OPFAttachmentName>
      <OPFAttachmentContentType>application/pdf</OPFAttachmentContentType>
      <OPFAttachmentContentFileSize>2048</OPFAttachmentContentFileSize>
    </messageAttachment>
  </OPFMessageCopyAttachmentList>
</email></emails>"""
        parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
        assert parsed is not None
        assert parsed.has_attachments is True
        assert parsed.attachment_names == ["file.pdf"]
        assert len(parsed.attachments) == 1
        assert parsed.attachments[0]["name"] == "file.pdf"
        assert parsed.attachments[0]["size"] == 2048

    def test_extracts_namespaced_attachment(self):
        xml = b"""<?xml version="1.0"?>
<email xmlns="http://schemas.microsoft.com/outlook/mac/2011">
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>NS Attach</OPFMessageCopySubject>
  <OPFMessageCopyAttachmentList>
    <messageAttachment>
      <OPFAttachmentName>image.png</OPFAttachmentName>
      <OPFAttachmentContentType>image/png</OPFAttachmentContentType>
      <OPFAttachmentContentID>cid123</OPFAttachmentContentID>
    </messageAttachment>
  </OPFMessageCopyAttachmentList>
</email>"""
        parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
        assert parsed is not None
        assert parsed.has_attachments is True
        assert parsed.attachments[0]["content_id"] == "cid123"
        assert parsed.attachments[0]["is_inline"] is True


# ── _extract_attachment_contents (lines 592-642) ─────────────


class TestExtractAttachmentContents:
    def test_extract_inline_base64_attachment(self, tmp_path: Path):
        """Extract attachment content from inline base64 data."""
        content = b"Hello World!"
        b64 = base64.b64encode(content).decode()
        xml_content = f"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Attach content</OPFMessageCopySubject>
  <OPFMessageCopyAttachmentList>
    <messageAttachment>
      <OPFAttachmentName>hello.txt</OPFAttachmentName>
      <OPFAttachmentContentData>{b64}</OPFAttachmentContentData>
    </messageAttachment>
  </OPFMessageCopyAttachmentList>
</email></emails>""".encode()

        archive = tmp_path / "attach.olm"
        xml_path = "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(xml_path, xml_content)

        with zipfile.ZipFile(archive, "r") as zf:
            result = _extract_attachment_contents(xml_content, xml_path, zf)

        assert len(result) == 1
        assert result[0][0] == "hello.txt"
        assert result[0][1] == content

    def test_extract_attachment_from_zip_path(self, tmp_path: Path):
        """Extract attachment content via relative path in ZIP."""
        xml_content = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Attach file</OPFMessageCopySubject>
  <OPFMessageCopyAttachmentList>
    <messageAttachment>
      <OPFAttachmentName>data.bin</OPFAttachmentName>
      <OPFAttachmentURL>data.bin</OPFAttachmentURL>
    </messageAttachment>
  </OPFMessageCopyAttachmentList>
</email></emails>"""
        archive = tmp_path / "attach_url.olm"
        xml_path = "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml"
        att_path = "Accounts/a/com.microsoft.__Messages/Inbox/data.bin"

        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(xml_path, xml_content)
            zf.writestr(att_path, b"binary data here")

        with zipfile.ZipFile(archive, "r") as zf:
            result = _extract_attachment_contents(xml_content, xml_path, zf)

        assert len(result) == 1
        assert result[0][0] == "data.bin"
        assert result[0][1] == b"binary data here"

    def test_extract_attachment_skips_unnamed(self, tmp_path: Path):
        """Attachments without a name are skipped."""
        xml_content = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopyAttachmentList>
    <messageAttachment>
      <OPFAttachmentContentData>dGVzdA==</OPFAttachmentContentData>
    </messageAttachment>
  </OPFMessageCopyAttachmentList>
</email></emails>"""
        archive = tmp_path / "noname.olm"
        xml_path = "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(xml_path, xml_content)
        with zipfile.ZipFile(archive, "r") as zf:
            result = _extract_attachment_contents(xml_content, xml_path, zf)
        assert result == []

    def test_extract_attachment_invalid_xml(self, tmp_path: Path):
        """Malformed XML returns empty list."""
        archive = tmp_path / "badxml.olm"
        xml_path = "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(xml_path, b"<email")
        with zipfile.ZipFile(archive, "r") as zf:
            result = _extract_attachment_contents(b"<email", xml_path, zf)
        assert result == []

    def test_extract_attachment_invalid_base64(self, tmp_path: Path):
        """Invalid base64 is handled gracefully; fallback to URL path."""
        xml_content = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopyAttachmentList>
    <messageAttachment>
      <OPFAttachmentName>bad.txt</OPFAttachmentName>
      <OPFAttachmentContentData>!!!not_base64!!!</OPFAttachmentContentData>
    </messageAttachment>
  </OPFMessageCopyAttachmentList>
</email></emails>"""
        archive = tmp_path / "badb64.olm"
        xml_path = "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(xml_path, xml_content)
        with zipfile.ZipFile(archive, "r") as zf:
            result = _extract_attachment_contents(xml_content, xml_path, zf)
        # Invalid base64 should be logged but not crash; no URL fallback so empty
        assert result == []

    def test_parse_olm_with_extract_attachments(self, tmp_path: Path):
        """parse_olm with extract_attachments=True populates attachment_contents."""
        content = b"attachment data"
        b64 = base64.b64encode(content).decode()
        xml_content = f"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>With attach</OPFMessageCopySubject>
  <OPFMessageCopyAttachmentList>
    <messageAttachment>
      <OPFAttachmentName>file.dat</OPFAttachmentName>
      <OPFAttachmentContentData>{b64}</OPFAttachmentContentData>
    </messageAttachment>
  </OPFMessageCopyAttachmentList>
</email></emails>""".encode()
        archive = tmp_path / "with_attach.olm"
        xml_path = "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr(xml_path, xml_content)

        emails = list(parse_olm(str(archive), extract_attachments=True))
        assert len(emails) == 1
        assert len(emails[0].attachment_contents) == 1
        assert emails[0].attachment_contents[0][0] == "file.dat"
        assert emails[0].attachment_contents[0][1] == content


# ── _extract_exchange_smart_links (lines 699-713) ────────────


class TestExtractExchangeSmartLinks:
    def test_extracts_smart_links_with_attributes_and_children(self):
        xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Links test</OPFMessageCopySubject>
  <OPFMessageGetExchangeExtractedSmartLinks>
    <link href="https://example.com">https://example.com/page</link>
    <link>
      <url>https://example.com/sub</url>
      <title>Example</title>
    </link>
  </OPFMessageGetExchangeExtractedSmartLinks>
</email></emails>"""
        parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
        assert parsed is not None
        links = parsed.exchange_extracted_links
        assert len(links) == 2
        # First link has attribute + text
        assert links[0]["href"] == "https://example.com"
        assert links[0]["url"] == "https://example.com/page"
        # Second link has child elements
        assert links[1]["url"] == "https://example.com/sub"
        assert links[1]["title"] == "Example"


# ── _extract_exchange_meetings (lines 735-746) ───────────────


class TestExtractExchangeMeetings:
    def test_extracts_meetings_with_attributes_and_children(self):
        xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Meeting test</OPFMessageCopySubject>
  <OPFMessageGetExchangeExtractedMeetings>
    <meeting status="accepted">
      <subject>Standup</subject>
      <location>Room 42</location>
    </meeting>
  </OPFMessageGetExchangeExtractedMeetings>
</email></emails>"""
        parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
        assert parsed is not None
        meetings = parsed.exchange_extracted_meetings
        assert len(meetings) == 1
        assert meetings[0]["status"] == "accepted"
        assert meetings[0]["subject"] == "Standup"
        assert meetings[0]["location"] == "Room 42"


# ── _extract_categories ──────────────────────────────────────


class TestExtractCategories:
    def test_extracts_categories(self):
        xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Cat test</OPFMessageCopySubject>
  <OPFMessageCopyCategoryList>
    <category>Important</category>
    <category>Work</category>
    <category>   </category>
  </OPFMessageCopyCategoryList>
</email></emails>"""
        parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
        assert parsed is not None
        assert parsed.categories == ["Important", "Work"]


# ── _extract_meeting_data ────────────────────────────────────


class TestExtractMeetingData:
    def test_extracts_meeting_data(self):
        xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Meeting data</OPFMessageCopySubject>
  <OPFMessageCopyIsCalendarMessage>true</OPFMessageCopyIsCalendarMessage>
  <OPFMessageCopyMeetingData>
    <OPFMeetingStartDate>2025-01-15T10:00:00</OPFMeetingStartDate>
    <OPFMeetingEndDate>2025-01-15T11:00:00</OPFMeetingEndDate>
    <OPFMeetingLocation>Room A</OPFMeetingLocation>
  </OPFMessageCopyMeetingData>
</email></emails>"""
        parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
        assert parsed is not None
        assert parsed.is_calendar_message is True
        assert parsed.meeting_data["OPFMeetingStartDate"] == "2025-01-15T10:00:00"
        assert parsed.meeting_data["OPFMeetingLocation"] == "Room A"


# ── _extract_html_body with child elements (line 759-768) ────


class TestExtractHtmlBody:
    def test_html_body_with_child_elements(self):
        xml = b"""<OPFMessageCopyHTMLBody>
  Before <p>Hello</p> After <br/> End
</OPFMessageCopyHTMLBody>"""
        el = etree.fromstring(xml)
        result = _extract_html_body(el)
        assert "<p>" in result
        assert "Hello" in result
        assert "Before" in result

    def test_html_body_pure_text(self):
        xml = b"<OPFMessageCopyHTMLBody>Just plain text</OPFMessageCopyHTMLBody>"
        el = etree.fromstring(xml)
        result = _extract_html_body(el)
        assert result == "Just plain text"


# ── Source fallback: BCC, In-Reply-To, References (lines 374-383) ──


class TestSourceFallbackHeaders:
    def test_source_fallback_bcc_in_reply_to_references(self):
        xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-06-25T08:52:47</OPFMessageCopySentTime>
  <OPFMessageCopySource>From: sender@example.com
To: to@example.com
BCC: bcc@example.com
In-Reply-To: &lt;parent@example.com&gt;
References: &lt;ref1@example.com&gt; &lt;ref2@example.com&gt;
Subject: Test

Body text here.</OPFMessageCopySource>
</email></emails>"""
        parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
        assert parsed is not None
        assert parsed.bcc == ["bcc@example.com"]
        assert parsed.in_reply_to == "parent@example.com"
        assert "ref1@example.com" in parsed.references
        assert "ref2@example.com" in parsed.references


# ── Body extraction from source (line 386-387) ───────────────


class TestBodyFromSource:
    def test_body_extracted_from_raw_source(self):
        xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySource>From: sender@example.com
Subject: Test
Date: Wed, 01 Jan 2025 00:00:00 +0000

This is the extracted body from the raw source.</OPFMessageCopySource>
</email></emails>"""
        parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
        assert parsed is not None
        # Body should be extracted from raw source
        assert "extracted body" in parsed.body_text or "extracted body" in parsed.body_html


# ── _extract_exchange_list ───────────────────────────────────


class TestExtractExchangeList:
    def test_extracts_exchange_emails_and_contacts(self):
        xml = b"""<?xml version="1.0"?>
<emails><email>
  <OPFMessageCopySentTime>2025-01-01T00:00:00</OPFMessageCopySentTime>
  <OPFMessageCopySubject>Exchange list</OPFMessageCopySubject>
  <OPFMessageGetExchangeExtractedEmails>
    <email>alice@example.com</email>
    <email>bob@example.com</email>
  </OPFMessageGetExchangeExtractedEmails>
  <OPFMessageGetExchangeExtractedContacts>
    <contact>Alice Smith</contact>
  </OPFMessageGetExchangeExtractedContacts>
</email></emails>"""
        parsed = _parse_email_xml(xml, "Accounts/a/com.microsoft.__Messages/Inbox/msg.xml")
        assert parsed is not None
        assert parsed.exchange_extracted_emails == ["alice@example.com", "bob@example.com"]
        assert parsed.exchange_extracted_contacts == ["Alice Smith"]


# ── _find and _find_text helpers ──────────────────────────────


class TestFindHelpers:
    def test_find_with_namespace(self):
        xml = b"""<?xml version="1.0"?>
<email xmlns="http://schemas.microsoft.com/outlook/mac/2011">
  <OPFMessageCopySubject>Test</OPFMessageCopySubject>
</email>"""
        root = etree.fromstring(xml)
        el = _find(root, "OPFMessageCopySubject", _NS_OUTLOOK)
        assert el is not None
        assert el.text == "Test"

    def test_find_without_namespace(self):
        xml = b"<email><OPFMessageCopySubject>Test</OPFMessageCopySubject></email>"
        root = etree.fromstring(xml)
        el = _find(root, "OPFMessageCopySubject", {})
        assert el is not None

    def test_find_text_default(self):
        xml = b"<email></email>"
        root = etree.fromstring(xml)
        result = _find_text(root, "NonExistent", {}, default="fallback")
        assert result == "fallback"
