# makeitdown: Cloud-Default OCR + Consent + MinerU Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make both the primary OCR conversion (Paddle) and the dual-OCR cross-check verifier (MinerU) default to cloud, gated by an explicit consent that never lets documents upload silently; wire MinerU cloud (mineru.net v4) and local (mineru CLI) so cross-check actually works and degrades honestly.

**Architecture:** A pure `cloud_consent` module decides whether cloud uploads are permitted (flag or env) and carries the user-facing notice. `OCRDispatcher` defaults the primary engine to cloud and enforces consent before building any cloud backend; the cross-check verifier gains a `cross_check_mode` (cloud|local|auto) that picks `MinerUCloud` / `MinerULocal` and, when nothing is available, skips cleanly instead of falsely flagging. `MinerULocal` shells out to the stable `mineru` CLI; `MinerUCloud` mirrors the existing `CloudOCR` requests-based submit/poll flow against mineru.net v4.

**Tech Stack:** Python 3.11/3.12, `requests` (HTTP, already a dep), `subprocess` (mineru CLI), `zipfile`/`tempfile` (stdlib), pytest with monkeypatch/mocks (no network, no models in tests).

## Global Constraints

- **Default cloud, never silent upload:** primary `--ocr-engine` default is `cloud`; cloud OCR (primary or verifier) runs ONLY with explicit consent (`--cloud-consent` flag OR `MAKEITDOWN_CLOUD_CONSENT` in {1,true,yes,on}). No consent + cloud selected → stop with guidance; this holds in non-interactive runs too.
- **Local is the opt-out:** `--ocr-engine local` (Paddle) and `--cross-check-mode local` (MinerU) keep documents on-device and never require consent.
- **Tokens from env/flags, never hardcoded:** Paddle `PADDLEOCR_AISTUDIO_TOKEN`; MinerU `MINERU_API_TOKEN`.
- **Cross-check never loses a conversion:** verifier/diff failure or unavailability degrades to keeping the primary result (a warning, or a clean skip) — never aborts the file/batch.
- **Honest skip, no false suspect:** when the verifier is genuinely unavailable, emit a clear "互校跳过…" reason only when the user explicitly asked for cross-check; do not silently mark every OCR file suspect for a missing optional engine. (Skip reason is still a warning — that is acceptable and expected only under `--ocr-cross-check`.)
- **All tests offline:** mock `subprocess`, `requests`, and filesystem; never download a model or call a network service in tests.
- Rotation wiring is out of scope (cross-check works without it).

---

## File Structure

**makeitdown (`src/makeitdown/`):**
- `cloud_consent.py` — CREATE: `CloudConsentRequired`, `has_consent()`, `require_cloud_consent()`, `CLOUD_NOTICE`. Pure logic.
- `ocr_mineru.py` — MODIFY: `read_mineru_markdown()` helper; rewrite `MinerULocal` to shell out to the `mineru` CLI; add `MinerUCloud` (mineru.net v4).
- `convert_ocr.py` — MODIFY: `OCRDispatcher` primary default → cloud, consent gate on cloud, `cross_check_mode` verifier selection, clean skip.
- `cli.py` — MODIFY: `--ocr-engine` default `cloud`; add `--cloud-consent`, `--cross-check-mode`; deprecate `--cross-check-engine`; print notice / enforce gate; thread through.
- `pipeline.py` — MODIFY: `convert_tree` accepts `cross_check_mode` and `cloud_consent`, passes to dispatcher.

**Tests (`tests/`):**
- `test_cloud_consent.py` — CREATE.
- `test_ocr_mineru.py` — MODIFY/EXTEND (MinerULocal CLI, MinerUCloud, helper).
- `test_convert_ocr.py` — MODIFY/EXTEND (default cloud, consent gate, mode selection, skip).
- `test_cli.py` — MODIFY/EXTEND (flags, default, notice/gate).
- `test_pipeline.py` — MODIFY/EXTEND (threading, skip reason reaches report).

---

## Task 1: `cloud_consent` module (pure consent logic + notice)

**Files:**
- Create: `makeitdown/src/makeitdown/cloud_consent.py`
- Test: `makeitdown/tests/test_cloud_consent.py`

**Interfaces:**
- Produces:
  - `class CloudConsentRequired(RuntimeError)`
  - `has_consent(flag: bool, env: dict | None = None) -> bool`
  - `require_cloud_consent(flag: bool, env: dict | None = None) -> None` (raises `CloudConsentRequired(CLOUD_NOTICE)` when not consented)
  - `CLOUD_NOTICE: str`

- [ ] **Step 1: Write the failing test**

Create `makeitdown/tests/test_cloud_consent.py`:

```python
import pytest

from makeitdown.cloud_consent import (
    CLOUD_NOTICE, CloudConsentRequired, has_consent, require_cloud_consent,
)


def test_flag_grants_consent():
    assert has_consent(True, env={}) is True


def test_env_grants_consent():
    assert has_consent(False, env={"MAKEITDOWN_CLOUD_CONSENT": "1"}) is True
    assert has_consent(False, env={"MAKEITDOWN_CLOUD_CONSENT": "yes"}) is True


def test_no_consent_by_default():
    assert has_consent(False, env={}) is False
    assert has_consent(False, env={"MAKEITDOWN_CLOUD_CONSENT": "0"}) is False


def test_require_raises_with_guidance_when_absent():
    with pytest.raises(CloudConsentRequired) as exc:
        require_cloud_consent(False, env={})
    msg = str(exc.value)
    assert "local" in msg and "--cloud-consent" in msg


def test_require_passes_with_consent():
    require_cloud_consent(True, env={})  # must not raise


def test_notice_mentions_upload_and_local():
    assert "上传" in CLOUD_NOTICE and "local" in CLOUD_NOTICE
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd makeitdown && .venv/Scripts/python.exe -m pytest tests/test_cloud_consent.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'makeitdown.cloud_consent'`.

- [ ] **Step 3: Write minimal implementation**

Create `makeitdown/src/makeitdown/cloud_consent.py`:

```python
"""Explicit-consent gate for cloud OCR — documents must never upload silently.

Cloud OCR (Paddle AI Studio or MinerU mineru.net) uploads the document off the
machine. This module decides whether that is permitted (an explicit flag or env
opt-in) and carries the user-facing notice. Pure logic, no I/O.
"""

from __future__ import annotations

import os

CLOUD_NOTICE = (
    "⚠️  即将使用云端 OCR：文档会上传至云端服务"
    "（Paddle→百度 AI Studio / MinerU→mineru.net）。\n"
    "    同意上传：设置 token 并加 --cloud-consent（或环境变量 MAKEITDOWN_CLOUD_CONSENT=1）。\n"
    "    不希望上传（本机性能足够）：加 --ocr-engine local（需安装本地版）。"
)

_TRUTHY = {"1", "true", "yes", "on"}


class CloudConsentRequired(RuntimeError):
    """Raised when a cloud OCR engine is selected but the user has not consented."""


def has_consent(flag: bool, env: dict | None = None) -> bool:
    """True if cloud upload is permitted via the flag or MAKEITDOWN_CLOUD_CONSENT."""
    if flag:
        return True
    env = os.environ if env is None else env
    return env.get("MAKEITDOWN_CLOUD_CONSENT", "").strip().lower() in _TRUTHY


def require_cloud_consent(flag: bool, env: dict | None = None) -> None:
    """Raise CloudConsentRequired(CLOUD_NOTICE) unless consent is present."""
    if not has_consent(flag, env):
        raise CloudConsentRequired(CLOUD_NOTICE)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd makeitdown && .venv/Scripts/python.exe -m pytest tests/test_cloud_consent.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add makeitdown/src/makeitdown/cloud_consent.py makeitdown/tests/test_cloud_consent.py
git commit -m "feat(makeitdown): explicit cloud-consent gate (pure logic + notice)"
```

---

## Task 2: `read_mineru_markdown` helper + `MinerULocal` via the `mineru` CLI

**Files:**
- Modify: `makeitdown/src/makeitdown/ocr_mineru.py`
- Test: `makeitdown/tests/test_ocr_mineru.py`

**Interfaces:**
- Produces:
  - `read_mineru_markdown(out_dir: Path) -> tuple[str, int | None]` — concatenate all `*.md` under a MinerU output dir; pages unknown → None; raise `RuntimeError` if none.
  - `MinerULocal(backend: str = "pipeline")` with `is_available()` (staticmethod, `shutil.which("mineru")`), `engine_label == "mineru"`, `convert(path) -> ConversionResult`. The CLI call is isolated in `_run_mineru(path, out_dir)`.

- [ ] **Step 1: Write the failing test**

Replace the body of `makeitdown/tests/test_ocr_mineru.py` with (this supersedes the stub-era tests):

```python
from pathlib import Path

import pytest

from makeitdown.models import ConversionResult
from makeitdown.ocr_mineru import MinerULocal, read_mineru_markdown


def test_read_markdown_concatenates_md_files(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "doc.md").write_text("# 标题\n\n正文一", encoding="utf-8")
    (tmp_path / "b.md").write_text("正文二", encoding="utf-8")
    text, pages = read_mineru_markdown(tmp_path)
    assert "正文一" in text and "正文二" in text
    assert pages is None


def test_read_markdown_raises_when_empty(tmp_path):
    with pytest.raises(RuntimeError):
        read_mineru_markdown(tmp_path)


def test_engine_label_and_availability():
    assert MinerULocal().engine_label == "mineru"
    assert isinstance(MinerULocal.is_available(), bool)


def test_convert_runs_cli_then_reads_markdown(monkeypatch, tmp_path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    eng = MinerULocal()

    def fake_run(path, out_dir):
        # emulate the mineru CLI writing markdown into the output dir
        (Path(out_dir) / "scan.md").write_text("# 合同\n\n金额五十万元", encoding="utf-8")

    monkeypatch.setattr(eng, "_run_mineru", fake_run)
    result = eng.convert(f)
    assert isinstance(result, ConversionResult)
    assert "金额五十万元" in result.text
    assert result.engine == "mineru"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd makeitdown && .venv/Scripts/python.exe -m pytest tests/test_ocr_mineru.py -v`
Expected: FAIL (`read_mineru_markdown` missing; `MinerULocal._run_mineru` currently raises NotImplementedError and signature differs).

- [ ] **Step 3: Write minimal implementation**

Replace `makeitdown/src/makeitdown/ocr_mineru.py` entirely with:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd makeitdown && .venv/Scripts/python.exe -m pytest tests/test_ocr_mineru.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add makeitdown/src/makeitdown/ocr_mineru.py makeitdown/tests/test_ocr_mineru.py
git commit -m "feat(makeitdown): MinerULocal via mineru CLI + markdown reader"
```

---

## Task 3: `MinerUCloud` (mineru.net v4 submit → upload → poll → zip)

**Files:**
- Modify: `makeitdown/src/makeitdown/ocr_mineru.py`
- Test: `makeitdown/tests/test_ocr_mineru.py`

**Interfaces:**
- Consumes: `read_mineru_markdown` (Task 2).
- Produces: `MinerUCloud(token: str, model_version: str = "pipeline", poll_interval: float = 5.0, request_timeout: float = 60.0, max_poll_seconds: float = 1800.0)` with `engine_label == "mineru-cloud"` and `convert(path) -> ConversionResult`. Steps isolated as `_request_upload(name)->(batch_id,url)`, `_upload(url,path)`, `_poll(batch_id)->zip_url`, `_fetch_markdown(zip_url)->(text,pages)`.

- [ ] **Step 1: Write the failing test**

Add to `makeitdown/tests/test_ocr_mineru.py`:

```python
import io
import zipfile

from makeitdown.ocr_mineru import MinerUCloud


def test_cloud_requires_token():
    import pytest
    with pytest.raises(ValueError):
        MinerUCloud(token="")


def test_cloud_convert_full_flow(monkeypatch, tmp_path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    # Build a fake result zip containing one markdown file.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("scan/auto/scan.md", "# 合同\n\n金额八十万元")
    zip_bytes = buf.getvalue()

    eng = MinerUCloud(token="tok", poll_interval=0)

    monkeypatch.setattr(eng, "_request_upload", lambda name: ("batch1", "https://signed/put"))
    uploaded = {}
    monkeypatch.setattr(eng, "_upload", lambda url, path: uploaded.update(url=url))
    monkeypatch.setattr(eng, "_poll", lambda batch_id: "https://cdn/result.zip")

    class _Resp:
        status_code = 200
        content = zip_bytes
        def raise_for_status(self): pass

    monkeypatch.setattr("makeitdown.ocr_mineru.requests.get", lambda url, timeout=60: _Resp())

    result = eng.convert(f)
    assert "金额八十万元" in result.text
    assert result.engine == "mineru-cloud"
    assert uploaded["url"] == "https://signed/put"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd makeitdown && .venv/Scripts/python.exe -m pytest tests/test_ocr_mineru.py -k cloud -v`
Expected: FAIL (`MinerUCloud` undefined).

- [ ] **Step 3: Write minimal implementation**

In `ocr_mineru.py`, add `import io`, `import time`, `import zipfile`, `import requests` to the imports, then append:

```python
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
            raise ValueError("MinerU cloud needs a token (env MINERU_API_TOKEN or --cross-check-token).")
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd makeitdown && .venv/Scripts/python.exe -m pytest tests/test_ocr_mineru.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add makeitdown/src/makeitdown/ocr_mineru.py makeitdown/tests/test_ocr_mineru.py
git commit -m "feat(makeitdown): MinerUCloud client (mineru.net v4)"
```

---

## Task 4: `OCRDispatcher` — cloud-default primary, consent gate, verifier mode

**Files:**
- Modify: `makeitdown/src/makeitdown/convert_ocr.py`
- Test: `makeitdown/tests/test_convert_ocr.py`

**Interfaces:**
- Consumes: `require_cloud_consent`/`CloudConsentRequired` (Task 1), `MinerULocal`/`MinerUCloud` (Tasks 2-3), `CloudOCR`/`LocalOCR` (existing).
- Produces: `OCRDispatcher(engine="cloud", ..., cross_check=False, cross_check_ratio=0.1, cross_check_mode="cloud", cloud_consent=False, mineru_token=None)`. Primary cloud build calls `require_cloud_consent`. `_make_verifier()` returns a verifier per `cross_check_mode` or `None` (→ clean skip).

- [ ] **Step 1: Write the failing test**

Add to `makeitdown/tests/test_convert_ocr.py`:

```python
import pytest

from makeitdown import convert_ocr
from makeitdown.cloud_consent import CloudConsentRequired
from makeitdown.models import ConversionResult


def test_primary_cloud_default_requires_consent(monkeypatch, tmp_path):
    # engine defaults to cloud; without consent, resolving the backend must refuse.
    d = convert_ocr.OCRDispatcher(token="tok", cloud_consent=False)
    assert d.engine == "cloud"
    with pytest.raises(CloudConsentRequired):
        d.convert(tmp_path / "x.pdf")


def test_primary_local_bypasses_consent(monkeypatch, tmp_path):
    d = convert_ocr.OCRDispatcher(engine="local", cloud_consent=False)
    sentinel = ConversionResult(text="本地结果", engine="local:pp-structurev3", pages=1)

    class _Local:
        @staticmethod
        def is_available(): return True
        def convert(self, p): return sentinel

    monkeypatch.setattr(convert_ocr, "_LocalOCR_cls", _Local)
    monkeypatch.setattr(convert_ocr, "LocalOCR", lambda model=None: _Local())
    assert d.convert(tmp_path / "x.pdf").text == "本地结果"


def test_verifier_mode_local_unavailable_skips(monkeypatch):
    d = convert_ocr.OCRDispatcher(engine="local", cross_check=True, cross_check_mode="local")
    monkeypatch.setattr(convert_ocr.MinerULocal, "is_available", staticmethod(lambda: False))
    assert d._make_verifier() is None


def test_verifier_mode_cloud_needs_consent(monkeypatch):
    # cross_check_mode=cloud but no consent → verifier unavailable (skip), never upload.
    d = convert_ocr.OCRDispatcher(engine="local", cross_check=True,
                                  cross_check_mode="cloud", cloud_consent=False,
                                  mineru_token="tok")
    assert d._make_verifier() is None


def test_verifier_mode_cloud_builds_with_consent(monkeypatch):
    d = convert_ocr.OCRDispatcher(engine="local", cross_check=True,
                                  cross_check_mode="cloud", cloud_consent=True,
                                  mineru_token="tok")
    v = d._make_verifier()
    assert v is not None and v.engine_label == "mineru-cloud"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd makeitdown && .venv/Scripts/python.exe -m pytest tests/test_convert_ocr.py -k "cloud or verifier or local_bypasses" -v`
Expected: FAIL (`OCRDispatcher` has no `cross_check_mode`/`cloud_consent`/`mineru_token`; default engine is `auto`).

- [ ] **Step 3: Write minimal implementation**

In `convert_ocr.py`, update imports:

```python
from .cloud_consent import require_cloud_consent
from .ocr_mineru import MinerUCloud, MinerULocal
```

Change `__init__` defaults and store new args (replace the signature + body assignments):

```python
    def __init__(
        self,
        engine: str = "cloud",
        model: str | None = None,
        token: str | None = None,
        poll_interval: float = 5.0,
        cross_check: bool = False,
        cross_check_ratio: float = 0.1,
        cross_check_mode: str = "cloud",
        cloud_consent: bool = False,
        mineru_token: str | None = None,
    ):
        self.engine = engine
        self.model = model
        self.token = token
        self.poll_interval = poll_interval
        self.cross_check = cross_check
        self.cross_check_ratio = cross_check_ratio
        self.cross_check_mode = cross_check_mode
        self.cloud_consent = cloud_consent
        self.mineru_token = mineru_token
        self._backend = None
        self._verifier = None
        self._verifier_resolved = False
        self._lock = threading.Lock()
```

In `_resolve_backend`, gate every cloud build with consent. Replace the `cloud` and `auto`-fallback-to-cloud branches so each `self._make_cloud()` is preceded by `require_cloud_consent(self.cloud_consent)`:

```python
            if self.engine == "local":
                if not _LocalOCR_cls.is_available():
                    raise OCRUnavailableError(_INSTALL_HINT)
                self._backend = LocalOCR(model=self.model)
            elif self.engine == "cloud":
                require_cloud_consent(self.cloud_consent)
                if not self.token:
                    raise OCRUnavailableError(_CLOUD_HINT)
                self._backend = self._make_cloud()
            elif self.engine == "auto":
                if _LocalOCR_cls.is_available():
                    self._backend = LocalOCR(model=self.model)
                elif self.token:
                    require_cloud_consent(self.cloud_consent)
                    self._backend = self._make_cloud()
                else:
                    raise OCRUnavailableError(_INSTALL_HINT)
            else:
                raise ValueError(f"unknown ocr engine: {self.engine}")
```

Replace `_make_verifier` with mode-aware, consent-respecting, cached selection:

```python
    def _make_verifier(self):
        """The cross-check verifier (MinerU), or None to skip cleanly. Resolved once."""
        if self._verifier_resolved:
            return self._verifier
        with self._lock:
            if not self._verifier_resolved:
                self._verifier = self._resolve_verifier()
                self._verifier_resolved = True
        return self._verifier

    def _resolve_verifier(self):
        mode = self.cross_check_mode
        want_local = mode in ("local", "auto")
        want_cloud = mode in ("cloud", "auto")
        if want_local and MinerULocal.is_available():
            return MinerULocal()
        # Cloud only with explicit consent and a token; otherwise skip (never upload).
        from .cloud_consent import has_consent
        if want_cloud and self.mineru_token and has_consent(self.cloud_consent):
            return MinerUCloud(token=self.mineru_token)
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd makeitdown && .venv/Scripts/python.exe -m pytest tests/test_convert_ocr.py -v`
Expected: PASS for the new tests. Pre-existing cross-check tests that constructed `OCRDispatcher(engine="local", cross_check=True)` and monkeypatched `_make_verifier`/`_resolve_backend` still pass (engine="local" needs no consent; they patch the verifier directly).

- [ ] **Step 5: Commit**

```bash
git add makeitdown/src/makeitdown/convert_ocr.py makeitdown/tests/test_convert_ocr.py
git commit -m "feat(makeitdown): cloud-default primary with consent gate + verifier mode selection"
```

---

## Task 5: CLI + pipeline plumbing (defaults, flags, notice, deprecate `--cross-check-engine`)

**Files:**
- Modify: `makeitdown/src/makeitdown/pipeline.py`, `makeitdown/src/makeitdown/cli.py`
- Test: `makeitdown/tests/test_pipeline.py`, `makeitdown/tests/test_cli.py`

**Interfaces:**
- Consumes: `OCRDispatcher(..., cross_check_mode, cloud_consent, mineru_token)` (Task 4), `CloudConsentRequired`/`CLOUD_NOTICE` (Task 1).
- Produces: `convert_tree(..., cross_check_mode="cloud", cloud_consent=False, mineru_token=None)`; CLI flags `--cloud-consent` (store_true), `--cross-check-mode {cloud,local,auto}` (default cloud), `--ocr-engine` default `cloud`; `--cross-check-engine` removed.

- [ ] **Step 1: Write the failing test**

Add to `makeitdown/tests/test_cli.py`:

```python
from makeitdown.cli import _build_parser


def test_ocr_engine_defaults_to_cloud():
    args = _build_parser().parse_args(["indir"])
    assert args.ocr_engine == "cloud"
    assert args.cloud_consent is False
    assert args.cross_check_mode == "cloud"


def test_cloud_consent_flag_parses():
    args = _build_parser().parse_args(["indir", "--cloud-consent", "--cross-check-mode", "local"])
    assert args.cloud_consent is True
    assert args.cross_check_mode == "local"
```

Add to `makeitdown/tests/test_pipeline.py`:

```python
from makeitdown import pipeline as pipeline_mod


def test_convert_tree_threads_consent_and_mode(monkeypatch, tmp_path):
    src = tmp_path / "in"; src.mkdir()
    (src / "scan.pdf").write_bytes(b"%PDF fake")
    out = tmp_path / "out"
    monkeypatch.setattr(pipeline_mod, "classify", lambda p, text_threshold: "ocr")

    captured = {}

    class _Disp:
        def __init__(self, *a, **k):
            captured.update(k)
        def convert(self, path):
            from makeitdown.models import ConversionResult
            return ConversionResult(text="结果", engine="cloud:paddleocr-vl-1.6", pages=1)

    monkeypatch.setattr(pipeline_mod, "OCRDispatcher", _Disp)
    pipeline_mod.convert_tree(
        src, out, ocr_engine="cloud", ocr_model=None, cloud_token="tok",
        workers=1, skip_existing=False, text_threshold=50,
        report_path=out / "report.json",
        cross_check=True, cross_check_mode="local", cloud_consent=True, mineru_token="mt",
    )
    assert captured["cross_check_mode"] == "local"
    assert captured["cloud_consent"] is True
    assert captured["mineru_token"] == "mt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd makeitdown && .venv/Scripts/python.exe -m pytest tests/test_cli.py tests/test_pipeline.py -k "cloud or consent or mode or threads" -v`
Expected: FAIL (defaults still `auto`; `convert_tree` lacks the params).

- [ ] **Step 3: Write minimal implementation**

In `pipeline.py`, extend `convert_tree` signature (add after `cross_check_ratio`):

```python
    cross_check_mode: str = "cloud",
    cloud_consent: bool = False,
    mineru_token: str | None = None,
```

Pass them to the dispatcher (replace the `dispatcher = OCRDispatcher(...)` call):

```python
    dispatcher = OCRDispatcher(
        engine=ocr_engine, model=ocr_model, token=cloud_token,
        cross_check=cross_check, cross_check_ratio=cross_check_ratio,
        cross_check_mode=cross_check_mode, cloud_consent=cloud_consent,
        mineru_token=mineru_token,
    )
```

In `cli.py`, change the `--ocr-engine` default and add flags. Replace the `--ocr-engine` line:

```python
    p.add_argument("--ocr-engine", choices=["local", "cloud", "auto"], default="cloud",
                   help="OCR backend (default: cloud — uploads documents; needs --cloud-consent. "
                        "Use 'local' to keep documents on-device.)")
```

Remove the `--cross-check-engine` argument and add (near `--ocr-cross-check`):

```python
    p.add_argument("--cloud-consent", action="store_true",
                   help="consent to uploading documents to cloud OCR services")
    p.add_argument("--cross-check-mode", choices=["cloud", "local", "auto"], default="cloud",
                   help="MinerU verifier mode for --ocr-cross-check (default: cloud)")
```

In `main`, read the MinerU token, enforce the consent gate early with the notice, and thread through. After computing `token` and before `convert_tree`, add:

```python
    from .cloud_consent import CLOUD_NOTICE, has_consent
    mineru_token = os.environ.get("MINERU_API_TOKEN")
    # Early, friendly gate: if a cloud engine is selected without consent, stop with guidance.
    cloud_selected = args.ocr_engine in ("cloud", "auto") or (
        args.ocr_cross_check and args.cross_check_mode in ("cloud", "auto"))
    if cloud_selected and not has_consent(args.cloud_consent):
        print(CLOUD_NOTICE, file=sys.stderr)
        if args.ocr_engine in ("cloud", "auto"):
            # primary needs cloud → cannot proceed without an explicit choice
            return 2
    if args.ocr_engine in ("cloud", "auto") and has_consent(args.cloud_consent):
        print("使用云端 OCR：文档将上传至云端服务。", file=sys.stderr)
```

Extend the `convert_tree(...)` call with:

```python
        cross_check=args.ocr_cross_check,
        cross_check_ratio=args.warn_cross_check_ratio,
        cross_check_mode=args.cross_check_mode,
        cloud_consent=args.cloud_consent,
        mineru_token=mineru_token,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd makeitdown && .venv/Scripts/python.exe -m pytest tests/test_cli.py tests/test_pipeline.py -v`
Expected: PASS. Fix any pre-existing CLI/pipeline test that assumed the old `auto` default or `--cross-check-engine` by updating it to pass `--ocr-engine local` (or `--cloud-consent`) as appropriate.

- [ ] **Step 5: Commit**

```bash
git add makeitdown/src/makeitdown/pipeline.py makeitdown/src/makeitdown/cli.py makeitdown/tests/test_cli.py makeitdown/tests/test_pipeline.py
git commit -m "feat(makeitdown): cloud-default CLI with consent gate and cross-check-mode"
```

---

## Task 6: README + setup docs + full suite

**Files:**
- Modify: `makeitdown/README.md`, `lawiki/skill/lawiki/references/setup.md`
- Test: full suites

- [ ] **Step 1: Update docs**

In `makeitdown/README.md`, replace the local-vs-cloud framing with cloud-default + consent + local opt-out, and document MinerU. Add a subsection:

```markdown
### OCR 后端：云端默认 + 显式同意

makeitdown 默认走**云端 OCR**（开箱即用、无需重型安装），但**绝不静默上传**：必须
显式同意才会把文档传到云端服务。

- 同意上云：设置 token（`PADDLEOCR_AISTUDIO_TOKEN`）并加 `--cloud-consent`
  （或环境变量 `MAKEITDOWN_CLOUD_CONSENT=1`）。
- 不希望上传（本机性能足够）：加 `--ocr-engine local`（需安装本地版），文档不出本机。

**双 OCR 互校**（`--ocr-cross-check`，法律高危件用）：用 Paddle + MinerU 两个独立引擎
比对，分歧（尤其金额/日期）标记 `quality: suspect`。校验方 MinerU 用
`--cross-check-mode {cloud,local,auto}` 选择：cloud 需 `MINERU_API_TOKEN` + 同意；
local 需本机安装 `mineru`；auto 优先本地、否则云端、都没有则干净跳过。
```

In `lawiki/skill/lawiki/references/setup.md`, update the OCR-choice step wording to reflect cloud-default + explicit consent + local opt-out (keep it consistent with the makeitdown README; do not change unrelated setup steps).

- [ ] **Step 2: Run both full suites**

Run: `cd makeitdown && .venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all makeitdown tests).
Run: `cd "D:/Vibe Coding Items/AnyDocsMarked/rag-retriever" && .venv/Scripts/python.exe -m pytest -q`
Expected: PASS (unchanged; sanity check nothing cross-broke).

- [ ] **Step 3: Commit**

```bash
git add makeitdown/README.md lawiki/skill/lawiki/references/setup.md
git commit -m "docs: cloud-default OCR + consent + MinerU usage"
```

---

## Self-Review

**Spec coverage:**
- 默认云端（主转换+互校）→ Task 4 (engine default "cloud"), Task 5 (CLI default). ✓
- 必须显式同意才上云（含非交互）→ Task 1 (gate), Task 4 (dispatcher enforces on cloud build), Task 5 (CLI early gate, no TTY prompt → never silent). ✓
- 本地始终可选 → Task 4 (`engine="local"` bypasses consent; `cross_check_mode="local"`). ✓
- 启动醒目提示 → Task 1 (`CLOUD_NOTICE`), Task 5 (printed). ✓
- MinerU 云端现在接 → Task 3. 本地照 CLI 接 → Task 2. ✓
- 互校真正可用 + 诚实跳过（不误标）→ Task 4 (`_resolve_verifier` returns None → existing convert() skip path emits the skip reason only under `--ocr-cross-check`). ✓
- token 从 env/flag 不硬编码 → Task 3 (`MINERU_API_TOKEN`), Task 5 (read env). ✓
- 废弃 `--cross-check-engine` → Task 5. ✓
- 旋转延后（非目标）→ not implemented, by design. ✓
- 文档 → Task 6. ✓

**Placeholder scan:** No TBD/TODO. The two external unknowns (mineru CLI flags; mineru.net field names) are isolated in `_run_mineru` / `MinerUCloud` with the documented values filled in and labeled as integration points — real code, verifiable by the user against their install/token, not placeholders.

**Type consistency:** `OCRDispatcher(..., cross_check_mode, cloud_consent, mineru_token)` defined in Task 4, consumed by Task 5 with matching kwarg names; `MinerULocal()`/`MinerUCloud(token=...)` with `engine_label` "mineru"/"mineru-cloud" defined in Tasks 2-3 and used in Task 4; `read_mineru_markdown(out_dir)->(str,int|None)` defined in Task 2 and reused in Task 3; `require_cloud_consent`/`has_consent`/`CLOUD_NOTICE`/`CloudConsentRequired` defined in Task 1 and used in Tasks 4-5. ✓

**Note (carried from spec):** verifier cloud without consent skips (soft) rather than raising; primary cloud without consent raises/stops (hard). Both honor "never upload without consent" — confirmed consistent across Tasks 4-5.
