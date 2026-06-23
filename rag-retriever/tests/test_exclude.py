"""index --exclude: skip files matching a glob (e.g. a sidecar ledger).

General feature — any consumer may want to keep certain files out of the index.
(lawiki uses it to exclude makeitdown's report.json from the case index.)
"""

from __future__ import annotations

from rag_retriever.extract import iter_files


def test_iter_files_excludes_by_name_glob(tmp_path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "report.json").write_text("{}", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y", encoding="utf-8")

    names = sorted(p.name for p in iter_files(tmp_path, exclude=("report.json",)))
    assert names == ["a.md", "b.txt"]


def test_iter_files_exclude_glob_pattern(tmp_path):
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    (tmp_path / "data.json").write_text("{}", encoding="utf-8")

    names = [p.name for p in iter_files(tmp_path, exclude=("*.json",))]
    assert names == ["a.md"]
