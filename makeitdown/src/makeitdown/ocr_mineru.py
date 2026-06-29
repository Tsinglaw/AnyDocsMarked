"""MinerU OCR backend — verifier engine for dual-OCR cross-check.

Mirrors LocalOCR's interface so OCRDispatcher can run it alongside Paddle. The
heavy `mineru` import is deferred to first conversion. The single library call is
isolated in `_run_mineru` so the wrapper is testable without MinerU installed.
"""

from __future__ import annotations

import importlib.util
import threading
from pathlib import Path

from .models import ConversionResult


class MinerULocal:
    """Local MinerU pipeline (PDF/image -> markdown)."""

    def __init__(self, model: str | None = None):
        self.model = model or "mineru"
        self._lock = threading.Lock()

    @staticmethod
    def is_available() -> bool:
        try:
            return importlib.util.find_spec("mineru") is not None
        except Exception:
            return False

    @property
    def engine_label(self) -> str:
        return "mineru"

    def _run_mineru(self, path: Path) -> tuple[str, int]:
        """Run MinerU and return (markdown_text, page_count).

        Integration point — verify the exact MinerU API at implementation time.
        As of MinerU's documented Python API this is roughly:
            from mineru.cli.common import do_parse  # or the documented entry
        and reading the produced markdown. Keep all MinerU specifics inside here.
        """
        raise NotImplementedError("wire MinerU's documented Python API here")

    def convert(self, path: Path) -> ConversionResult:
        with self._lock:
            text, pages = self._run_mineru(path)
        return ConversionResult(text=text, engine=self.engine_label, pages=pages)
