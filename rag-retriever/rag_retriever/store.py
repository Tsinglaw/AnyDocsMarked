"""LanceDB-backed vector store: one embedded table of chunks, no server.

Each row = one chunk: id, source (file path), ord (chunk index), text, vector.
Re-indexing a file deletes its old chunks first so updates stay clean.
"""

from __future__ import annotations

import json
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
        if _TABLE in self._db.table_names():
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
        meta: dict | None = None,
    ) -> int:
        if not chunks:
            return 0
        meta_json = json.dumps(meta or {}, ensure_ascii=False)
        tbl = self._table(dim=len(vectors[0]))
        rows = [
            {"id": f"{source}::{i}", "source": source, "ord": i,
             "text": chunk, "meta": meta_json, "vector": vec}
            for i, (chunk, vec) in enumerate(zip(chunks, vectors))
        ]
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
