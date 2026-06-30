"""MinerU OCR backends — verifier engine(s) for dual-OCR cross-check.

MinerULocal shells out to the stable `mineru` CLI (the documented public
interface), so we don't depend on MinerU's internal Python API. MinerUCloud
(added in the next task) uses mineru.net's v4 HTTP API. Both produce a directory
of markdown that read_mineru_markdown() turns into one string.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path

import requests

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


class MinerUCloud:
    """Client for mineru.net's v4 file-parse API (submit → upload → poll → zip).

    Integration point — verify endpoints/fields against the live API
    (https://mineru.net/apiManage/docs). As documented:
      POST /api/v4/file-urls/batch  -> {data:{batch_id, file_urls:[signed]}}
      PUT signed_url (raw bytes)    -> upload auto-triggers parsing
      GET /api/v4/extract-results/batch/{batch_id}
          -> data.extract_result[i].state in {running,done,failed}; done has full_zip_url
    """

    BASE = "https://mineru.net/api/v4"

    def __init__(self, token: str, model_version: str = "pipeline",
                 poll_interval: float = 5.0, request_timeout: float = 60.0,
                 max_poll_seconds: float = 1800.0):
        if not token:
            raise ValueError("MinerU cloud needs a token (env MINERU_API_TOKEN).")
        self.token = token
        self.model_version = model_version
        self.poll_interval = poll_interval
        self.request_timeout = request_timeout
        self.max_poll_seconds = max_poll_seconds

    @property
    def engine_label(self) -> str:
        return "mineru-cloud"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def _request_upload(self, name: str) -> tuple[str, str]:
        body = {"files": [{"name": name}], "model_version": self.model_version}
        resp = requests.post(f"{self.BASE}/file-urls/batch", headers=self._headers(),
                             json=body, timeout=self.request_timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"mineru upload-url request failed ({resp.status_code}): {resp.text}")
        data = resp.json()["data"]
        return data["batch_id"], data["file_urls"][0]

    def _upload(self, signed_url: str, path: Path) -> None:
        with open(path, "rb") as fh:
            resp = requests.put(signed_url, data=fh, timeout=self.request_timeout)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"mineru file upload failed ({resp.status_code})")

    def _poll(self, batch_id: str) -> str:
        start = time.monotonic()
        while True:
            resp = requests.get(f"{self.BASE}/extract-results/batch/{batch_id}",
                                headers=self._headers(), timeout=self.request_timeout)
            if resp.status_code != 200:
                raise RuntimeError(f"mineru poll failed ({resp.status_code}): {resp.text}")
            items = resp.json()["data"]["extract_result"]
            item = items[0]
            state = item.get("state")
            if state == "done":
                return item["full_zip_url"]
            if state == "failed":
                raise RuntimeError(f"mineru cloud job failed: {item.get('err_msg')}")
            if time.monotonic() - start > self.max_poll_seconds:
                raise RuntimeError(f"mineru cloud job timed out (last state: {state})")
            time.sleep(self.poll_interval)

    def _fetch_markdown(self, zip_url: str) -> tuple[str, int | None]:
        resp = requests.get(zip_url, timeout=self.request_timeout)
        resp.raise_for_status()
        with tempfile.TemporaryDirectory() as tmp:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                zf.extractall(tmp)
            return read_mineru_markdown(Path(tmp))

    def convert(self, path: Path) -> ConversionResult:
        batch_id, signed_url = self._request_upload(path.name)
        self._upload(signed_url, path)
        zip_url = self._poll(batch_id)
        text, pages = self._fetch_markdown(zip_url)
        return ConversionResult(text=text, engine=self.engine_label, pages=pages)
