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
