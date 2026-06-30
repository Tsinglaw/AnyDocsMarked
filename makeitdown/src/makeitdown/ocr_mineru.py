"""MinerU OCR backends — verifier engine(s) for dual-OCR cross-check.

MinerULocal shells out to the stable `mineru` CLI (the documented public
interface), so we don't depend on MinerU's internal Python API. MinerUCloud
(added in the next task) uses mineru.net's v4 HTTP API. Both produce a directory
of markdown that read_mineru_markdown() turns into one string.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from .models import ConversionResult


def read_mineru_markdown(out_dir: Path) -> tuple[str, int | None]:
    """Concatenate every *.md MinerU wrote under out_dir. Page count isn't exposed
    by the markdown, so it's None. Raise if MinerU produced no markdown."""
    mds = sorted(Path(out_dir).rglob("*.md"))
    if not mds:
        raise RuntimeError("MinerU produced no markdown output")
    text = "\n\n".join(p.read_text("utf-8", errors="replace") for p in mds)
    return text, None


class MinerULocal:
    """Local MinerU via its CLI: `mineru -p <file> -o <out> -b <backend>`."""

    def __init__(self, backend: str = "pipeline"):
        self.backend = backend  # "pipeline" (CPU-capable) | "vlm" (GPU)
        self._lock = threading.Lock()

    @staticmethod
    def is_available() -> bool:
        return shutil.which("mineru") is not None

    @property
    def engine_label(self) -> str:
        return "mineru"

    def _run_mineru(self, path: Path, out_dir: Path) -> None:
        """Run the mineru CLI to parse `path` into `out_dir`.

        Integration point — verify the flags against the installed mineru version
        (`mineru --help`). As documented: `mineru -p <input> -o <output> -b pipeline`.
        """
        subprocess.run(
            ["mineru", "-p", str(path), "-o", str(out_dir), "-b", self.backend],
            check=True, capture_output=True,
        )

    def convert(self, path: Path) -> ConversionResult:
        with self._lock, tempfile.TemporaryDirectory() as tmp:
            self._run_mineru(path, Path(tmp))
            text, pages = read_mineru_markdown(Path(tmp))
        return ConversionResult(text=text, engine=self.engine_label, pages=pages)
