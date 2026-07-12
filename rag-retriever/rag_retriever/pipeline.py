"""Orchestration: ingest (extract -> chunk -> embed -> store) and search.

This is the whole "front half of RAG". There is deliberately no LLM here —
search() returns passages; the calling agent does the reasoning.
"""

from __future__ import annotations

from pathlib import Path

from .chunk import Chunk, chunk_document
from .config import Config
from .embed import get_embedder
from .extract import extract_text, iter_files
from .frontmatter import read_frontmatter, select_fields
from .rerank import get_reranker
from .store import VectorStore

# Sentinel for the lazily-resolved reranker: get_reranker() legitimately returns
# None (reranking off), so None can't double as "not yet resolved".
_UNSET = object()


def _rrf_fuse(vector_hits: list[dict], text_hits: list[dict], rrf_k: int, k: int) -> list[dict]:
    """Reciprocal Rank Fusion over two ranked lists, keyed by (source, ord)."""
    scores: dict[tuple, float] = {}
    rep: dict[tuple, dict] = {}
    for ranked in (vector_hits, text_hits):
        for rank, hit in enumerate(ranked):
            key = (hit["source"], hit["ord"])
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            rep.setdefault(key, hit)
    fused = []
    for key, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True):
        hit = dict(rep[key])
        hit["score"] = round(score, 6)
        fused.append(hit)
    return fused[:k]


def _compose(chunk: Chunk) -> str:
    """Stored/embedded text: breadcrumb-prefixed so the vector carries section context."""
    if chunk.heading_path:
        return f"{chunk.heading_path}\n\n{chunk.text}"
    return chunk.text


def _relative_source(path: Path, source_root: str | Path | None) -> str:
    """Stored source id for a file: POSIX-relative to source_root, else absolute."""
    if source_root is None:
        return str(path)
    root = Path(source_root).resolve()
    return path.relative_to(root).as_posix()


class Retriever:
    def __init__(self, cfg: Config | None = None):
        self.cfg = cfg or Config.load()
        self.store = VectorStore(self.cfg.data_dir)
        self._embedder = None  # lazy: don't load the model until needed
        self._reranker = _UNSET  # lazy; None is a valid resolved value (rerank off)

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = get_embedder(self.cfg)
        return self._embedder

    @property
    def reranker(self):
        if self._reranker is _UNSET:
            self._reranker = get_reranker(self.cfg)
        return self._reranker

    def index_file(self, path: str | Path, source_root: str | Path | None = None) -> dict:
        """Extract, chunk, embed, and store one file. Re-indexes cleanly.

        If source_root is given, the stored `source` is the file path relative to
        that root, with POSIX separators (e.g. `_md/合同/采购.md`) so downstream
        anchors stay stable across machines and OSes. Otherwise it's the absolute
        path (backward-compatible default).
        """
        path = Path(path).resolve()
        source = _relative_source(path, source_root)
        # Isolate per-file extraction failures: a single encrypted/corrupt file
        # in a folder must not abort the whole batch (mirrors makeitdown). It's
        # reported as skipped-with-reason, like an empty extraction.
        try:
            text = extract_text(path)
        except Exception as e:
            return {"source": source, "indexed": False, "chunks": 0,
                    "reason": f"extraction failed: {type(e).__name__}: {e}"}
        if not text:
            return {"source": source, "indexed": False, "chunks": 0,
                    "reason": "no extractable text (scanned image without OCR?)"}
        doc_chunks = chunk_document(
            text, self.cfg.chunk_tokens, self.cfg.chunk_overlap, self.cfg.chunk_strategy
        )
        texts = [_compose(c) for c in doc_chunks]
        # Omit the key for headingless chunks: {} is cleaner than {"heading_path": ""}
        # and keeps existing metadata tests (which expect no key when absent) green.
        metas = [{"heading_path": c.heading_path} if c.heading_path else {} for c in doc_chunks]
        vectors = self.embedder.embed_documents(texts)
        meta = select_fields(read_frontmatter(path), self.cfg.metadata_fields)
        self.store.delete_source(source)
        n = self.store.add(source, texts, vectors, meta=meta, metas=metas)
        return {"source": source, "indexed": True, "chunks": n}

    def index_path(
        self, path: str | Path, recursive: bool = True,
        source_root: str | Path | None = None, exclude: tuple[str, ...] = (),
    ) -> dict:
        """Index a file or every supported file under a folder."""
        files = iter_files(path, recursive=recursive, exclude=exclude)
        results = [self.index_file(f, source_root=source_root) for f in files]
        indexed = [r for r in results if r["indexed"]]
        skipped = [r for r in results if not r["indexed"]]
        # Record the index-time model once per run (not per file), and build the
        # full-text index once for the whole batch rather than once per file.
        if indexed:
            self.store.record_model(self.cfg.embed_backend, self.cfg.embed_model)
            self.store.rebuild_fts()
        return {
            "path": str(Path(path).resolve()),
            "files_seen": len(files),
            "files_indexed": len(indexed),
            "files_skipped": len(skipped),
            "total_chunks": sum(r["chunks"] for r in indexed),
            "skipped": skipped,
        }

    def search(self, query: str, k: int = 5,
               source_prefix: str | None = None) -> list[dict]:
        """Top-k relevant chunks. Hybrid (BM25+vector RRF) when enabled and FTS
        is available; otherwise pure vector. Optional source_prefix scopes the
        search to sources under that path prefix. No answer generation."""
        if not query.strip():
            return []
        sp = (source_prefix or "").strip() or None
        qvec = self.embedder.embed_query(query)
        if not self.cfg.hybrid:
            return self.store.search(qvec, k=k, source_prefix=sp)
        cand = max(k, self.cfg.hybrid_candidates)
        vector_hits = self.store.search(qvec, k=cand, source_prefix=sp)
        text_hits = self.store.search_text(query, k=cand, source_prefix=sp)
        if text_hits:
            fused = _rrf_fuse(vector_hits, text_hits, self.cfg.rrf_k, cand)
        else:
            fused = vector_hits[:cand]
        if self.reranker is not None:
            return self.reranker.rerank(query, fused, k)
        return fused[:k]

    def list_sources(self) -> list[dict]:
        return self.store.list_sources()

    def doctor(self, fix: bool = False) -> dict:
        """Check the cached manifest against the table's real contents.

        The manifest is a sidecar that can desync from the table (crash mid-write,
        manual deletion). Report any drift; with fix=True, rebuild the manifest
        from the table so list/count are correct again.
        """
        manifest = {row["source"]: row["chunks"] for row in self.store.list_sources()}
        truth = self.store.table_manifest()
        in_sync = manifest == truth
        result = {
            "in_sync": in_sync,
            "manifest_documents": len(manifest),
            "manifest_chunks": sum(manifest.values()),
            "table_documents": len(truth),
            "table_chunks": sum(truth.values()),
        }
        if not in_sync and fix:
            # Reuse the scan we already did rather than re-scanning the table.
            result["repaired"] = self.store.reconcile(truth=truth)
        return result

    def stats(self) -> dict:
        # Report BOTH the index-time model (persisted, None until first index)
        # and the live query model, so a consumer can detect a mismatch that
        # would silently break similarity — without re-deriving either itself.
        info = self.store.model_info() or {}
        return {
            "index_backend": info.get("backend"),
            "index_model": info.get("model"),
            "query_backend": self.cfg.embed_backend,
            "query_model": self.cfg.embed_model,
            "data_dir": str(self.cfg.data_dir),
            "documents": len(self.store.list_sources()),
            "chunks": self.store.count(),
            # BM25 health — search_text degrades to [] silently, so this is the
            # visible signal that "hybrid" has quietly become vector-only.
            "fts": self.store.fts_status(),
        }
