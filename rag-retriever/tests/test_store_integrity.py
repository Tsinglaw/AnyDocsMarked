"""VectorStore integrity: dimension guard + manifest reconciliation.

Self-contained (no conftest) so it can run without importing the chunking path,
which pulls a tiktoken BPE file at import time.
"""

from __future__ import annotations

import json

import pytest

from rag_retriever.store import VectorStore


def test_add_rejects_a_dimension_change(tmp_path):
    """Mixing vector dimensions in one table silently breaks similarity; the
    store must refuse it with a clear, actionable error instead of a cryptic
    backend failure."""
    s = VectorStore(tmp_path / ".rag")
    s.add("a.md", ["x"], [[0.1, 0.2, 0.3]])

    with pytest.raises(ValueError, match="dimension"):
        s.add("b.md", ["y"], [[0.1, 0.2]])  # 2-dim against a 3-dim table


def test_add_same_dimension_still_works(tmp_path):
    s = VectorStore(tmp_path / ".rag")
    s.add("a.md", ["x"], [[0.1, 0.2, 0.3]])
    s.add("b.md", ["y"], [[0.4, 0.5, 0.6]])
    assert s.count() == 2


def test_reconcile_rebuilds_manifest_from_table(tmp_path):
    """If the sidecar manifest desyncs from the table (crash mid-write, manual
    deletion), reconcile() rebuilds it from the table's actual contents."""
    data_dir = tmp_path / ".rag"
    s = VectorStore(data_dir)
    s.add("a.md", ["x", "y"], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    s.add("b.md", ["z"], [[0.0, 0.0, 1.0]])

    # Simulate desync: wipe the manifest and reopen.
    (data_dir / "manifest.json").unlink()
    s2 = VectorStore(data_dir)
    assert s2.count() == 0  # manifest gone -> nothing known

    report = s2.reconcile()
    assert s2.count() == 3
    assert {row["source"] for row in s2.list_sources()} == {"a.md", "b.md"}
    assert report["after"] == {"a.md": 2, "b.md": 1}


def test_reconcile_on_empty_store(tmp_path):
    s = VectorStore(tmp_path / ".rag")
    report = s.reconcile()
    assert report["after"] == {}
    assert s.count() == 0
