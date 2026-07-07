# Makeitdown Image Placeholder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop makeitdown from silently double-dropping images; instead leave a traceable `〔图像：…〕` placeholder marker in the default (text-only) output and count omitted images in report.json.

**Architecture:** Two tasks in `makeitdown/` only. Task 1 adds a pure `_mark_images` helper (unit-tested) alongside the existing `_strip_images`. Task 2 switches the default pipeline path to `_mark_images`, adds an `images_omitted` report counter, removes the now-dead `_strip_images`, and updates the affected pipeline tests + SKILL.md. No vision LLM, no upload; rag-retriever untouched.

**Tech Stack:** Python 3.11, pytest. Run tests with `makeitdown/.venv/Scripts/python.exe -m pytest` from the `makeitdown/` directory.

## Global Constraints

- Zero dependency, zero upload, zero fabrication: no model calls, no network. The marker states only "an image existed here + its filename," never image content.
- Default behavior change is limited to replacing silent deletion with a marker + counter. `--keep-images` behavior must remain byte-for-byte unchanged (keeps `![]()`/`<img>` refs and writes image files; `images_omitted` stays 0).
- Marker format: `〔图像：<name> —— 已省略未保留，请查原件〕`. Keyword is `图像` (not `来源`) so lawiki's anchor lint never treats it as a source anchor.
- `<name>` precedence: basename of the image path → else the alt text → else `未命名`.
- No `quality: suspect` change for omitted images.
- Work on branch `feat/absorb-nexusrag-batch2`. Run all commands from `makeitdown/`.

---

### Task 1: Add the `_mark_images` helper (pure function + unit tests)

**Files:**
- Modify: `makeitdown/src/makeitdown/pipeline.py` (add helper near `_strip_images` at :25-41; leave `_strip_images` in place for now)
- Test: `makeitdown/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `re` (already imported at pipeline.py:2).
- Produces: `_mark_images(text: str) -> tuple[str, int]` — returns (marked_text, n_images_marked). Also module-level helpers `_image_marker(name: str) -> str` and `_basename_or(path: str, alt: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Add to `makeitdown/tests/test_pipeline.py`:

```python
def test_mark_images_helper():
    from makeitdown.pipeline import _mark_images
    t = ('正文 <img src="imgs/seal.jpg" alt="Image"> 中间 ![cap](pic.png) 末尾 '
         '<div style="text-align: center;"><table>keep</table></div>')
    out, n = _mark_images(t)
    assert "<img" not in out
    assert "![" not in out
    assert "imgs/seal.jpg" not in out            # full path gone
    assert "〔图像：seal.jpg" in out               # html <img> -> marker by basename
    assert "〔图像：pic.png" in out                # md ![]() -> marker by basename
    assert "<table>keep</table>" in out           # table content preserved
    assert n == 2


def test_mark_images_falls_back_to_alt_then_placeholder():
    from makeitdown.pipeline import _mark_images
    out1, n1 = _mark_images("![说明]()")           # alt present, no path
    assert "〔图像：说明" in out1 and n1 == 1
    out2, n2 = _mark_images("<img>")               # no src attribute
    assert "〔图像：未命名" in out2 and n2 == 1


def test_mark_images_collapses_genuinely_empty_div():
    from makeitdown.pipeline import _mark_images
    out, n = _mark_images('<div style="x"></div>正文')   # empty for non-image reasons
    assert "<div" not in out and n == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pipeline.py -k mark_images -v`
Expected: FAIL with `ImportError`/`AttributeError` — `_mark_images` does not exist yet.

- [ ] **Step 3: Implement the helper**

In `makeitdown/src/makeitdown/pipeline.py`, keep the existing `_IMG_HTML_RE`/`_IMG_MD_RE`/`_EMPTY_DIV_RE` and `_strip_images`, but change `_IMG_MD_RE` to capture groups and add the new helpers. Replace the block at lines 25-41 with:

```python
_IMG_HTML_RE = re.compile(r"<img\b[^>]*?>", re.IGNORECASE)
_IMG_MD_RE = re.compile(r"!\[([^\]]*)\]\(([^)]*)\)")
_IMG_SRC_RE = re.compile(r"""src\s*=\s*["']([^"']*)["']""", re.IGNORECASE)
_EMPTY_DIV_RE = re.compile(r"<div\b[^>]*>\s*</div>", re.IGNORECASE)


def _strip_images(text: str) -> str:
    """Remove image references (HTML <img> and markdown ![]()) and collapse any
    wrapper <div> left empty as a result. Text-only output for LLM ingestion;
    table-wrapping divs keep their content and are preserved.
    """
    text = _IMG_HTML_RE.sub("", text)
    text = _IMG_MD_RE.sub("", text)
    prev = None
    while prev != text:
        prev = text
        text = _EMPTY_DIV_RE.sub("", text)
    return text


def _image_marker(name: str) -> str:
    return f"〔图像：{name} —— 已省略未保留，请查原件〕"


def _basename_or(path: str, alt: str) -> str:
    """Filename handle for the marker: basename of path, else alt, else 未命名."""
    if path:
        base = path.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if base:
            return base
    alt = (alt or "").strip()
    return alt or "未命名"


def _mark_images(text: str) -> tuple[str, int]:
    """Replace image references with a traceable placeholder marker instead of
    deleting them, so _md records that an image existed (and its filename) even
    when the bytes are not kept. Returns (marked_text, n_marked)."""
    count = 0

    def _md_sub(m: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        return _image_marker(_basename_or(m.group(2), m.group(1)))

    def _html_sub(m: "re.Match[str]") -> str:
        nonlocal count
        count += 1
        src_m = _IMG_SRC_RE.search(m.group(0))
        return _image_marker(_basename_or(src_m.group(1) if src_m else "", ""))

    text = _IMG_MD_RE.sub(_md_sub, text)
    text = _IMG_HTML_RE.sub(_html_sub, text)
    prev = None
    while prev != text:
        prev = text
        text = _EMPTY_DIV_RE.sub("", text)
    return text, count
```

Note: `_IMG_MD_RE` now has capture groups; `_strip_images` still works because `.sub("", text)` ignores groups. The existing `test_strip_images_helper` therefore stays green in this task.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pipeline.py -v`
Expected: PASS — the three new `mark_images` tests and all pre-existing tests (including `test_strip_images_helper`) pass.

- [ ] **Step 5: Commit**

```bash
git add makeitdown/src/makeitdown/pipeline.py makeitdown/tests/test_pipeline.py
git commit -m "feat(makeitdown): add _mark_images placeholder helper

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire markers into the default path + report counter; retire `_strip_images`

**Files:**
- Modify: `makeitdown/src/makeitdown/pipeline.py` (`convert_tree` report dict ~:104-114; `handle()` returns ~:147-188; the `if not keep_images` block :174-176; the aggregation loop :190-201; remove `_strip_images` added-back-in-Task-1)
- Modify: `makeitdown/skill/makeitdown/SKILL.md` (the `--keep-images` line under "Common options", ~:127)
- Test: `makeitdown/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `_mark_images(text) -> tuple[str, int]` from Task 1.
- Produces: `convert_tree(...)` report dict now contains an `"images_omitted"` int key. `handle()`'s internal return tuple gains a 5th element (images omitted for that file).

- [ ] **Step 1: Update the tests to the new behavior (RED)**

In `makeitdown/tests/test_pipeline.py`:

(a) Delete `test_strip_images_helper` entirely (the function is being removed).

(b) Replace `test_images_stripped_by_default` with:

```python
def test_images_marked_by_default(tmp_path, monkeypatch):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.docx").write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    text = "正文内容很长很长很长" * 5 + '\n\n<div style="text-align: center;"><img src="imgs/seal.jpg"></div>'
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text=text, engine="markitdown",
                                                   assets={"imgs/seal.jpg": b"JPG"}))

    report = pl.convert_tree(src, out, ocr_engine="auto", ocr_model="PP-StructureV3",
                             cloud_token=None, workers=1, skip_existing=False,
                             text_threshold=50, report_path=out / "report.json")
    md = (out / "a.md").read_text(encoding="utf-8")
    assert "<img" not in md and "imgs/seal.jpg" not in md   # ref + full path gone
    assert "〔图像：seal.jpg" in md                          # placeholder marker left
    assert not (out / "imgs" / "seal.jpg").exists()          # bytes still not written
    assert report["images_omitted"] == 1
    saved = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert saved["images_omitted"] == 1
```

(c) In `test_keep_images_preserves_assets`, capture the report and assert the counter stays 0. Change the `pl.convert_tree(...)` call to `report = pl.convert_tree(...)` and add after the existing assertions:

```python
    assert report["images_omitted"] == 0          # keep-images path does not mark/omit
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pipeline.py -k "images_marked or keep_images" -v`
Expected: FAIL — `report["images_omitted"]` raises `KeyError` (counter not added yet); the marker assertion also fails.

- [ ] **Step 3: Add the report counter and switch the default path to `_mark_images`**

In `makeitdown/src/makeitdown/pipeline.py`:

(a) Add the counter to the report dict initializer (after `"skipped_unsupported": 0,`):

```python
        "images_omitted": 0,
```

(b) Replace the `if not keep_images:` block (currently at :174-176) with:

```python
            n_omitted = 0
            if not keep_images:
                result.text, n_omitted = _mark_images(result.text)
                result.assets = {}
```

(c) Add a 5th element (images omitted) to every `handle()` return. The success/warned returns use `n_omitted`; all others use `0`:

```python
        if skip_existing and _is_up_to_date(src, out_md):
            return ("skipped_existing", rel, None, False, 0)
        route = classify(src, text_threshold=text_threshold)
        if route == "unsupported":
            return ("skipped_unsupported", rel, None, False, 0)
```

```python
            if reasons:
                return ("warned", rel, reasons, structured_ok, n_omitted)
            return ("succeeded", rel, None, structured_ok, n_omitted)
        except LegacyConversionUnavailable as e:
            return ("skipped_unsupported", rel, str(e), False, 0)
        except Exception as e:  # never abort the batch
            return ("failed", rel, f"{type(e).__name__}: {e}", False, 0)
```

(d) Update the aggregation loop (currently :191-193) to unpack 5 and accumulate:

```python
        for future in as_completed(pool.submit(handle, src) for src in files):
            status, rel, detail, structured, images_omitted = future.result()
            report[status] += 1
            report["images_omitted"] += images_omitted
```

(e) Remove the `_strip_images` function (the definition re-stated in Task 1) — it now has no callers. Leave `_IMG_HTML_RE`, `_IMG_MD_RE`, `_IMG_SRC_RE`, `_EMPTY_DIV_RE`, `_image_marker`, `_basename_or`, `_mark_images` in place.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_pipeline.py -v`
Expected: PASS — new/updated tests pass; no reference to the removed `_strip_images` remains.

- [ ] **Step 5: Update SKILL.md**

In `makeitdown/skill/makeitdown/SKILL.md`, change the `--keep-images` bullet under "Common options" (~:127) to:

```markdown
- `--keep-images` — extract image files from scans and keep standard `![]()`
  references (default: text-only, but each image now leaves a `〔图像：文件名〕`
  placeholder marker recording that an image existed at that spot — never
  silently dropped; `report.json` reports `images_omitted`).
```

- [ ] **Step 6: Run the whole makeitdown suite**

Run: `.venv\Scripts\python.exe -m pytest`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add makeitdown/src/makeitdown/pipeline.py makeitdown/tests/test_pipeline.py makeitdown/skill/makeitdown/SKILL.md
git commit -m "feat(makeitdown): mark omitted images with a traceable placeholder + count them

Default path no longer silently drops images: leaves 〔图像：…〕 markers and
reports images_omitted. --keep-images unchanged. Removes _strip_images.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- Run all commands from the `makeitdown/` directory; tests use `makeitdown/.venv/Scripts/python.exe -m pytest`.
- Tasks are ordered: Task 1 adds `_mark_images` while leaving `_strip_images` working (tree stays green); Task 2 flips the caller and removes `_strip_images`. Do not remove `_strip_images` in Task 1.
- The marker deliberately contains only the filename basename, so `test_images_marked_by_default` asserts the full path `imgs/seal.jpg` is absent but basename `seal.jpg` is present inside the marker.
