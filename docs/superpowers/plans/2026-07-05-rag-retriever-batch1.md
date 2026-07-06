# RAG-Retriever Batch 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Absorb two NexusRAG retrieval strengths into `rag-retriever` conservatively: make the opt-in local reranker Chinese-capable, and let `search` be scoped to a source-path prefix.

**Architecture:** Two independent, additive changes. (A) is a config default swap — the reranker stays default-off; only the model that loads *when a user opts in* changes. (B) threads an optional `source_prefix` filter from the CLI/MCP surface down through `pipeline.search` into the LanceDB query as a prefilter, applied to both the vector and FTS paths before top-k/RRF/rerank.

**Tech Stack:** Python 3.12, LanceDB, fastembed (cross-encoder rerank), FastMCP, pytest.

## Global Constraints

- Default behavior must not change unless the user explicitly opts in (`RAG_RERANK=local` for A; `--filter`/`source_prefix` for B). Verified by regression tests.
- Offline / zero-model default preserved: `RAG_RERANK` default stays `none`.
- Tests must run offline — use the existing `FakeEmbedder`/`VectorStore` idioms; never download a model or hit a network.
- Surface only via CLI + MCP + JSON. No Web UI, no new heavy dependencies.
- SQL built for LanceDB `.where()` must escape user input via the existing `store._escape()`.
- Every task ends green (`pytest` from `rag-retriever/`) and is committed on branch `feat/absorb-nexusrag-batch1`.

---

### Task 1: Make the opt-in local reranker Chinese-capable (Section A)

**Files:**
- Verify: fastembed model list (command below)
- Modify: `rag-retriever/rag_retriever/config.py:104` and `:132`
- Modify: `rag-retriever/README.md` (the `RAG_RERANK` description line)
- Test: `rag-retriever/tests/test_rerank.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `Config.rerank_model` default value = `"BAAI/bge-reranker-v2-m3"`. No signature change.

**Note — verification deferred (by decision):** the reranker is default-off
(`RAG_RERANK=none`), so this model id is never loaded unless a user explicitly
opts into `RAG_RERANK=local`. We are NOT doing the live
`TextCrossEncoder.list_supported_models()` check now. If a user later opts in
and fastembed does not support `BAAI/bge-reranker-v2-m3`, the fallback is
`jinaai/jina-reranker-v2-base-multilingual` — revisit then. Task 1's tests only
exercise config parsing, so they stay fully offline.

- [ ] **Step 1: Write the failing test**

Add to `rag-retriever/tests/test_rerank.py`:

```python
def test_default_rerank_model_is_multilingual(monkeypatch):
    monkeypatch.delenv("RAG_RERANK_MODEL", raising=False)
    cfg = Config.load()
    assert cfg.rerank_model == "BAAI/bge-reranker-v2-m3"


def test_rerank_default_stays_off(monkeypatch):
    monkeypatch.delenv("RAG_RERANK", raising=False)
    assert get_reranker(Config.load()) is None
```

Ensure the file imports `Config` and `get_reranker` — the existing top of `test_rerank.py` already imports `get_reranker`; add `from rag_retriever.config import Config` if not present.

- [ ] **Step 2: Run tests to verify the first fails**

Run: `pytest tests/test_rerank.py -v`
Expected: `test_default_rerank_model_is_multilingual` FAILS (asserts `Xenova/...` != `BAAI/...`); `test_rerank_default_stays_off` PASSES.

- [ ] **Step 3: Make the change**

In `rag-retriever/rag_retriever/config.py`, line 104:

```python
    # cross-encoder model used when rerank == "local" (only loaded then).
    # Multilingual (same family as bge-m3 embeddings) so Chinese legal terms
    # rerank meaningfully; the English ms-marco default did not.
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
```

And line 132 (inside `load()`):

```python
            rerank_model=_env("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
```

- [ ] **Step 4: Update the README line**

In `rag-retriever/README.md`, the `RAG_RERANK` row/description: note that `local` now loads a multilingual cross-encoder (`BAAI/bge-reranker-v2-m3`) suitable for Chinese, and that it remains **off by default** (offline, zero-model).

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_rerank.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
git add rag-retriever/rag_retriever/config.py rag-retriever/README.md rag-retriever/tests/test_rerank.py
git commit -m "feat(rag): default opt-in reranker to multilingual bge-reranker-v2-m3

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: source-prefix filter in the store layer (Section B, part 1)

**Files:**
- Modify: `rag-retriever/rag_retriever/store.py` (add helper near `_escape` at :20; extend `search` at :144 and `search_text` at :182)
- Test: `rag-retriever/tests/test_store.py`

**Interfaces:**
- Consumes: existing `VectorStore._escape` (:20).
- Produces:
  - `VectorStore.search(self, query_vector, k=5, source_prefix=None)`
  - `VectorStore.search_text(self, query, k=5, source_prefix=None)`
  - module-level `_source_prefix_where(prefix: str) -> str`
  When `source_prefix` is falsy, both methods behave exactly as before.

- [ ] **Step 1: Write the failing tests**

Add to `rag-retriever/tests/test_store.py`:

```python
def test_search_filters_by_source_prefix(tmp_path):
    # explicit non-zero vectors — cosine similarity is undefined on zero vectors.
    s = VectorStore(tmp_path)
    s.add("caseA/合同.md", ["表见代理的构成要件"], [[1.0, 0.0, 0.0]],
          metas=[{"heading_path": ""}])
    s.add("caseB/判决.md", ["无权代理的法律后果"], [[0.0, 1.0, 0.0]],
          metas=[{"heading_path": ""}])
    hits = s.search([1.0, 0.0, 0.0], k=5, source_prefix="caseA/")
    assert hits, "expected at least one hit within caseA/"
    assert all(h["source"].startswith("caseA/") for h in hits)


def test_search_text_filters_by_source_prefix(tmp_path):
    s = VectorStore(tmp_path)
    _add(s, "caseA/合同.md", ["表见代理的构成要件"])
    _add(s, "caseB/判决.md", ["表见代理的其他表述"])
    hits = s.search_text("表见代理", k=5, source_prefix="caseB/")
    assert hits, "expected a BM25 hit within caseB/"
    assert all(h["source"].startswith("caseB/") for h in hits)


def test_search_no_prefix_unchanged(tmp_path):
    s = VectorStore(tmp_path)
    s.add("caseA/合同.md", ["表见代理"], [[1.0, 0.0, 0.0]], metas=[{"heading_path": ""}])
    s.add("caseB/判决.md", ["无权代理"], [[0.0, 1.0, 0.0]], metas=[{"heading_path": ""}])
    assert len(s.search([1.0, 0.0, 0.0], k=5)) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_store.py -v`
Expected: the two prefix tests FAIL with `TypeError: search() got an unexpected keyword argument 'source_prefix'`.

- [ ] **Step 3: Implement the filter**

In `rag-retriever/rag_retriever/store.py`, add below `_escape` (after :21):

```python
def _source_prefix_where(prefix: str) -> str:
    """SQL predicate matching rows whose `source` starts with `prefix` (literal).
    Used as a LanceDB prefilter to scope search to a case dir / doc-type subtree."""
    return f"starts_with(source, '{_escape(prefix)}')"
```

Change `search` (:144) to:

```python
    def search(self, query_vector: list[float], k: int = 5,
               source_prefix: str | None = None) -> list[dict]:
        tbl = self._table()
        if tbl is None:
            return []
        q = tbl.search(query_vector).metric("cosine")
        if source_prefix:
            q = q.where(_source_prefix_where(source_prefix), prefilter=True)
        results = q.limit(k).to_list()
```

(Leave the result-building loop below unchanged.)

Change `search_text` (:182) — after the `q = tokenize_for_fts(query)` / self-heal block, replace the `tbl.search(...)` call:

```python
        try:
            s = tbl.search(q, query_type="fts")
            if source_prefix:
                s = s.where(_source_prefix_where(source_prefix), prefilter=True)
            results = s.limit(k).to_list()
        except Exception:
            return []
```

and update its signature to:

```python
    def search_text(self, query: str, k: int = 5,
                    source_prefix: str | None = None) -> list[dict]:
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_store.py -v`
Expected: all tests PASS.
Fallback: if a prefix test fails with a DataFusion error about `starts_with` being unsupported in a filter, replace the helper body with a LIKE form that escapes wildcards:

```python
def _source_prefix_where(prefix: str) -> str:
    esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"source LIKE '{_escape(esc)}%' ESCAPE '\\'"
```

Re-run; expected PASS.

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/store.py rag-retriever/tests/test_store.py
git commit -m "feat(rag): source-prefix prefilter in store search/search_text

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: thread source_prefix through the pipeline (Section B, part 2)

**Files:**
- Modify: `rag-retriever/rag_retriever/pipeline.py:132-149` (`search`)
- Test: `rag-retriever/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `VectorStore.search(..., source_prefix=)` and `search_text(..., source_prefix=)` from Task 2.
- Produces: `Retriever.search(self, query, k=5, source_prefix=None)`. Empty/whitespace `source_prefix` is normalized to `None` (full-index search).

- [ ] **Step 1: Write the failing test + update the existing fake**

In `rag-retriever/tests/test_pipeline.py`, update the `_S` fake inside `test_search_falls_back_to_vector_when_no_fts` so its methods accept the new kwarg:

```python
    class _S:
        def search(self, vec, k, source_prefix=None):
            return [{"source": "d", "ord": 0, "text": "hit", "score": 0.5, "metadata": {}}]
        def search_text(self, q, k, source_prefix=None):
            return []  # no FTS
```

Then add a new test:

```python
def test_search_passes_source_prefix_to_store(monkeypatch, tmp_path):
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path, "hybrid": True})
    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()
    seen = {}

    class _S:
        def search(self, vec, k, source_prefix=None):
            seen["vec"] = source_prefix
            return [{"source": "caseA/x", "ord": 0, "text": "hit", "score": 0.5, "metadata": {}}]
        def search_text(self, q, k, source_prefix=None):
            seen["fts"] = source_prefix
            return []

    r.store = _S()
    r.search("query", k=3, source_prefix="caseA/")
    assert seen["vec"] == "caseA/"
    # empty prefix is normalized to None (full-index search)
    seen.clear()
    r.search("query", k=3, source_prefix="   ")
    assert seen["vec"] is None
```

- [ ] **Step 2: Run tests to verify the new one fails**

Run: `pytest tests/test_pipeline.py -v`
Expected: `test_search_passes_source_prefix_to_store` FAILS with `TypeError: search() got an unexpected keyword argument 'source_prefix'`.

- [ ] **Step 3: Implement pipeline plumbing**

Replace `Retriever.search` (`pipeline.py:132`) body with:

```python
    def search(self, query: str, k: int = 5,
               source_prefix: str | None = None) -> list[dict]:
        """Top-k relevant chunks. Hybrid (BM25+vector RRF) when enabled and FTS
        is available; otherwise pure vector. Optional source_prefix scopes the
        search to sources under that path prefix. No answer generation."""
        if not query.strip():
            return []
        sp = (source_prefix or "").strip() or None
        qvec = self.embedder.embed_query(query)
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

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: all tests PASS (including the updated fallback test).

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/pipeline.py rag-retriever/tests/test_pipeline.py
git commit -m "feat(rag): thread optional source_prefix through Retriever.search

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: expose the filter on CLI and MCP (Section B, part 3)

**Files:**
- Modify: `rag-retriever/rag_retriever/cli.py:55-61` (search parser) and `:90` (dispatch)
- Modify: `rag-retriever/rag_retriever/server.py:63-68` (`search` tool)
- Test: `rag-retriever/tests/test_cli.py`

**Interfaces:**
- Consumes: `Retriever.search(query, k, source_prefix)` from Task 3.
- Produces: CLI flag `--filter <prefix>` and MCP `search(query, k, source_prefix="")`.

- [ ] **Step 1: Write the failing CLI test**

`test_cli.py` uses a recording `FakeRetriever` (monkeypatched over `cli.Retriever` via the `_run` helper) — no embedder is loaded. Match that idiom exactly: this task only tests that the flag plumbs into `Retriever.search`.

First, make the fake record the search call. In `tests/test_cli.py`, add a class attribute and update `FakeRetriever.search`:

```python
class FakeRetriever:
    ...
    last_search: dict = {}
    ...
    def search(self, query, k=5, source_prefix=None):
        FakeRetriever.last_search = {"query": query, "k": k, "source_prefix": source_prefix}
        return [{"source": "_md/a.md", "ord": 0, "text": "命中原文", "score": 0.5}]
```

Then add two tests:

```python
def test_search_filter_flag_reaches_retriever(monkeypatch, capsys):
    _run(monkeypatch, ["rag-retriever", "search", "表见代理", "--filter", "caseA/", "--json"])
    capsys.readouterr()
    assert FakeRetriever.last_search["source_prefix"] == "caseA/"


def test_search_without_filter_defaults_none(monkeypatch, capsys):
    _run(monkeypatch, ["rag-retriever", "search", "表见代理", "--json"])
    capsys.readouterr()
    assert FakeRetriever.last_search["source_prefix"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli.py -v -k filter`
Expected: `test_search_filter_flag_reaches_retriever` FAILS — `--filter` is an unrecognized argument (argparse `SystemExit`).

- [ ] **Step 3: Add the CLI flag and pass it through**

In `rag-retriever/rag_retriever/cli.py`, after the search parser's `--json` arg (:61):

```python
    p_search.add_argument(
        "--filter", dest="source_prefix", default=None,
        help="scope search to sources under this path prefix (e.g. a case dir)",
    )
```

And update the dispatch at :90:

```python
        hits = r.search(args.query, k=args.k, source_prefix=args.source_prefix)
```

- [ ] **Step 4: Add the MCP parameter and pass it through**

In `rag-retriever/rag_retriever/server.py`, replace the `search` tool (:63):

```python
@mcp.tool()
def search(query: str, k: int = 5, source_prefix: str = "") -> str:
    """Search the indexed documents for passages relevant to `query` and return the
    top `k` chunks (with source path and similarity score). Use these passages as
    grounding to answer the user's question yourself — this tool does NOT answer.

    Optional source_prefix scopes the search to sources under that path prefix
    (e.g. a single case directory), for multi-case isolation."""
    hits = retriever().search(query, k=k, source_prefix=source_prefix or None)
    if not hits:
        return "No relevant passages found (is anything indexed yet? run index_path first)."
    parts = []
    for i, h in enumerate(hits, 1):
        parts.append(
            f"[{i}] source={h['source']} (chunk {h['ord']}, score {h['score']})\n{h['text']}"
        )
    return "\n\n---\n\n".join(parts)
```

- [ ] **Step 5: Run the CLI test to verify it passes**

Run: `pytest tests/test_cli.py -v`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `pytest` (from `rag-retriever/`)
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add rag-retriever/rag_retriever/cli.py rag-retriever/rag_retriever/server.py rag-retriever/tests/test_cli.py
git commit -m "feat(rag): expose --filter (CLI) and source_prefix (MCP) source scoping

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- Run all commands from the `rag-retriever/` subdirectory (that's where `pytest`/the package live).
- Live fastembed model verification is intentionally deferred (reranker is default-off); just set the default id. Revisit only if a user opts into `RAG_RERANK=local`.
- Tasks 2→3→4 are ordered by dependency (store → pipeline → surface). Task 1 is independent and can go first or last.
