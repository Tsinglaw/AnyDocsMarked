"""Orchestration: ingest (extract -> chunk -> embed -> store) and search.

This is the whole "front half of RAG". There is deliberately no LLM here —
search() returns passages; the calling agent does the reasoning.
"""

from __future__ import annotations

from pathlib import Path

from .chunk import chunk_text
from .config import Config
from .embed import get_embedder
from .extract import extract_text, iter_files
from .frontmatter import read_frontmatter, select_fields
from .store import VectorStore


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

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = get_embedder(self.cfg)
        return self._embedder

    def index_file(self, path: str | Path, source_root: str | Path | None = None) -> dict:
        """Extract, chunk, embed, and store one file. Re-indexes cleanly.

        If source_root is given, the stored `source` is the file path relative to
        that root, with POSIX separators (e.g. `_md/合同/采购.md`) so downstream
        anchors stay stable across machines and OSes. Otherwise it's the absolute
        path (backward-compatible default).
        """
        path = Path(path).resolve()
        source = _relative_source(path, source_root)
        text = extract_text(path)
        if not text:
            return {"source": source, "indexed": False, "chunks": 0,
                    "reason": "no extractable text (scanned image without OCR?)"}
        chunks = chunk_text(text, self.cfg.chunk_tokens, self.cfg.chunk_overlap)
        vectors = self.embedder.embed_documents(chunks)
        meta = select_fields(read_frontmatter(path), self.cfg.metadata_fields)
        self.store.delete_source(source)
        n = self.store.add(source, chunks, vectors, meta=meta)
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
        # Record the index-time model once per run (not per file).
        if indexed:
            self.store.record_model(self.cfg.embed_backend, self.cfg.embed_model)
        return {
            "path": str(Path(path).resolve()),
            "files_seen": len(files),
            "files_indexed": len(indexed),
            "files_skipped": len(skipped),
            "total_chunks": sum(r["chunks"] for r in indexed),
            "skipped": skipped,
        }

    def search(self, query: str, k: int = 5) -> list[dict]:
        """Return the top-k most relevant chunks for a query. No answer generation."""
        if not query.strip():
            return []
        qvec = self.embedder.embed_query(query)
        return self.store.search(qvec, k=k)

    def list_sources(self) -> list[dict]:
        return self.store.list_sources()

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
        }
