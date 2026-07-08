# Offline Embedding Bundle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a second, offline-ready release bundle (`…-offline.zip`, embedding ONNX + tiktoken vendored in) alongside the source-only bundle, so the v1.1.2 release offers both.

**Architecture:** Commit the already-written offline-load WIP (rag-retriever), give `build_bundle.py` a `--offline` mode, and make `release.yml` build + attach both zips. Offline loading itself is already implemented in the WIP; this plan finishes packaging + release plumbing + tests + docs.

**Tech Stack:** Python 3.12, hatchling wheel build, fastembed (ONNX embeddings), tiktoken, GitHub Actions, pytest (rag-retriever) + stdlib unittest (build_bundle).

## Global Constraints

- Two artifacts per release: `anydocsmarked-v<ver>.zip` (source only) and `anydocsmarked-v<ver>-offline.zip` (source + vendored `rag_retriever/_models/**` + `rag_retriever/_tiktoken/**`).
- Vendored scope is embedding (`BAAI/bge-small-zh-v1.5`) + tiktoken ONLY. The opt-in reranker is NOT vendored (documented limitation).
- Commit ONLY the offline-related WIP files (`embed.py`, `chunk.py`, `pyproject.toml`, `.gitignore`, `scripts/fetch_bundled_model.py`) — the working tree has other unrelated uncommitted changes that MUST stay uncommitted.
- Tests must not download a real model: use monkeypatched fastembed and a tiny fake `_models` file.
- `--offline` with no vendored `_models` present must fail loudly (non-zero exit), never silently produce an empty "offline" bundle.
- Work on branch `feat/offline-embedding-bundle`. rag-retriever tests: `rag-retriever/.venv/Scripts/python.exe -m pytest`. build_bundle tests: `python lawiki/scripts/test_build_bundle.py -v` (stdlib, no pytest).

---

### Task 1: Commit the offline-load WIP + characterization test

**Files:**
- Commit (already written in working tree): `rag-retriever/rag_retriever/embed.py`, `rag-retriever/rag_retriever/chunk.py`, `rag-retriever/pyproject.toml`, `rag-retriever/.gitignore`, `rag-retriever/scripts/fetch_bundled_model.py`
- Create: `rag-retriever/tests/test_embed_offline.py`

**Interfaces:**
- Consumes (already present in the WIP `embed.py`): `LocalEmbedder(model_name: str, model_path: str | None = None)`, module attribute `_BUNDLED_MODELS_DIR`, and `_bundled_model_dir(model_name) -> Path`.
- Produces: nothing new for later tasks (this task just lands the WIP + a test).

**Note:** the implementation already exists (uncommitted WIP). There is no RED phase for the production code; the new test is a characterization test that exercises both the vendored-load branch and the download branch via a fake `TextEmbedding`, then the WIP is committed alongside it.

- [ ] **Step 1: Write the test**

Create `rag-retriever/tests/test_embed_offline.py`:

```python
import fastembed


class _FakeTE:
    """Records the kwargs LocalEmbedder passes to fastembed.TextEmbedding."""
    last_kwargs: dict = {}

    @staticmethod
    def list_supported_models():
        return [{"model": "BAAI/bge-small-zh-v1.5"}]

    def __init__(self, **kwargs):
        _FakeTE.last_kwargs = dict(kwargs)


def test_local_embedder_offline_when_model_path_given(monkeypatch, tmp_path):
    monkeypatch.setattr(fastembed, "TextEmbedding", _FakeTE)
    from rag_retriever.embed import LocalEmbedder
    LocalEmbedder("BAAI/bge-small-zh-v1.5", model_path=str(tmp_path))  # tmp_path exists
    assert _FakeTE.last_kwargs.get("specific_model_path") == str(tmp_path)
    assert _FakeTE.last_kwargs.get("local_files_only") is True


def test_local_embedder_uses_bundled_dir_when_present(monkeypatch, tmp_path):
    import rag_retriever.embed as e
    monkeypatch.setattr(fastembed, "TextEmbedding", _FakeTE)
    vendored = tmp_path / "BAAI--bge-small-zh-v1.5"
    vendored.mkdir()
    monkeypatch.setattr(e, "_BUNDLED_MODELS_DIR", tmp_path)
    e.LocalEmbedder("BAAI/bge-small-zh-v1.5")  # no model_path -> resolves bundled dir
    assert _FakeTE.last_kwargs.get("specific_model_path") == str(vendored)
    assert _FakeTE.last_kwargs.get("local_files_only") is True


def test_local_embedder_downloads_when_no_vendored(monkeypatch, tmp_path):
    import rag_retriever.embed as e
    monkeypatch.setattr(fastembed, "TextEmbedding", _FakeTE)
    monkeypatch.setattr(e, "_BUNDLED_MODELS_DIR", tmp_path / "nonexistent")
    e.LocalEmbedder("BAAI/bge-small-zh-v1.5")  # no vendored dir -> download branch
    assert "specific_model_path" not in _FakeTE.last_kwargs
    assert _FakeTE.last_kwargs.get("model_name") == "BAAI/bge-small-zh-v1.5"
```

- [ ] **Step 2: Run the test against the working-tree WIP**

Run: `rag-retriever/.venv/Scripts/python.exe -m pytest tests/test_embed_offline.py -v` (from `rag-retriever/`)
Expected: PASS — the WIP `embed.py` already implements the vendored/download branches. (If any test fails, the WIP is inconsistent with the spec — STOP and report; do not "fix" embed.py blindly.)

- [ ] **Step 3: Run the full rag-retriever suite to confirm the WIP breaks nothing**

Run: `rag-retriever/.venv/Scripts/python.exe -m pytest -q` (from `rag-retriever/`)
Expected: PASS (all pre-existing tests + the 3 new ones).

- [ ] **Step 4: Commit the WIP + test (only these files)**

```bash
git add rag-retriever/rag_retriever/embed.py rag-retriever/rag_retriever/chunk.py \
        rag-retriever/pyproject.toml rag-retriever/.gitignore \
        rag-retriever/scripts/fetch_bundled_model.py \
        rag-retriever/tests/test_embed_offline.py
git commit -m "feat(rag): offline embedding + tiktoken vendoring (bundled-load path)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

Do NOT `git add -A` — other unrelated working-tree changes must stay uncommitted. Verify with `git status` that only the six intended files were committed.

---

### Task 2: `build_bundle.py` — default-exclude vendored assets + `--offline` mode

**Files:**
- Modify: `lawiki/scripts/build_bundle.py`
- Create: `lawiki/scripts/test_build_bundle.py`

**⚠ Working-tree starting state (reconcile, do not collide):** `build_bundle.py`
already has uncommitted WIP that took a *different, now-superseded* approach —
(a) a docstring paragraph claiming the embedding ONNX is shipped **unconditionally**,
and (b) a post-vendor-copy block in `main()` that just *prints* whether `_models`
was included. The approved design replaces that with the two-mode (`--offline`)
approach below. So in addition to the edits in Step 3, you MUST:
- **Remove** the WIP's post-copy guard-print block in `main()` (the `model_dir = root / "vendor" / ...` / `print("✓ 已含..." / "⚠ 包内无...")` lines). The new pre-copy `--offline` guard supersedes it.
- **Fix** the WIP docstring paragraph so it describes two bundles: the plain bundle is source-only (models excluded); `--offline` ships the vendored embedding ONNX. Do not leave the "本地 embedding 的 ONNX 会随包发出" (unconditional) wording.
The final committed file must match the two-mode design — the WIP is evolving into it, nothing is "kept" from the superseded guard-print.

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces (module-level, testable helpers):
  - `_VENDORED = {"_models", "_tiktoken"}`
  - `_make_ignore(offline: bool) -> callable` — an `shutil.copytree` ignore function.
  - `_zip_name(version: str, offline: bool) -> str`
  - `_has_vendored_models(rag_src: Path) -> bool`
  - `main(argv)` accepts `--offline`.

- [ ] **Step 1: Write the failing tests**

Create `lawiki/scripts/test_build_bundle.py`:

```python
# -*- coding: utf-8 -*-
"""build_bundle 回归测试（stdlib unittest，零依赖）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_bundle  # noqa: E402


class IgnoreTests(unittest.TestCase):
    def test_excludes_vendored_by_default(self):
        ig = build_bundle._make_ignore(offline=False)
        out = ig("d", ["_models", "_tiktoken", "foo.py"])
        self.assertIn("_models", out)
        self.assertIn("_tiktoken", out)
        self.assertNotIn("foo.py", out)

    def test_keeps_vendored_when_offline(self):
        ig = build_bundle._make_ignore(offline=True)
        out = ig("d", ["_models", "_tiktoken", "foo.py"])
        self.assertNotIn("_models", out)
        self.assertNotIn("_tiktoken", out)
        # normal junk still excluded in both modes
        self.assertIn("__pycache__", ig("d", ["__pycache__"]))
        self.assertIn("x.pyc", ig("d", ["x.pyc"]))


class NameTests(unittest.TestCase):
    def test_zip_name(self):
        self.assertEqual(build_bundle._zip_name("1.1.2", False),
                         "anydocsmarked-v1.1.2.zip")
        self.assertEqual(build_bundle._zip_name("1.1.2", True),
                         "anydocsmarked-v1.1.2-offline.zip")


class VendoredCheckTests(unittest.TestCase):
    def test_absent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(build_bundle._has_vendored_models(Path(d)))

    def test_present(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "rag_retriever" / "_models" / "BAAI--x"
            m.mkdir(parents=True)
            (m / "model.onnx").write_bytes(b"x")
            self.assertTrue(build_bundle._has_vendored_models(Path(d)))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python lawiki/scripts/test_build_bundle.py -v`
Expected: FAIL with `AttributeError: module 'build_bundle' has no attribute '_make_ignore'` (helpers don't exist yet).

- [ ] **Step 3: Refactor build_bundle.py to add the helpers + `--offline`**

In `lawiki/scripts/build_bundle.py`, replace the existing `_ignore` / `_copy_tree` block with:

```python
# Vendored offline assets (embedding ONNX + tiktoken BPE). Excluded from the
# plain bundle; included only when building the --offline bundle.
_VENDORED = {"_models", "_tiktoken"}


def _make_ignore(offline: bool):
    exclude = _EXCLUDE if offline else (_EXCLUDE | _VENDORED)

    def _ignore(_dir: str, names: list[str]) -> set:
        out = set()
        for n in names:
            if n in exclude or any(n.endswith(s) for s in _EXCLUDE_SUFFIX) or n.endswith(".egg-info"):
                out.add(n)
        return out

    return _ignore


def _copy_tree(src: Path, dst: Path, offline: bool) -> None:
    if not src.is_dir():
        sys.exit(f"找不到源目录：{src}")
    shutil.copytree(src, dst, ignore=_make_ignore(offline))


def _zip_name(version: str, offline: bool) -> str:
    return f"anydocsmarked-v{version}{'-offline' if offline else ''}.zip"


def _has_vendored_models(rag_src: Path) -> bool:
    d = rag_src / "rag_retriever" / "_models"
    return d.is_dir() and any(f.is_file() for f in d.rglob("*"))
```

Then in `main`, after parsing args, add the `--offline` argument and wire everything:

```python
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--offline", action="store_true",
                    help="include vendored embedding/tiktoken assets; names the zip -offline")
    args = ap.parse_args(argv[1:])

    if args.offline and not _has_vendored_models(RAG_SRC):
        sys.exit("--offline 需要先运行 rag-retriever/scripts/fetch_bundled_model.py 生成 _models/")

    out_zip = LAWIKI / "dist" / _zip_name(args.version, args.offline)
```

And update the three `_copy_tree(...)` calls to pass `args.offline`:

```python
        _copy_tree(LAWIKI / "skill" / "lawiki", root / "skill" / "lawiki", args.offline)
        _copy_tree(RAG_SRC, root / "vendor" / "rag-retriever", args.offline)
        _copy_tree(MD_SRC, root / "vendor" / "makeitdown", args.offline)
```

(Leave MANIFEST/README/zip-writing logic unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python lawiki/scripts/test_build_bundle.py -v`
Expected: PASS — all Ignore/Name/VendoredCheck tests pass.

- [ ] **Step 5: Smoke-build the plain bundle (no vendored assets present)**

Run: `python lawiki/scripts/build_bundle.py --version 0.0.0-test`
Expected: writes `lawiki/dist/anydocsmarked-v0.0.0-test.zip`; no error. Then delete it: `rm lawiki/dist/anydocsmarked-v0.0.0-test.zip`.
Also confirm the guard: `python lawiki/scripts/build_bundle.py --version 0.0.0-test --offline` should exit non-zero with the `_models/` message (no vendored assets locally).

- [ ] **Step 6: Commit**

```bash
git add lawiki/scripts/build_bundle.py lawiki/scripts/test_build_bundle.py
git commit -m "feat(bundle): build_bundle --offline mode (vendored assets + naming + guard)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `release.yml` — build & attach both bundles

**Files:**
- Modify: `.github/workflows/release.yml`

**Interfaces:**
- Consumes: `build_bundle.py --offline` (Task 2), `scripts/fetch_bundled_model.py` (Task 1).
- Produces: a release with both zips. No unit test (CI YAML); the verify step below is the in-workflow gate.

- [ ] **Step 1: Replace the workflow body**

Overwrite `.github/workflows/release.yml` with:

```yaml
name: release

# Publish BOTH downloadable bundles to a GitHub Release when a version tag is
# pushed, e.g.:  git tag v1.1.2 && git push origin v1.1.2
#   - anydocsmarked-v<ver>.zip          (source only, ~500KB)
#   - anydocsmarked-v<ver>-offline.zip  (+ vendored embedding ONNX + tiktoken)
on:
  push:
    tags: ["v*"]

permissions:
  contents: write

jobs:
  bundle:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0  # build_bundle.py records each component's commit hash
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      # 1) Source-only bundle FIRST, before any vendored assets exist.
      - name: Build source bundle
        run: python lawiki/scripts/build_bundle.py --version "${GITHUB_REF_NAME#v}"

      # 2) Vendor the offline assets (embedding ONNX + tiktoken BPE) into the
      #    rag-retriever source tree. CI can reach HuggingFace/OpenAI.
      - name: Vendor offline embedding + tiktoken assets
        run: |
          pip install ./rag-retriever
          python rag-retriever/scripts/fetch_bundled_model.py

      # 3) Offline bundle (now the vendored assets are present).
      - name: Build offline bundle
        run: python lawiki/scripts/build_bundle.py --version "${GITHUB_REF_NAME#v}" --offline

      # 4) Fail the release if the offline bundle didn't actually capture the model.
      - name: Verify offline bundle contains the model
        run: |
          ver="${GITHUB_REF_NAME#v}"
          zip="lawiki/dist/anydocsmarked-v${ver}-offline.zip"
          unzip -l "$zip" | grep -Eq "vendor/rag-retriever/rag_retriever/_models/.*\.onnx" \
            || { echo "::error::offline bundle is missing the .onnx model"; exit 1; }

      # 5) Publish the release with BOTH bundles attached.
      - name: Publish release with both bundles
        uses: softprops/action-gh-release@v2
        with:
          files: |
            lawiki/dist/anydocsmarked-*.zip
          generate_release_notes: true
```

- [ ] **Step 2: Lint the YAML locally**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/release.yml',encoding='utf-8')); print('yaml ok')"`
Expected: `yaml ok` (if PyYAML isn't installed, skip — the workflow is validated by GitHub on push instead).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/release.yml
git commit -m "ci(release): build and attach both source and offline bundles

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Docs — installer hint + two-editions note

**Files:**
- Modify: `lawiki/install.py`
- Modify: `lawiki/skill/lawiki/references/setup.md`
- Modify: `README.md` (repo root)

**Interfaces:**
- Consumes: the bundle layout (`vendor/rag-retriever/rag_retriever/_models` present only in the offline bundle).
- Produces: user-facing guidance. No automated test (prints + markdown); verified by review.

- [ ] **Step 1: Add the installer hint**

In `lawiki/install.py`, in `main()`, just before the final `return 0`, add:

```python
    offline_models = VENDOR / "rag-retriever" / "rag_retriever" / "_models"
    if offline_models.is_dir():
        _say("✓ 离线包：已内置 embedding 模型，首次建索引无需联网下载。")
    else:
        _say("提示：本包首次建索引会联网下载 embedding 模型（bge-small-zh-v1.5，境外 HuggingFace）。")
        _say("  国内如慢：设 HF_ENDPOINT=https://hf-mirror.com，或改用 -offline 版发布包。")
```

- [ ] **Step 2: Run install.py --dry-run to confirm it still works**

Run: `python lawiki/install.py --dry-run`
Expected: exits 0; prints the summary and (since this checkout has no vendored `_models`) the "首次建索引会联网下载" hint.

- [ ] **Step 3: Add the two-editions note to setup.md**

In `lawiki/skill/lawiki/references/setup.md`, add a short subsection (place near the install/RAG section):

```markdown
## 两种发布包（Release 二选一）

- **`anydocsmarked-v<ver>-offline.zip`（离线包，推荐国内/内网）**：已内置 embedding
  模型（`bge-small-zh-v1.5`）与 tiktoken，`install.py` 装完首次建索引**无需联网**。
- **`anydocsmarked-v<ver>.zip`（源码小包）**：不含模型，首次建索引会从境外
  HuggingFace 下 embedding；国内可设 `HF_ENDPOINT=https://hf-mirror.com` 加速。
- 两者其余一致。注意：可选的**重排模型**（`RAG_RERANK=local`）两种包都不内置，
  开启时仍需联网下载。
```

- [ ] **Step 4: Add a one-line note to the root README**

In `README.md`, in the 安装 / Releases section, add a sentence: Release 提供两种包——`-offline.zip`（内置 embedding 模型、解压即离线可用）与普通源码包（首次建索引需联网下模型）；国内推荐 `-offline`。

- [ ] **Step 5: Commit**

```bash
git add lawiki/install.py lawiki/skill/lawiki/references/setup.md README.md
git commit -m "docs: explain the two release bundles (offline vs source) + installer hint

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- Task order matters: Task 1 lands the WIP the offline bundle depends on; Task 2 adds the packaging mode; Task 3 wires CI; Task 4 documents. They are independently reviewable.
- NEVER `git add -A` / `git add .` — the working tree has unrelated uncommitted changes (makeitdown/README, ocr_mineru.py, lawiki/install.py's other edits, etc.). Stage only the files each task names.
- `lawiki/install.py` and `lawiki/skill/lawiki/references/setup.md` and `README.md` may already have unrelated uncommitted edits in the working tree; when you edit them for Task 4, keep those edits intact and add yours (do not revert surrounding lines).
- The real end-to-end "does it install offline" proof happens in CI on the tag push (Task 3's verify step) and a manual smoke after release; the unit tests here deliberately avoid a 90 MB download.
- After all tasks merge, the release is cut by tagging: `git tag v1.1.2 && git push origin v1.1.2`.
