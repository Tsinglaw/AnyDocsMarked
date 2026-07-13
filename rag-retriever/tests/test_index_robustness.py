"""Batch indexing must isolate per-file failures.

A real document folder will contain the occasional encrypted PDF or corrupt
office file that the extractor cannot read. One such file must not abort the
whole `index_path` run — it should be reported as skipped, and every other file
must still be indexed (mirroring makeitdown's per-file error isolation).
"""

from __future__ import annotations

import rag_retriever.pipeline as pipeline_mod
from conftest import make_retriever


def test_index_path_isolates_a_failing_file(tmp_path, monkeypatch):
    docs = tmp_path / "docs"  # keep source files out of the .rag data dir
    docs.mkdir()
    (docs / "good.md").write_text("可正常抽取的内容。", encoding="utf-8")
    (docs / "bad.md").write_text("无所谓内容", encoding="utf-8")

    real_extract = pipeline_mod.extract_text

    def flaky_extract(path):
        if str(path).replace("\\", "/").endswith("bad.md"):
            raise RuntimeError("simulated corrupt file")
        return real_extract(path)

    monkeypatch.setattr(pipeline_mod, "extract_text", flaky_extract)

    r = make_retriever(tmp_path)
    result = r.index_path(docs)

    assert result["files_seen"] == 2
    assert result["files_indexed"] == 1
    assert result["files_skipped"] == 1
    # the failure is reported, not raised, and names the offending file + reason
    skipped = result["skipped"]
    assert len(skipped) == 1
    assert skipped[0]["source"].endswith("bad.md")
    assert "RuntimeError" in skipped[0]["reason"]


def test_index_file_reports_extraction_error(tmp_path, monkeypatch):
    f = tmp_path / "x.md"
    f.write_text("内容", encoding="utf-8")

    def boom(path):
        raise ValueError("nope")

    monkeypatch.setattr(pipeline_mod, "extract_text", boom)
    r = make_retriever(tmp_path)
    res = r.index_file(f)
    assert res["indexed"] is False
    assert "ValueError" in res["reason"]
    assert res["chunks"] == 0


def test_index_path_isolates_an_embed_failure(tmp_path):
    # A failure downstream of extraction (embed/chunk/store) must ALSO be isolated,
    # not just extraction — otherwise one bad file aborts the batch mid-run and
    # leaves a partial index with a nonzero exit (the "伪失败" seen in the field).
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "good.md").write_text("可正常嵌入的内容。", encoding="utf-8")
    (docs / "bad.md").write_text("触发嵌入失败的内容。", encoding="utf-8")

    r = make_retriever(tmp_path)
    real_embed = r._embedder.embed_documents

    def flaky_embed(chunks):
        if any("触发嵌入失败" in c for c in chunks):
            raise RuntimeError("simulated embed crash")
        return real_embed(chunks)

    r._embedder.embed_documents = flaky_embed

    result = r.index_path(docs)

    assert result["files_seen"] == 2
    assert result["files_indexed"] == 1        # good.md still made it in
    assert result["files_skipped"] == 1
    skipped = result["skipped"]
    assert len(skipped) == 1
    assert skipped[0]["source"].endswith("bad.md")
    assert "RuntimeError" in skipped[0]["reason"]
