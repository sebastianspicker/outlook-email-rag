"""Extract text content from common attachment file types."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .attachment_extractor_profiles import (
    SOURCE_FORMAT_INGESTION_MATRIX_VERSION,
    attachment_format_profile,
    extraction_quality_profile,
)
from .attachment_extractor_text import (
    MAX_EXTRACTED_CHARS,
    _decode_text_bytes,
    _dispatch_extension,
    _docx_extractor,
    _extract_html,
    _extract_legacy_binary_office,
    _extract_ods,
    _extract_plain_text,
    _extract_text_with_dispatch,
    _get_extension,
    _optional_extract,
    _pdf_extractor,
    _pptx_extractor,
    _truncate,
    _xlsx_extractor,
    is_image_attachment,
)
from .image_embedder import _IMAGE_EXTENSIONS

_image_embedder = None


def _get_image_embedder():
    global _image_embedder
    if _image_embedder is None:
        from .image_embedder import ImageEmbedder

        _image_embedder = ImageEmbedder()
    return _image_embedder


def extract_image_embedding(filename: str, content: bytes) -> list[float] | None:
    if not is_image_attachment(filename) or not content:
        return None
    try:
        embedder = _get_image_embedder()
        if not embedder.is_available:
            return None
        return embedder.encode_image(content, filename=filename)
    except Exception:
        return None


def _extract_pdf(content: bytes) -> str | None:
    return _optional_extract(content, "PyPDF2", "PdfReader", _pdf_extractor, "PDF")


def _extract_docx(content: bytes) -> str | None:
    return _optional_extract(content, "docx", "Document", _docx_extractor, "DOCX")


def _extract_xlsx(content: bytes) -> str | None:
    return _optional_extract(content, "openpyxl", "load_workbook", _xlsx_extractor, "XLSX")


def _extract_pptx(content: bytes) -> str | None:
    return _optional_extract(content, "pptx", "Presentation", _pptx_extractor, "PPTX")


def extract_text(filename: str, content: bytes, *, mime_type: str | None = None) -> str | None:
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


def image_ocr_available() -> bool:
    """Return whether the local Tesseract OCR binary is available."""
    return bool(shutil.which("tesseract"))


def extract_image_text_ocr(filename: str, content: bytes, *, timeout_seconds: int = 30) -> str | None:
    """Best-effort OCR for image attachments using the local Tesseract binary."""
    if not is_image_attachment(filename) or not content or not image_ocr_available():
        return None
    suffix = Path(filename).suffix or ".img"
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(content)
            temp_path = temp_file.name
        command = ["tesseract", temp_path, "stdout", "--psm", os.environ.get("ATTACHMENT_OCR_PSM", "6")]
        language_hint = str(os.environ.get("ATTACHMENT_OCR_LANG") or "").strip()
        if language_hint:
            command.extend(["-l", language_hint])
        result = subprocess.run(  # nosec B603
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        if result.returncode != 0:
            return None
        text = _truncate(str(result.stdout or "").strip())
        return text or None
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        if temp_path:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


__all__ = [
    "MAX_EXTRACTED_CHARS",
    "SOURCE_FORMAT_INGESTION_MATRIX_VERSION",
    "_IMAGE_EXTENSIONS",
    "_decode_text_bytes",
    "_dispatch_extension",
    "_docx_extractor",
    "_extract_docx",
    "_extract_html",
    "_extract_legacy_binary_office",
    "_extract_ods",
    "_extract_pdf",
    "_extract_plain_text",
    "_extract_pptx",
    "_extract_text_with_dispatch",
    "_extract_xlsx",
    "_get_extension",
    "_get_image_embedder",
    "_image_embedder",
    "_optional_extract",
    "_pdf_extractor",
    "_pptx_extractor",
    "_truncate",
    "_xlsx_extractor",
    "attachment_format_profile",
    "extract_image_embedding",
    "extract_image_text_ocr",
    "extract_text",
    "extraction_quality_profile",
    "image_ocr_available",
    "is_image_attachment",
]
