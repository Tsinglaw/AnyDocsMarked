"""CLI wiring: flags reach the Retriever / produce the right output.

A recording fake stands in for Retriever so we test argument plumbing and
output formatting without loading an embedding model.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import rag_retriever.cli as cli


class FakeRetriever:
    """Records construction + calls; returns trivial canned data."""

    last_cfg = None
    last_index: dict = {}

    def __init__(self, cfg=None):
        FakeRetriever.last_cfg = cfg

    def index_path(self, path, recursive=True, source_root=None, exclude=()):
        FakeRetriever.last_index = {
            "path": path, "recursive": recursive, "source_root": source_root,
            "exclude": exclude,
        }
        return {
            "path": path, "files_seen": 0, "files_indexed": 0,
            "files_skipped": 0, "total_chunks": 0, "skipped": [],
        }

    def search(self, query, k=5):
        return [{"source": "_md/a.md", "ord": 0, "text": "命中原文", "score": 0.5}]

    def list_sources(self):
        return [{"source": "_md/a.md", "chunks": 1}]

    def stats(self):
        return {"documents": 1, "chunks": 1}

    def doctor(self, fix=False):
        FakeRetriever.last_index = {"doctor_fix": fix}
        return {"in_sync": True, "manifest_chunks": 1, "table_chunks": 1}


def _run(monkeypatch, argv):
    monkeypatch.setattr(cli, "Retriever", FakeRetriever)
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()


def test_index_passes_source_root(monkeypatch):
    _run(monkeypatch, ["rag-retriever", "index", "/case/_md", "--source-root", "/case"])
    assert FakeRetriever.last_index["path"] == "/case/_md"
    assert FakeRetriever.last_index["source_root"] == "/case"


def test_data_dir_overrides_config(monkeypatch):
    _run(monkeypatch, ["rag-retriever", "--data-dir", "/tmp/x/.rag", "list"])
    assert FakeRetriever.last_cfg is not None
    assert FakeRetriever.last_cfg.data_dir == Path("/tmp/x/.rag")


def test_exclude_flag_reaches_index(monkeypatch):
    _run(monkeypatch, ["rag-retriever", "index", "/case/_md", "--exclude", "report.json"])
    assert FakeRetriever.last_index["exclude"] == ("report.json",)


def test_metadata_fields_flag_overrides_config(monkeypatch):
    _run(monkeypatch, [
        "rag-retriever", "index", "/case/_md",
        "--metadata-fields", "quality,source_type",
    ])
    assert FakeRetriever.last_cfg.metadata_fields == ("quality", "source_type")


def test_search_json_outputs_machine_readable(monkeypatch, capsys):
    _run(monkeypatch, ["rag-retriever", "search", "欠款", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data[0]["source"] == "_md/a.md"
    assert data[0]["text"] == "命中原文"


def test_doctor_fix_flag_reaches_retriever(monkeypatch, capsys):
    _run(monkeypatch, ["rag-retriever", "doctor", "--fix"])
    assert FakeRetriever.last_index == {"doctor_fix": True}
    assert json.loads(capsys.readouterr().out)["in_sync"] is True


def test_doctor_without_fix_defaults_false(monkeypatch, capsys):
    _run(monkeypatch, ["rag-retriever", "doctor"])
    assert FakeRetriever.last_index == {"doctor_fix": False}
    capsys.readouterr()
