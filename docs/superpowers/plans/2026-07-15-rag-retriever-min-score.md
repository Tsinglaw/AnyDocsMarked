# Vector-Channel Relevance Cutoff (min-score) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in cosine-similarity floor (`RAG_MIN_SCORE`) that strips semantically-distant vector hits before fusion/rerank, without touching BM25/keyword hits or changing any default behavior.

**Architecture:** One new `Config` field parsed from env, and one small pure function (`_above_floor`) in `pipeline.py` applied to the vector channel's hits at both of `search()`'s two call sites (pure-vector path and the hybrid path's `vector_hits`, before RRF fusion). BM25 (`text_hits`) is never filtered.

**Tech Stack:** Python 3.12, existing `rag_retriever` package — no new dependencies.

## Global Constraints

- No new third-party dependency, no new model, no network call.
- Default behavior byte-identical: `RAG_MIN_SCORE` unset (or `0.0`) must produce exactly today's output on every code path.
- The floor applies **only to the vector channel**. BM25 hits (`store.search_text` results, and any hit that survives into the fused/reranked list via a keyword match) are never dropped by this feature — a legal exact-term match must survive even when its vector similarity is below the floor.
- No new CLI flag, no new MCP parameter — env-only, consistent with `RAG_HYBRID` / `RAG_RRF_K` / `RAG_HYBRID_CANDIDATES` / `RAG_RERANK`.
- Filtering happens before `_attach_parents` — parent-context behavior (from the prior feature) is unaffected.
- `store.search()`'s `score` field is confirmed to be cosine similarity: `rag-retriever/rag_retriever/store.py` computes it as `round(1.0 - distance, 4)` where the LanceDB query uses `.metric("cosine")` — this is the number the floor compares against. (Hard dependency already verified by reading the source; no separate verification task needed.)
- CI is Ubuntu + Windows; repo-level `ruff` (`uvx ruff check --select E9,F .`) must pass.
- `Config` is `frozen=True`; the new field has a default, never mutates existing instances.

---

### Task 1: `Config.min_score` field

**Files:**
- Modify: `rag-retriever/rag_retriever/config.py`
- Test: `rag-retriever/tests/test_config.py`

**Interfaces:**
- Produces: `Config.min_score: float` (default `0.0`). Env: `RAG_MIN_SCORE`. New helper `_env_float(name: str, default: float) -> float` (module-level, alongside the existing `_env` / `_env_int` / `_env_bool`).

- [ ] **Step 1: Write the failing tests**

Add to `rag-retriever/tests/test_config.py`:

```python
def test_min_score_defaults_to_zero(monkeypatch):
    monkeypatch.delenv("RAG_MIN_SCORE", raising=False)
    from rag_retriever.config import Config
    assert Config.load().min_score == 0.0


def test_min_score_env_parses_float(monkeypatch):
    monkeypatch.setenv("RAG_MIN_SCORE", "0.35")
    from rag_retriever.config import Config
    assert Config.load().min_score == 0.35


def test_min_score_invalid_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("RAG_MIN_SCORE", "not-a-number")
    from rag_retriever.config import Config
    assert Config.load().min_score == 0.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd rag-retriever && python -m pytest tests/test_config.py -k min_score -v`
Expected: FAIL (`AttributeError: 'Config' object has no attribute 'min_score'`).

- [ ] **Step 3: Add `_env_float` and the field**

In `rag-retriever/rag_retriever/config.py`, add the helper right after `_env_bool` (around line 31):

```python
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
```

Add the field to the `Config` dataclass, after `rerank_model` and before the `parent_context` block (around line 106-107):

```python
    # Vector-channel relevance floor (cosine similarity). Hits with score below
    # this are dropped before fusion/rerank; BM25/keyword hits are never
    # filtered by this. 0.0 (default) = off, byte-identical to before this
    # feature. Only meaningful in (0, 1] — cosine similarity's range.
    min_score: float = 0.0
```

In `Config.load()`, add the corresponding argument to the `cls(...)` call, after `rerank_model=...` and before `parent_context=...` (around line 143-144):

```python
            min_score=_env_float("RAG_MIN_SCORE", 0.0),
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_config.py -v`
Expected: PASS (all config tests, including the new ones and every pre-existing one).

- [ ] **Step 5: Commit**

```bash
git add rag-retriever/rag_retriever/config.py rag-retriever/tests/test_config.py
git commit -m "feat(rag-retriever): add min_score config (vector-channel relevance floor, off by default)"
```

---

### Task 2: Apply the floor in `search()`

**Files:**
- Modify: `rag-retriever/rag_retriever/pipeline.py`
- Modify: `rag-retriever/README.md`
- Test: `rag-retriever/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Config.min_score` (Task 1).
- Produces: `_above_floor(hits: list[dict], floor: float) -> list[dict]` (module-level function in `pipeline.py`, alongside `_rrf_fuse`). `Retriever.search` applies it to the vector channel at both of its two hit-producing branches.

- [ ] **Step 1: Write the failing tests**

Add to `rag-retriever/tests/test_pipeline.py` (the file already has `_FakeEmbedder`, `Config`, `pipeline_mod` imported — reuse them; follow the existing inline-`_S`-fake-store pattern used by `test_search_falls_back_to_vector_when_no_fts` and `test_search_passes_source_prefix_to_store`):

```python
def test_search_min_score_default_zero_is_noop(tmp_path):
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path, "hybrid": False})
    assert cfg.min_score == 0.0

    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()

    class _S:
        def search(self, vec, k, source_prefix=None):
            return [{"source": "d", "ord": 0, "text": "low", "score": 0.01, "metadata": {}}]
        def search_text(self, q, k, source_prefix=None):
            return []

    r.store = _S()
    hits = r.search("query", k=5)
    assert hits and hits[0]["text"] == "low"  # floor is off, nothing is dropped


def test_search_min_score_filters_pure_vector_path(tmp_path):
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path, "hybrid": False, "min_score": 0.6})
    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()

    class _S:
        def search(self, vec, k, source_prefix=None):
            return [
                {"source": "d", "ord": 0, "text": "strong", "score": 0.9, "metadata": {}},
                {"source": "d", "ord": 1, "text": "weak", "score": 0.4, "metadata": {}},
            ]
        def search_text(self, q, k, source_prefix=None):
            return []

    r.store = _S()
    hits = r.search("query", k=5)
    assert [h["text"] for h in hits] == ["strong"]


def test_search_min_score_hybrid_preserves_keyword_only_hits(tmp_path):
    # Core contract: BM25/keyword hits must survive even when their vector
    # similarity is below the floor; a pure-vector hit below the floor with
    # no keyword match must be dropped.
    cfg = Config.load()
    cfg = type(cfg)(**{
        **cfg.__dict__, "data_dir": tmp_path, "hybrid": True, "min_score": 0.6, "rerank": "none",
    })
    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()

    class _S:
        def search(self, vec, k, source_prefix=None):
            return [
                {"source": "d", "ord": 0, "text": "strong_vec", "score": 0.9, "metadata": {}},
                {"source": "d", "ord": 1, "text": "weak_vec_no_kw", "score": 0.2, "metadata": {}},
                {"source": "d", "ord": 2, "text": "weak_vec_with_kw", "score": 0.2, "metadata": {}},
            ]
        def search_text(self, q, k, source_prefix=None):
            return [{"source": "d", "ord": 2, "text": "weak_vec_with_kw", "score": 5.0, "metadata": {}}]

    r.store = _S()
    hits = r.search("query", k=5)
    ids = {(h["source"], h["ord"]) for h in hits}
    assert ("d", 0) in ids       # strong vector match: survives
    assert ("d", 2) in ids       # weak vector but BM25-matched: survives via keyword channel
    assert ("d", 1) not in ids   # weak vector, no keyword match: dropped


def test_search_min_score_all_filtered_returns_empty(tmp_path):
    cfg = Config.load()
    cfg = type(cfg)(**{**cfg.__dict__, "data_dir": tmp_path, "hybrid": False, "min_score": 0.99})
    r = pipeline_mod.Retriever(cfg)
    r._embedder = _FakeEmbedder()

    class _S:
        def search(self, vec, k, source_prefix=None):
            return [{"source": "d", "ord": 0, "text": "low", "score": 0.5, "metadata": {}}]
        def search_text(self, q, k, source_prefix=None):
            return []

    r.store = _S()
    assert r.search("query", k=5) == []


def test_search_min_score_with_parent_context_still_attaches(monkeypatch, tmp_path):
    # Filtering happens before _attach_parents; a surviving hit still gets
    # parent_text when parent_context is on.
    text = "# 合同\n\n" + "\n\n".join(f"第{i}条 关于货款与违约金的约定条款。" for i in range(30))
    r = _real_store_retriever(
        monkeypatch, tmp_path, text,
        parent_context=True, parent_tokens=120, chunk_tokens=30,
        chunk_overlap=0, hybrid=False, rerank="none", min_score=0.5,
    )
    r.index_file(tmp_path / "doc.md", source_root=tmp_path)
    hits = r.search("货款 违约金", k=3)
    assert hits
    assert hits[0]["parent_text"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd rag-retriever && python -m pytest tests/test_pipeline.py -k min_score -v`
Expected: FAIL — `test_search_min_score_default_zero_is_noop` fails at `cfg.min_score == 0.0` (`AttributeError`, since Task 1 already landed this should actually pass the assert but fail elsewhere) or more precisely, the filtering tests (`filters_pure_vector_path`, `hybrid_preserves_keyword_only_hits`, `all_filtered_returns_empty`) fail because `search()` does not yet drop anything — e.g. `test_search_min_score_filters_pure_vector_path` fails with `assert ["strong", "weak"] == ["strong"]`.

- [ ] **Step 3: Add `_above_floor` and wire it into `search()`**

In `rag-retriever/rag_retriever/pipeline.py`, add the helper right after `_rrf_fuse` (around line 38-39):

```python
def _above_floor(hits: list[dict], floor: float) -> list[dict]:
    """Drop hits whose (cosine) score is below floor. No-op when floor <= 0.

    Applied to the vector channel only — BM25/keyword hits are never filtered,
    so a legal exact-term match (amount, statute number) still surfaces even
    when its vector similarity is weak."""
    if floor <= 0.0:
        return hits
    return [h for h in hits if h["score"] >= floor]
```

Change `search()`'s body. Replace:

```python
        if not self.cfg.hybrid:
            hits = self.store.search(qvec, k=k, source_prefix=sp)
        else:
            cand = max(k, self.cfg.hybrid_candidates)
            vector_hits = self.store.search(qvec, k=cand, source_prefix=sp)
            text_hits = self.store.search_text(query, k=cand, source_prefix=sp)
            if text_hits:
                fused = _rrf_fuse(vector_hits, text_hits, self.cfg.rrf_k, cand)
            else:
                fused = vector_hits[:cand]
            hits = self.reranker.rerank(query, fused, k) if self.reranker is not None else fused[:k]
        return self._attach_parents(hits)
```

with:

```python
        if not self.cfg.hybrid:
            hits = _above_floor(self.store.search(qvec, k=k, source_prefix=sp), self.cfg.min_score)
        else:
            cand = max(k, self.cfg.hybrid_candidates)
            vector_hits = _above_floor(
                self.store.search(qvec, k=cand, source_prefix=sp), self.cfg.min_score
            )
            text_hits = self.store.search_text(query, k=cand, source_prefix=sp)
            if text_hits:
                fused = _rrf_fuse(vector_hits, text_hits, self.cfg.rrf_k, cand)
            else:
                fused = vector_hits[:cand]
            hits = self.reranker.rerank(query, fused, k) if self.reranker is not None else fused[:k]
        return self._attach_parents(hits)
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd rag-retriever && python -m pytest tests/test_pipeline.py -v`
Expected: PASS (all new `min_score` tests, plus every pre-existing pipeline test — in particular `test_search_falls_back_to_vector_when_no_fts` and `test_search_passes_source_prefix_to_store`, which use `min_score`'s default of `0.0` implicitly and must be unaffected).

- [ ] **Step 5: Document it in the README**

In `rag-retriever/README.md`, the env-var table has this row at line 57:

```
| `RAG_HYBRID_CANDIDATES` | `50` | per-channel candidate pool before fusion |
```

Insert a new row immediately after it (before the `RAG_RERANK` row):

```
| `RAG_MIN_SCORE` | `0` | cosine-similarity floor on the vector channel only (`0` = off). Hits below it are dropped before fusion/rerank; BM25/keyword hits are never filtered, so an exact-term match still surfaces |
```

- [ ] **Step 6: Run the full suite**

Run: `cd rag-retriever && python -m pytest -q`
Expected: PASS (whole suite green — 96 tests plus the new ones from this plan).

- [ ] **Step 7: Lint**

Run (from the repo root, `D:\Vibe Coding Items\AnyDocsMarked`): `uvx ruff check --select E9,F .`
Expected: `All checks passed!`

- [ ] **Step 8: Commit**

```bash
git add rag-retriever/rag_retriever/pipeline.py rag-retriever/README.md rag-retriever/tests/test_pipeline.py
git commit -m "feat(rag-retriever): vector-channel relevance floor (RAG_MIN_SCORE)"
```

---

## Self-Review

**1. Spec coverage:**
- Section 1 (config, `min_score` default 0.0, `(0, 1]` meaningful range documented in a comment) → Task 1. ✅
- Section 2 (`_above_floor`, applied at pure-vector path and hybrid's `vector_hits` before fusion, BM25 untouched, filtering before `_attach_parents`, may return fewer/zero with no backfill) → Task 2. ✅ (the "may return `[]`" behavior is exercised by `test_search_min_score_all_filtered_returns_empty`; no backfill logic exists anywhere to add, so there's nothing further to implement for that clause — it's a consequence of the filter being a plain drop.)
- Section 3 (no new CLI/MCP surface, README doc) → Task 2 Step 5; no CLI/MCP files touched anywhere in this plan, matching "明确不做". ✅
- Hard dependency #1 (score is cosine similarity) → verified directly from `store.py` source in Global Constraints, no task needed. ✅
- Hard dependency #2 (BM25 hits untouched by the filter) → exercised by `test_search_min_score_hybrid_preserves_keyword_only_hits`. ✅
- Hard dependency #3 (`_env_float` fallback, `min_score=0` no-op) → exercised by `test_min_score_invalid_value_falls_back_to_default` and `test_search_min_score_default_zero_is_noop`. ✅
- Explicitly-excluded items (adaptive per-query threshold, reranker/RRF-score gate, relative top-% cutoff, new CLI flag/MCP param) → none implemented anywhere in this plan. ✅

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; every test step shows full assertions. ✅

**3. Type consistency:** `_above_floor(hits: list[dict], floor: float) -> list[dict]` is defined once in Task 2 Step 3 and called with that exact signature at both of `search()`'s two call sites in the same step. `Config.min_score: float` (Task 1) is the only source of the `floor` argument passed in Task 2 — no renaming across tasks. ✅
