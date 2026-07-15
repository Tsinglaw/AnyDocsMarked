import re

from rag_retriever import pipeline as pipeline_mod
from rag_retriever.pipeline import _rrf_fuse
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

    def add(self, source, chunks, vectors, meta=None, metas=None):
        self.added = {"source": source, "chunks": chunks, "meta": metas}
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
        def search(self, vec, k, source_prefix=None):
            return [{"source": "d", "ord": 0, "text": "hit", "score": 0.5, "metadata": {}}]
        def search_text(self, q, k, source_prefix=None):
            return []  # no FTS

    r.store = _S()
    hits = r.search("query", k=3)
    assert hits and hits[0]["text"] == "hit"


def test_search_passes_source_prefix_to_store(tmp_path):
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
    assert seen["fts"] == "caseA/"
    # empty prefix is normalized to None (full-index search)
    seen.clear()
    r.search("query", k=3, source_prefix="   ")
    assert seen["vec"] is None
    assert seen["fts"] is None


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
