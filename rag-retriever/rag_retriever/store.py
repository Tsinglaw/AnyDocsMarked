"""LanceDB-backed vector store: one embedded table of chunks, no server.

Each row = one chunk: id, source (file path), ord (chunk index), text, vector.
Re-indexing a file deletes its old chunks first so updates stay clean.
"""

from __future__ import annotations

import inspect
import json
import os
import tempfile
from collections import Counter
from pathlib import Path

import lancedb

from .tokenize import tokenize_for_fts

_TABLE = "chunks"


def _escape(value: str) -> str:
    # LanceDB predicates are DataFusion SQL: in a standard single-quoted string
    # literal the only metacharacter is the quote itself ('' escape); backslash
    # (Windows paths) is literal. Sufficient as long as values are interpolated
    # into '...' literals only.
    return value.replace("'", "''")


def _source_prefix_where(prefix: str) -> str:
    """SQL predicate matching rows whose `source` starts with `prefix` (literal).
    Used as a LanceDB prefilter to scope search to a case dir / doc-type subtree."""
    return f"starts_with(source, '{_escape(prefix)}')"


def _read_json(path: Path, default):
    """Read a JSON file, returning `default` if it's missing or unreadable."""
    if path.exists():
        try:
            return json.loads(path.read_text("utf-8"))
        except (ValueError, OSError):
            return default
    return default


def _write_json(path: Path, data, *, indent: int | None = None) -> None:
    """Atomically write JSON so a crash never leaves a truncated sidecar."""
    payload = json.dumps(data, ensure_ascii=False, indent=indent)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _file_stamp(path: Path) -> tuple[int, int, int] | None:
    try:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size
    except OSError:
        return None


def _parse_meta(row: dict) -> dict:
    """Deserialize a search-result row's stored meta JSON, tolerating absence/corruption."""
    try:
        return json.loads(row.get("meta") or "{}")
    except (ValueError, TypeError):
        return {}


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
        # Parent blocks for small-to-big retrieval, keyed by source and indexed by
        # parent_ord. Sidecar (not a table column) so children stay the only indexed
        # rows; empty/absent for indexes built without parent context.
        self._parents_path = data_dir / "parents.json"
        self._parents: dict[str, list[str]] = _read_json(self._parents_path, {})
        self._parents_stamp = _file_stamp(self._parents_path)
        # One-shot guard so search_text self-heals a missing FTS index at most once
        # (e.g. rows added via a direct add() that bypassed the batch rebuild_fts()).
        self._fts_heal_attempted = False

    def _save_manifest(self) -> None:
        _write_json(self._manifest_path, self._manifest, indent=2)

    def _save_parents(self) -> None:
        _write_json(self._parents_path, self._parents)
        self._parents_stamp = _file_stamp(self._parents_path)

    def _refresh_parents(self) -> None:
        """Reload a sidecar replaced by another CLI/MCP process."""
        current = _file_stamp(self._parents_path)
        if current != self._parents_stamp:
            self._parents = _read_json(self._parents_path, {})
            self._parents_stamp = current

    def set_parents(self, source: str, parents: list[str]) -> None:
        """Store (overwrite) the parent blocks for a source, indexed by parent_ord."""
        self._refresh_parents()
        self._parents[source] = list(parents)
        self._save_parents()

    def get_parent(self, source: str, ord: int | None) -> str | None:
        """Parent block text for (source, parent_ord); None if absent/out of range."""
        self._refresh_parents()
        if ord is None:
            return None
        blocks = self._parents.get(source)
        if blocks is None or ord < 0 or ord >= len(blocks):
            return None
        return blocks[ord]

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
            "text_tokens": "", "meta": "{}", "vector": [0.0] * dim,
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
        self._refresh_parents()
        if self._parents.pop(source, None) is not None:
            self._save_parents()

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
        # Detect whether this table (legacy or new) has the text_tokens column.
        # Legacy indexes (schema: id/source/ord/text/meta/vector) lack it; new
        # tables created by _table(dim=...) always include it.  We omit the
        # field entirely for legacy tables so the row matches their schema.
        include_tokens = "text_tokens" in tbl.schema.names
        base_meta = meta or {}
        rows = []
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            row_meta = dict(base_meta)
            if metas and i < len(metas):
                row_meta.update(metas[i])
            row = {
                # id is a human-readable label, never parsed back — lookups and
                # deletes go through the `source` column, so a source containing
                # "::" is display-ambiguous but functionally harmless.
                "id": f"{source}::{i}", "source": source, "ord": i,
                "text": chunk,
                "meta": json.dumps(row_meta, ensure_ascii=False),
                "vector": vec,
            }
            if include_tokens:
                row["text_tokens"] = tokenize_for_fts(chunk)
            rows.append(row)
        tbl.add(rows)
        # Note: the FTS index is built once per batch via rebuild_fts(), not here —
        # building it per file would rebuild the whole index N times during a batch.
        self._manifest[source] = len(rows)
        self._save_manifest()
        return len(rows)

    def rebuild_fts(self) -> None:
        """(Re)build the full-text index over text_tokens. Best-effort; call once
        after a batch index, never per row. No-op for legacy tables lacking the column."""
        tbl = self._table()
        if tbl is None or "text_tokens" not in tbl.schema.names:
            return
        try:
            # LanceDB's unified API replaced create_fts_index in newer releases,
            # while 0.33's synchronous LanceTable still exposes only the legacy
            # method. Prefer the unified public API when the installed version
            # supports it and retain a bounded compatibility path for the lock.
            from lancedb.index import FTS

            options = {
                "base_tokenizer": "whitespace",
                "stem": False,
                "remove_stop_words": False,
                "ascii_folding": False,
            }
            if "config" in inspect.signature(tbl.create_index).parameters:
                tbl.create_index("text_tokens", config=FTS(**options), replace=True)
            else:
                tbl.create_fts_index("text_tokens", replace=True, **options)
        except Exception:
            # FTS is an optimization; never fail because of it.
            pass

    def search(self, query_vector: list[float], k: int = 5,
               source_prefix: str | None = None) -> list[dict]:
        tbl = self._table()
        if tbl is None:
            return []
        q = tbl.search(query_vector).metric("cosine")
        if source_prefix:
            q = q.where(_source_prefix_where(source_prefix), prefilter=True)
        results = q.limit(k).to_list()
        out = []
        for r in results:
            # LanceDB returns cosine *distance*; similarity = 1 - distance.
            distance = r.get("_distance", 0.0)
            out.append({
                "source": r["source"],
                "ord": r["ord"],
                "text": r["text"],
                "score": round(1.0 - distance, 4),
                "metadata": _parse_meta(r),
            })
        return out

    def has_fts(self) -> bool:
        tbl = self._table()
        if tbl is None:
            return False
        try:
            return "text_tokens" in tbl.schema.names
        except Exception:
            return False

    def fts_status(self) -> dict:
        """BM25 health: {"column": .., "index": ..}. `column` = the schema carries
        text_tokens (legacy indexes lack it); `index` = the FTS index is actually
        built. search_text degrades to [] silently in both cases, which quietly
        turns hybrid search into vector-only — this is where that state becomes
        visible (surfaced via stats())."""
        has_col = self.has_fts()
        return {"column": has_col,
                "index": has_col and self._has_fts_index(self._table())}

    def _has_fts_index(self, tbl) -> bool:
        """Whether an FTS index over text_tokens exists (lancedb returns [] rather
        than erroring when it doesn't, so we must check before relying on it)."""
        try:
            return any("text_tokens" in (getattr(i, "columns", None) or [])
                       for i in tbl.list_indices())
        except Exception:
            return False

    def search_text(self, query: str, k: int = 5,
                    source_prefix: str | None = None) -> list[dict]:
        """BM25 full-text search over pre-tokenized text. [] if unavailable."""
        tbl = self._table()
        if tbl is None or "text_tokens" not in tbl.schema.names:
            return []
        q = tokenize_for_fts(query)
        if not q:
            return []
        # Self-heal a missing FTS index once — e.g. rows added via a direct add()
        # that bypassed the batch rebuild_fts(). lancedb returns [] (not an error)
        # when the index is absent, so detect it proactively rather than on except.
        if not self._fts_heal_attempted and not self._has_fts_index(tbl):
            self._fts_heal_attempted = True
            self.rebuild_fts()
            tbl = self._table()  # reopen so the freshly-built index is visible
        try:
            s = tbl.search(q, query_type="fts")
            if source_prefix:
                s = s.where(_source_prefix_where(source_prefix), prefilter=True)
            results = s.limit(k).to_list()
        except Exception:
            return []
        out = []
        for r in results:
            out.append({
                "source": r["source"], "ord": r["ord"], "text": r["text"],
                "score": round(float(r.get("_score", 0.0)), 4),
                "metadata": _parse_meta(r),
            })
        return out

    def record_model(self, backend: str, model: str) -> None:
        """Persist the embedding model used to build this index."""
        _write_json(self._index_meta_path, {"backend": backend, "model": model}, indent=2)

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
