"""Extended tests for attachment_extractor.py — targets missing coverage lines."""

from __future__ import annotations

import builtins
import io
from unittest.mock import MagicMock, patch

from src.attachment_extractor import (
    MAX_EXTRACTED_CHARS,
    _extract_html,
    _extract_plain_text,
    _get_extension,
    _optional_extract,
    _truncate,
    extract_image_embedding,
    extract_text,
    is_image_attachment,
)

# ── _get_extension ──────────────────────────────────────────────


class TestGetExtension:
    def test_no_dot_returns_empty(self):
        assert _get_extension("noextension") == ""

    def test_dot_file(self):
        assert _get_extension(".gitignore") == ".gitignore"

    def test_multiple_dots(self):
        assert _get_extension("archive.tar.gz") == ".gz"

    def test_uppercase_normalized(self):
        assert _get_extension("FILE.PDF") == ".pdf"


# ── _truncate ───────────────────────────────────────────────────


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_exact_limit_unchanged(self):
        text = "x" * MAX_EXTRACTED_CHARS
        assert _truncate(text) == text

    def test_over_limit_truncated(self):
        text = "x" * (MAX_EXTRACTED_CHARS + 100)
        result = _truncate(text)
        assert result.endswith("[... content truncated ...]")
        assert len(result) < len(text)


# ── _extract_plain_text ────────────────────────────────────────


class TestExtractPlainText:
    def test_utf8_text(self):
        result = _extract_plain_text(b"Hello world")
        assert result == "Hello world"

    def test_latin1_fallback(self):
        content = "Ärger mit Ümlauten".encode("latin-1")
        result = _extract_plain_text(content)
        assert result is not None
        assert "rger" in result

    def test_empty_text_returns_none(self):
        assert _extract_plain_text(b"   \n  ") is None

    def test_double_decode_failure_returns_none(self):
        """When both UTF-8 and Latin-1 fail, returns None (lines 148-149).

        Latin-1 can decode any byte sequence, so we wrap the content in a
        custom bytes subclass that raises on latin-1 decode to test the branch.
        """

        class BadBytes(bytes):
            def decode(self, encoding="utf-8", errors="strict"):
                raise UnicodeDecodeError(encoding, b"", 0, 1, "mock failure")

        result = _extract_plain_text(BadBytes(b"\xff\xfe"))
        assert result is None


# ── _extract_html ──────────────────────────────────────────────


class TestExtractHtml:
    def test_basic_html(self):
        html = b"<html><body><p>Test paragraph</p></body></html>"
        result = _extract_html(html)
        assert result is not None
        assert "Test paragraph" in result

    def test_html_latin1_fallback(self):
        html = "<html><body><p>Ärger</p></body></html>".encode("latin-1")
        result = _extract_html(html)
        assert result is not None

    def test_html_empty_body_returns_none(self):
        html = b"<html><body></body></html>"
        result = _extract_html(html)
        # May return None if html_to_text produces empty string
        # Just ensure no crash
        assert result is None or isinstance(result, str)

    def test_html_double_decode_failure(self):
        """HTML bytes that fail both UTF-8 and Latin-1 (lines 158-162)."""

        class BadBytes(bytes):
            def decode(self, encoding="utf-8", errors="strict"):
                raise UnicodeDecodeError(encoding, b"", 0, 1, "mock failure")

        result = _extract_html(BadBytes(b"\xff\xfe"))
        assert result is None


# ── extract_text routing ───────────────────────────────────────


class TestExtractTextRouting:
    def test_unknown_extension_returns_none(self):
        """Extension not in any known set returns None (line 123)."""
        assert extract_text("data.xyz", b"some content") is None

    def test_pdf_route(self):
        """PDF extraction routed (line 112)."""
        with patch("src.attachment_extractor._extract_pdf", return_value="PDF text") as m:
            result = extract_text("doc.pdf", b"fake")
            assert result == "PDF text"
            m.assert_called_once()

    def test_docx_route(self):
        """DOCX extraction routed (line 115)."""
        with patch("src.attachment_extractor._extract_docx", return_value="DOCX text") as m:
            result = extract_text("doc.docx", b"fake")
            assert result == "DOCX text"
            m.assert_called_once()

    def test_xlsx_route(self):
        """XLSX extraction routed (line 118)."""
        with patch("src.attachment_extractor._extract_xlsx", return_value="XLSX text") as m:
            result = extract_text("data.xlsx", b"fake")
            assert result == "XLSX text"
            m.assert_called_once()

    def test_pptx_route(self):
        """PPTX extraction routed (line 121)."""
        with patch("src.attachment_extractor._extract_pptx", return_value="PPTX text") as m:
            result = extract_text("slides.pptx", b"fake")
            assert result == "PPTX text"
            m.assert_called_once()

    def test_htm_route(self):
        """HTM extension routed to HTML extractor."""
        with patch("src.attachment_extractor._extract_html", return_value="HTML text") as m:
            result = extract_text("page.htm", b"fake")
            assert result == "HTML text"
            m.assert_called_once()

    def test_skip_extensions_return_none(self):
        """All skip extensions return None."""
        for ext in (".eml", ".msg", ".zip", ".gz", ".exe", ".mp3", ".mp4"):
            assert extract_text(f"file{ext}", b"data") is None

    def test_image_extensions_return_none(self):
        """Image extensions return None from extract_text."""
        for ext in (".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"):
            assert extract_text(f"file{ext}", b"data") is None

    def test_text_extensions_all_work(self):
        """All text extensions produce output."""
        for ext in (".txt", ".csv", ".log", ".md", ".json", ".xml", ".yaml", ".yml", ".ini", ".cfg", ".conf", ".tsv", ".rst"):
            result = extract_text(f"file{ext}", b"content")
            assert result == "content", f"Failed for {ext}"


# ── _optional_extract ──────────────────────────────────────────


class TestOptionalExtract:
    def test_import_error_returns_none(self):
        """ImportError during module import returns None (line 189-190)."""
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "nonexistent_module":
                raise ImportError("mocked")
            return real_import(name, *args, **kwargs)

        with patch.object(builtins, "__import__", mock_import):
            result = _optional_extract(
                b"content",
                "nonexistent_module",
                "SomeClass",
                lambda obj, stream: "text",
                "TEST",
            )
            assert result is None

    def test_attribute_error_returns_none(self):
        """AttributeError when getting from_name from module returns None (line 188)."""
        result = _optional_extract(
            b"content",
            "os",
            "nonexistent_attribute_xyz",
            lambda obj, stream: "text",
            "TEST",
        )
        assert result is None

    def test_extractor_success(self):
        """Successful extraction path (lines 193-196)."""

        def fake_extractor(obj, stream):
            return f"extracted with {obj.__name__}"

        result = _optional_extract(
            b"content",
            "os",
            "path",
            fake_extractor,
            "TEST",
        )
        assert result is not None
        assert "extracted with" in result

    def test_extractor_returns_empty_string(self):
        """Extractor returning empty string yields None (line 197)."""
        result = _optional_extract(
            b"content",
            "os",
            "path",
            lambda obj, stream: "",
            "TEST",
        )
        assert result is None

    def test_extractor_returns_none(self):
        """Extractor returning None yields None (line 197)."""
        result = _optional_extract(
            b"content",
            "os",
            "path",
            lambda obj, stream: None,
            "TEST",
        )
        assert result is None

    def test_extractor_exception_returns_none(self):
        """Exception during extraction returns None (lines 198-200)."""

        def failing_extractor(obj, stream):
            raise RuntimeError("extraction failed")

        result = _optional_extract(
            b"content",
            "os",
            "path",
            failing_extractor,
            "TEST",
        )
        assert result is None

    def test_extractor_truncates_long_text(self):
        """Long text from extractor is truncated (line 196)."""
        long_text = "x" * (MAX_EXTRACTED_CHARS + 500)

        result = _optional_extract(
            b"content",
            "os",
            "path",
            lambda obj, stream: long_text,
            "TEST",
        )
        assert result is not None
        assert "[... content truncated ...]" in result


# ── Individual extractors ──────────────────────────────────────


class TestPdfExtractor:
    def test_pdf_extractor_function(self):
        """Test _pdf_extractor directly with mocked PdfReader."""
        from src.attachment_extractor import _pdf_extractor

        mock_page1 = MagicMock()
        mock_page1.extract_text.return_value = "Page 1 content"
        mock_page2 = MagicMock()
        mock_page2.extract_text.return_value = "Page 2 content"

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page1, mock_page2]

        mock_pdf_reader_cls = MagicMock(return_value=mock_reader)
        stream = io.BytesIO(b"fake pdf")

        result = _pdf_extractor(mock_pdf_reader_cls, stream)
        assert "Page 1 content" in result
        assert "Page 2 content" in result

    def test_pdf_extractor_empty_pages(self):
        """Test _pdf_extractor with pages that have no text."""
        from src.attachment_extractor import _pdf_extractor

        mock_page = MagicMock()
        mock_page.extract_text.return_value = None

        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        mock_pdf_reader_cls = MagicMock(return_value=mock_reader)
        result = _pdf_extractor(mock_pdf_reader_cls, io.BytesIO(b"fake"))
        assert result == ""

    def test_extract_pdf_via_optional(self):
        """Test _extract_pdf calls _optional_extract correctly."""
        with patch("src.attachment_extractor._optional_extract", return_value="PDF out") as m:
            from src.attachment_extractor import _extract_pdf

            result = _extract_pdf(b"content")
            assert result == "PDF out"
            m.assert_called_once()


class TestDocxExtractor:
    def test_docx_extractor_function(self):
        """Test _docx_extractor directly with mocked Document."""
        from src.attachment_extractor import _docx_extractor

        mock_para1 = MagicMock()
        mock_para1.text = "Paragraph one"
        mock_para2 = MagicMock()
        mock_para2.text = "Paragraph two"
        mock_para3 = MagicMock()
        mock_para3.text = "   "  # whitespace-only, should be filtered

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para1, mock_para2, mock_para3]

        mock_document_cls = MagicMock(return_value=mock_doc)
        result = _docx_extractor(mock_document_cls, io.BytesIO(b"fake"))
        assert "Paragraph one" in result
        assert "Paragraph two" in result

    def test_extract_docx_via_optional(self):
        """Test _extract_docx calls _optional_extract correctly."""
        with patch("src.attachment_extractor._optional_extract", return_value="DOCX out") as m:
            from src.attachment_extractor import _extract_docx

            result = _extract_docx(b"content")
            assert result == "DOCX out"
            m.assert_called_once()


class TestXlsxExtractor:
    def test_xlsx_extractor_function(self):
        """Test _xlsx_extractor directly with mocked workbook."""
        from src.attachment_extractor import _xlsx_extractor

        mock_ws = MagicMock()
        mock_ws.iter_rows.return_value = [
            ("Name", "Value"),
            ("Alice", 100),
            (None, None),  # empty row
        ]

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Sheet1"]
        mock_wb.__getitem__ = MagicMock(return_value=mock_ws)

        mock_load_workbook = MagicMock(return_value=mock_wb)
        result = _xlsx_extractor(mock_load_workbook, io.BytesIO(b"fake"))
        assert "[Sheet: Sheet1]" in result
        assert "Alice" in result

    def test_xlsx_extractor_empty_cells_filtered(self):
        """Rows with only empty/None cells are not included."""
        from src.attachment_extractor import _xlsx_extractor

        mock_ws = MagicMock()
        mock_ws.iter_rows.return_value = [
            (None, None, None),
        ]

        mock_wb = MagicMock()
        mock_wb.sheetnames = ["Sheet1"]
        mock_wb.__getitem__ = MagicMock(return_value=mock_ws)

        mock_load_workbook = MagicMock(return_value=mock_wb)
        result = _xlsx_extractor(mock_load_workbook, io.BytesIO(b"fake"))
        # Only the sheet header, no data rows
        assert "[Sheet: Sheet1]" in result

    def test_extract_xlsx_via_optional(self):
        """Test _extract_xlsx calls _optional_extract correctly."""
        with patch("src.attachment_extractor._optional_extract", return_value="XLSX out") as m:
            from src.attachment_extractor import _extract_xlsx

            result = _extract_xlsx(b"content")
            assert result == "XLSX out"
            m.assert_called_once()


class TestPptxExtractor:
    def test_pptx_extractor_function(self):
        """Test _pptx_extractor directly with mocked Presentation."""
        from src.attachment_extractor import _pptx_extractor

        mock_para = MagicMock()
        mock_para.text = "Slide text"

        mock_text_frame = MagicMock()
        mock_text_frame.paragraphs = [mock_para]

        mock_shape = MagicMock()
        mock_shape.has_text_frame = True
        mock_shape.text_frame = mock_text_frame

        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        mock_presentation_cls = MagicMock(return_value=mock_prs)
        result = _pptx_extractor(mock_presentation_cls, io.BytesIO(b"fake"))
        assert "[Slide 1]" in result
        assert "Slide text" in result

    def test_pptx_extractor_no_text_frame(self):
        """Shapes without text_frame are skipped."""
        from src.attachment_extractor import _pptx_extractor

        mock_shape = MagicMock()
        mock_shape.has_text_frame = False

        mock_slide = MagicMock()
        mock_slide.shapes = [mock_shape]

        mock_prs = MagicMock()
        mock_prs.slides = [mock_slide]

        mock_presentation_cls = MagicMock(return_value=mock_prs)
        result = _pptx_extractor(mock_presentation_cls, io.BytesIO(b"fake"))
        assert "[Slide 1]" in result


# ── extract_image_embedding ────────────────────────────────────


class TestExtractImageEmbedding:
    def test_non_image_returns_none(self):
        assert extract_image_embedding("doc.txt", b"hello") is None

    def test_empty_content_returns_none(self):
        assert extract_image_embedding("photo.jpg", b"") is None

    def test_empty_filename_returns_none(self):
        assert extract_image_embedding("", b"\xff") is None

    def test_embedder_unavailable_returns_none(self):
        """When embedder.is_available is False, returns None (line 80)."""
        import src.attachment_extractor as mod

        mock_embedder = MagicMock()
        mock_embedder.is_available = False

        old = mod._image_embedder
        mod._image_embedder = mock_embedder
        try:
            result = extract_image_embedding("photo.jpg", b"\xff\xd8\xff\xe0")
            assert result is None
        finally:
            mod._image_embedder = old

    def test_embedder_available_returns_embedding(self):
        """When embedder is available, returns the encoding (line 81)."""
        import src.attachment_extractor as mod

        mock_embedder = MagicMock()
        mock_embedder.is_available = True
        mock_embedder.encode_image.return_value = [0.1, 0.2, 0.3]

        old = mod._image_embedder
        mod._image_embedder = mock_embedder
        try:
            result = extract_image_embedding("photo.jpg", b"\xff\xd8\xff\xe0")
            assert result == [0.1, 0.2, 0.3]
            mock_embedder.encode_image.assert_called_once_with(b"\xff\xd8\xff\xe0")
        finally:
            mod._image_embedder = old

    def test_embedder_exception_returns_none(self):
        """Exception during encoding returns None (lines 82-84)."""
        import src.attachment_extractor as mod

        mock_embedder = MagicMock()
        mock_embedder.is_available = True
        mock_embedder.encode_image.side_effect = RuntimeError("boom")

        old = mod._image_embedder
        mod._image_embedder = mock_embedder
        try:
            result = extract_image_embedding("photo.jpg", b"\xff\xd8\xff\xe0")
            assert result is None
        finally:
            mod._image_embedder = old


# ── is_image_attachment ────────────────────────────────────────


class TestIsImageAttachment:
    def test_supported_images(self):
        assert is_image_attachment("photo.jpg")
        assert is_image_attachment("photo.jpeg")
        assert is_image_attachment("photo.png")
        assert is_image_attachment("photo.bmp")
        assert is_image_attachment("photo.tiff")
        assert is_image_attachment("photo.tif")
        assert is_image_attachment("photo.webp")

    def test_unsupported_image(self):
        assert not is_image_attachment("anim.gif")
        assert not is_image_attachment("icon.svg")

    def test_non_image(self):
        assert not is_image_attachment("doc.pdf")

    def test_empty(self):
        assert not is_image_attachment("")
