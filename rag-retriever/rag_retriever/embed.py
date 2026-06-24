"""Pluggable embedding backends: local (fastembed) | ollama | openai-compatible.

All three expose the same interface so the rest of the pipeline never cares
where vectors come from. Index-time and query-time MUST use the same backend +
model, or similarity is meaningless — switching models requires re-indexing.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

import httpx

from .config import Config


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class LocalEmbedder:
    """In-process embeddings via fastembed (ONNX, no torch, no server)."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding

        supported = {m["model"] for m in TextEmbedding.list_supported_models()}
        if model_name not in supported:
            raise ValueError(
                f"fastembed does not support '{model_name}'.\n"
                f"Pick one of (Chinese-capable first): "
                f"{sorted(s for s in supported if 'bge' in s.lower() or 'e5' in s.lower() or 'm3' in s.lower())}\n"
                f"Set RAG_EMBED_MODEL to a supported id, or switch RAG_EMBED_BACKEND."
            )
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


class OllamaEmbedder:
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

    def _embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for batch in _batched(texts, self._batch_size):
            out.extend(self._embed_batch(batch))
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class OpenAICompatEmbedder:
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

    def _embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for batch in _batched(texts, self._batch_size):
            out.extend(self._embed_batch(batch))
        return out

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


@lru_cache(maxsize=1)
def get_embedder(cfg: Config) -> Embedder:
    if cfg.embed_backend == "local":
        return LocalEmbedder(cfg.embed_model)
    if cfg.embed_backend == "ollama":
        return OllamaEmbedder(cfg.embed_model, cfg.ollama_url, cfg.embed_batch_size)
    if cfg.embed_backend == "openai":
        return OpenAICompatEmbedder(
            cfg.embed_model, cfg.openai_base_url, cfg.openai_api_key, cfg.embed_batch_size
        )
    raise ValueError(f"Unknown embed backend: {cfg.embed_backend}")
