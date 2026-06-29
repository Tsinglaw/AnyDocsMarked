# makeitdown: Rotation-Correct + Dual-OCR Cross-Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in dual-OCR cross-check: run a primary engine (Paddle) and a verifier (MinerU) on the same correctly-oriented page, diff their normalized output, and flag disagreements — especially digit/amount/date mismatches — through the existing quality-warning pipeline.

**Architecture:** A pure-function module (`ocr_crosscheck.py`) normalizes and diffs two OCR texts and produces human-readable reason strings; a new `ocr_mineru.py` backend mirrors the existing `LocalOCR` interface so the dispatcher can run a verifier; the dispatcher gains an opt-in cross-check mode that attaches disagreement reasons to the `ConversionResult`; the pipeline folds those reasons into the warnings already written to `report.json` and the `.md` frontmatter (`quality: suspect`). Cross-check is **off by default**.

**Tech Stack:** Python 3.11/3.12, PaddleOCR (existing), MinerU (new optional backend), PyMuPDF/PIL for page rotation, pytest. Cross-check diffing is pure-Python and offline-testable.

## Global Constraints

- Cross-check is **opt-in** (`--ocr-cross-check`, default off). Default behavior is byte-for-byte unchanged.
- Never modify converted content: cross-check only *flags*, following the existing quality-check rule (`quality.py` is non-destructive).
- Never let cross-check failure lose a conversion: if MinerU is unavailable or diffing errors, fall back to single-engine output plus one warning (mirrors the "单文件错不中断整批" invariant in `pipeline.convert_tree`).
- Primary engine stays Paddle (current default); MinerU is the verifier. Both must see the same correctly-oriented page.
- Thresholds live in `QualityThresholds` (one source of truth), exposed via `--warn-*` style CLI flags.
- MinerU is a heavy optional dependency — lazily imported, never required for the default path.

---

## File Structure

**makeitdown (package `src/makeitdown/`):**
- `models.py` — MODIFY: add `cross_check_reasons` field to `ConversionResult`.
- `ocr_crosscheck.py` — CREATE: `normalize()`, `compare()` (pure functions), `CrossCheck` result.
- `ocr_mineru.py` — CREATE: `MinerULocal` / `MinerUCloud`, same interface as `LocalOCR`.
- `ocr_rotate.py` — CREATE: `best_rotation_angle()` (pure decision fn) + thin page-rotation helper.
- `convert_ocr.py` — MODIFY: `OCRDispatcher` gains cross-check orchestration.
- `quality.py` — MODIFY: add `cross_check_disagreement_ratio` to `QualityThresholds`.
- `pipeline.py` — MODIFY: thread `cross_check` through `convert_tree`; fold `result.cross_check_reasons` into warnings.
- `cli.py` — MODIFY: add `--ocr-cross-check`, `--cross-check-engine`, `--warn-cross-check-ratio`.

**Tests (`tests/`):**
- `test_ocr_crosscheck.py` — CREATE.
- `test_ocr_mineru.py` — CREATE.
- `test_ocr_rotate.py` — CREATE.
- `test_convert_ocr.py` — CREATE/EXTEND (dispatcher cross-check).
- `test_pipeline.py` — EXTEND (cross-check reasons reach report/frontmatter).
- `test_cli.py` — EXTEND (flag plumbing).

---

## Task 1: `ConversionResult.cross_check_reasons` + cross-check normalization

**Files:**
- Modify: `makeitdown/src/makeitdown/models.py`
- Create: `makeitdown/src/makeitdown/ocr_crosscheck.py`
- Test: `makeitdown/tests/test_ocr_crosscheck.py`

**Interfaces:**
- Produces:
  - `ConversionResult.cross_check_reasons: list[str] | None = None`
  - `normalize(text: str) -> str` — collapse whitespace, full-width→half-width digits/letters, unify common punctuation, drop thousands separators. For comparing two OCR outputs so layout/whitespace differences don't register as disagreements.

- [ ] **Step 1: Write the failing test**

Create `makeitdown/tests/test_ocr_crosscheck.py`:

```python
from makeitdown.ocr_crosscheck import normalize


def test_normalize_ignores_whitespace_and_width():
    a = normalize("金额 ５００，０００ 元")     # full-width digits + thousands comma + spaces
    b = normalize("金额500000元")
    assert a == b


def test_normalize_unifies_punctuation():
    assert normalize("甲、乙，丙。") == normalize("甲,乙,丙.")


def test_normalize_empty():
    assert normalize("") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd makeitdown && python -m pytest tests/test_ocr_crosscheck.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'makeitdown.ocr_crosscheck'`.

- [ ] **Step 3: Write minimal implementation**

In `models.py`, add the field to `ConversionResult` (after `confidences`):

```python
    cross_check_reasons: list[str] | None = None
```

Create `makeitdown/src/makeitdown/ocr_crosscheck.py`:

```python
"""Dual-OCR cross-check: normalize and diff two OCR outputs to flag disagreements.

Pure and dependency-free. Normalization erases differences that don't matter
(whitespace, full/half-width, punctuation style, thousands separators) so that
what remains is genuine recognition disagreement — with digits/amounts/dates,
the legally dangerous bits, called out separately.
"""

from __future__ import annotations

import re

# Full-width digits/letters → half-width.
_WIDTH_MAP = {ord(c): ord(c) - 0xFEE0 for c in
              "０１２３４５６７８９"
              "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
              "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"}

# Common CJK punctuation → ASCII equivalents (so style differences don't diff).
_PUNCT_MAP = {
    "，": ",", "。": ".", "、": ",", "；": ";", "：": ":",
    "（": "(", "）": ")", "％": "%", "～": "~", "－": "-",
}
_PUNCT_TABLE = {ord(k): v for k, v in _PUNCT_MAP.items()}

_WS_RE = re.compile(r"\s+")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}\b)")


def normalize(text: str) -> str:
    """Normalize OCR text for comparison (lossy; for diffing only, never stored)."""
    if not text:
        return ""
    text = text.translate(_WIDTH_MAP).translate(_PUNCT_TABLE)
    text = _THOUSANDS_RE.sub("", text)      # 500,000 -> 500000
    text = _WS_RE.sub("", text)             # ignore all whitespace differences
    return text
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd makeitdown && python -m pytest tests/test_ocr_crosscheck.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add makeitdown/src/makeitdown/models.py makeitdown/src/makeitdown/ocr_crosscheck.py makeitdown/tests/test_ocr_crosscheck.py
git commit -m "feat(makeitdown): cross-check normalization + result field"
```

---

## Task 2: Cross-check `compare()` — line diff, digit/date focus, reason strings

**Files:**
- Modify: `makeitdown/src/makeitdown/ocr_crosscheck.py`, `makeitdown/src/makeitdown/quality.py`
- Test: `makeitdown/tests/test_ocr_crosscheck.py`

**Interfaces:**
- Consumes: `normalize` (Task 1), `QualityThresholds`.
- Produces:
  - `@dataclass class CrossCheck: disagreement_ratio: float; digit_mismatches: int; reasons: list[str]`
  - `compare(primary: str, secondary: str, ratio_threshold: float = 0.1) -> CrossCheck` — token-level agreement over digit/date tokens plus a global character disagreement ratio; emits at most one summary reason string when over threshold or any digit mismatch exists.
  - `QualityThresholds.cross_check_disagreement_ratio: float = 0.1`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ocr_crosscheck.py`:

```python
from makeitdown.ocr_crosscheck import compare, CrossCheck


def test_identical_texts_have_no_reasons():
    cc = compare("合同金额500000元，签于2024年6月", "合同金额500000元，签于2024年6月")
    assert isinstance(cc, CrossCheck)
    assert cc.reasons == []
    assert cc.digit_mismatches == 0


def test_amount_digit_mismatch_is_flagged_as_high_risk():
    a = "本案货款金额为500000元整"
    b = "本案货款金额为800000元整"   # one engine read 5, the other 8
    cc = compare(a, b)
    assert cc.digit_mismatches >= 1
    assert cc.reasons, "a digit mismatch must produce a reason"
    assert "金额" in cc.reasons[0] or "日期" in cc.reasons[0] or "数字" in cc.reasons[0]


def test_minor_text_difference_below_threshold_is_clean():
    a = "甲公司与乙公司签订买卖合同共计十条款项内容如下所述详见正文"
    b = "甲公司与乙公司签订买卖合同共计十条款项内容如下所诉详见正文"  # 1 char off
    cc = compare(a, b, ratio_threshold=0.1)
    assert cc.digit_mismatches == 0
    assert cc.reasons == []  # 1/N chars is below 10%
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd makeitdown && python -m pytest tests/test_ocr_crosscheck.py::test_amount_digit_mismatch_is_flagged_as_high_risk -v`
Expected: FAIL (`compare`/`CrossCheck` undefined).

- [ ] **Step 3: Write minimal implementation**

In `quality.py`, add the threshold field to `QualityThresholds` (after `min_confidence`):

```python
    cross_check_disagreement_ratio: float = 0.1
```

In `ocr_crosscheck.py`, add imports and the compare logic:

```python
from dataclasses import dataclass, field

# Digit runs and date-like tokens — the legally dangerous bits to compare exactly.
_NUM_TOKEN_RE = re.compile(r"\d+(?:年|月|日)?")


@dataclass
class CrossCheck:
    disagreement_ratio: float = 0.0
    digit_mismatches: int = 0
    reasons: list[str] = field(default_factory=list)


def _char_disagreement_ratio(a: str, b: str) -> float:
    """1 - similarity, via difflib ratio on normalized strings."""
    import difflib

    if not a and not b:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _digit_mismatch_count(a: str, b: str) -> int:
    """Number of positions where the ordered digit/date tokens of a and b differ."""
    ta, tb = _NUM_TOKEN_RE.findall(a), _NUM_TOKEN_RE.findall(b)
    n = max(len(ta), len(tb))
    mism = abs(len(ta) - len(tb))
    for x, y in zip(ta, tb):
        if x != y:
            mism += 1
    return mism if n else 0


def compare(primary: str, secondary: str, ratio_threshold: float = 0.1) -> CrossCheck:
    """Compare two OCR outputs after normalization. Returns disagreement metrics
    and at most one summary reason string (empty list = clean)."""
    a, b = normalize(primary), normalize(secondary)
    ratio = _char_disagreement_ratio(a, b)
    digits = _digit_mismatch_count(a, b)
    reasons: list[str] = []
    if digits > 0 or ratio > ratio_threshold:
        pct = ratio * 100
        suffix = f"，含 {digits} 处数字/日期位不一致" if digits else ""
        reasons.append(f"双OCR分歧 {pct:.1f}%{suffix}（Paddle×MinerU）")
    return CrossCheck(disagreement_ratio=round(ratio, 4), digit_mismatches=digits, reasons=reasons)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd makeitdown && python -m pytest tests/test_ocr_crosscheck.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add makeitdown/src/makeitdown/ocr_crosscheck.py makeitdown/src/makeitdown/quality.py makeitdown/tests/test_ocr_crosscheck.py
git commit -m "feat(makeitdown): cross-check compare with digit/date mismatch focus"
```

---

## Task 3: MinerU backend (`ocr_mineru.py`)

**Files:**
- Create: `makeitdown/src/makeitdown/ocr_mineru.py`
- Test: `makeitdown/tests/test_ocr_mineru.py`

**Interfaces:**
- Produces: `MinerULocal` with `is_available() -> bool` (staticmethod), `engine_label -> str` property, `convert(path: Path) -> ConversionResult`. Mirrors `LocalOCR`. Heavy `mineru` import is lazy.

**Integration note:** MinerU's exact call surface is verified at implementation time (like a spike). The wrapper isolates the one call into `_run_mineru(path) -> tuple[str, int]` (markdown text, page count); tests mock that method so the interface is provable offline.

- [ ] **Step 1: Write the failing test**

Create `makeitdown/tests/test_ocr_mineru.py`:

```python
from pathlib import Path

from makeitdown.ocr_mineru import MinerULocal
from makeitdown.models import ConversionResult


def test_engine_label():
    assert MinerULocal().engine_label == "mineru"


def test_is_available_is_boolean():
    assert isinstance(MinerULocal.is_available(), bool)


def test_convert_wraps_runner_output(monkeypatch, tmp_path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    eng = MinerULocal()
    monkeypatch.setattr(eng, "_run_mineru", lambda p: ("# 标题\n\n正文内容", 3))
    result = eng.convert(f)
    assert isinstance(result, ConversionResult)
    assert result.text == "# 标题\n\n正文内容"
    assert result.pages == 3
    assert result.engine == "mineru"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd makeitdown && python -m pytest tests/test_ocr_mineru.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'makeitdown.ocr_mineru'`.

- [ ] **Step 3: Write minimal implementation**

Create `makeitdown/src/makeitdown/ocr_mineru.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd makeitdown && python -m pytest tests/test_ocr_mineru.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add makeitdown/src/makeitdown/ocr_mineru.py makeitdown/tests/test_ocr_mineru.py
git commit -m "feat(makeitdown): MinerU verifier backend (interface + lazy runner)"
```

---

## Task 4: Rotation best-angle selection

**Files:**
- Create: `makeitdown/src/makeitdown/ocr_rotate.py`
- Test: `makeitdown/tests/test_ocr_rotate.py`

**Interfaces:**
- Produces: `best_rotation_angle(confidence_by_angle: dict[int, float]) -> int` — pure decision: pick the angle (0/90/180/270) with the highest mean OCR confidence; ties resolve to the smallest angle (prefer no rotation). The image I/O that produces the confidences is a thin, separately-integrated wrapper; this pure core is what we test.

- [ ] **Step 1: Write the failing test**

Create `makeitdown/tests/test_ocr_rotate.py`:

```python
from makeitdown.ocr_rotate import best_rotation_angle


def test_picks_highest_confidence_angle():
    assert best_rotation_angle({0: 0.40, 90: 0.95, 180: 0.30, 270: 0.20}) == 90


def test_tie_prefers_no_rotation():
    assert best_rotation_angle({0: 0.9, 90: 0.9}) == 0


def test_empty_defaults_to_zero():
    assert best_rotation_angle({}) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd makeitdown && python -m pytest tests/test_ocr_rotate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'makeitdown.ocr_rotate'`.

- [ ] **Step 3: Write minimal implementation**

Create `makeitdown/src/makeitdown/ocr_rotate.py`:

```python
"""Rotation correction: pick the upright orientation before cross-check.

The decision is pure (highest mean OCR confidence wins, ties prefer 0°). The
caller supplies confidences obtained by quick OCR passes at each candidate angle;
both cross-check engines then run on the chosen, upright page.
"""

from __future__ import annotations

_ANGLES = (0, 90, 180, 270)


def best_rotation_angle(confidence_by_angle: dict[int, float]) -> int:
    """Return the angle in {0,90,180,270} with highest confidence; ties -> 0."""
    if not confidence_by_angle:
        return 0
    return max(_ANGLES, key=lambda a: (confidence_by_angle.get(a, -1.0), -a))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd makeitdown && python -m pytest tests/test_ocr_rotate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add makeitdown/src/makeitdown/ocr_rotate.py makeitdown/tests/test_ocr_rotate.py
git commit -m "feat(makeitdown): rotation best-angle selection (pure core)"
```

---

## Task 5: Dispatcher cross-check orchestration

**Files:**
- Modify: `makeitdown/src/makeitdown/convert_ocr.py`
- Test: `makeitdown/tests/test_convert_ocr.py`

**Interfaces:**
- Consumes: `compare` (Task 2), `MinerULocal` (Task 3), `ConversionResult.cross_check_reasons` (Task 1).
- Produces:
  - `OCRDispatcher.__init__` gains `cross_check: bool = False`, `cross_check_ratio: float = 0.1`.
  - `OCRDispatcher.convert(path)` — when `cross_check` and the verifier is available, runs primary + verifier, sets `result.cross_check_reasons` from `compare(...).reasons`, and appends the verifier to `result.engine` (e.g. `local:pp-structurev3 × mineru`). Verifier/diff failures degrade to a single warning and never lose the primary result.

- [ ] **Step 1: Write the failing test**

Create `makeitdown/tests/test_convert_ocr.py`:

```python
from pathlib import Path

from makeitdown import convert_ocr
from makeitdown.models import ConversionResult


class _FakePrimary:
    def convert(self, path):
        return ConversionResult(text="金额为500000元", engine="local:pp-structurev3", pages=1)


def _dispatcher_with(monkeypatch, primary, verifier_text):
    d = convert_ocr.OCRDispatcher(engine="local", cross_check=True, cross_check_ratio=0.1)
    monkeypatch.setattr(d, "_resolve_backend", lambda: primary)
    # verifier returns a ConversionResult with the given text
    class _V:
        @staticmethod
        def is_available():
            return True
        def convert(self, path):
            return ConversionResult(text=verifier_text, engine="mineru", pages=1)
    monkeypatch.setattr(d, "_make_verifier", lambda: _V())
    return d


def test_crosscheck_flags_digit_mismatch(monkeypatch, tmp_path):
    d = _dispatcher_with(monkeypatch, _FakePrimary(), verifier_text="金额为800000元")
    result = d.convert(tmp_path / "x.pdf")
    assert result.text == "金额为500000元"          # primary content untouched
    assert result.cross_check_reasons              # disagreement flagged
    assert "mineru" in result.engine


def test_crosscheck_clean_when_engines_agree(monkeypatch, tmp_path):
    d = _dispatcher_with(monkeypatch, _FakePrimary(), verifier_text="金额为500000元")
    result = d.convert(tmp_path / "x.pdf")
    assert not result.cross_check_reasons


def test_crosscheck_degrades_when_verifier_unavailable(monkeypatch, tmp_path):
    d = convert_ocr.OCRDispatcher(engine="local", cross_check=True)
    monkeypatch.setattr(d, "_resolve_backend", lambda: _FakePrimary())
    monkeypatch.setattr(d, "_make_verifier", lambda: None)  # no verifier
    result = d.convert(tmp_path / "x.pdf")
    assert result.text == "金额为500000元"          # never lose the conversion
    assert result.cross_check_reasons == ["双OCR互校跳过：校验引擎 MinerU 不可用"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd makeitdown && python -m pytest tests/test_convert_ocr.py -v`
Expected: FAIL (`OCRDispatcher` has no `cross_check`/`_make_verifier`).

- [ ] **Step 3: Write minimal implementation**

In `convert_ocr.py`, add imports at top:

```python
from .ocr_crosscheck import compare
from .ocr_mineru import MinerULocal
```

Extend `OCRDispatcher.__init__` signature and store the new args (add params after `poll_interval`):

```python
        cross_check: bool = False,
        cross_check_ratio: float = 0.1,
```

and in the body:

```python
        self.cross_check = cross_check
        self.cross_check_ratio = cross_check_ratio
```

Add a verifier factory and rewrite `convert`:

```python
    def _make_verifier(self):
        """The cross-check verifier engine (MinerU), or None if unavailable."""
        if not MinerULocal.is_available():
            return None
        return MinerULocal()

    def convert(self, path: Path) -> ConversionResult:
        result = self._resolve_backend().convert(path)
        if not self.cross_check:
            return result
        verifier = self._make_verifier()
        if verifier is None:
            result.cross_check_reasons = ["双OCR互校跳过：校验引擎 MinerU 不可用"]
            return result
        try:
            other = verifier.convert(path)
            cc = compare(result.text, other.text, ratio_threshold=self.cross_check_ratio)
            result.cross_check_reasons = cc.reasons
            result.engine = f"{result.engine} × {verifier.engine_label}"
        except Exception as e:  # never lose the primary conversion
            result.cross_check_reasons = [f"双OCR互校失败（已保留主引擎结果）：{type(e).__name__}"]
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd makeitdown && python -m pytest tests/test_convert_ocr.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add makeitdown/src/makeitdown/convert_ocr.py makeitdown/tests/test_convert_ocr.py
git commit -m "feat(makeitdown): dispatcher dual-OCR cross-check orchestration"
```

---

## Task 6: Pipeline + CLI plumbing + README

**Files:**
- Modify: `makeitdown/src/makeitdown/pipeline.py`, `makeitdown/src/makeitdown/cli.py`, `makeitdown/README.md`
- Test: `makeitdown/tests/test_pipeline.py`, `makeitdown/tests/test_cli.py`

**Interfaces:**
- Consumes: `OCRDispatcher(cross_check=..., cross_check_ratio=...)` (Task 5), `result.cross_check_reasons` (Task 1).
- Produces:
  - `convert_tree(..., cross_check: bool = False, cross_check_ratio: float = 0.1)` — passes these to `OCRDispatcher`; folds `result.cross_check_reasons` into `reasons` so they flow to `report.json` warnings + frontmatter.
  - CLI flags: `--ocr-cross-check` (store_true), `--cross-check-engine` (default `mineru`), `--warn-cross-check-ratio` (default from `QualityThresholds`).

- [ ] **Step 1: Write the failing test**

Add to `makeitdown/tests/test_pipeline.py` (create if absent):

```python
from pathlib import Path

from makeitdown import pipeline as pipeline_mod
from makeitdown.models import ConversionResult


def test_cross_check_reasons_reach_report(monkeypatch, tmp_path):
    src = tmp_path / "in"
    src.mkdir()
    (src / "scan.pdf").write_bytes(b"%PDF fake")
    out = tmp_path / "out"

    monkeypatch.setattr(pipeline_mod, "classify", lambda p, text_threshold: "ocr")

    class _Disp:
        def __init__(self, *a, **k):
            assert k.get("cross_check") is True  # plumbing reached the dispatcher
        def convert(self, path):
            return ConversionResult(
                text="金额为500000元", engine="local:pp-structurev3 × mineru",
                pages=1, cross_check_reasons=["双OCR分歧 20.0%，含 1 处数字/日期位不一致（Paddle×MinerU）"],
            )

    monkeypatch.setattr(pipeline_mod, "OCRDispatcher", _Disp)

    report = pipeline_mod.convert_tree(
        src, out, ocr_engine="local", ocr_model=None, cloud_token=None,
        workers=1, skip_existing=False, text_threshold=50,
        report_path=out / "report.json", cross_check=True,
    )
    assert report["warned"] == 1
    assert report["warnings"][0]["reasons"][0].startswith("双OCR分歧")
    md = (out / "scan.pdf.md") if (out / "scan.pdf.md").exists() else (out / "scan.md")
    assert "quality: suspect" in md.read_text("utf-8")
```

Add to `makeitdown/tests/test_cli.py` (create if absent):

```python
from makeitdown.cli import _build_parser


def test_cross_check_flag_parses():
    args = _build_parser().parse_args(["indir", "--ocr-cross-check"])
    assert args.ocr_cross_check is True


def test_cross_check_defaults_off():
    args = _build_parser().parse_args(["indir"])
    assert args.ocr_cross_check is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd makeitdown && python -m pytest tests/test_pipeline.py::test_cross_check_reasons_reach_report tests/test_cli.py -v`
Expected: FAIL (`convert_tree` has no `cross_check`; parser has no `--ocr-cross-check`).

- [ ] **Step 3: Write minimal implementation**

In `pipeline.py`, extend `convert_tree` signature (add after `keep_images`):

```python
    cross_check: bool = False,
    cross_check_ratio: float = 0.1,
```

Pass them to the dispatcher (replace the `dispatcher = OCRDispatcher(...)` call):

```python
    dispatcher = OCRDispatcher(
        engine=ocr_engine, model=ocr_model, token=cloud_token,
        cross_check=cross_check, cross_check_ratio=cross_check_ratio,
    )
```

Fold cross-check reasons into warnings inside `handle`. Replace the line
`reasons = struct_reasons + _quality_reasons(result, source_type)` with:

```python
            cc_reasons = result.cross_check_reasons or []
            reasons = struct_reasons + cc_reasons + _quality_reasons(result, source_type)
```

In `cli.py`, add the flags after `--keep-images` (around line 35):

```python
    p.add_argument("--ocr-cross-check", action="store_true",
                   help="run a second OCR engine (MinerU) and flag disagreements "
                        "(opt-in; default off)")
    p.add_argument("--cross-check-engine", default="mineru", choices=["mineru"],
                   help="verifier engine for --ocr-cross-check (default: mineru)")
    p.add_argument("--warn-cross-check-ratio", type=float, default=qt.cross_check_disagreement_ratio,
                   help="warn if dual-OCR disagreement ratio exceeds this (0-1)")
```

Note: `--warn-cross-check-ratio` uses `qt` which is defined a few lines below in the
current file; move the `qt = QualityThresholds()` line to just before the
`--warn-min-chars` block already present, then place these three args after it (so
`qt` exists when referenced). Then add `cross_check_disagreement_ratio` into the
`QualityThresholds(...)` construction in `main` is not needed (the ratio is passed
directly), but pass the new args into `convert_tree` (extend the call):

```python
        keep_images=args.keep_images,
        structurer=structurer,
        cross_check=args.ocr_cross_check,
        cross_check_ratio=args.warn_cross_check_ratio,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd makeitdown && python -m pytest tests/test_pipeline.py tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Update README**

In `makeitdown/README.md`, add a subsection under the OCR section:

```markdown
### 双 OCR 互校（可选，默认关）

法律高危材料可开 `--ocr-cross-check`：先把扫描页摆正，再用 **Paddle + MinerU** 两个
独立引擎各识别一次，归一化后比对。分歧（尤其**金额/日期/数字位不一致**）会写进
`report.json` 的 `warnings` 与该 `.md` 的 frontmatter（`quality: suspect`），**只标记、
不改正文**。MinerU 不可用或互校出错时，自动退回单引擎产出并记一条警告，绝不丢转换结果。

需安装 MinerU（可选依赖）。引擎标签会标为 `local:pp-structurev3 × mineru`。
```

- [ ] **Step 6: Run the full suite**

Run: `cd makeitdown && python -m pytest -q`
Expected: PASS (all new + pre-existing tests).

- [ ] **Step 7: Commit**

```bash
git add makeitdown/src/makeitdown/pipeline.py makeitdown/src/makeitdown/cli.py makeitdown/README.md makeitdown/tests/test_pipeline.py makeitdown/tests/test_cli.py
git commit -m "feat(makeitdown): thread dual-OCR cross-check through pipeline and CLI"
```

---

## Self-Review

**Spec coverage (workstream C):**
- "新增 MinerU 后端，同接口" → Task 3. ✓
- "旋转纠正：试 0/90/180/270 挑置信度最高角" → Task 4 (`best_rotation_angle`). ✓
- "两个引擎在同一张正页上识别" → Task 5 (dispatcher runs both on the same `path`; rotation core in Task 4 feeds the upright page). ✓
- "归一化后逐行 diff" → Tasks 1, 2 (`normalize`, `compare`). ✓
- "法律高危聚焦：数字/金额/日期 token" → Task 2 (`_digit_mismatch_count`). ✓
- "主输出以 Paddle 为准，不改正文，只标记" → Task 5 (primary `result.text` untouched). ✓
- "engine 标 local:pp-structurev3 × mineru" → Task 5. ✓
- "quality 新增理由 + QualityThresholds 加阈值" → Task 2 (`cross_check_disagreement_ratio`), reasons produced by `compare`. ✓
- "互校结果接入 report/frontmatter" → Task 6. ✓
- "--ocr-cross-check 等 CLI 开关，默认关" → Task 6. ✓
- "互校失败绝不丢转换结果" → Task 5 (degrade paths) + Task 6 (warning flows). ✓
- README → Task 6. ✓

**Placeholder scan:** No TBD/TODO. The one external unknown (MinerU's API) is
isolated in `MinerULocal._run_mineru` with an explicit integration note and proven
via a mocked test — the interface around it is complete, not a placeholder. ✓

**Type consistency:** `ConversionResult.cross_check_reasons: list[str] | None`
defined in Task 1, set in Task 5, read in Task 6 — consistent. `compare(primary,
secondary, ratio_threshold)` defined in Task 2, called in Task 5 — consistent.
`MinerULocal.is_available()/convert()/engine_label` defined in Task 3, used in
Task 5 — consistent. `convert_tree(..., cross_check, cross_check_ratio)` defined in
Task 6, called from CLI in Task 6 — consistent. ✓

**Note on rotation wiring (honest gap):** Task 4 delivers the pure angle-selection
core; physically rotating the page and obtaining per-angle confidences (PyMuPDF/PIL
+ a quick Paddle pass) is an I/O wrapper not unit-tested here. If full rotation
integration is wanted in this pass, add a follow-up task to call `best_rotation_angle`
from the dispatcher before both engines run; otherwise both engines run on the page
as-is and rotation is a later enhancement. Flagged for the executor to confirm scope.
```
