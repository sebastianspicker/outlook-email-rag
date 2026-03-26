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
from collections.abc import Generator
from dataclasses import dataclass, field

from lxml import etree

from .html_converter import clean_text as _clean_text
from .html_converter import html_to_text as _html_to_text
from .html_converter import looks_like_html as _looks_like_html
from .olm_xml_helpers import (
    _NS_OUTLOOK,  # noqa: F401 — re-exported for backward compat
    _detect_namespace,
    _extract_address_details,
    _extract_addresses,
    _extract_attachment_contents,
    _extract_attachment_field,  # noqa: F401 — re-exported for backward compat
    _extract_attachments,
    _extract_categories,
    _extract_exchange_list,
    _extract_exchange_meetings,
    _extract_exchange_smart_links,
    _extract_folder,
    _extract_html_body,
    _extract_meeting_data,
    _find,
    _find_text,
    _new_xml_parser,
    _parse_address_element,  # noqa: F401 — re-exported for backward compat
    _parse_references,
    _read_limited_bytes,
)
from .rfc2822 import (
    _calendar_to_text,  # noqa: F401 — re-exported for backward compat (used by tests)
    _decode_mime_words,
    _extract_body_from_source,
    _extract_email_from_header,
    _extract_header,
    _extract_name_from_header,
    _normalize_date,
    _parse_address_list,
    _parse_int,
)

logger = logging.getLogger(__name__)
MAX_XML_BYTES = int(os.environ.get("OLM_MAX_XML_BYTES", 50_000_000))  # 50 MB default
MAX_XML_FILES = int(os.environ.get("OLM_MAX_XML_FILES", 500_000))
MAX_TOTAL_XML_BYTES = 20_000_000_000  # 20 GB — safe because parse_olm is a generator


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
    categories: list[str] = field(default_factory=list)
    thread_topic: str = ""
    thread_index: str = ""
    inference_classification: str = ""  # "Focused" / "Other"
    is_calendar_message: bool = False
    meeting_data: dict = field(default_factory=dict)
    exchange_extracted_links: list[dict] = field(default_factory=list)
    exchange_extracted_emails: list[str] = field(default_factory=list)
    exchange_extracted_contacts: list[str] = field(default_factory=list)
    exchange_extracted_meetings: list[dict] = field(default_factory=list)

    @property
    def uid(self) -> str:
        """Stable unique ID for deduplication.

        When ``message_id`` is available, hashes it directly.  For the
        fallback case (no Message-ID), includes a body hash to reduce
        collision risk when subject+date+sender are identical.
        """
        if self.message_id:
            return hashlib.md5(self.message_id.encode(), usedforsecurity=False).hexdigest()
        body_snippet = (self.body_text or "")[:500]
        key = f"{self.subject}|{self.date}|{self.sender_email}|{body_snippet}"
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
            subj = subj[match.end() :].strip()
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
            "categories": self.categories,
            "thread_topic": self.thread_topic,
            "thread_index": self.thread_index,
            "inference_classification": self.inference_classification,
            "is_calendar_message": self.is_calendar_message,
            "meeting_data": self.meeting_data,
            "exchange_extracted_links": self.exchange_extracted_links,
            "exchange_extracted_emails": self.exchange_extracted_emails,
            "exchange_extracted_contacts": self.exchange_extracted_contacts,
            "exchange_extracted_meetings": self.exchange_extracted_meetings,
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

    # Context manager ensures the ZIP file handle is closed even if the
    # generator is abandoned mid-iteration (GeneratorExit).
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
                            xml_bytes,
                            xml_path,
                            zf,
                        )
                    if email:
                        yield email
            except Exception as exc:  # pragma: no cover - defensive branch
                logger.warning("Failed to parse %s: %s", xml_path, exc)


# ── Email XML Parsing ─────────────────────────────────────────


def _parse_email_xml(xml_bytes: bytes, source_path: str) -> Email | None:
    """Parse a single email XML file from the OLM archive."""
    try:
        root = etree.fromstring(xml_bytes, parser=_new_xml_parser())
    except etree.XMLSyntaxError as exc:
        logger.warning("Failed to parse email XML %s: %s", source_path, exc)
        return None

    ns = _detect_namespace(root)
    folder = _extract_folder(source_path)

    # ── Direct XML fields ──────────────────────────────────
    message_id = _find_text(root, "OPFMessageCopyMessageID", ns)
    subject = _find_text(root, "OPFMessageCopySubject", ns)
    date = _normalize_date(_find_text(root, "OPFMessageCopySentTime", ns))
    # Body fields: plain text uses itertext(); HTML preserves child element
    # structure (e.g. <p>, <br/>, <table>) via serialization when present.
    body_text_el = _find(root, "OPFMessageCopyBody", ns)
    body_text = "".join(body_text_el.itertext()) if body_text_el is not None else ""
    body_html_el = _find(root, "OPFMessageCopyHTMLBody", ns)
    body_html = _extract_html_body(body_html_el) if body_html_el is not None else ""

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

    # ── New OLM metadata fields ───────────────────────────
    categories = _extract_categories(root, ns)
    thread_topic = _find_text(root, "OPFMessageCopyThreadTopic", ns)
    thread_index = _find_text(root, "OPFMessageCopyThreadIndex", ns)
    inference_classification = _find_text(root, "OPFMessageCopyInferenceClassification", ns)
    is_calendar_raw = _find_text(root, "OPFMessageCopyIsCalendarMessage", ns)
    is_calendar_message = is_calendar_raw.lower() == "true" if is_calendar_raw else False
    meeting_data = _extract_meeting_data(root, ns)
    exchange_extracted_links = _extract_exchange_smart_links(root, ns)
    exchange_extracted_emails = _extract_exchange_list(root, ns, "OPFMessageGetExchangeExtractedEmails")
    exchange_extracted_contacts = _extract_exchange_list(root, ns, "OPFMessageGetExchangeExtractedContacts")
    exchange_extracted_meetings = _extract_exchange_meetings(root, ns)

    # ── Fallback: parse OPFMessageCopySource headers ───────
    raw_source_el = _find(root, "OPFMessageCopySource", ns)
    raw_source = "".join(raw_source_el.itertext()) if raw_source_el is not None else ""
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
            date = _normalize_date(_extract_header(raw_source, "Date"))
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

    # Decode MIME encoded-words in subject and sender name
    subject = _decode_mime_words(subject) if subject else ""
    sender_name = _decode_mime_words(sender_name) if sender_name else ""

    # Normalize sender email: strip whitespace, lowercase
    sender_email = sender_email.strip().lower() if sender_email else ""

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
        categories=categories,
        thread_topic=thread_topic,
        thread_index=thread_index,
        inference_classification=inference_classification,
        is_calendar_message=is_calendar_message,
        meeting_data=meeting_data,
        exchange_extracted_links=exchange_extracted_links,
        exchange_extracted_emails=exchange_extracted_emails,
        exchange_extracted_contacts=exchange_extracted_contacts,
        exchange_extracted_meetings=exchange_extracted_meetings,
    )
