from rag_retriever.store import VectorStore


def _add(store, source, texts):
    vecs = [[float(i), 0.0, 0.0] for i, _ in enumerate(texts)]
    store.add(source, texts, vecs, metas=[{"heading_path": ""} for _ in texts])
    # add() no longer builds the FTS index per-call; the batch driver does it once.
    store.rebuild_fts()


def test_search_text_finds_keyword(tmp_path):
    s = VectorStore(tmp_path)
    _add(s, "doc.md", ["表见代理的构成要件", "无权代理的法律后果", "合同的解除条件"])
    hits = s.search_text("表见代理", k=3)
    assert hits, "BM25 should return at least one hit"
    assert "表见代理" in hits[0]["text"]


def test_search_text_self_heals_missing_fts_index(tmp_path):
    # Rows added via a direct add() that bypassed the batch rebuild_fts():
    # search_text should self-heal (build the FTS index once) and still return hits,
    # rather than silently degrading to no BM25 results.
    s = VectorStore(tmp_path)
    vecs = [[float(i), 0.0, 0.0] for i in range(3)]
    s.add("doc.md", ["表见代理的构成要件", "无权代理的法律后果", "合同的解除条件"],
          vecs, metas=[{"heading_path": ""}] * 3)  # note: no rebuild_fts()
    hits = s.search_text("表见代理", k=3)
    assert hits, "search_text should self-heal the missing FTS index and return a hit"
    assert "表见代理" in hits[0]["text"]


def test_search_text_empty_on_old_index_without_fts(tmp_path):
    # Simulate an old table without text_tokens/FTS by writing via the legacy path.
    s = VectorStore(tmp_path)
    # no add() yet → no table → search_text returns []
    assert s.search_text("anything", k=3) == []


def test_text_tokens_column_present_after_add(tmp_path):
    s = VectorStore(tmp_path)
    _add(s, "doc.md", ["合同价款五十万元"])
    assert s.has_fts() is True


def test_add_into_legacy_table_without_text_tokens(tmp_path):
    import lancedb

    db = lancedb.connect(str(tmp_path))
    # Legacy schema: no text_tokens column
    seed = [{"id": "seed", "source": "", "ord": 0, "text": "", "meta": "{}", "vector": [0.0, 0.0, 0.0]}]
    t = db.create_table("chunks", data=seed)
    t.delete("id = 'seed'")

    s = VectorStore(tmp_path)
    # Must NOT raise even though the table lacks text_tokens
    s.add("doc.md", ["合同价款五十万元"], [[1.0, 0.0, 0.0]], metas=[{"heading_path": ""}])
    assert s.has_fts() is False          # legacy table has no FTS column
    assert s.search_text("合同", k=3) == []  # search_text falls back to empty
    assert s.count() == 1               # the row was actually written


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


def test_search_prefix_with_single_quote_is_escaped(tmp_path):
    # A prefix containing a single quote must not break/inject the where clause.
    s = VectorStore(tmp_path)
    s.add("it's a trap/doc.md", ["text"], [[1.0, 0.0, 0.0]],
          metas=[{"heading_path": ""}])
    s.add("other/doc.md", ["text"], [[0.0, 1.0, 0.0]],
          metas=[{"heading_path": ""}])
    hits = s.search([1.0, 0.0, 0.0], k=5, source_prefix="it's a trap/")
    assert hits, "quote-containing prefix should still match, not error"
    assert all(h["source"].startswith("it's a trap/") for h in hits)


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
