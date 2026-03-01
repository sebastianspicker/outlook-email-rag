"""
Parse .olm (Outlook for Mac) archive files.

OLM files are ZIP archives containing XML-formatted email messages.
Structure: Accounts/<email>/com.microsoft.__Messages/<folder>/<message>.xml
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from html import unescape
from typing import Generator

from lxml import etree

logger = logging.getLogger(__name__)
MAX_XML_BYTES = 10_000_000
MAX_XML_FILES = 200_000
MAX_TOTAL_XML_BYTES = 1_000_000_000


@dataclass
class Email:
    """Represents a single parsed email."""

    message_id: str
    subject: str
    sender_name: str
    sender_email: str
    to: list[str]
    cc: list[str]
    date: str  # ISO format string
    body_text: str
    body_html: str
    folder: str
    has_attachments: bool
    attachment_names: list[str] = field(default_factory=list)

    @property
    def uid(self) -> str:
        """Stable unique ID for deduplication."""
        if self.message_id:
            return hashlib.md5(self.message_id.encode(), usedforsecurity=False).hexdigest()
        key = f"{self.subject}|{self.date}|{self.sender_email}"
        return hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()

    @property
    def clean_body(self) -> str:
        """Best available plain text body."""
        if self.body_text and self.body_text.strip():
            return _clean_text(self.body_text)
        if self.body_html:
            return _html_to_text(self.body_html)
        return ""

    def to_dict(self) -> dict:
        return {
            "uid": self.uid,
            "message_id": self.message_id,
            "subject": self.subject,
            "sender_name": self.sender_name,
            "sender_email": self.sender_email,
            "to": self.to,
            "cc": self.cc,
            "date": self.date,
            "body": self.clean_body,
            "folder": self.folder,
            "has_attachments": self.has_attachments,
            "attachment_names": self.attachment_names,
        }


def parse_olm(olm_path: str) -> Generator[Email, None, None]:
    """
    Parse an .olm file and yield Email objects.

    Args:
        olm_path: Path to the .olm file.

    Yields:
        Email objects for each message found.
    """
    if not os.path.exists(olm_path):
        raise FileNotFoundError(f"OLM file not found: {olm_path}")

    with zipfile.ZipFile(olm_path, "r") as zf:
        processed_xml_files = 0
        processed_xml_bytes = 0

        for info in zf.infolist():
            xml_path = info.filename
            if not xml_path.endswith(".xml") or "com.microsoft.__Messages" not in xml_path:
                continue

            if processed_xml_files >= MAX_XML_FILES:
                logger.warning(
                    "Stopping parse due to MAX_XML_FILES limit (%s).",
                    MAX_XML_FILES,
                )
                break

            if processed_xml_bytes + info.file_size > MAX_TOTAL_XML_BYTES:
                logger.warning(
                    "Stopping parse due to MAX_TOTAL_XML_BYTES limit (%s).",
                    MAX_TOTAL_XML_BYTES,
                )
                break

            try:
                if info.file_size > MAX_XML_BYTES:
                    logger.warning(
                        "Skipping oversized XML payload (%s bytes): %s",
                        info.file_size,
                        xml_path,
                    )
                    continue
                processed_xml_files += 1
                processed_xml_bytes += info.file_size
                with zf.open(xml_path) as file_obj:
                    email = _parse_email_xml(file_obj.read(), xml_path)
                    if email:
                        yield email
            except Exception as exc:  # pragma: no cover - defensive branch
                logger.warning("Failed to parse %s: %s", xml_path, exc)


def _parse_email_xml(xml_bytes: bytes, source_path: str) -> Email | None:
    """Parse a single email XML file from the OLM archive."""
    try:
        root = etree.fromstring(xml_bytes, parser=_new_xml_parser())
    except etree.XMLSyntaxError:
        return None

    ns = {"o": "http://schemas.microsoft.com/outlook/mac/2011"}

    def text(xpath: str, default: str = "") -> str:
        element = root.find(xpath, ns)
        return (element.text or default) if element is not None else default

    folder = _extract_folder(source_path)

    sender_el = root.find(".//o:OPFMessageCopySenderAddress", ns)
    sender_name_el = root.find(".//o:OPFMessageCopySenderName", ns)

    to_addresses = _extract_addresses(root, ns, "OPFMessageCopyToAddresses")
    cc_addresses = _extract_addresses(root, ns, "OPFMessageCopyCCAddresses")

    body_text = text(".//o:OPFMessageCopyBody")
    body_html = text(".//o:OPFMessageCopyHTMLBody")

    attachment_els = root.findall(".//o:OPFMessageCopyAttachmentList/o:messageAttachment", ns)
    attachment_names: list[str] = []
    for attachment in attachment_els:
        name_el = attachment.find("o:OPFAttachmentName", ns)
        if name_el is not None and name_el.text:
            attachment_names.append(name_el.text)

    return Email(
        message_id=text(".//o:OPFMessageCopyMessageID"),
        subject=text(".//o:OPFMessageCopySubject", "(no subject)"),
        sender_name=sender_name_el.text if sender_name_el is not None and sender_name_el.text else "",
        sender_email=sender_el.text if sender_el is not None and sender_el.text else "",
        to=to_addresses,
        cc=cc_addresses,
        date=text(".//o:OPFMessageCopySentTime"),
        body_text=body_text,
        body_html=body_html,
        folder=folder,
        has_attachments=bool(attachment_names),
        attachment_names=attachment_names,
    )


def _extract_addresses(root, ns: dict, tag_name: str) -> list[str]:
    """Extract email addresses from an address list element."""
    container = root.find(f".//o:{tag_name}", ns)
    if container is None:
        return []

    addresses: list[str] = []
    for addr_el in container.findall(".//o:emailAddress", ns):
        addr = addr_el.find("o:OPFContactEmailAddressAddress", ns)
        if addr is not None and addr.text:
            addresses.append(addr.text)
    return addresses


def _extract_folder(path: str) -> str:
    """Extract the folder name from the OLM internal path."""
    # Path looks like: Accounts/.../com.microsoft.__Messages/Inbox/msg.xml
    parts = path.split("/")
    msg_idx = None
    for i, part in enumerate(parts):
        if "com.microsoft.__Messages" in part:
            msg_idx = i
            break
    if msg_idx is not None and msg_idx + 1 < len(parts):
        return parts[msg_idx + 1]
    return "Unknown"


def _html_to_text(html: str) -> str:
    """Simple HTML to text conversion."""
    # Remove style and script blocks
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # Convert <br> and <p> to newlines
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</div>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return _clean_text(text)


def _clean_text(text: str) -> str:
    """Normalize whitespace and collapse repeated blank lines."""
    lines = text.splitlines()
    cleaned: list[str] = []
    blank_count = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            blank_count += 1
            if blank_count <= 2:
                cleaned.append("")
        else:
            blank_count = 0
            cleaned.append(stripped)

    return "\n".join(cleaned).strip()


def _new_xml_parser() -> etree.XMLParser:
    """Create a parser with safe defaults for untrusted XML."""
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
