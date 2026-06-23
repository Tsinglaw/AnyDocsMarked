"""File -> plain text extraction via markitdown.

markitdown handles pdf / docx / pptx / xlsx / html / md / txt and more,
emitting markdown. Scanned/image-only PDFs need an OCR backend (tesseract)
installed separately; without it they extract empty and are skipped.
"""

from __future__ import annotations

from fnmatch import fnmatch
from pathlib import Path

from markitdown import MarkItDown

# Extensions we attempt to extract. Anything else is skipped during folder walks.
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls",
    ".html", ".htm", ".md", ".markdown", ".txt", ".csv", ".json",
    ".epub",
}

_md = MarkItDown()


def extract_text(path: str | Path) -> str:
    """Extract markdown/plain text from a single file. Returns '' if nothing usable."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")
    result = _md.convert(str(path))
    text = (result.text_content or "").strip()
    return text


def iter_files(
    root: str | Path, recursive: bool = True, exclude: tuple[str, ...] = (),
) -> list[Path]:
    """List supported files under a path. A file path returns just itself.

    `exclude` is a tuple of filename globs (matched against each file's name,
    e.g. `report.json` or `*.json`) to skip.
    """
    root = Path(root)
    if root.is_file():
        return [root]
    if not root.exists():
        raise FileNotFoundError(f"Path does not exist: {root}")
    globber = root.rglob("*") if recursive else root.glob("*")
    return sorted(
        p for p in globber
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
        and not any(fnmatch(p.name, pat) for pat in exclude)
    )
