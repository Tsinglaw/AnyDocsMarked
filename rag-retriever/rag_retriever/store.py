"""LanceDB-backed vector store: one embedded table of chunks, no server.

Each row = one chunk: id, source (file path), ord (chunk index), text, vector.
Re-indexing a file deletes its old chunks first so updates stay clean.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import lancedb

_TABLE = "chunks"


def _escape(value: str) -> str:
    return value.replace("'", "''")


def _read_json(path: Path, default):
    """Read a JSON file, returning `default` if it's missing or unreadable."""
    if path.exists():
        try:
            return json.loads(path.read_text("utf-8"))
        except (ValueError, OSError):
            return default
    return default


class VectorStore:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(data_dir))
        # Lightweight sidecar manifest {source: chunk_count} so listing/counting
        # never needs a full-table scan (which would require the extra pylance dep).
        self._manifest_path = data_dir / "manifest.json"
        self._manifest: dict[str, int] = _read_json(self._manifest_path, {})
        # Records the embedding model the index was built with, so a consumer
        # can detect "indexed with X, querying with Y" (which silently breaks
        # similarity). Separate sidecar to avoid touching the source manifest.
        self._index_meta_path = data_dir / "index_meta.json"

    def _save_manifest(self) -> None:
        self._manifest_path.write_text(
            json.dumps(self._manifest, ensure_ascii=False, indent=2), "utf-8"
        )

    def _table(self, dim: int | None = None):
        # list_tables() (table_names() is deprecated) returns a paginated
        # response object; .tables is the name list. We only ever hold the one
        # "chunks" table, so pagination never matters here.
        if _TABLE in self._db.list_tables().tables:
            return self._db.open_table(_TABLE)
        if dim is None:
            return None
        schema_row = [{
            "id": "seed", "source": "", "ord": 0, "text": "",
            "meta": "{}", "vector": [0.0] * dim,
        }]
        tbl = self._db.create_table(_TABLE, data=schema_row)
        tbl.delete("id = 'seed'")
        return tbl

    def delete_source(self, source: str) -> None:
        tbl = self._table()
        if tbl is not None:
            tbl.delete(f"source = '{_escape(source)}'")
        if self._manifest.pop(source, None) is not None:
            self._save_manifest()

    def add(
        self, source: str, chunks: list[str], vectors: list[list[float]],
        meta: dict | None = None, metas: list[dict] | None = None,
    ) -> int:
        if not chunks:
            return 0
        new_dim = len(vectors[0])
        tbl = self._table()
        if tbl is not None:
            existing = tbl.schema.field("vector").type.list_size
            if existing != new_dim:
                raise ValueError(
                    f"embedding dimension changed ({existing} -> {new_dim}): the index "
                    f"was built with a different model. Vectors of mixed dimension can't "
                    f"share a table — rebuild the index from scratch (delete the data dir "
                    f"/ .rag and re-run index) using a single embedding model."
                )
        else:
            tbl = self._table(dim=new_dim)
        base_meta = meta or {}
        rows = []
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            row_meta = dict(base_meta)
            if metas and i < len(metas):
                row_meta.update(metas[i])
            rows.append({
                "id": f"{source}::{i}", "source": source, "ord": i,
                "text": chunk, "meta": json.dumps(row_meta, ensure_ascii=False),
                "vector": vec,
            })
        tbl.add(rows)
        self._manifest[source] = len(rows)
        self._save_manifest()
        return len(rows)

    def search(self, query_vector: list[float], k: int = 5) -> list[dict]:
        tbl = self._table()
        if tbl is None:
            return []
        results = (
            tbl.search(query_vector).metric("cosine").limit(k).to_list()
        )
        out = []
        for r in results:
            # LanceDB returns cosine *distance*; similarity = 1 - distance.
            distance = r.get("_distance", 0.0)
            try:
                metadata = json.loads(r.get("meta") or "{}")
            except (ValueError, TypeError):
                metadata = {}
            out.append({
                "source": r["source"],
                "ord": r["ord"],
                "text": r["text"],
                "score": round(1.0 - distance, 4),
                "metadata": metadata,
            })
        return out

    def record_model(self, backend: str, model: str) -> None:
        """Persist the embedding model used to build this index."""
        self._index_meta_path.write_text(
            json.dumps({"backend": backend, "model": model}, ensure_ascii=False, indent=2),
            "utf-8",
        )

    def model_info(self) -> dict | None:
        """The persisted index-time embedding model, or None if never indexed."""
        return _read_json(self._index_meta_path, None)

    def list_sources(self) -> list[dict]:
        return [{"source": s, "chunks": n} for s, n in sorted(self._manifest.items())]

    def count(self) -> int:
        return sum(self._manifest.values())

    def table_manifest(self) -> dict[str, int]:
        """{source: chunk_count} read from the table itself (a full scan).

        This is the ground truth, in contrast to the cached sidecar manifest.
        Uses only pyarrow (a hard lancedb dependency); no pandas/pylance.
        """
        tbl = self._table()
        if tbl is None:
            return {}
        return dict(Counter(tbl.to_arrow().column("source").to_pylist()))

    def reconcile(self, truth: dict[str, int] | None = None) -> dict:
        """Rebuild the sidecar manifest from the table's real contents.

        Use after a crash mid-write or a manually deleted manifest desynced the
        cached counts from the table. Pass `truth` to reuse an already-scanned
        table_manifest() and avoid a second full scan. Returns {before, after}.
        """
        before = dict(self._manifest)
        self._manifest = self.table_manifest() if truth is None else dict(truth)
        self._save_manifest()
        return {"before": before, "after": dict(self._manifest)}
