"""Attachment text and image embedding extraction helpers."""

from __future__ import annotations

import io
import logging
import re
import zipfile
from collections.abc import Callable
from typing import Any

from .image_embedder import _IMAGE_EXTENSIONS

logger = logging.getLogger(__name__)

MAX_EXTRACTED_CHARS = 50_000

_TEXT_EXTENSIONS = frozenset(
    {
        ".txt",
        ".csv",
        ".log",
        ".md",
        ".json",
        ".xml",
        ".yaml",
        ".yml",
        ".ini",
        ".cfg",
        ".conf",
        ".tsv",
        ".rst",
        ".ics",
        ".ical",
        ".vcs",
        ".rtf",
        ".odt",
        ".doc",
    }
)

_SKIP_EXTENSIONS = frozenset(
    {
        ".eml",
        ".msg",
        ".zip",
        ".gz",
        ".tar",
        ".rar",
        ".7z",
        ".exe",
        ".dll",
        ".bin",
        ".dat",
        ".iso",
        ".gif",
        ".ico",
        ".svg",
        ".mp3",
        ".mp4",
        ".wav",
        ".avi",
        ".mov",
        ".mkv",
        ".flac",
        ".ppt",
    }
)

_MIME_EXTENSION_MAP = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "application/json": ".json",
    "text/xml": ".xml",
    "application/xml": ".xml",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "text/calendar": ".ics",
    "application/ics": ".ics",
    "application/rtf": ".rtf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
}

_MIME_OVERRIDE_EXTENSIONS = frozenset({"", ".bin", ".dat"})

_image_embedder: object | None = None


def _get_extension(filename: str) -> str:
    dot_pos = filename.rfind(".")
    if dot_pos == -1:
        return ""
    return filename[dot_pos:].lower()


def is_image_attachment(filename: str) -> bool:
    """Check if a filename is a supported image format for embedding."""
    if not filename:
        return False
    return _get_extension(filename) in _IMAGE_EXTENSIONS


def _dispatch_extension(filename: str, mime_type: str | None = None) -> str:
    ext = _get_extension(filename)
    normalized_mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    mime_ext = _MIME_EXTENSION_MAP.get(normalized_mime, "")
    if mime_ext and ext in _MIME_OVERRIDE_EXTENSIONS:
        return mime_ext
    return ext or mime_ext


def _get_image_embedder():
    """Return the module-level ImageEmbedder singleton (lazy-init)."""
    global _image_embedder
    if _image_embedder is None:
        from .image_embedder import ImageEmbedder

        _image_embedder = ImageEmbedder()
    return _image_embedder


def extract_image_embedding(filename: str, content: bytes) -> list[float] | None:
    """Encode an image attachment into a 1024-d embedding vector."""
    if not is_image_attachment(filename) or not content:
        return None

    try:
        embedder = _get_image_embedder()
        if not embedder.is_available:
            return None
        return embedder.encode_image(content, filename=filename)
    except Exception:
        logger.debug("Failed to encode image attachment: %s", filename, exc_info=True)
        return None


def _extract_text_with_dispatch(
    filename: str,
    content: bytes,
    *,
    mime_type: str | None = None,
    plain_text_extractor: Callable[[bytes], str | None],
    html_extractor: Callable[[bytes], str | None],
    pdf_extractor: Callable[[bytes], str | None],
    docx_extractor: Callable[[bytes], str | None],
    xlsx_extractor: Callable[[bytes], str | None],
    ods_extractor: Callable[[bytes], str | None],
    legacy_office_extractor: Callable[[bytes, str], str | None],
    pptx_extractor: Callable[[bytes], str | None],
) -> str | None:
    """Shared routing matrix for attachment text extraction."""
    if not filename or not content:
        return None

    ext = _dispatch_extension(filename, mime_type)

    if ext in _SKIP_EXTENSIONS or ext in _IMAGE_EXTENSIONS:
        return None
    if ext in _TEXT_EXTENSIONS:
        return plain_text_extractor(content)
    if ext in (".html", ".htm"):
        return html_extractor(content)
    if ext == ".pdf":
        return pdf_extractor(content)
    if ext == ".docx":
        return docx_extractor(content)
    if ext in {".xlsx", ".xlsm"}:
        return xlsx_extractor(content)
    if ext == ".ods":
        return ods_extractor(content)
    if ext == ".xls":
        return legacy_office_extractor(content, "XLS")
    if ext == ".doc":
        return legacy_office_extractor(content, "DOC")
    if ext == ".pptx":
        return pptx_extractor(content)
    return None


def extract_text(filename: str, content: bytes, *, mime_type: str | None = None) -> str | None:
    """Extract readable text from an attachment."""
    return _extract_text_with_dispatch(
        filename,
        content,
        mime_type=mime_type,
        plain_text_extractor=_extract_plain_text,
        html_extractor=_extract_html,
        pdf_extractor=_extract_pdf,
        docx_extractor=_extract_docx,
        xlsx_extractor=_extract_xlsx,
        ods_extractor=_extract_ods,
        legacy_office_extractor=lambda data, label: _extract_legacy_binary_office(data, format_label=label),
        pptx_extractor=_extract_pptx,
    )


def _truncate(text: str) -> str:
    if len(text) <= MAX_EXTRACTED_CHARS:
        return text
    return text[:MAX_EXTRACTED_CHARS] + "\n[... content truncated ...]"


def _extract_plain_text(content: bytes) -> str | None:
    text = _decode_text_bytes(content)
    text = text.strip()
    return _truncate(text) if text else None


def _extract_html(content: bytes) -> str | None:
    html = _decode_text_bytes(content)

    from .html_converter import html_to_text as _html_to_text

    text = _html_to_text(html).strip()
    return _truncate(text) if text else None


def _looks_like_utf16(content: bytes) -> bool:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    if len(content) < 4:
        return False
    sample = content[:32]
    null_count = sum(1 for byte in sample if byte == 0)
    return null_count >= max(2, len(sample) // 4)


def _decode_text_bytes(content: bytes) -> str:
    encodings = ["utf-8-sig"]
    if _looks_like_utf16(content):
        encodings.extend(["utf-16", "utf-16le", "utf-16be"])
    encodings.extend(["cp1252", "latin-1"])
    for encoding in encodings:
        try:
            decoded = content.decode(encoding)
        except UnicodeDecodeError:
            continue
        if decoded.replace("\x00", "").strip():
            return decoded
    return content.decode("latin-1", errors="ignore")


def _optional_extract(
    content: bytes,
    import_name: str,
    import_from: str,
    extractor: Callable[[Any, io.BytesIO], str | None],
    fmt_label: str,
) -> str | None:
    try:
        mod = __import__(import_name)
        obj = getattr(mod, import_from)
    except (ImportError, AttributeError):
        logger.debug("%s not installed; skipping %s extraction.", import_name, fmt_label)
        return None

    try:
        text = extractor(obj, io.BytesIO(content))
        if text:
            return _truncate(text)
        return None
    except Exception:
        logger.debug("Failed to extract %s text from attachment.", fmt_label, exc_info=True)
        return None


def _pdf_extractor(PdfReader, stream):
    reader = PdfReader(stream)
    pages = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            pages.append(page_text)
    return "\n\n".join(pages).strip()


def _docx_extractor(Document, stream):
    doc = Document(stream)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs).strip()


def _xlsx_extractor(load_workbook, stream):
    wb = load_workbook(stream, read_only=True, data_only=True)
    lines = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        lines.append(f"[Sheet: {sheet}]")
        for row in ws.iter_rows(values_only=True):
            cells = [str(cell) if cell is not None else "" for cell in row]
            if any(c.strip() for c in cells):
                lines.append("\t".join(cells))
    wb.close()
    return "\n".join(lines).strip()


def _extract_ods(content: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            xml_text = archive.read("content.xml")
    except (KeyError, OSError, zipfile.BadZipFile):
        logger.debug("Failed to extract ODS content.xml.", exc_info=True)
        return None

    try:
        decoded = _decode_text_bytes(xml_text)
    except Exception:
        logger.debug("Failed to decode ODS content.xml.", exc_info=True)
        return None

    # ODS stores readable strings in XML. Strip tags conservatively.
    flattened = re.sub(r"<[^>]+>", " ", decoded)
    text = " ".join(flattened.split()).strip()
    return _truncate(text) if text else None


def _extract_legacy_binary_office(content: bytes, *, format_label: str) -> str | None:
    """Return a conservative printable-text fallback for legacy binary Office files."""
    decoded_variants = [
        _decode_text_bytes(content),
        content.decode("utf-16le", errors="ignore"),
        content.decode("utf-16be", errors="ignore"),
    ]
    candidates: list[str] = []
    for decoded in decoded_variants:
        normalized = " ".join(decoded.replace("\x00", " ").split())
        if normalized:
            candidates.append(normalized)
    printable_pattern = r"[A-Za-z0-9ÄÖÜäöüß@:/._%+\-(),]{4,}(?:\s+[A-Za-z0-9ÄÖÜäöüß@:/._%+\-(),]{2,})*"
    printable_runs = re.findall(printable_pattern, " ".join(candidates))
    text = " ".join(dict.fromkeys(run.strip() for run in printable_runs if run.strip()))
    if not text:
        logger.debug("No printable fallback text recovered from %s attachment.", format_label)
        return None
    return _truncate(text)


def _pptx_extractor(Presentation, stream):
    prs = Presentation(stream)
    lines: list[str] = []
    for slide_num, slide in enumerate(prs.slides, 1):
        lines.append(f"[Slide {slide_num}]")
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        lines.append(text)
    return "\n".join(lines).strip()


def _extract_pdf(content: bytes) -> str | None:
    return _optional_extract(content, "PyPDF2", "PdfReader", _pdf_extractor, "PDF")


def _extract_docx(content: bytes) -> str | None:
    return _optional_extract(content, "docx", "Document", _docx_extractor, "DOCX")


def _extract_xlsx(content: bytes) -> str | None:
    return _optional_extract(content, "openpyxl", "load_workbook", _xlsx_extractor, "XLSX")


def _extract_pptx(content: bytes) -> str | None:
    return _optional_extract(content, "pptx", "Presentation", _pptx_extractor, "PPTX")
