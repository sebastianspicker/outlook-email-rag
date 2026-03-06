"""
Parse .olm (Outlook for Mac) archive files.

OLM files are ZIP archives containing XML-formatted email messages.
Structure: Accounts/<email>/com.microsoft.__Messages/<folder>/<message>.xml

Supports two OLM variants:
- Namespaced XML (older Outlook for Mac, namespace: http://schemas.microsoft.com/outlook/mac/2011)
- Non-namespaced XML (newer Outlook for Mac, plain element names)

When structured XML elements are missing (e.g. Inbox emails with only
OPFMessageCopySource), fields are extracted from the raw RFC 2822 headers.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import zipfile
from dataclasses import dataclass, field
from html import unescape
from typing import IO, Generator

from lxml import etree

logger = logging.getLogger(__name__)
MAX_XML_BYTES = 10_000_000
MAX_XML_FILES = 200_000
MAX_TOTAL_XML_BYTES = 1_000_000_000

_NS_OUTLOOK = {"o": "http://schemas.microsoft.com/outlook/mac/2011"}


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
            normalized_path = xml_path.lower()
            if not normalized_path.endswith(".xml") or "com.microsoft.__messages" not in normalized_path:
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
                with zf.open(xml_path) as file_obj:
                    xml_bytes = _read_limited_bytes(file_obj, byte_limit=MAX_XML_BYTES)
                    if processed_xml_bytes + len(xml_bytes) > MAX_TOTAL_XML_BYTES:
                        logger.warning(
                            "Stopping parse due to MAX_TOTAL_XML_BYTES limit (%s).",
                            MAX_TOTAL_XML_BYTES,
                        )
                        break
                    processed_xml_bytes += len(xml_bytes)
                    email = _parse_email_xml(xml_bytes, xml_path)
                    if email:
                        yield email
            except Exception as exc:  # pragma: no cover - defensive branch
                logger.warning("Failed to parse %s: %s", xml_path, exc)


# ── XML Element Lookup ────────────────────────────────────────


def _detect_namespace(root: etree._Element) -> dict[str, str]:
    """Detect whether the OLM XML uses the Outlook namespace or plain tags."""
    # Try namespaced first (older OLM format)
    if root.find(".//o:OPFMessageCopySentTime", _NS_OUTLOOK) is not None:
        return _NS_OUTLOOK
    if root.find(".//o:OPFMessageCopySubject", _NS_OUTLOOK) is not None:
        return _NS_OUTLOOK
    # No namespace (newer OLM format)
    return {}


def _find(root: etree._Element, tag: str, ns: dict[str, str]) -> etree._Element | None:
    """Find an element by tag name, using the detected namespace."""
    if ns:
        return root.find(f".//o:{tag}", ns)
    return root.find(f".//{tag}")


def _find_text(root: etree._Element, tag: str, ns: dict[str, str], default: str = "") -> str:
    """Find an element and return its text, or the default."""
    el = _find(root, tag, ns)
    return (el.text or default) if el is not None else default


# ── Email XML Parsing ─────────────────────────────────────────


def _parse_email_xml(xml_bytes: bytes, source_path: str) -> Email | None:
    """Parse a single email XML file from the OLM archive."""
    try:
        root = etree.fromstring(xml_bytes, parser=_new_xml_parser())
    except etree.XMLSyntaxError:
        return None

    ns = _detect_namespace(root)
    folder = _extract_folder(source_path)

    # ── Direct XML fields ──────────────────────────────────
    message_id = _find_text(root, "OPFMessageCopyMessageID", ns)
    subject = _find_text(root, "OPFMessageCopySubject", ns)
    date = _find_text(root, "OPFMessageCopySentTime", ns)
    body_text = _find_text(root, "OPFMessageCopyBody", ns)
    body_html = _find_text(root, "OPFMessageCopyHTMLBody", ns)

    sender_el = _find(root, "OPFMessageCopySenderAddress", ns)
    sender_name_el = _find(root, "OPFMessageCopySenderName", ns)
    sender_email = sender_el.text if sender_el is not None and sender_el.text else ""
    sender_name = sender_name_el.text if sender_name_el is not None and sender_name_el.text else ""

    to_addresses = _extract_addresses(root, ns, "OPFMessageCopyToAddresses")
    cc_addresses = _extract_addresses(root, ns, "OPFMessageCopyCCAddresses")

    # Also try OPFMessageCopyFromAddresses (non-namespaced format)
    if not sender_email:
        from_addresses = _extract_addresses(root, ns, "OPFMessageCopyFromAddresses")
        if from_addresses:
            sender_email = from_addresses[0]

    # ── Fallback: parse OPFMessageCopySource headers ───────
    raw_source = _find_text(root, "OPFMessageCopySource", ns)
    if raw_source:
        if not message_id:
            message_id = _extract_header(raw_source, "Message-ID").strip("<>")
        if not subject:
            subject = _extract_header(raw_source, "Subject")
        if not sender_email:
            sender_email = _extract_email_from_header(raw_source, "From")
        if not sender_name:
            sender_name = _extract_name_from_header(raw_source, "From")
        if not date:
            date = _extract_header(raw_source, "Date")
        if not to_addresses:
            to_raw = _extract_header(raw_source, "To")
            if to_raw:
                to_addresses = _parse_address_list(to_raw)
        if not cc_addresses:
            cc_raw = _extract_header(raw_source, "CC")
            if cc_raw:
                cc_addresses = _parse_address_list(cc_raw)

    # ── Fallback: OPFMessageCopyPreview for body ───────────
    if not body_text and not body_html:
        preview = _find_text(root, "OPFMessageCopyPreview", ns)
        if preview:
            body_text = preview

    # ── Attachments ────────────────────────────────────────
    attachment_names = _extract_attachments(root, ns)

    # Default subject
    if not subject:
        subject = "(no subject)"

    return Email(
        message_id=message_id,
        subject=subject,
        sender_name=sender_name,
        sender_email=sender_email,
        to=to_addresses,
        cc=cc_addresses,
        date=date,
        body_text=body_text,
        body_html=body_html,
        folder=folder,
        has_attachments=bool(attachment_names),
        attachment_names=attachment_names,
    )


# ── Address Extraction ────────────────────────────────────────


def _extract_addresses(root: etree._Element, ns: dict[str, str], tag_name: str) -> list[str]:
    """Extract email addresses from an address list element."""
    container = _find(root, tag_name, ns)
    if container is None:
        return []

    addresses: list[str] = []
    # Try namespaced child elements
    if ns:
        for addr_el in container.findall(".//o:emailAddress", ns):
            addr = addr_el.find("o:OPFContactEmailAddressAddress", ns)
            if addr is not None and addr.text:
                addresses.append(addr.text)
    else:
        for addr_el in container.findall(".//emailAddress"):
            addr = addr_el.find("OPFContactEmailAddressAddress")
            if addr is not None and addr.text:
                addresses.append(addr.text)

    return addresses


def _extract_attachments(root: etree._Element, ns: dict[str, str]) -> list[str]:
    """Extract attachment filenames from the email XML."""
    names: list[str] = []
    if ns:
        attachment_els = root.findall(".//o:OPFMessageCopyAttachmentList/o:messageAttachment", ns)
        for att in attachment_els:
            name_el = att.find("o:OPFAttachmentName", ns)
            if name_el is not None and name_el.text:
                names.append(name_el.text)
    else:
        attachment_els = root.findall(".//OPFMessageCopyAttachmentList/messageAttachment")
        for att in attachment_els:
            name_el = att.find("OPFAttachmentName")
            if name_el is not None and name_el.text:
                names.append(name_el.text)
    return names


# ── RFC 2822 Header Extraction ────────────────────────────────


def _extract_header(source: str, header_name: str) -> str:
    """Extract a single header value from raw RFC 2822 source.

    Handles continuation lines (lines starting with whitespace).
    Stops at the blank line separating headers from body.
    """
    pattern = re.compile(
        rf"^{re.escape(header_name)}:\s*(.+?)(?=\n\S|\n\n|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(source)
    if not match:
        return ""
    value = match.group(1).strip()
    # Collapse continuation whitespace
    value = re.sub(r"\s+", " ", value)
    return value


def _extract_email_from_header(source: str, header_name: str) -> str:
    """Extract the email address from a From/To header like 'Name <email>'."""
    raw = _extract_header(source, header_name)
    if not raw:
        return ""
    # HTML-encoded angle brackets from OLM: &lt; and &gt;
    raw = raw.replace("&lt;", "<").replace("&gt;", ">")
    match = re.search(r"<([^>]+@[^>]+)>", raw)
    if match:
        return match.group(1)
    # Bare email
    match = re.search(r"[\w.+-]+@[\w.-]+", raw)
    return match.group(0) if match else raw


def _extract_name_from_header(source: str, header_name: str) -> str:
    """Extract the display name from a header like '"Name" <email>'."""
    raw = _extract_header(source, header_name)
    if not raw:
        return ""
    raw = raw.replace("&lt;", "<").replace("&gt;", ">")
    # "Quoted Name" <email>
    match = re.search(r'"([^"]+)"', raw)
    if match:
        return match.group(1)
    # Unquoted Name <email>
    match = re.search(r"^([^<]+)<", raw)
    if match:
        return match.group(1).strip().strip('"')
    return ""


def _parse_address_list(raw: str) -> list[str]:
    """Parse a comma-separated list of addresses into email strings."""
    raw = raw.replace("&lt;", "<").replace("&gt;", ">")
    addresses: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        match = re.search(r"<([^>]+@[^>]+)>", part)
        if match:
            addresses.append(match.group(1))
        else:
            match = re.search(r"[\w.+-]+@[\w.-]+", part)
            if match:
                addresses.append(match.group(0))
    return addresses


# ── Folder / Text / XML Utilities ─────────────────────────────


def _extract_folder(path: str) -> str:
    """Extract the folder name from the OLM internal path."""
    # Path looks like: Accounts/.../com.microsoft.__Messages/Inbox/msg.xml
    parts = path.split("/")
    msg_idx = None
    for i, part in enumerate(parts):
        if "com.microsoft.__messages" in part.lower():
            msg_idx = i
            break
    if msg_idx is not None and msg_idx + 1 < len(parts):
        folder_parts = parts[msg_idx + 1 : -1]
        if folder_parts:
            return "/".join(folder_parts)
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


def _read_limited_bytes(stream: IO[bytes], byte_limit: int, chunk_size: int = 64 * 1024) -> bytes:
    """Read bytes from stream while enforcing a hard byte limit."""
    chunks: list[bytes] = []
    total = 0

    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > byte_limit:
            raise ValueError(f"XML payload exceeds limit of {byte_limit} bytes.")
        chunks.append(chunk)

    return b"".join(chunks)
