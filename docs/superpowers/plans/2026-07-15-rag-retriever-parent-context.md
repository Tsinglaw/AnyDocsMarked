# Parent-Context (small-to-big) Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Index fine-grained child chunks for retrieval precision and attach the enclosing larger parent block to each hit, so the calling agent gets precise matches with broad context — opt-in, backward compatible.

**Architecture:** Two-level structure-aware chunking inside `chunk.py` (parents per section, children split from each parent). Only children are embedded/indexed; parents live in a `parents.json` sidecar keyed by source, looked up at search time and attached as `parent_text`. Off by default; the existing single-level path is untouched.

**Tech Stack:** Python 3.12, LanceDB, tiktoken (o200k), fastembed (only when reranking); pytest. No new dependencies.

## Global Constraints

- **No new third-party dependency, no new model, no network.** Parents are plain text; all new logic must be testable offline without loading an embedder.
- **Default behavior byte-identical.** With `RAG_PARENT_CONTEXT` off (the default), every code path must produce exactly today's output. The existing `chunk_document` / `chunk_text` single-level path is not modified.
- **No table schema change.** `parent_ord` rides inside the existing per-row `meta` JSON; parents live in a sidecar, mirroring `manifest.json` / `index_meta.json`.
- **RAG stays the verification/recall layer.** `parent_text` is context only; verbatim anchors remain the upstream `_md`. Do not couple this to lawiki anchoring.
- **CI is Ubuntu + Windows.** Use `Path`, POSIX-relative sources, UTF-8 explicitly. Repo-level `ruff` must pass.
- **Frozen dataclasses.** `Config` and `Chunk` are `frozen=True`; add fields with defaults, never mutate instances.

---

### Task 1: Config fields `parent_context` / `parent_tokens`

**Files:**
- Modify: `rag-retriever/rag_retriever/config.py`
- Test: `rag-retriever/tests/test_config.py`

**Interfaces:**
- Produces: `Config.parent_context: bool` (default `False`), `Config.parent_tokens: int` (default `1600`, guaranteed `>= chunk_tokens * 2` after `load()`). Env: `RAG_PARENT_CONTEXT`, `RAG_PARENT_TOKENS`.

- [ ] **Step 1: Write the failing tests**

Add to `rag-retriever/tests/test_config.py`:

```python
def test_parent_context_defaults_off(monkeypatch):
    monkeypatch.delenv("RAG_PARENT_CONTEXT", raising=False)
    monkeypatch.delenv("RAG_PARENT_TOKENS", raising=False)
    from rag_retriever.config import Config
    cfg = Config.load()
    assert cfg.parent_context is False
    assert cfg.parent_tokens == 1600


def test_parent_context_env_on(monkeypatch):
    monkeypatch.setenv("RAG_PARENT_CONTEXT", "1")
    from rag_retriever.config import Config
    assert Config.load().parent_context is True


def test_parent_tokens_floored_to_twice_child(monkeypatch):
    # A parent must be materially larger than a child; a too-small value is floored.
    monkeypatch.setenv("RAG_CHUNK_TOKENS", "800")
    monkeypatch.setenv("RAG_PARENT_TOKENS", "500")
    from rag_retriever.config import Config
    assert Config.load().parent_tokens == 1600  # max(500, 800*2)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd rag-retriever && python -m pytest tests/test_config.py -k parent -v`
Expected: FAIL (`AttributeError: 'Config' object has no attribute 'parent_context'`).

- [ ] **Step 3: Add the fields and env parsing**

In `config.py`, add to the `Config` dataclass (after the rerank fields, keeping defaulted fields together):

```python
    # parent-context (small-to-big) retrieval: index fine child chunks, return the
    # enclosing parent block for context. Off by default (backward compatible).
    parent_context: bool = False
    parent_tokens: int = 1600
```

In `Config.load()`, replace the inline `chunk_tokens=...` argument with a local var and add the two fields. Change:

```python
        model = _env("RAG_EMBED_MODEL", _DEFAULT_MODEL[backend])
        data_dir = Path(_env("RAG_DATA_DIR", str(Path.home() / ".rag-retriever" / "data")))
        return cls(
```

to:

```python
        model = _env("RAG_EMBED_MODEL", _DEFAULT_MODEL[backend])
        data_dir = Path(_env("RAG_DATA_DIR", str(Path.home() / ".rag-retriever" / "data")))
        chunk_tokens = _env_int("RAG_CHUNK_TOKENS", _DEFAULT_CHUNK_TOKENS[backend])
        # A parent must be materially larger than a child, else small-to-big
        # degenerates into single-level chunking.
        parent_tokens = max(_env_int("RAG_PARENT_TOKENS", 1600), chunk_tokens * 2)
        return cls(
```

Then in the `cls(...)` call, change `chunk_tokens=_env_int("RAG_CHUNK_TOKENS", _DEFAULT_CHUNK_TOKENS[backend]),` to `chunk_tokens=chunk_tokens,` and add before the closing paren:

```python
            parent_context=_env_bool("RAG_PARENT_CONTEXT", False),
            parent_tokens=parent_tokens,
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_config.py -v`
Expected: PASS (all config tests, including the new ones and the untouched existing ones).

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/config.py rag-retriever/tests/test_config.py
git commit -m "feat(rag-retriever): add parent_context/parent_tokens config (off by default)"
```

---

### Task 2: `Chunk.parent_ord` + `chunk_document_hierarchical`

**Files:**
- Modify: `rag-retriever/rag_retriever/chunk.py`
- Test: `rag-retriever/tests/test_chunk.py`

**Interfaces:**
- Consumes: existing `parse_sections`, `Section`, `_split_structured_units`, `_split_units`, `_pack_units`.
- Produces:
  - `Chunk(text, heading_path, parent_ord=None)` — new optional field, default `None`.
  - `chunk_document_hierarchical(text: str, child_tokens: int = 384, overlap: int = 100, parent_tokens: int = 1600, strategy: str = "structure") -> tuple[list[Chunk], list[str]]` — returns `(children, parents)`. Every child's `parent_ord` indexes `parents`. `parents[i]` is raw parent-block text (no breadcrumb prefix). Children of one parent, concatenated with `overlap=0`, reproduce the parent modulo whitespace.

- [ ] **Step 1: Write the failing tests**

Add to `rag-retriever/tests/test_chunk.py` (add `import re` and extend the existing import line to include `chunk_document_hierarchical`):

```python
import re

from rag_retriever.chunk import chunk_document_hierarchical  # add to existing imports


def _nows(s: str) -> str:
    return re.sub(r"\s+", "", s)


def test_chunk_field_parent_ord_defaults_none():
    assert Chunk("t", "h").parent_ord is None


def test_single_level_chunks_have_no_parent_ord():
    chunks = chunk_document("# H\n\nsome body text here", 800, 100, "structure")
    assert all(c.parent_ord is None for c in chunks)


def test_hierarchical_children_have_valid_parent_ord():
    text = "\n\n".join(f"第{i}段：这是用于测试父子分块的中文内容，需要足够长以触发切分。" for i in range(20))
    children, parents = chunk_document_hierarchical(
        text, child_tokens=30, overlap=0, parent_tokens=90, strategy="structure"
    )
    assert len(parents) >= 2
    assert len(children) > len(parents)
    assert all(c.parent_ord is not None and 0 <= c.parent_ord < len(parents) for c in children)


def test_hierarchical_children_cover_their_parent():
    text = "\n\n".join(f"第{i}段：这是用于测试父子分块的中文内容，需要足够长以触发切分。" for i in range(20))
    children, parents = chunk_document_hierarchical(
        text, child_tokens=30, overlap=0, parent_tokens=90, strategy="structure"
    )
    for ord_ in range(len(parents)):
        group = [c.text for c in children if c.parent_ord == ord_]
        assert group, f"parent {ord_} has no children"
        # overlap=0 → children re-joined reproduce the parent's non-whitespace content.
        assert _nows("".join(group)) == _nows(parents[ord_])


def test_hierarchical_parent_does_not_cross_section():
    text = "# 甲节\n\n" + ("甲内容需要足够长。" * 20) + "\n\n# 乙节\n\n" + ("乙内容需要足够长。" * 20)
    children, parents = chunk_document_hierarchical(
        text, child_tokens=30, overlap=0, parent_tokens=200, strategy="structure"
    )
    for p in parents:
        assert not ("甲内容" in p and "乙内容" in p), "a parent block spanned two sections"
    paths = {c.heading_path for c in children}
    assert "甲节" in paths and "乙节" in paths


def test_hierarchical_keeps_table_atomic():
    table = "| 项目 | 金额 |\n| --- | --- |\n| 货款 | 500000 |\n| 利息 | 12000 |"
    text = "## 表\n\n" + table
    children, parents = chunk_document_hierarchical(
        text, child_tokens=200, overlap=0, parent_tokens=400, strategy="structure"
    )
    assert any("项目" in c.text and "货款" in c.text and "利息" in c.text for c in children)
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd rag-retriever && python -m pytest tests/test_chunk.py -k "parent or hierarchical or single_level" -v`
Expected: FAIL (`ImportError: cannot import name 'chunk_document_hierarchical'`).

- [ ] **Step 3: Add the field and function**

In `chunk.py`, add the field to `Chunk`:

```python
@dataclass(frozen=True)
class Chunk:
    """A chunk plus the heading breadcrumb of the section it came from.

    `heading_path` is a " > "-joined trail like "民事判决书 > 本院认为", or "" when
    the chunk has no enclosing markdown heading. `parent_ord`, when set, indexes
    the document's parent-block list (small-to-big retrieval); None for single-level.
    """

    text: str
    heading_path: str
    parent_ord: int | None = None
```

Add at the end of `chunk.py` (after `chunk_document`):

```python
def chunk_document_hierarchical(
    text: str,
    child_tokens: int = 384,
    overlap: int = 100,
    parent_tokens: int = 1600,
    strategy: str = "structure",
) -> tuple[list[Chunk], list[str]]:
    """Two-level structure-aware chunking for small-to-big retrieval.

    Within each section, pack units into large PARENT blocks (no overlap), then
    split each parent into small CHILD chunks (with overlap). Children carry
    parent_ord into the returned `parents` list. Only children are meant to be
    embedded/indexed; parents are stored for context lookup at search time.

    Returns (children, parents). `parents[i]` is raw parent text (no breadcrumb);
    the child text is composed with its breadcrumb by the pipeline, as before.
    """
    text = text.strip()
    if not text:
        return [], []
    sections = (
        parse_sections(text) if strategy == "structure" else [Section(heading_path="", body=text)]
    )
    children: list[Chunk] = []
    parents: list[str] = []
    for sec in sections:
        if strategy == "structure":
            section_units = _split_structured_units(sec.body, parent_tokens)
        else:
            section_units = _split_units(sec.body, parent_tokens)
        for parent_text in _pack_units(section_units, parent_tokens, overlap=0):
            parent_ord = len(parents)
            parents.append(parent_text)
            if strategy == "structure":
                child_units = _split_structured_units(parent_text, child_tokens)
            else:
                child_units = _split_units(parent_text, child_tokens)
            for piece in _pack_units(child_units, child_tokens, overlap):
                children.append(
                    Chunk(text=piece, heading_path=sec.heading_path, parent_ord=parent_ord)
                )
    return children, parents
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_chunk.py -v`
Expected: PASS (new tests + all existing chunk invariants).

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/chunk.py rag-retriever/tests/test_chunk.py
git commit -m "feat(rag-retriever): hierarchical (parent/child) chunker"
```

---

### Task 3: `parents.json` sidecar in the store

**Files:**
- Modify: `rag-retriever/rag_retriever/store.py`
- Test: `rag-retriever/tests/test_store.py`

**Interfaces:**
- Consumes: existing `_read_json`, `data_dir`, `delete_source`.
- Produces:
  - `VectorStore.set_parents(source: str, parents: list[str]) -> None`
  - `VectorStore.get_parent(source: str, ord: int | None) -> str | None` — `None` for `ord is None`, out-of-range, missing source, or missing sidecar.
  - `delete_source` also drops the source's parents.

- [ ] **Step 1: Write the failing tests**

Add to `rag-retriever/tests/test_store.py`:

```python
def test_parents_roundtrip(tmp_path):
    s = VectorStore(tmp_path)
    s.set_parents("doc.md", ["P0", "P1"])
    assert s.get_parent("doc.md", 0) == "P0"
    assert s.get_parent("doc.md", 1) == "P1"
    assert s.get_parent("doc.md", 2) is None       # out of range
    assert s.get_parent("doc.md", None) is None     # no parent_ord
    assert s.get_parent("missing.md", 0) is None     # unknown source


def test_parents_persist_across_instances(tmp_path):
    VectorStore(tmp_path).set_parents("doc.md", ["P0"])
    assert VectorStore(tmp_path).get_parent("doc.md", 0) == "P0"


def test_delete_source_clears_parents(tmp_path):
    s = VectorStore(tmp_path)
    s.set_parents("doc.md", ["P0"])
    s.delete_source("doc.md")
    assert s.get_parent("doc.md", 0) is None


def test_legacy_index_get_parent_is_none(tmp_path):
    # Fresh store, no parents.json written → get_parent never raises, returns None.
    assert VectorStore(tmp_path).get_parent("doc.md", 0) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd rag-retriever && python -m pytest tests/test_store.py -k parent -v`
Expected: FAIL (`AttributeError: 'VectorStore' object has no attribute 'set_parents'`).

- [ ] **Step 3: Implement the sidecar**

In `store.py` `__init__`, after the `_index_meta_path` assignment, add:

```python
        # Parent blocks for small-to-big retrieval, keyed by source and indexed by
        # parent_ord. Sidecar (not a table column) so children stay the only indexed
        # rows; empty/absent for indexes built without parent context.
        self._parents_path = data_dir / "parents.json"
        self._parents: dict[str, list[str]] = _read_json(self._parents_path, {})
```

After `_save_manifest`, add:

```python
    def _save_parents(self) -> None:
        self._parents_path.write_text(
            json.dumps(self._parents, ensure_ascii=False), "utf-8"
        )

    def set_parents(self, source: str, parents: list[str]) -> None:
        """Store (overwrite) the parent blocks for a source, indexed by parent_ord."""
        self._parents[source] = list(parents)
        self._save_parents()

    def get_parent(self, source: str, ord: int | None) -> str | None:
        """Parent block text for (source, parent_ord); None if absent/out of range."""
        if ord is None:
            return None
        blocks = self._parents.get(source)
        if blocks is None or ord < 0 or ord >= len(blocks):
            return None
        return blocks[ord]
```

Extend `delete_source` — after the manifest-pop block, add:

```python
        if self._parents.pop(source, None) is not None:
            self._save_parents()
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_store.py -v`
Expected: PASS (new parent tests + all existing store tests).

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/store.py rag-retriever/tests/test_store.py
git commit -m "feat(rag-retriever): parents.json sidecar (set/get, delete clears)"
```

---

### Task 4: Wire parent context through the pipeline

**Files:**
- Modify: `rag-retriever/rag_retriever/pipeline.py`
- Test: `rag-retriever/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `chunk_document_hierarchical` (Task 2), `store.set_parents` / `store.get_parent` (Task 3), `cfg.parent_context` / `cfg.parent_tokens` (Task 1).
- Produces: `index_file` stores children (with `parent_ord` in each meta) + parents when `parent_context`; `search` attaches `hit["parent_text"]: str | None` to every hit when `parent_context` is on (key absent when off — strictly non-breaking).

- [ ] **Step 1: Write the failing tests**

Add to `rag-retriever/tests/test_pipeline.py` (the file already defines `_FakeEmbedder`; reuse it):

```python
import re


def _real_store_retriever(monkeypatch, tmp_path, text, **overrides):
    """A Retriever with a REAL VectorStore (parents sidecar) but a fake embedder."""
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path / ".rag", **overrides})
    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()
    monkeypatch.setattr(pipeline_mod, "extract_text", lambda p: text)
    monkeypatch.setattr(pipeline_mod, "read_frontmatter", lambda p: {})
    monkeypatch.setattr(pipeline_mod, "select_fields", lambda fm, fields: {})
    return r


def test_search_attaches_parent_text_when_enabled(monkeypatch, tmp_path):
    text = "# 合同\n\n" + "\n\n".join(f"第{i}条 关于货款与违约金的约定条款。" for i in range(30))
    r = _real_store_retriever(
        monkeypatch, tmp_path, text,
        parent_context=True, parent_tokens=120, chunk_tokens=30,
        chunk_overlap=0, hybrid=False, rerank="none",
    )
    r.index_file(tmp_path / "doc.md", source_root=tmp_path)
    hits = r.search("货款 违约金", k=3)
    assert hits
    assert hits[0]["parent_text"]
    # child body (breadcrumb stripped) is contained in its parent block.
    nows = lambda s: re.sub(r"\s+", "", s)
    child_body = hits[0]["text"]
    if child_body.startswith("合同"):
        child_body = child_body[len("合同"):]
    assert nows(child_body) in nows(hits[0]["parent_text"])


def test_search_no_parent_text_when_disabled(monkeypatch, tmp_path):
    text = "# 合同\n\n" + "\n\n".join(f"第{i}条 关于货款与违约金的约定条款。" for i in range(30))
    r = _real_store_retriever(
        monkeypatch, tmp_path, text,
        parent_context=False, chunk_tokens=30, chunk_overlap=0, hybrid=False, rerank="none",
    )
    r.index_file(tmp_path / "doc.md", source_root=tmp_path)
    hits = r.search("货款 违约金", k=3)
    assert hits
    assert "parent_text" not in hits[0]  # off → key absent, strictly non-breaking


def test_index_file_parent_context_writes_ords_and_parents(monkeypatch, tmp_path):
    text = "# 合同\n\n" + "\n\n".join(f"第{i}条 关于货款与违约金的约定条款。" for i in range(30))
    r = _real_store_retriever(
        monkeypatch, tmp_path, text,
        parent_context=True, parent_tokens=120, chunk_tokens=30, chunk_overlap=0,
    )
    r.index_file(tmp_path / "doc.md", source_root=tmp_path)
    # Parents were stored, and every child carries a valid parent_ord.
    assert r.store.get_parent("doc.md", 0) is not None
    assert r.store.list_sources()[0]["source"] == "doc.md"
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd rag-retriever && python -m pytest tests/test_pipeline.py -k "parent" -v`
Expected: FAIL (`assert hits[0]["parent_text"]` → `KeyError`, since search does not yet attach it).

- [ ] **Step 3: Implement the wiring**

In `pipeline.py`, extend the imports:

```python
from .chunk import Chunk, chunk_document, chunk_document_hierarchical
```

Add a meta helper near `_compose`:

```python
def _chunk_meta(c: Chunk) -> dict:
    """Per-chunk metadata: heading breadcrumb (when present) and parent_ord (when
    the chunk came from the hierarchical path). Empty dict when neither applies —
    keeps existing metadata tests (which expect no key when absent) green."""
    m: dict = {}
    if c.heading_path:
        m["heading_path"] = c.heading_path
    if c.parent_ord is not None:
        m["parent_ord"] = c.parent_ord
    return m
```

In `index_file`, replace the chunk/metas block. Change:

```python
            doc_chunks = chunk_document(
                text, self.cfg.chunk_tokens, self.cfg.chunk_overlap, self.cfg.chunk_strategy
            )
            texts = [_compose(c) for c in doc_chunks]
            # Omit the key for headingless chunks: {} is cleaner than {"heading_path": ""}
            # and keeps existing metadata tests (which expect no key when absent) green.
            metas = [{"heading_path": c.heading_path} if c.heading_path else {} for c in doc_chunks]
            vectors = self.embedder.embed_documents(texts)
            meta = select_fields(read_frontmatter(path), self.cfg.metadata_fields)
            self.store.delete_source(source)
            n = self.store.add(source, texts, vectors, meta=meta, metas=metas)
```

to:

```python
            if self.cfg.parent_context:
                doc_chunks, parents = chunk_document_hierarchical(
                    text, self.cfg.chunk_tokens, self.cfg.chunk_overlap,
                    self.cfg.parent_tokens, self.cfg.chunk_strategy,
                )
            else:
                doc_chunks = chunk_document(
                    text, self.cfg.chunk_tokens, self.cfg.chunk_overlap, self.cfg.chunk_strategy
                )
                parents = None
            texts = [_compose(c) for c in doc_chunks]
            metas = [_chunk_meta(c) for c in doc_chunks]
            vectors = self.embedder.embed_documents(texts)
            meta = select_fields(read_frontmatter(path), self.cfg.metadata_fields)
            self.store.delete_source(source)
            n = self.store.add(source, texts, vectors, meta=meta, metas=metas)
            if parents is not None:
                self.store.set_parents(source, parents)
```

In `search`, route all return paths through a parent-attach helper. Change the tail of `search`:

```python
        if not self.cfg.hybrid:
            return self.store.search(qvec, k=k, source_prefix=sp)
        cand = max(k, self.cfg.hybrid_candidates)
        vector_hits = self.store.search(qvec, k=cand, source_prefix=sp)
        text_hits = self.store.search_text(query, k=cand, source_prefix=sp)
        if text_hits:
            fused = _rrf_fuse(vector_hits, text_hits, self.cfg.rrf_k, cand)
        else:
            fused = vector_hits[:cand]
        if self.reranker is not None:
            return self.reranker.rerank(query, fused, k)
        return fused[:k]
```

to:

```python
        if not self.cfg.hybrid:
            return self._attach_parents(self.store.search(qvec, k=k, source_prefix=sp))
        cand = max(k, self.cfg.hybrid_candidates)
        vector_hits = self.store.search(qvec, k=cand, source_prefix=sp)
        text_hits = self.store.search_text(query, k=cand, source_prefix=sp)
        if text_hits:
            fused = _rrf_fuse(vector_hits, text_hits, self.cfg.rrf_k, cand)
        else:
            fused = vector_hits[:cand]
        if self.reranker is not None:
            return self._attach_parents(self.reranker.rerank(query, fused, k))
        return self._attach_parents(fused[:k])
```

Add the helper as a method on `Retriever`:

```python
    def _attach_parents(self, hits: list[dict]) -> list[dict]:
        """Attach each hit's enclosing parent block (small-to-big) as `parent_text`.

        Only when parent context is enabled — off is a no-op so hits keep exactly
        today's shape (strictly non-breaking for existing consumers). `parent_text`
        is None for a hit whose index predates parent context (legacy sidecar-less).
        """
        if not self.cfg.parent_context:
            return hits
        for h in hits:
            ord_ = (h.get("metadata") or {}).get("parent_ord")
            h["parent_text"] = self.store.get_parent(h["source"], ord_)
        return hits
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_pipeline.py -v`
Expected: PASS (new parent tests + all existing pipeline tests, including `test_rrf_fuse_rewards_agreement` and the fallback test whose fake stores are never asked for `get_parent` because those tests keep `parent_context` off).

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/pipeline.py rag-retriever/tests/test_pipeline.py
git commit -m "feat(rag-retriever): index parents + attach parent_text at search"
```

---

### Task 5: Surface `parent_text` in CLI / MCP + document it

**Files:**
- Modify: `rag-retriever/rag_retriever/cli.py`
- Modify: `rag-retriever/rag_retriever/server.py`
- Modify: `rag-retriever/README.md`
- Test: `rag-retriever/tests/test_cli.py`

**Interfaces:**
- Consumes: `hit["parent_text"]` from Task 4.
- Produces: CLI `search --show-parent` prints the parent block under each hit; `--json` already carries `parent_text` unchanged; MCP `search` appends the parent block as context when present.

- [ ] **Step 1: Write the failing test**

Add to `rag-retriever/tests/test_cli.py` (follow the file's existing pattern for invoking `main` with argv + capsys; if it uses a helper, reuse it — otherwise use `monkeypatch.setattr(sys, "argv", [...])`):

```python
def test_search_show_parent_prints_parent_block(monkeypatch, tmp_path, capsys):
    import sys
    from rag_retriever import cli, pipeline as pm

    # Stub the retriever's search to return a hit carrying parent_text.
    def fake_search(self, query, k=5, source_prefix=None):
        return [{"source": "doc.md", "ord": 0, "text": "child", "score": 0.9,
                 "metadata": {}, "parent_text": "THE PARENT BLOCK"}]
    monkeypatch.setattr(pm.Retriever, "search", fake_search)
    monkeypatch.setattr(sys, "argv", ["rag-retriever", "search", "q", "--show-parent"])
    cli.main()
    out = capsys.readouterr().out
    assert "child" in out
    assert "THE PARENT BLOCK" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd rag-retriever && python -m pytest tests/test_cli.py -k show_parent -v`
Expected: FAIL (`--show-parent` is an unrecognized argument → SystemExit).

- [ ] **Step 3: Implement CLI flag + human output**

In `cli.py`, add the flag to the search subparser (after the `--filter` argument):

```python
    p_search.add_argument(
        "--show-parent", action="store_true",
        help="also print each hit's enclosing parent block (small-to-big context)",
    )
```

In the `search` command branch, change the human-readable loop:

```python
        for i, h in enumerate(hits, 1):
            print(f"\n[{i}] {h['source']} (chunk {h['ord']}, score {h['score']})")
            print(h["text"])
```

to:

```python
        for i, h in enumerate(hits, 1):
            print(f"\n[{i}] {h['source']} (chunk {h['ord']}, score {h['score']})")
            print(h["text"])
            if args.show_parent and h.get("parent_text"):
                print(f"--- parent ---\n{h['parent_text']}")
```

- [ ] **Step 4: Implement MCP context passthrough**

In `server.py`, change the `search` result formatting loop. Find:

```python
    for i, h in enumerate(hits, 1):
```

and the line that builds each part (`f"[{i}] source={h['source']} (chunk {h['ord']}, score {h['score']})\n{h['text']}"`). Replace that single f-string with a small block that appends the parent when present:

```python
    parts = []
    for i, h in enumerate(hits, 1):
        block = f"[{i}] source={h['source']} (chunk {h['ord']}, score {h['score']})\n{h['text']}"
        if h.get("parent_text"):
            block += f"\n[context] {h['parent_text']}"
        parts.append(block)
    return "\n\n---\n\n".join(parts)
```

(Adjust to the file's existing variable names — the existing code already builds a `parts` list joined by `"\n\n---\n\n"`; only the per-hit block changes.)

- [ ] **Step 5: Document it in the README**

In `rag-retriever/README.md`, in the environment-variable section (near `RAG_HYBRID` / `RAG_RERANK`), add:

```markdown
- `RAG_PARENT_CONTEXT` (default `false`): enable small-to-big retrieval — index
  fine-grained child chunks for precision and return each hit's enclosing parent
  block (as `parent_text`) for context. Requires a re-index to populate parents.
- `RAG_PARENT_TOKENS` (default `1600`): target size of a parent block in tokens
  (floored to `2 × RAG_CHUNK_TOKENS`). Only used when `RAG_PARENT_CONTEXT` is on.
```

Also note under the search/CLI section that `search --show-parent` prints the parent block.

- [ ] **Step 6: Run the full suite**

Run: `cd rag-retriever && python -m pytest -q`
Expected: PASS (whole suite green).

- [ ] **Step 7: Lint**

Run: `cd rag-retriever && python -m ruff check rag_retriever tests` (or the repo-level ruff invocation).
Expected: no errors.

- [ ] **Step 8: Commit**

```bash
git add rag-retriever/rag_retriever/cli.py rag-retriever/rag_retriever/server.py rag-retriever/README.md rag-retriever/tests/test_cli.py
git commit -m "feat(rag-retriever): surface parent_text in CLI (--show-parent) and MCP; document"
```

---

## Self-Review

**1. Spec coverage:**
- Config `parent_context`/`parent_tokens` + floor → Task 1. ✅
- `Chunk.parent_ord` + `chunk_document_hierarchical`, structure-aware, tables atomic, child ⊂ parent, no cross-section → Task 2. ✅
- `parents.json` sidecar, `set/get_parent`, `delete_source` clears, `parent_ord` in existing meta (no schema change) → Task 3. ✅
- `index_file` hierarchical branch + `search` attaches `parent_text` (child text unchanged, rerank on child) → Task 4. ✅
- Return-shape contract, CLI/MCP passthrough, README → Task 5. ✅
- Backward compat / default byte-identical → guarded in Task 1 (off default), Task 2 (single-level path untouched), Task 4 (`_attach_parents` no-op when off; regression tests). ✅
- Offline / no new deps → all new tests run with `_FakeEmbedder`, no model load. ✅

**Deviation from spec (intentional, stricter):** spec Section 5 described `parent_text` as always-present-`None` when off; the plan makes it **absent when off** (attach only when `cfg.parent_context`). This is strictly more non-breaking and avoids calling `get_parent` on the fake stores in existing search tests. Consumers already tolerate an absent key; when enabled, `parent_text` is present and may be `None` for legacy indexes.

**2. Placeholder scan:** No TBD/TODO; every code step shows full code; every test step shows the assertion. The one soft instruction ("adjust to the file's existing variable names" in Task 5 Step 4) is bounded by the shown target block and the grep anchor. ✅

**3. Type consistency:** `chunk_document_hierarchical(text, child_tokens, overlap, parent_tokens, strategy) -> tuple[list[Chunk], list[str]]` is defined in Task 2 and called with the same positional order in Task 4. `get_parent(source, ord)` / `set_parents(source, parents)` signatures match between Task 3 (def) and Task 4 (calls). `parent_ord` key name is consistent across chunk meta (Task 4 `_chunk_meta`), store lookup (`metadata.get("parent_ord")`), and tests. ✅
