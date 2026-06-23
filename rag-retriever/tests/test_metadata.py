"""Generic frontmatter passthrough.

rag-retriever knows nothing about any domain's frontmatter semantics. It only
carries forward the YAML fields a caller configured, returning them as opaque
`metadata` on each hit. (lawiki configures `quality` and reads it itself.)
"""

from __future__ import annotations

from conftest import make_retriever


def test_search_returns_only_configured_frontmatter_fields(tmp_path):
    case = tmp_path / "case"
    md = case / "_md"
    md.mkdir(parents=True)
    f = md / "doc.md"
    f.write_text(
        "---\nquality: suspect\nsource_type: pdf\n---\n欠款金额为 50000 元。",
        encoding="utf-8",
    )

    r = make_retriever(tmp_path, metadata_fields=("quality",))
    r.index_file(f, source_root=case)

    hits = r.search("欠款")
    assert hits
    # only the configured field is carried; source_type is dropped
    assert hits[0]["metadata"] == {"quality": "suspect"}


def test_no_configured_fields_yields_empty_metadata(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("---\nquality: suspect\n---\n正文。", encoding="utf-8")

    r = make_retriever(tmp_path)  # metadata_fields defaults to ()
    r.index_file(f)

    assert r.search("正文")[0]["metadata"] == {}


def test_missing_field_is_simply_absent(tmp_path):
    f = tmp_path / "doc.md"
    f.write_text("---\nsource_type: pdf\n---\n正文。", encoding="utf-8")

    r = make_retriever(tmp_path, metadata_fields=("quality",))
    r.index_file(f)

    assert r.search("正文")[0]["metadata"] == {}
