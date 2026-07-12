"""stats() reports the model the index was BUILT with, not the live config.

This is what lets a consumer detect the dangerous "indexed with model X, now
querying with model Y" mismatch — comparing stats()'s model against its own
current config.
"""

from __future__ import annotations

from conftest import make_retriever


def _doc(tmp_path):
    f = tmp_path / "_md" / "doc.md"
    f.parent.mkdir(parents=True)
    f.write_text("欠款金额为 50000 元。", encoding="utf-8")
    return f


def test_stats_index_model_is_none_before_any_indexing(tmp_path):
    r = make_retriever(tmp_path, embed_model="fake", embed_backend="local")
    s = r.stats()
    assert s["index_model"] is None
    assert s["index_backend"] is None
    # live query model is always reported
    assert s["query_model"] == "fake"


def test_stats_reports_index_time_and_live_query_model(tmp_path):
    f = _doc(tmp_path)

    # index with model A
    r1 = make_retriever(tmp_path, embed_backend="ollama", embed_model="model-A")
    r1.index_path(f, source_root=tmp_path)

    # a fresh retriever over the SAME store, but a different live config
    r2 = make_retriever(tmp_path, embed_backend="local", embed_model="model-B")
    s = r2.stats()
    assert (s["index_backend"], s["index_model"]) == ("ollama", "model-A")
    assert (s["query_backend"], s["query_model"]) == ("local", "model-B")


def test_stats_exposes_fts_health(tmp_path):
    # Before indexing: no table at all -> both signals false.
    r = make_retriever(tmp_path, embed_model="fake", embed_backend="local")
    assert r.stats()["fts"] == {"column": False, "index": False}

    # After a batch index: schema has text_tokens AND the FTS index is built,
    # so a hybrid consumer can trust BM25 is actually in play.
    r.index_path(_doc(tmp_path), source_root=tmp_path)
    assert r.stats()["fts"] == {"column": True, "index": True}
