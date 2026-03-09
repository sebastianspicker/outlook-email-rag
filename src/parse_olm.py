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
MAX_TOTAL_XML_BYTES = 20_000_000_000  # 20 GB — safe because parse_olm is a generator

_NS_OUTLOOK = {"o": "http://schemas.microsoft.com/outlook/mac/2011"}


_RE_FW_PREFIX = re.compile(
    r"^(RE|AW|FW|WG|SV|VS|Antw|Doorst)\s*:\s*",
    re.IGNORECASE,
)


@dataclass
class Email:
    """Represents a single parsed email."""

    message_id: str
    subject: str
    sender_name: str
    sender_email: str
    to: list[str]
    cc: list[str]
    bcc: list[str]
    date: str  # ISO format string
    body_text: str
    body_html: str
    folder: str
    has_attachments: bool
    attachment_names: list[str] = field(default_factory=list)
    attachments: list[dict] = field(default_factory=list)
    conversation_id: str = ""
    in_reply_to: str = ""
    references: list[str] = field(default_factory=list)
    priority: int = 0  # 0 = normal
    is_read: bool = True
    attachment_contents: list[tuple[str, bytes]] = field(default_factory=list)

    @property
    def uid(self) -> str:
        """Stable unique ID for deduplication."""
        if self.message_id:
            return hashlib.md5(self.message_id.encode(), usedforsecurity=False).hexdigest()
        key = f"{self.subject}|{self.date}|{self.sender_email}"
        return hashlib.md5(key.encode(), usedforsecurity=False).hexdigest()

    @property
    def email_type(self) -> str:
        """Classify email as 'reply', 'forward', or 'original'.

        Uses subject prefix first, then falls back to ``in_reply_to``
        for implicit replies that lack a RE:/AW: prefix.
        """
        subj = (self.subject or "").strip()
        prefix_match = _RE_FW_PREFIX.match(subj)
        if prefix_match:
            prefix = prefix_match.group(1).upper()
            if prefix in ("FW", "WG", "DOORST", "VS"):
                return "forward"
            return "reply"
        # No subject prefix — check in_reply_to as secondary signal
        if self.in_reply_to:
            return "reply"
        return "original"

    @property
    def base_subject(self) -> str:
        """Subject with RE:/FW:/AW:/WG: prefixes stripped for thread grouping."""
        subj = (self.subject or "").strip()
        while True:
            match = _RE_FW_PREFIX.match(subj)
            if not match:
                break
            subj = subj[match.end():].strip()
        return subj

    @property
    def clean_body(self) -> str:
        """Best available plain text body, with HTML stripped."""
        if self.body_text and self.body_text.strip():
            # OLM sometimes puts HTML in the "plain text" body field
            if _looks_like_html(self.body_text):
                return _html_to_text(self.body_text)
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
            "bcc": self.bcc,
            "date": self.date,
            "body": self.clean_body,
            "folder": self.folder,
            "has_attachments": self.has_attachments,
            "attachment_names": self.attachment_names,
            "attachments": self.attachments,
            "attachment_count": len(self.attachment_names),
            "conversation_id": self.conversation_id,
            "in_reply_to": self.in_reply_to,
            "references": self.references,
            "priority": self.priority,
            "is_read": self.is_read,
            "email_type": self.email_type,
            "base_subject": self.base_subject,
        }


def parse_olm(
    olm_path: str,
    extract_attachments: bool = False,
) -> Generator[Email, None, None]:
    """
    Parse an .olm file and yield Email objects.

    Args:
        olm_path: Path to the .olm file.
        extract_attachments: If True, extract binary attachment content
            and populate ``Email.attachment_contents``. Default False
            to avoid memory bloat.

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
                    if email and extract_attachments:
                        email.attachment_contents = _extract_attachment_contents(
                            xml_bytes, xml_path, zf,
                        )
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
    # Use itertext() for body fields — el.text only returns text before the
    # first child element, silently truncating bodies that contain <br/> etc.
    body_text_el = _find(root, "OPFMessageCopyBody", ns)
    body_text = "".join(body_text_el.itertext()) if body_text_el is not None else ""
    body_html_el = _find(root, "OPFMessageCopyHTMLBody", ns)
    body_html = "".join(body_html_el.itertext()) if body_html_el is not None else ""

    sender_el = _find(root, "OPFMessageCopySenderAddress", ns)
    sender_name_el = _find(root, "OPFMessageCopySenderName", ns)
    sender_email = sender_el.text if sender_el is not None and sender_el.text else ""
    sender_name = sender_name_el.text if sender_name_el is not None and sender_name_el.text else ""

    to_addresses = _extract_addresses(root, ns, "OPFMessageCopyToAddresses")
    cc_addresses = _extract_addresses(root, ns, "OPFMessageCopyCCAddresses")
    bcc_addresses = _extract_addresses(root, ns, "OPFMessageCopyBCCAddresses")

    # Fallback: OPFMessageCopyDisplayTo (display name only, no email)
    if not to_addresses:
        display_to = _find_text(root, "OPFMessageCopyDisplayTo", ns)
        if display_to:
            to_addresses = [name.strip() for name in display_to.split(";") if name.strip()]

    # Also try OPFMessageCopyFromAddresses (provides both name and email)
    if not sender_email or not sender_name:
        from_pairs = _extract_address_details(root, ns, "OPFMessageCopyFromAddresses")
        if from_pairs:
            if not sender_name and from_pairs[0][0]:
                sender_name = from_pairs[0][0]
            if not sender_email and from_pairs[0][1]:
                sender_email = from_pairs[0][1]

    # ── Threading / metadata fields ────────────────────────
    conversation_id = _find_text(root, "OPFMessageCopyExchangeConversationId", ns)
    in_reply_to = _find_text(root, "OPFMessageCopyInReplyTo", ns)
    references_raw = _find_text(root, "OPFMessageCopyReferences", ns)
    references = _parse_references(references_raw)
    priority = _parse_int(_find_text(root, "OPFMessageGetPriority", ns), default=0)
    is_read = _find_text(root, "OPFMessageGetIsRead", ns).lower() != "false"

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
        if not bcc_addresses:
            bcc_raw = _extract_header(raw_source, "BCC")
            if bcc_raw:
                bcc_addresses = _parse_address_list(bcc_raw)
        # Threading fallbacks from RFC 2822 headers
        if not in_reply_to:
            in_reply_to = _extract_header(raw_source, "In-Reply-To").strip("<>")
        if not references:
            refs_raw = _extract_header(raw_source, "References")
            if refs_raw:
                references = _parse_references(refs_raw)

    # ── Fallback: extract body from raw RFC 2822 source ─────
    if not body_text and not body_html and raw_source:
        body_text, body_html = _extract_body_from_source(raw_source)

    # ── Fallback: OPFMessageCopyPreview for body ───────────
    if not body_text and not body_html:
        preview = _find_text(root, "OPFMessageCopyPreview", ns)
        if preview:
            body_text = preview

    # ── Attachments ────────────────────────────────────────
    attachment_names, attachments = _extract_attachments(root, ns)

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
        bcc=bcc_addresses,
        date=date,
        body_text=body_text,
        body_html=body_html,
        folder=folder,
        has_attachments=bool(attachment_names),
        attachment_names=attachment_names,
        attachments=attachments,
        conversation_id=conversation_id,
        in_reply_to=in_reply_to,
        references=references,
        priority=priority,
        is_read=is_read,
    )


# ── Address Extraction ────────────────────────────────────────


def _parse_address_element(element: etree._Element) -> tuple[str, str]:
    """Parse a single address element, returning (name, email).

    Supports two OLM variants:
    - **Attribute format** (common in newer OLM exports)::

        <emailAddress OPFContactEmailAddressAddress="a@b.com"
                      OPFContactEmailAddressName="Alice" />

    - **Child-element format** (older OLM exports)::

        <emailAddress>
          <OPFContactEmailAddressAddress>a@b.com</OPFContactEmailAddressAddress>
          <OPFContactEmailAddressName>Alice</OPFContactEmailAddressName>
        </emailAddress>

    Uses fuzzy matching: any attribute or child tag containing ``'name'`` is
    treated as the display name; ``'address'`` as the email address.
    """
    name = ""
    email_addr = ""

    # Strategy 1: check XML attributes (newer OLM format)
    for attr_name, attr_value in element.attrib.items():
        attr_lower = attr_name.lower()
        if "address" in attr_lower and "@" in attr_value:
            email_addr = attr_value.strip()
        elif "name" in attr_lower and attr_value.strip():
            name = attr_value.strip()

    # Strategy 2: check child elements (older OLM format / fallback)
    if not email_addr:
        for child in element:
            child_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
            child_tag_lower = child_tag.lower()
            if "name" in child_tag_lower and child.text and not name:
                name = child.text.strip()
            elif "address" in child_tag_lower and child.text and not email_addr:
                email_addr = child.text.strip()

    return name, email_addr


def _extract_address_details(
    root: etree._Element, ns: dict[str, str], tag_name: str,
) -> list[tuple[str, str]]:
    """Extract (name, email) pairs from an address list element."""
    container = _find(root, tag_name, ns)
    if container is None:
        return []
    return [_parse_address_element(addr_el) for addr_el in container]


def _extract_addresses(root: etree._Element, ns: dict[str, str], tag_name: str) -> list[str]:
    """Extract addresses from an address list element.

    Returns addresses in ``"Name <email>"`` format when a display name is
    available, otherwise just the bare email address.
    """
    pairs = _extract_address_details(root, ns, tag_name)
    addresses: list[str] = []
    for name, email_addr in pairs:
        if name and email_addr:
            addresses.append(f"{name} <{email_addr}>")
        elif email_addr:
            addresses.append(email_addr)
        elif name:
            addresses.append(name)
    return addresses


def _extract_attachments(root: etree._Element, ns: dict[str, str]) -> tuple[list[str], list[dict]]:
    """Extract attachment info from the email XML.

    Returns:
        (names, attachments) — list of filenames and list of metadata dicts
        with keys ``name``, ``mime_type``, ``size``.
    """
    names: list[str] = []
    attachments: list[dict] = []
    if ns:
        attachment_els = root.findall(".//o:OPFMessageCopyAttachmentList/o:messageAttachment", ns)
    else:
        attachment_els = root.findall(".//OPFMessageCopyAttachmentList/messageAttachment")

    for att in attachment_els:
        info = _extract_attachment_info(att, ns)
        if info["name"]:
            names.append(info["name"])
            attachments.append(info)
    return names, attachments


def _extract_attachment_info(att: etree._Element, ns: dict[str, str]) -> dict:
    """Extract attachment name, MIME type and size from a messageAttachment element."""
    name = _extract_attachment_field(att, ns, "OPFAttachmentName", attr_hint="name")
    mime_type = _extract_attachment_field(att, ns, "OPFAttachmentContentType", attr_hint="contenttype")
    size_str = _extract_attachment_field(att, ns, "OPFAttachmentContentFileSize", attr_hint="filesize")
    size = _parse_int(size_str) if size_str else 0
    return {"name": name, "mime_type": mime_type, "size": size}


def _extract_attachment_field(
    att: etree._Element, ns: dict[str, str], tag: str, attr_hint: str,
) -> str:
    """Extract a field from an attachment element (child element or attribute)."""
    # Strategy 1: child element
    if ns:
        el = att.find(f"o:{tag}", ns)
    else:
        el = att.find(tag)
    if el is not None and el.text:
        return el.text.strip()

    # Strategy 2: XML attributes (fuzzy matching)
    hint_lower = attr_hint.lower()
    for attr_name, attr_value in att.attrib.items():
        if hint_lower in attr_name.lower() and attr_value.strip():
            return attr_value.strip()

    return ""


MAX_ATTACHMENT_BYTES = 20_000_000  # 20MB per attachment


def _extract_attachment_contents(
    xml_bytes: bytes,
    xml_path: str,
    zf: zipfile.ZipFile,
) -> list[tuple[str, bytes]]:
    """Extract binary content of attachments from OLM.

    OLM stores attachment content in two ways:
    1. Inline base64 in ``OPFAttachmentContentData`` element
    2. Relative file path in ``OPFAttachmentURL`` (within the ZIP)

    Returns:
        List of (filename, content_bytes) tuples.
    """
    import base64

    try:
        root = etree.fromstring(xml_bytes, parser=_new_xml_parser())
    except etree.XMLSyntaxError:
        return []

    ns = _detect_namespace(root)
    contents: list[tuple[str, bytes]] = []

    if ns:
        attachment_els = root.findall(".//o:OPFMessageCopyAttachmentList/o:messageAttachment", ns)
    else:
        attachment_els = root.findall(".//OPFMessageCopyAttachmentList/messageAttachment")

    for att in attachment_els:
        name = _extract_attachment_field(att, ns, "OPFAttachmentName", attr_hint="name")
        if not name:
            continue

        # Strategy 1: inline base64 content
        content_data = _extract_attachment_field(
            att, ns, "OPFAttachmentContentData", attr_hint="contentdata",
        )
        if content_data:
            try:
                decoded = base64.b64decode(content_data)
                if len(decoded) <= MAX_ATTACHMENT_BYTES:
                    contents.append((name, decoded))
                continue
            except Exception:  # noqa: BLE001
                pass

        # Strategy 2: relative file path within the ZIP
        url = _extract_attachment_field(att, ns, "OPFAttachmentURL", attr_hint="url")
        if url:
            # URL can be relative to the XML file's directory
            xml_dir = "/".join(xml_path.split("/")[:-1])
            candidates = [url, f"{xml_dir}/{url}"]
            for candidate in candidates:
                try:
                    with zf.open(candidate) as att_file:
                        data = att_file.read(MAX_ATTACHMENT_BYTES + 1)
                        if len(data) <= MAX_ATTACHMENT_BYTES:
                            contents.append((name, data))
                    break
                except KeyError:
                    continue

    return contents


def _parse_references(raw: str) -> list[str]:
    """Parse a References header value into a list of message IDs."""
    if not raw or not raw.strip():
        return []
    # References are space-separated message IDs in angle brackets: <id1> <id2>
    ids = re.findall(r"<([^>]+)>", raw)
    if ids:
        return ids
    # Fallback: try splitting on whitespace for bare IDs
    return [ref.strip("<>").strip() for ref in raw.split() if ref.strip()]


def _parse_int(value: str, default: int = 0) -> int:
    """Safely parse an integer from a string."""
    if not value or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


# ── RFC 2822 Body Extraction ──────────────────────────────────


def _extract_body_from_source(raw_source: str) -> tuple[str, str]:
    """Extract body text and HTML from raw RFC 2822 source.

    When OLM has no OPFMessageCopyBody/HTMLBody elements, the full email
    (headers + body) is in OPFMessageCopySource.  This function splits
    headers from body at the first blank line, then handles MIME multipart
    and Content-Transfer-Encoding.
    """
    import email
    import email.policy

    try:
        msg = email.message_from_string(raw_source, policy=email.policy.default)
    except Exception:
        # Fallback: simple header/body split
        parts = raw_source.split("\n\n", 1)
        if len(parts) == 2:
            return parts[1].strip(), ""
        return "", ""

    body_text = ""
    body_html = ""

    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain" and not body_text:
                payload = part.get_content()
                if isinstance(payload, str):
                    body_text = payload
            elif ct == "text/html" and not body_html:
                payload = part.get_content()
                if isinstance(payload, str):
                    body_html = payload
    else:
        ct = msg.get_content_type()
        payload = msg.get_content()
        if isinstance(payload, str):
            if ct == "text/html":
                body_html = payload
            else:
                body_text = payload

    return body_text.strip(), body_html.strip()


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


def _looks_like_html(text: str) -> bool:
    """Detect whether a string contains HTML markup.

    OLM sometimes puts HTML content in the 'plain text' body field.
    This catches those cases so we can route them through _html_to_text().
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


def _html_to_text(html: str) -> str:
    """Convert HTML to readable plain text, preserving semantic structure."""
    # Remove style and script blocks
    text = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)

    # Headings → markdown-style
    for level in range(1, 7):
        prefix = "#" * level + " "
        text = re.sub(
            rf"<h{level}[^>]*>(.*?)</h{level}>",
            lambda m, p=prefix: f"\n{p}{m.group(1).strip()}\n",
            text,
            flags=re.DOTALL | re.IGNORECASE,
        )

    # Blockquote
    text = re.sub(
        r"<blockquote[^>]*>(.*?)</blockquote>",
        lambda m: "\n" + "\n".join(f"> {line}" for line in m.group(1).strip().splitlines()) + "\n",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # Lists: <li> → bullet
    text = re.sub(r"<li[^>]*>", "\n- ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?[ou]l[^>]*>", "\n", text, flags=re.IGNORECASE)

    # Tables: <tr> → newline, <td>/<th> → tab-separated
    text = re.sub(r"<tr[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</tr>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<t[dh][^>]*>", "\t", text, flags=re.IGNORECASE)
    text = re.sub(r"</t[dh]>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</?table[^>]*>", "\n", text, flags=re.IGNORECASE)

    # Links: <a href="url">text</a> → text (url)
    text = re.sub(
        r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        lambda m: f"{m.group(2).strip()} ({m.group(1)})" if m.group(1).strip() else m.group(2).strip(),
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )

    # <br> and block-level elements → newlines
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
    return etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)


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
