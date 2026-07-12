import json
import time
from pathlib import Path

import requests

from .models import ConversionResult

JOB_URL = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
DEFAULT_MODEL = "PaddleOCR-VL-1.6"

# GET retry policy: a single network blip during a long poll loop must not turn
# a 500-page batch job into a per-file failure. Only idempotent GETs are retried;
# job-submit/upload POSTs are not (they create work, and a duplicate would
# double-bill). Shared by every cloud OCR backend (paddle here, MinerU cloud).
_GET_ATTEMPTS = 3
_RETRY_BACKOFF = 2.0  # seconds; doubles per retry


def get_with_retries(url: str, *, timeout: float, **kwargs):
    """requests.get with bounded retries on transient failures (connection
    errors, timeouts, HTTP 5xx). Transparent to callers: the final attempt's
    response (even a lasting 5xx) or exception is surfaced as-is, so their
    existing error handling stays authoritative. 4xx returns immediately —
    retrying a bad token or a vanished job only wastes the poll budget."""
    delay = _RETRY_BACKOFF
    for _ in range(_GET_ATTEMPTS - 1):
        try:
            resp = requests.get(url, timeout=timeout, **kwargs)
            if resp.status_code < 500:
                return resp
        except requests.RequestException:
            pass
        time.sleep(delay)
        delay *= 2
    return requests.get(url, timeout=timeout, **kwargs)


class CloudOCR:
    """Client for the PaddleOCR AI Studio job-based HTTP API."""

    def __init__(self, token: str, model: str | None = None, poll_interval: float = 5.0,
                 request_timeout: float = 60.0, max_poll_seconds: float = 1800.0):
        self.token = token
        self.model = model or DEFAULT_MODEL
        self.poll_interval = poll_interval
        self.request_timeout = request_timeout
        self.max_poll_seconds = max_poll_seconds

    def _get(self, url: str, **kwargs):
        return get_with_retries(url, timeout=self.request_timeout, **kwargs)

    @property
    def engine_label(self) -> str:
        return f"cloud:{self.model.lower()}"

    def _headers(self) -> dict:
        return {"Authorization": f"bearer {self.token}"}

    def _submit(self, path: Path) -> str:
        optional = {
            "useDocOrientationClassify": False,
            "useDocUnwarping": False,
            "useChartRecognition": False,
        }
        data = {"model": self.model, "optionalPayload": json.dumps(optional)}
        with open(path, "rb") as fh:
            resp = requests.post(JOB_URL, headers=self._headers(),
                                 data=data, files={"file": fh},
                                 timeout=self.request_timeout)
        if resp.status_code != 200:
            raise RuntimeError(f"job submit failed ({resp.status_code}): {resp.text}")
        return resp.json()["data"]["jobId"]

    def _poll(self, job_id: str) -> str:
        start = time.monotonic()
        while True:
            resp = self._get(f"{JOB_URL}/{job_id}", headers=self._headers())
            if resp.status_code != 200:
                raise RuntimeError(f"job poll failed ({resp.status_code}): {resp.text}")
            data = resp.json()["data"]
            state = data["state"]
            if state == "done":
                return data["resultUrl"]["jsonUrl"]
            if state == "failed":
                raise RuntimeError(f"cloud OCR job failed: {data.get('errorMsg')}")
            if time.monotonic() - start > self.max_poll_seconds:
                raise RuntimeError(
                    f"cloud OCR job timed out after {self.max_poll_seconds}s "
                    f"(last state: {state})")
            time.sleep(self.poll_interval)

    def _fetch_markdown(self, jsonl_url: str) -> tuple[str, dict[str, bytes], int]:
        resp = self._get(jsonl_url)
        resp.raise_for_status()
        parts: list[str] = []
        assets: dict[str, bytes] = {}
        pages = 0
        for line in resp.text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            result = json.loads(line)["result"]
            for res in result["layoutParsingResults"]:
                pages += 1
                parts.append(res["markdown"]["text"])
                for img_rel, img_url in res["markdown"].get("images", {}).items():
                    img = self._get(img_url)
                    if img.status_code == 200:
                        assets[img_rel] = img.content
        return "\n\n".join(parts), assets, pages

    def convert(self, path: Path) -> ConversionResult:
        job_id = self._submit(path)
        jsonl_url = self._poll(job_id)
        text, assets, pages = self._fetch_markdown(jsonl_url)
        return ConversionResult(text=text, engine=self.engine_label, assets=assets, pages=pages)
