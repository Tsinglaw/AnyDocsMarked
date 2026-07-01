import threading
from pathlib import Path

from markitdown import MarkItDown

from .models import ConversionResult

_local = threading.local()


def _get_converter() -> MarkItDown:
    converter = getattr(_local, "converter", None)
    if converter is None:
        converter = MarkItDown()
        _local.converter = converter
    return converter


def _pdf_page_count(path: Path) -> int | None:
    """Page count for a PDF, so the per-page quality check works on text PDFs too
    (markitdown doesn't surface it). Best-effort: None if unreadable/not a PDF."""
    if path.suffix.lower() != ".pdf":
        return None
    try:
        import fitz  # PyMuPDF (already a dependency)

        with fitz.open(path) as doc:
            return doc.page_count or None
    except Exception:
        return None


def convert(path: Path) -> ConversionResult:
    path = Path(path)
    result = _get_converter().convert(str(path))
    return ConversionResult(
        text=result.text_content, engine="markitdown", pages=_pdf_page_count(path)
    )
