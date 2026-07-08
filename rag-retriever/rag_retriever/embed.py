"""Pluggable embedding backends: local (fastembed) | ollama | openai-compatible.

All three expose the same interface so the rest of the pipeline never cares
where vectors come from. Index-time and query-time MUST use the same backend +
model, or similarity is meaningless — switching models requires re-indexing.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import httpx

from .config import Config

# Directory where a vendored ONNX copy of the local model is shipped in the
# release bundle so the first index works offline (no HuggingFace download).
# Layout: _models/<model_name with '/' -> '--'>/ containing the files fastembed
# expects (model_optimized.onnx + tokenizer.json/config.json/vocab.txt/...).
_BUNDLED_MODELS_DIR = Path(__file__).resolve().parent / "_models"


def _bundled_model_dir(model_name: str) -> Path:
    return _BUNDLED_MODELS_DIR / model_name.replace("/", "--")


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class LocalEmbedder:
    """In-process embeddings via fastembed (ONNX, no torch, no server)."""

    def __init__(self, model_name: str, model_path: str | None = None):
        from fastembed import TextEmbedding

        supported = {m["model"] for m in TextEmbedding.list_supported_models()}
        if model_name not in supported:
            raise ValueError(
                f"fastembed does not support '{model_name}'.\n"
                f"Pick one of (Chinese-capable first): "
                f"{sorted(s for s in supported if 'bge' in s.lower() or 'e5' in s.lower() or 'm3' in s.lower())}\n"
                f"Set RAG_EMBED_MODEL to a supported id, or switch RAG_EMBED_BACKEND."
            )
        # Prefer a locally vendored copy (release bundle, or RAG_EMBED_MODEL_PATH)
        # so the first index needs no network. specific_model_path short-circuits
        # fastembed's HuggingFace/GCS download entirely; fall back to the normal
        # download only when no local copy is present.
        local_dir = Path(model_path) if model_path else _bundled_model_dir(model_name)
        if local_dir.is_dir():
            self._model = TextEmbedding(
                model_name=model_name,
                specific_model_path=str(local_dir),
                local_files_only=True,
            )
        else:
            self._model = TextEmbedding(model_name=model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text))).tolist()


def _batched(texts: list[str], size: int):
    """Yield successive slices of `texts` of at most `size` (>=1) items."""
    step = max(1, size)
    for i in range(0, len(texts), step):
        yield texts[i:i + step]


class _HttpEmbedder:
    """Shared batching + interface for HTTP-backed embedders. Subclasses set
    ``self._batch_size`` and implement ``_embed_batch`` (one request); the loop,
    the document/query split, and the order contract live here once."""

    _batch_size: int

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def _embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for batch in _batched(texts, self._batch_size):
            out.extend(self._embed_batch(batch))
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class OllamaEmbedder(_HttpEmbedder):
    """Local Ollama server (http). Lighter Python deps, but a daemon must run."""

    def __init__(self, model_name: str, base_url: str, batch_size: int = 64):
        self._model = model_name
        self._url = base_url.rstrip("/") + "/api/embed"
        self._batch_size = batch_size

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = httpx.post(
            self._url, json={"model": self._model, "input": texts}, timeout=120
        )
        resp.raise_for_status()
        return resp.json()["embeddings"]


class OpenAICompatEmbedder(_HttpEmbedder):
    """Any OpenAI-compatible /embeddings endpoint (SiliconFlow, DashScope, etc.)."""

    def __init__(self, model_name: str, base_url: str, api_key: str, batch_size: int = 64):
        if not api_key:
            raise ValueError(
                "RAG_OPENAI_API_KEY is required for the 'openai' backend "
                "(e.g. your SiliconFlow key)."
            )
        self._model = model_name
        self._url = base_url.rstrip("/") + "/embeddings"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._batch_size = batch_size

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        resp = httpx.post(
            self._url,
            json={"model": self._model, "input": texts},
            headers=self._headers,
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        # Preserve input order regardless of provider response ordering.
        data.sort(key=lambda d: d["index"])
        return [d["embedding"] for d in data]


@lru_cache(maxsize=1)
def get_embedder(cfg: Config) -> Embedder:
    if cfg.embed_backend == "local":
        return LocalEmbedder(cfg.embed_model, cfg.embed_model_path or None)
    if cfg.embed_backend == "ollama":
        return OllamaEmbedder(cfg.embed_model, cfg.ollama_url, cfg.embed_batch_size)
    if cfg.embed_backend == "openai":
        return OpenAICompatEmbedder(
            cfg.embed_model, cfg.openai_base_url, cfg.openai_api_key, cfg.embed_batch_size
        )
    raise ValueError(f"Unknown embed backend: {cfg.embed_backend}")
