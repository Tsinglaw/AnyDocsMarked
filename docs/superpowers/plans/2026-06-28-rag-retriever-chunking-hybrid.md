# rag-retriever: Structure-Aware Chunking + Hybrid Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace naive token chunking with structure-aware chunking that carries section context, and add a BM25+vector hybrid retrieval path (RRF fusion) with an optional cross-encoder reranker — all local-first and offline by default.

**Architecture:** Workstream A adds a `Chunk` dataclass and a `chunk_document()` layer that parses markdown structure (headings → breadcrumb, atomic tables, legal-marker soft boundaries) on top of the existing proven token packer. Workstream B pre-tokenizes Chinese text with jieba into a `text_tokens` column, builds a LanceDB full-text index on it, and fuses BM25 + vector results with Reciprocal Rank Fusion at query time; an optional reranker (default off) re-orders the fused candidates.

**Tech Stack:** Python 3.12, LanceDB (embedded vector store, FTS), fastembed/ollama/openai embedders, jieba (pure-python Chinese segmentation, offline), tiktoken (token counting), pytest.

## Global Constraints

- Local-first and offline by default: the default path adds **no model download and no network**. (jieba is pure-python and offline; the reranker model is opt-in only.)
- Index-time and query-time must use the **same embedding backend + model**; this plan does not change that contract.
- No new heavyweight server dependency (no ES/Redis/etc.). Only LanceDB's embedded store.
- Backward compatibility: an index built before this change (no `text_tokens` column / no FTS index) must still work — hybrid search falls back to pure vector.
- Every config knob is overridable via environment variable, following the existing `config.py` pattern (`_env`, `_env_int`).
- All tests must run offline. Tests that would require a real embedding model use the token-counting / structure functions directly, or fakes for the store/embedder.

---

## File Structure

**rag-retriever (package `rag_retriever/`):**
- `chunk.py` — MODIFY: add `Chunk` dataclass, `chunk_document()`, section parsing, table/legal-marker splitting; keep existing `chunk_text()` as the internal token packer.
- `config.py` — MODIFY: add A/B config fields (`chunk_strategy`, `hybrid`, `rrf_k`, `hybrid_candidates`, `rerank`).
- `store.py` — MODIFY: add `text_tokens` column to schema, build FTS index, add `search_text()`, carry `heading_path` through `meta`.
- `pipeline.py` — MODIFY: call `chunk_document()`, compose breadcrumb-prefixed text, RRF fusion in `search()`, optional rerank.
- `rerank.py` — CREATE: pluggable reranker (`none`/`local`/`cloud`), default `none`.
- `tokenize.py` — CREATE: jieba-based tokenizer helper (one place, lazily imported).
- `pyproject.toml` — MODIFY: add `jieba` dependency.

**Tests (`tests/`):**
- `test_chunk.py` — MODIFY/EXTEND: structure parsing, breadcrumb, tables, legal markers, `Chunk`.
- `test_store.py` — CREATE: FTS index build, `search_text`, old-index fallback, schema.
- `test_pipeline.py` — CREATE/EXTEND: chunk_document wiring, RRF fusion, heading_path in hits, rerank=none.
- `test_config.py` — MODIFY/EXTEND: new env knobs and defaults.
- `test_rerank.py` — CREATE: `none` path returns input order; interface contract.
- `test_tokenize.py` — CREATE: jieba segmentation output shape.

---

## Task 1: `Chunk` dataclass + `chunk_document` (token strategy) + extract `_pack_units`

**Files:**
- Modify: `rag-retriever/rag_retriever/chunk.py`
- Test: `rag-retriever/tests/test_chunk.py`

**Interfaces:**
- Consumes: existing `count_tokens`, `_split_units`, `_hard_split` in `chunk.py`.
- Produces:
  - `@dataclass(frozen=True) class Chunk: text: str; heading_path: str`
  - `_pack_units(units: list[str], chunk_tokens: int, overlap: int) -> list[str]`
  - `chunk_document(text: str, chunk_tokens: int = 800, overlap: int = 100, strategy: str = "structure") -> list[Chunk]`
  - existing `chunk_text(text, chunk_tokens=800, overlap=100) -> list[str]` unchanged in signature/behavior.

- [ ] **Step 1: Write the failing test**

Add to `rag-retriever/tests/test_chunk.py`:

```python
from rag_retriever.chunk import Chunk, chunk_document


def test_chunk_document_token_strategy_wraps_chunk_text():
    # token strategy must reproduce chunk_text exactly, wrapped as Chunk with empty path.
    from rag_retriever.chunk import chunk_text
    text = "\n\n".join(f"这是第{i}段内容。" * 5 for i in range(30))
    plain = chunk_text(text, chunk_tokens=100, overlap=0)
    docs = chunk_document(text, chunk_tokens=100, overlap=0, strategy="token")
    assert [c.text for c in docs] == plain
    assert all(isinstance(c, Chunk) for c in docs)
    assert all(c.heading_path == "" for c in docs)


def test_chunk_is_frozen():
    c = Chunk(text="x", heading_path="a > b")
    import dataclasses
    assert dataclasses.is_dataclass(c)
    try:
        c.text = "y"  # frozen → should raise
        raised = False
    except dataclasses.FrozenInstanceError:
        raised = True
    assert raised
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd rag-retriever && python -m pytest tests/test_chunk.py::test_chunk_document_token_strategy_wraps_chunk_text -v`
Expected: FAIL with `ImportError: cannot import name 'Chunk'` (or `chunk_document`).

- [ ] **Step 3: Write minimal implementation**

In `rag-retriever/rag_retriever/chunk.py`, add the import and dataclass near the top (after the existing imports):

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    """A chunk plus the heading breadcrumb of the section it came from.

    `heading_path` is a " > "-joined trail like "民事判决书 > 本院认为", or "" when
    the chunk has no enclosing markdown heading.
    """

    text: str
    heading_path: str
```

Refactor the packing loop out of `chunk_text` into `_pack_units`, then make `chunk_text` call it. Replace the body of `chunk_text` (lines 68-105) with:

```python
def _pack_units(units: list[str], chunk_tokens: int, overlap: int) -> list[str]:
    """Greedily pack pre-split units into ~chunk_tokens chunks with token overlap."""
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for unit in units:
        unit_tokens = count_tokens(unit)
        if current and current_tokens + unit_tokens > chunk_tokens:
            chunks.append("\n\n".join(current))
            if overlap > 0:
                tail: list[str] = []
                tail_tokens = 0
                for prev in reversed(current):
                    t = count_tokens(prev)
                    if tail_tokens + t > overlap:
                        break
                    tail.insert(0, prev)
                    tail_tokens += t
                current = tail
                current_tokens = tail_tokens
            else:
                current = []
                current_tokens = 0
        current.append(unit)
        current_tokens += unit_tokens

    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk_text(text: str, chunk_tokens: int = 800, overlap: int = 100) -> list[str]:
    """Pack text into overlapping chunks of ~chunk_tokens tokens."""
    text = text.strip()
    if not text:
        return []
    if count_tokens(text) <= chunk_tokens:
        return [text]
    return _pack_units(_split_units(text, chunk_tokens), chunk_tokens, overlap)


def chunk_document(
    text: str, chunk_tokens: int = 800, overlap: int = 100, strategy: str = "structure"
) -> list[Chunk]:
    """Structure-aware chunking. `strategy="token"` reproduces chunk_text exactly
    (every chunk gets an empty heading_path); `strategy="structure"` is added in a
    later task. Unknown strategies fall back to token.
    """
    if strategy != "structure":
        return [Chunk(text=t, heading_path="") for t in chunk_text(text, chunk_tokens, overlap)]
    # Structure path is implemented in Task 3; until then, behave like token.
    return [Chunk(text=t, heading_path="") for t in chunk_text(text, chunk_tokens, overlap)]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_chunk.py -v`
Expected: PASS (all existing token tests + the two new ones).

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/chunk.py rag-retriever/tests/test_chunk.py
git commit -m "feat(rag): add Chunk dataclass and chunk_document token strategy"
```

---

## Task 2: Markdown section parsing → heading breadcrumb

**Files:**
- Modify: `rag-retriever/rag_retriever/chunk.py`
- Test: `rag-retriever/tests/test_chunk.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class Section: heading_path: str; body: str`
  - `parse_sections(text: str) -> list[Section]` — splits on ATX headings (`#`..`######`), building a " > " breadcrumb from the heading stack. Text before the first heading is a leading section with `heading_path == ""`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_chunk.py`:

```python
from rag_retriever.chunk import Section, parse_sections


def test_parse_sections_builds_breadcrumb():
    text = (
        "前言段落。\n\n"
        "# 民事判决书\n\n"
        "开头。\n\n"
        "## 本院认为\n\n"
        "认定段。\n\n"
        "## 判决结果\n\n"
        "如下。\n"
    )
    secs = parse_sections(text)
    paths = [s.heading_path for s in secs]
    assert paths == ["", "民事判决书", "民事判决书 > 本院认为", "民事判决书 > 判决结果"]
    assert secs[0].body.strip() == "前言段落。"
    assert "认定段" in secs[2].body


def test_parse_sections_deeper_then_shallower_resets_stack():
    text = "# A\n\n## B\n\n### C\n\ncc\n\n## D\n\ndd\n"
    paths = [s.heading_path for s in parse_sections(text)]
    # going from ### C back to ## D must drop C from the trail
    assert paths == ["A", "A > B", "A > B > C", "A > D"]


def test_parse_sections_no_headings_is_single_empty_path():
    secs = parse_sections("just flat text\n\nmore")
    assert len(secs) == 1
    assert secs[0].heading_path == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd rag-retriever && python -m pytest tests/test_chunk.py::test_parse_sections_builds_breadcrumb -v`
Expected: FAIL with `ImportError: cannot import name 'Section'`.

- [ ] **Step 3: Write minimal implementation**

In `chunk.py`, add (after the `Chunk` dataclass):

```python
@dataclass(frozen=True)
class Section:
    heading_path: str
    body: str


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def parse_sections(text: str) -> list[Section]:
    """Split markdown into sections by ATX headings, tracking a heading breadcrumb.

    The text before the first heading becomes a leading section with an empty path.
    A heading at level L resets any deeper levels on the stack, so a `## D` after a
    `### C` drops C from the trail.
    """
    lines = text.splitlines()
    stack: list[tuple[int, str]] = []  # (level, title)
    cur_path = ""
    buf: list[str] = []
    out: list[Section] = []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            out.append(Section(heading_path=cur_path, body=body))
        buf.clear()

    for line in lines:
        m = _HEADING_RE.match(line)
        if not m:
            buf.append(line)
            continue
        flush()
        level = len(m.group(1))
        title = m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        cur_path = " > ".join(t for _, t in stack)
    flush()

    if not out:
        return [Section(heading_path="", body=text.strip())]
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_chunk.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/chunk.py rag-retriever/tests/test_chunk.py
git commit -m "feat(rag): parse markdown sections into heading breadcrumbs"
```

---

## Task 3: Atomic tables + legal-marker soft boundaries, wired into `chunk_document` structure path

**Files:**
- Modify: `rag-retriever/rag_retriever/chunk.py`
- Test: `rag-retriever/tests/test_chunk.py`

**Interfaces:**
- Produces:
  - `_split_structured_units(body: str, max_tokens: int) -> list[str]` — like `_split_units` but (a) keeps a markdown table block as one unit (row-split with header repeat only if over `max_tokens`), and (b) treats legal section markers as hard paragraph boundaries.
  - `chunk_document(..., strategy="structure")` now: `parse_sections` → per section `_pack_units(_split_structured_units(body, chunk_tokens), ...)` → `Chunk(text, heading_path)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_chunk.py`:

```python
def test_structure_chunk_carries_heading_path():
    text = "# 合同\n\n## 第一条 标的\n\n货物为钢材。\n\n## 第二条 价款\n\n总价十万元。\n"
    docs = chunk_document(text, chunk_tokens=800, overlap=0, strategy="structure")
    paths = {c.heading_path for c in docs}
    assert "合同 > 第一条 标的" in paths
    assert "合同 > 第二条 价款" in paths


def test_structure_keeps_small_table_intact():
    table = "| 项目 | 金额 |\n|---|---|\n| 货款 | 50万 |\n| 利息 | 2万 |"
    text = f"# 表\n\n{table}\n"
    docs = chunk_document(text, chunk_tokens=800, overlap=0, strategy="structure")
    # the whole table lands in a single chunk, header included
    table_chunks = [c for c in docs if "项目" in c.text]
    assert len(table_chunks) == 1
    assert "货款" in table_chunks[0].text and "利息" in table_chunks[0].text


def test_structure_oversize_table_row_split_repeats_header():
    header = "| 列A | 列B |\n|---|---|"
    rows = "\n".join(f"| 行{i}内容很长很长很长 | 值{i} |" for i in range(60))
    text = f"# 大表\n\n{header}\n{rows}\n"
    docs = chunk_document(text, chunk_tokens=120, overlap=0, strategy="structure")
    table_chunks = [c for c in docs if "列A" in c.text]
    assert len(table_chunks) > 1
    # every table chunk repeats the header row
    assert all("列A" in c.text and "---" in c.text for c in table_chunks)


def test_structure_legal_marker_is_soft_boundary():
    body = "第一条 当事人应诚信。第二条 标的为钢材。第三条 价款十万元。"
    text = f"# 合同\n\n{body}\n"
    # tiny budget so each 第X条 lands separately if they are split as units
    docs = chunk_document(text, chunk_tokens=12, overlap=0, strategy="structure")
    joined = [c.text for c in docs]
    # no chunk should glue two different 第X条 markers together at this budget
    assert any("第一条" in t and "第二条" not in t for t in joined)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd rag-retriever && python -m pytest tests/test_chunk.py::test_structure_keeps_small_table_intact -v`
Expected: FAIL (structure path currently falls back to token; table may be split / heading_path empty).

- [ ] **Step 3: Write minimal implementation**

In `chunk.py`, add the table + legal-marker helpers and replace the structure branch of `chunk_document`:

```python
# Legal section markers used as soft (preferred) split points when a section has
# no finer markdown structure. Order-independent; matched at unit-splitting time.
_LEGAL_MARKERS = re.compile(
    r"(第[一二三四五六七八九十百零\d]+条"
    r"|^[（(][一二三四五六七八九十]+[)）]"
    r"|^[一二三四五六七八九十]+、"
    r"|本院认为|审理终结|如不服本判决|事实和理由|本院查明)"
)


def _is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") or ("|" in s and set(s) <= set("|-: "))


def _split_table_block(block: str, max_tokens: int) -> list[str]:
    """Keep a markdown table whole; if it exceeds max_tokens, split by rows and
    repeat the header (first two lines: header + separator) on each piece."""
    if count_tokens(block) <= max_tokens:
        return [block]
    lines = block.splitlines()
    header = lines[:2]  # header row + separator
    body_rows = lines[2:]
    pieces: list[str] = []
    cur = list(header)
    cur_tokens = count_tokens("\n".join(cur))
    for row in body_rows:
        t = count_tokens(row)
        if len(cur) > 2 and cur_tokens + t > max_tokens:
            pieces.append("\n".join(cur))
            cur = list(header)
            cur_tokens = count_tokens("\n".join(cur))
        cur.append(row)
        cur_tokens += t
    if len(cur) > 2:
        pieces.append("\n".join(cur))
    return pieces


def _split_structured_units(body: str, max_tokens: int) -> list[str]:
    """Split a section body into units, keeping tables atomic and preferring legal
    markers as paragraph boundaries. Non-table prose falls back to _split_units."""
    units: list[str] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        if _is_table_line(lines[i]):
            j = i
            while j < len(lines) and (_is_table_line(lines[j]) or lines[j].strip() == ""):
                # stop at a blank line that ends the table
                if lines[j].strip() == "" and j > i:
                    break
                j += 1
            block = "\n".join(lines[i:j]).strip()
            if block:
                units.extend(_split_table_block(block, max_tokens))
            i = j
            continue
        # gather a prose run until the next table line
        j = i
        while j < len(lines) and not _is_table_line(lines[j]):
            j += 1
        prose = "\n".join(lines[i:j]).strip()
        if prose:
            # insert paragraph breaks before legal markers so they split cleanly
            marked = _LEGAL_MARKERS.sub(lambda m: "\n\n" + m.group(0), prose)
            units.extend(_split_units(marked, max_tokens))
        i = j
    return units
```

Replace the structure branch of `chunk_document` (the two trailing lines from Task 1) with:

```python
    sections = parse_sections(text)
    out: list[Chunk] = []
    for sec in sections:
        units = _split_structured_units(sec.body, chunk_tokens)
        for piece in _pack_units(units, chunk_tokens, overlap):
            out.append(Chunk(text=piece, heading_path=sec.heading_path))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_chunk.py -v`
Expected: PASS (all chunk tests, old and new).

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/chunk.py rag-retriever/tests/test_chunk.py
git commit -m "feat(rag): structure-aware chunking with atomic tables and legal markers"
```

---

## Task 4: Config knob + wire `chunk_document` into pipeline; store `heading_path`; prepend breadcrumb

**Files:**
- Modify: `rag-retriever/rag_retriever/config.py`, `rag-retriever/rag_retriever/pipeline.py`, `rag-retriever/rag_retriever/store.py`
- Test: `rag-retriever/tests/test_config.py`, `rag-retriever/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `chunk_document` (Task 3), `Config`.
- Produces:
  - `Config.chunk_strategy: str` (default `"structure"`, env `RAG_CHUNK_STRATEGY`).
  - `pipeline.index_file` composes stored text as breadcrumb-prefixed and stores `heading_path` in meta.
  - helper `_compose(chunk: Chunk) -> str` returning `f"{heading_path}\n\n{text}"` when path present, else `text`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
import os
from rag_retriever.config import Config


def test_chunk_strategy_defaults_to_structure(monkeypatch):
    monkeypatch.delenv("RAG_CHUNK_STRATEGY", raising=False)
    assert Config.load().chunk_strategy == "structure"


def test_chunk_strategy_env_override(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_STRATEGY", "token")
    assert Config.load().chunk_strategy == "token"
```

Create `tests/test_pipeline.py` (uses fakes so no real model/store needed):

```python
from pathlib import Path

import pytest

from rag_retriever.chunk import Chunk
from rag_retriever import pipeline as pipeline_mod
from rag_retriever.config import Config


class _FakeEmbedder:
    def embed_documents(self, texts):
        return [[float(len(t)), 0.0, 0.0] for t in texts]

    def embed_query(self, text):
        return [float(len(text)), 0.0, 0.0]


class _FakeStore:
    def __init__(self):
        self.added = None

    def delete_source(self, source):
        pass

    def add(self, source, chunks, vectors, meta=None):
        self.added = {"source": source, "chunks": chunks, "meta": meta}
        return len(chunks)

    def record_model(self, *a, **k):
        pass


def _retriever(monkeypatch, tmp_path, text, strategy="structure"):
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path, "chunk_strategy": strategy})
    r = pipeline_mod.Retriever(cfg)
    r.store = _FakeStore()
    r._embedder = _FakeEmbedder()
    monkeypatch.setattr(pipeline_mod, "extract_text", lambda p: text)
    monkeypatch.setattr(pipeline_mod, "read_frontmatter", lambda p: {})
    monkeypatch.setattr(pipeline_mod, "select_fields", lambda fm, fields: {})
    return r


def test_index_file_stores_heading_path_in_meta(monkeypatch, tmp_path):
    md = tmp_path / "case.md"
    md.write_text("# 判决书\n\n## 本院认为\n\n认定事实如下。\n", encoding="utf-8")
    r = _retriever(monkeypatch, tmp_path, md.read_text("utf-8"), strategy="structure")
    out = r.index_file(md)
    assert out["indexed"] is True
    stored = r.store.added
    # breadcrumb is prepended into the stored text
    assert any("判决书 > 本院认为" in c for c in stored["chunks"])
    # and recorded in per-chunk meta
    assert any(m.get("heading_path") == "判决书 > 本院认为" for m in stored["meta"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd rag-retriever && python -m pytest tests/test_config.py::test_chunk_strategy_defaults_to_structure tests/test_pipeline.py -v`
Expected: FAIL (`Config` has no `chunk_strategy`; `store.add` signature has no per-chunk meta list).

- [ ] **Step 3: Write minimal implementation**

In `config.py`, add the field to the dataclass (after `chunk_overlap`):

```python
    # chunking strategy: "structure" (heading/table/legal-marker aware) | "token"
    chunk_strategy: str = "structure"
```

And in `Config.load()` (inside the `return cls(...)` call), add:

```python
            chunk_strategy=_env("RAG_CHUNK_STRATEGY", "structure").lower(),
```

In `store.py`, change `add` to accept a per-chunk `metas` list and store `heading_path`. Replace the `add` method signature and row construction:

```python
    def add(
        self, source: str, chunks: list[str], vectors: list[list[float]],
        meta: dict | None = None, metas: list[dict] | None = None,
    ) -> int:
        if not chunks:
            return 0
        new_dim = len(vectors[0])
        tbl = self._table()
        if tbl is not None:
            existing = tbl.schema.field("vector").type.list_size
            if existing != new_dim:
                raise ValueError(
                    f"embedding dimension changed ({existing} -> {new_dim}): the index "
                    f"was built with a different model. Vectors of mixed dimension can't "
                    f"share a table — rebuild the index from scratch (delete the data dir "
                    f"/ .rag and re-run index) using a single embedding model."
                )
        else:
            tbl = self._table(dim=new_dim)
        base_meta = meta or {}
        rows = []
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            row_meta = dict(base_meta)
            if metas and i < len(metas):
                row_meta.update(metas[i])
            rows.append({
                "id": f"{source}::{i}", "source": source, "ord": i,
                "text": chunk, "meta": json.dumps(row_meta, ensure_ascii=False),
                "vector": vec,
            })
        tbl.add(rows)
        self._manifest[source] = len(rows)
        self._save_manifest()
        return len(rows)
```

In `pipeline.py`, update imports and `index_file`. Change the import line:

```python
from .chunk import Chunk, chunk_document
```

Add a module-level helper:

```python
def _compose(chunk: Chunk) -> str:
    """Stored/embedded text: breadcrumb-prefixed so the vector carries section context."""
    if chunk.heading_path:
        return f"{chunk.heading_path}\n\n{chunk.text}"
    return chunk.text
```

Replace the body of `index_file` from the `chunks = ...` line onward:

```python
        doc_chunks = chunk_document(
            text, self.cfg.chunk_tokens, self.cfg.chunk_overlap, self.cfg.chunk_strategy
        )
        texts = [_compose(c) for c in doc_chunks]
        metas = [{"heading_path": c.heading_path} for c in doc_chunks]
        vectors = self.embedder.embed_documents(texts)
        meta = select_fields(read_frontmatter(path), self.cfg.metadata_fields)
        self.store.delete_source(source)
        n = self.store.add(source, texts, vectors, meta=meta, metas=metas)
        return {"source": source, "indexed": True, "chunks": n}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_config.py tests/test_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/config.py rag-retriever/rag_retriever/store.py rag-retriever/rag_retriever/pipeline.py rag-retriever/tests/test_config.py rag-retriever/tests/test_pipeline.py
git commit -m "feat(rag): wire structure-aware chunking through pipeline and store"
```

---

## Task 5: jieba tokenizer helper + add `jieba` dependency

**Files:**
- Create: `rag-retriever/rag_retriever/tokenize.py`
- Modify: `rag-retriever/pyproject.toml`
- Test: `rag-retriever/tests/test_tokenize.py`

**Interfaces:**
- Produces: `tokenize_for_fts(text: str) -> str` — returns space-joined tokens (jieba for CJK runs; latin words pass through), suitable for a whitespace FTS tokenizer. Lazily imports jieba.

- [ ] **Step 1: Write the failing test**

Create `tests/test_tokenize.py`:

```python
from rag_retriever.tokenize import tokenize_for_fts


def test_tokenize_splits_chinese_into_space_separated_terms():
    out = tokenize_for_fts("表见代理与无权代理")
    # jieba segments into words; result is space-joined and contains the key terms
    terms = out.split()
    assert "表见" in out or "表见代理" in terms or "代理" in terms
    assert " " in out  # produced multiple whitespace-separated terms


def test_tokenize_preserves_latin_and_digits():
    out = tokenize_for_fts("Contract 2024 amount 500000")
    assert "2024" in out.split()
    assert "Contract" in out.split() or "contract" in out.split()


def test_tokenize_empty_is_empty():
    assert tokenize_for_fts("") == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd rag-retriever && python -m pytest tests/test_tokenize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rag_retriever.tokenize'`.

- [ ] **Step 3: Write minimal implementation**

Add `jieba` to `pyproject.toml` dependencies (next to the `lancedb` line):

```toml
    "jieba>=0.42",          # pure-python Chinese segmentation for BM25 (offline)
```

Then install it: `cd rag-retriever && uv sync` (or `pip install jieba`).

Create `rag-retriever/rag_retriever/tokenize.py`:

```python
"""Offline tokenizer for BM25/full-text search.

LanceDB's built-in tokenizers don't segment Chinese well, so we segment ourselves
with jieba (pure-python, offline) into space-separated terms and let the FTS index
use a plain whitespace tokenizer. Latin words and digits pass through unchanged.
"""

from __future__ import annotations

from functools import cache


@cache
def _jieba():
    import jieba  # lazy: importing jieba builds its dictionary

    return jieba


def tokenize_for_fts(text: str) -> str:
    """Return space-joined terms suitable for a whitespace FTS tokenizer."""
    text = text.strip()
    if not text:
        return ""
    terms = [t for t in _jieba().cut(text) if t.strip()]
    return " ".join(terms)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_tokenize.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/tokenize.py rag-retriever/tests/test_tokenize.py rag-retriever/pyproject.toml rag-retriever/uv.lock
git commit -m "feat(rag): add jieba-based offline tokenizer for BM25"
```

---

## Task 6: Store — `text_tokens` column, FTS index, `search_text()`, old-index fallback

**Files:**
- Modify: `rag-retriever/rag_retriever/store.py`
- Test: `rag-retriever/tests/test_store.py`

**Interfaces:**
- Consumes: `tokenize_for_fts` (Task 5).
- Produces:
  - schema gains a `text_tokens` column; `add()` populates it via `tokenize_for_fts`.
  - `add()` ensures an FTS index exists on `text_tokens` (built once, refreshed on add).
  - `search_text(query: str, k: int = 5) -> list[dict]` — BM25 over `text_tokens`, returns same dict shape as `search` but with `score` = BM25 relevance and a `rank` field (0-based). Returns `[]` if no FTS index / old schema.
  - `has_fts() -> bool`.

**Notes on the one flagged risk:** This task is where LanceDB FTS is first exercised. Step 2 below is a real spike — if `create_fts_index`/`query_type="fts"` differ in the installed LanceDB version, adjust the two calls accordingly (the rest of the design — pre-tokenized column + whitespace match — is version-independent).

- [ ] **Step 1: Write the failing test**

Create `tests/test_store.py`:

```python
from rag_retriever.store import VectorStore


def _add(store, source, texts):
    vecs = [[float(i), 0.0, 0.0] for i, _ in enumerate(texts)]
    store.add(source, texts, vecs, metas=[{"heading_path": ""} for _ in texts])


def test_search_text_finds_keyword(tmp_path):
    s = VectorStore(tmp_path)
    _add(s, "doc.md", ["表见代理的构成要件", "无权代理的法律后果", "合同的解除条件"])
    hits = s.search_text("表见代理", k=3)
    assert hits, "BM25 should return at least one hit"
    assert "表见代理" in hits[0]["text"]
    assert hits[0]["rank"] == 0


def test_search_text_empty_on_old_index_without_fts(tmp_path):
    # Simulate an old table without text_tokens/FTS by writing via the legacy path.
    s = VectorStore(tmp_path)
    # no add() yet → no table → search_text returns []
    assert s.search_text("anything", k=3) == []


def test_text_tokens_column_present_after_add(tmp_path):
    s = VectorStore(tmp_path)
    _add(s, "doc.md", ["合同价款五十万元"])
    assert s.has_fts() is True
```

- [ ] **Step 2: Run test to verify it fails (and spike the FTS API)**

Run: `cd rag-retriever && python -m pytest tests/test_store.py -v`
Expected: FAIL (`search_text`/`has_fts` not defined; schema has no `text_tokens`).

Before implementing, confirm the FTS API in the installed version:
Run: `cd rag-retriever && python -c "import lancedb; print(lancedb.__version__)"`
The calls used below (`tbl.create_fts_index('text_tokens', replace=True)` and `tbl.search(q, query_type='fts')`) are correct for lancedb ≥ 0.15. If this version errors, switch `create_fts_index(..., use_tantivy=True)` and re-run.

- [ ] **Step 3: Write minimal implementation**

In `store.py`, add the import at top:

```python
from .tokenize import tokenize_for_fts
```

Update the seed schema in `_table` to include `text_tokens` (change the `schema_row` dict):

```python
        schema_row = [{
            "id": "seed", "source": "", "ord": 0, "text": "",
            "text_tokens": "", "meta": "{}", "vector": [0.0] * dim,
        }]
```

In `add()`, populate `text_tokens` for each row (add the key to the row dict built in Task 4):

```python
            rows.append({
                "id": f"{source}::{i}", "source": source, "ord": i,
                "text": chunk, "text_tokens": tokenize_for_fts(chunk),
                "meta": json.dumps(row_meta, ensure_ascii=False),
                "vector": vec,
            })
```

At the end of `add()`, after `tbl.add(rows)` and before updating the manifest, (re)build the FTS index:

```python
        tbl.add(rows)
        try:
            tbl.create_fts_index("text_tokens", replace=True)
        except Exception:
            # FTS is an optimization; never fail an index write because of it.
            pass
```

Add the new methods to `VectorStore`:

```python
    def has_fts(self) -> bool:
        tbl = self._table()
        if tbl is None:
            return False
        try:
            return "text_tokens" in tbl.schema.names
        except Exception:
            return False

    def search_text(self, query: str, k: int = 5) -> list[dict]:
        """BM25 full-text search over pre-tokenized text. [] if unavailable."""
        tbl = self._table()
        if tbl is None or "text_tokens" not in tbl.schema.names:
            return []
        q = tokenize_for_fts(query)
        if not q:
            return []
        try:
            results = tbl.search(q, query_type="fts").limit(k).to_list()
        except Exception:
            return []
        out = []
        for rank, r in enumerate(results):
            try:
                metadata = json.loads(r.get("meta") or "{}")
            except (ValueError, TypeError):
                metadata = {}
            out.append({
                "source": r["source"], "ord": r["ord"], "text": r["text"],
                "score": round(float(r.get("_score", 0.0)), 4),
                "rank": rank, "metadata": metadata,
            })
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_store.py -v`
Expected: PASS. (If `test_search_text_finds_keyword` fails on the score field name, the spike in Step 2 indicates the correct field; `_score` vs `score`.)

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/store.py rag-retriever/tests/test_store.py
git commit -m "feat(rag): add BM25 full-text search over jieba-tokenized column"
```

---

## Task 7: RRF fusion in `pipeline.search` + hybrid config knobs + vector fallback

**Files:**
- Modify: `rag-retriever/rag_retriever/config.py`, `rag-retriever/rag_retriever/pipeline.py`
- Test: `rag-retriever/tests/test_config.py`, `rag-retriever/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `store.search` (vector), `store.search_text` (BM25).
- Produces:
  - `Config.hybrid: bool` (env `RAG_HYBRID`, default True), `Config.rrf_k: int` (env `RAG_RRF_K`, default 60), `Config.hybrid_candidates: int` (env `RAG_HYBRID_CANDIDATES`, default 50).
  - `pipeline._rrf_fuse(vector_hits, text_hits, rrf_k, k) -> list[dict]` — Reciprocal Rank Fusion by `(source, ord)` identity.
  - `pipeline.search()` runs both channels and fuses when `hybrid` is on and BM25 returns results; otherwise pure vector.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_config.py`:

```python
def test_hybrid_defaults(monkeypatch):
    for var in ("RAG_HYBRID", "RAG_RRF_K", "RAG_HYBRID_CANDIDATES"):
        monkeypatch.delenv(var, raising=False)
    cfg = Config.load()
    assert cfg.hybrid is True
    assert cfg.rrf_k == 60
    assert cfg.hybrid_candidates == 50


def test_hybrid_can_be_disabled(monkeypatch):
    monkeypatch.setenv("RAG_HYBRID", "0")
    assert Config.load().hybrid is False
```

Add to `tests/test_pipeline.py`:

```python
from rag_retriever.pipeline import _rrf_fuse


def test_rrf_fuse_rewards_agreement():
    # B is ranked highly by both channels → should win after fusion.
    vector = [
        {"source": "d", "ord": 1, "text": "A", "score": 0.9, "metadata": {}},
        {"source": "d", "ord": 2, "text": "B", "score": 0.8, "metadata": {}},
    ]
    text = [
        {"source": "d", "ord": 2, "text": "B", "score": 5.0, "rank": 0, "metadata": {}},
        {"source": "d", "ord": 3, "text": "C", "score": 3.0, "rank": 1, "metadata": {}},
    ]
    fused = _rrf_fuse(vector, text, rrf_k=60, k=3)
    assert fused[0]["ord"] == 2  # B appears in both → highest fused score
    ids = [(h["source"], h["ord"]) for h in fused]
    assert ids[0] == ("d", 2)


def test_search_falls_back_to_vector_when_no_fts(monkeypatch, tmp_path):
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path, "hybrid": True})
    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()

    class _S:
        def search(self, vec, k):
            return [{"source": "d", "ord": 0, "text": "hit", "score": 0.5, "metadata": {}}]
        def search_text(self, q, k):
            return []  # no FTS

    r.store = _S()
    hits = r.search("query", k=3)
    assert hits and hits[0]["text"] == "hit"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd rag-retriever && python -m pytest tests/test_config.py tests/test_pipeline.py -v`
Expected: FAIL (`Config` lacks hybrid fields; `_rrf_fuse` undefined).

- [ ] **Step 3: Write minimal implementation**

In `config.py`, add a bool env helper near `_env_int`:

```python
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
```

Add fields to the dataclass (after `chunk_strategy`):

```python
    # hybrid retrieval (BM25 + vector via RRF)
    hybrid: bool = True
    rrf_k: int = 60
    hybrid_candidates: int = 50
```

And in `Config.load()`:

```python
            hybrid=_env_bool("RAG_HYBRID", True),
            rrf_k=_env_int("RAG_RRF_K", 60),
            hybrid_candidates=_env_int("RAG_HYBRID_CANDIDATES", 50),
```

In `pipeline.py`, add the fusion helper and rewrite `search`:

```python
def _rrf_fuse(vector_hits: list[dict], text_hits: list[dict], rrf_k: int, k: int) -> list[dict]:
    """Reciprocal Rank Fusion over two ranked lists, keyed by (source, ord)."""
    scores: dict[tuple, float] = {}
    rep: dict[tuple, dict] = {}
    for ranked in (vector_hits, text_hits):
        for rank, hit in enumerate(ranked):
            key = (hit["source"], hit["ord"])
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            rep.setdefault(key, hit)
    fused = []
    for key, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        hit = dict(rep[key])
        hit["score"] = round(score, 6)
        fused.append(hit)
    return fused[:k]
```

Rewrite `Retriever.search`:

```python
    def search(self, query: str, k: int = 5) -> list[dict]:
        """Top-k relevant chunks. Hybrid (BM25+vector RRF) when enabled and FTS
        is available; otherwise pure vector. No answer generation."""
        if not query.strip():
            return []
        qvec = self.embedder.embed_query(query)
        if not self.cfg.hybrid:
            return self.store.search(qvec, k=k)
        cand = max(k, self.cfg.hybrid_candidates)
        vector_hits = self.store.search(qvec, k=cand)
        text_hits = self.store.search_text(query, k=cand)
        if not text_hits:
            return vector_hits[:k]
        return _rrf_fuse(vector_hits, text_hits, self.cfg.rrf_k, k)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_config.py tests/test_pipeline.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/config.py rag-retriever/rag_retriever/pipeline.py rag-retriever/tests/test_config.py rag-retriever/tests/test_pipeline.py
git commit -m "feat(rag): RRF hybrid retrieval with vector fallback"
```

---

## Task 8: Optional cross-encoder reranker (default off)

**Files:**
- Create: `rag-retriever/rag_retriever/rerank.py`
- Modify: `rag-retriever/rag_retriever/config.py`, `rag-retriever/rag_retriever/pipeline.py`
- Test: `rag-retriever/tests/test_rerank.py`, `rag-retriever/tests/test_config.py`

**Interfaces:**
- Produces:
  - `Config.rerank: str` (env `RAG_RERANK`, default `"none"`; values `none|local|cloud`).
  - `rerank.get_reranker(cfg) -> Reranker | None` — returns `None` for `"none"`.
  - `Reranker.rerank(query: str, hits: list[dict], k: int) -> list[dict]`.
  - `pipeline.search()` applies the reranker (when configured) to the fused candidates before truncating to `k`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_rerank.py`:

```python
from rag_retriever.config import Config
from rag_retriever.rerank import get_reranker


def test_rerank_none_returns_no_reranker(monkeypatch):
    monkeypatch.setenv("RAG_RERANK", "none")
    assert get_reranker(Config.load()) is None


def test_default_rerank_is_none(monkeypatch):
    monkeypatch.delenv("RAG_RERANK", raising=False)
    cfg = Config.load()
    assert cfg.rerank == "none"
    assert get_reranker(cfg) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd rag-retriever && python -m pytest tests/test_rerank.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rag_retriever.rerank'`.

- [ ] **Step 3: Write minimal implementation**

In `config.py`, add to the dataclass (after `hybrid_candidates`):

```python
    # optional cross-encoder rerank: "none" (default) | "local" | "cloud"
    rerank: str = "none"
```

And in `Config.load()`:

```python
            rerank=_env("RAG_RERANK", "none").lower(),
```

Create `rag-retriever/rag_retriever/rerank.py`:

```python
"""Optional cross-encoder reranking. OFF by default — the only place a model may
be loaded. `none` (default) keeps the pipeline zero-model and offline.

- local:  fastembed cross-encoder reranker (in-process, ONNX)
- cloud:  SiliconFlow/OpenAI-compatible rerank endpoint (text leaves the machine)
"""

from __future__ import annotations

from typing import Protocol

from .config import Config


class Reranker(Protocol):
    def rerank(self, query: str, hits: list[dict], k: int) -> list[dict]: ...


class LocalReranker:
    def __init__(self, model_name: str):
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        self._model = TextCrossEncoder(model_name=model_name)

    def rerank(self, query: str, hits: list[dict], k: int) -> list[dict]:
        if not hits:
            return []
        scores = list(self._model.rerank(query, [h["text"] for h in hits]))
        order = sorted(range(len(hits)), key=lambda i: scores[i], reverse=True)
        out = []
        for i in order[:k]:
            hit = dict(hits[i])
            hit["score"] = round(float(scores[i]), 6)
            out.append(hit)
        return out


def get_reranker(cfg: Config) -> Reranker | None:
    """Return a reranker, or None when reranking is disabled (the default)."""
    if cfg.rerank == "none":
        return None
    if cfg.rerank == "local":
        model = cfg.embed_model if "rerank" in cfg.embed_model.lower() else "Xenova/ms-marco-MiniLM-L-6-v2"
        return LocalReranker(model)
    if cfg.rerank == "cloud":
        raise NotImplementedError(
            "cloud rerank is reserved; configure a SiliconFlow rerank endpoint in a "
            "follow-up. Use RAG_RERANK=none or local for now."
        )
    raise ValueError(f"unknown RAG_RERANK: {cfg.rerank!r} (expected none|local|cloud)")
```

In `pipeline.py`, import and apply. Add to the import block:

```python
from .rerank import get_reranker
```

In `Retriever.__init__`, add lazy reranker state:

```python
        self._reranker = None
        self._reranker_resolved = False
```

Add a property:

```python
    @property
    def reranker(self):
        if not self._reranker_resolved:
            self._reranker = get_reranker(self.cfg)
            self._reranker_resolved = True
        return self._reranker
```

In `search`, apply the reranker before final truncation. Replace the fusion return lines:

```python
        cand = max(k, self.cfg.hybrid_candidates)
        vector_hits = self.store.search(qvec, k=cand)
        text_hits = self.store.search_text(query, k=cand)
        if text_hits:
            fused = _rrf_fuse(vector_hits, text_hits, self.cfg.rrf_k, cand)
        else:
            fused = vector_hits[:cand]
        if self.reranker is not None:
            return self.reranker.rerank(query, fused, k)
        return fused[:k]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_rerank.py tests/test_pipeline.py -v`
Expected: PASS (rerank=none path unchanged; `_rrf_fuse` now called with `cand` then truncated to `k`).

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/rerank.py rag-retriever/rag_retriever/config.py rag-retriever/rag_retriever/pipeline.py rag-retriever/tests/test_rerank.py
git commit -m "feat(rag): optional cross-encoder reranker (default off)"
```

---

## Task 9: README + full test sweep

**Files:**
- Modify: `rag-retriever/README.md`
- Test: full suite

**Interfaces:** none (docs + verification).

- [ ] **Step 1: Update README**

In `rag-retriever/README.md`, under the `.env` config table, add rows:

```markdown
| `RAG_CHUNK_STRATEGY` | `structure` (default) heading/table/legal-marker aware, or `token` for plain packing |
| `RAG_HYBRID` | `1` (default) BM25+vector RRF; `0` for pure vector |
| `RAG_RRF_K` | RRF constant (default 60) |
| `RAG_HYBRID_CANDIDATES` | per-channel candidate pool before fusion (default 50) |
| `RAG_RERANK` | `none` (default, zero-model) / `local` (fastembed cross-encoder) / `cloud` |
```

And add a short paragraph after the config table:

```markdown
### Retrieval quality

Search is **hybrid by default**: a BM25 keyword channel (Chinese segmented with
jieba, fully offline) runs alongside vector similarity and the two are merged with
Reciprocal Rank Fusion. This sharpens recall for exact legal terms (e.g. 表见代理
vs 无权代理) that pure vectors blur. Set `RAG_HYBRID=0` for pure vector. An optional
cross-encoder reranker (`RAG_RERANK=local`) further reorders results — it is the
only setting that loads a model and is off by default.

Chunking is **structure-aware by default**: documents are split along markdown
headings (each chunk carries its section breadcrumb), tables are kept intact, and
legal section markers (第X条, 本院认为, …) are preferred split points. Set
`RAG_CHUNK_STRATEGY=token` for the old plain packing.
```

- [ ] **Step 2: Run the full suite**

Run: `cd rag-retriever && python -m pytest -q`
Expected: PASS (all tests across chunk, store, pipeline, config, rerank, tokenize, plus pre-existing).

- [ ] **Step 3: Commit**

```bash
git add rag-retriever/README.md
git commit -m "docs(rag): document structure chunking, hybrid retrieval, rerank"
```

---

## Self-Review

**Spec coverage (workstreams A + B):**
- A "Markdown 结构层" → Tasks 2, 3 (parse_sections, structure path). ✓
- A "法律标记兜底层" → Task 3 (`_LEGAL_MARKERS`, soft boundaries). ✓
- A "表格不切开 / 超预算重复表头" → Task 3 (`_split_table_block`). ✓
- A "Chunk{text, heading_path}, 返回 list[Chunk]" → Tasks 1, 3. ✓
- A "面包屑前置进被嵌入文本 + 存进 meta" → Task 4 (`_compose`, `metas`). ✓
- A "RAG_CHUNK_STRATEGY, 退回 token" → Tasks 1, 4. ✓
- B "FTS 索引 + search_text" → Task 6. ✓
- B "中文分词风险 → jieba 预分词" → Tasks 5, 6 (resolved deterministically). ✓
- B "RRF 融合, 向量+BM25 各 top-N" → Task 7. ✓
- B "RAG_HYBRID/RAG_RRF_K/RAG_HYBRID_CANDIDATES" → Task 7. ✓
- B "老索引无 FTS 退回纯向量" → Tasks 6 (`search_text` → []), 7 (fallback). ✓
- B "可选 cross-encoder rerank, 默认关" → Task 8. ✓
- Backward-compat (A退token / B退向量) → Tasks 1, 7. ✓
- Tests A/B (test_chunk/test_store/test_pipeline/test_config) → all tasks. ✓
- README → Task 9. ✓

**Placeholder scan:** No TBD/TODO; every code step has runnable code. The one
external unknown (LanceDB FTS API) is handled as an explicit spike in Task 6 Step 2
with the exact fallback call, not a placeholder. ✓

**Type consistency:** `Chunk(text, heading_path)` used identically in Tasks 1/3/4;
`chunk_document(text, chunk_tokens, overlap, strategy)` signature consistent across
Tasks 1/3/4; `store.add(..., meta=, metas=)` defined in Task 4 and extended (not
re-signatured) in Task 6; `search_text` shape (with `rank`) defined in Task 6 and
consumed in Task 7 `_rrf_fuse`; `get_reranker`/`Reranker.rerank(query, hits, k)`
consistent across Task 8. ✓
