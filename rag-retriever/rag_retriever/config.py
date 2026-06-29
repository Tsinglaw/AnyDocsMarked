"""Configuration, loaded from environment variables with sane defaults.

Everything is overridable via env so the same code serves a fully-local
(fastembed / Ollama) setup or a domestic cloud API (SiliconFlow) setup.
The embedding backend is chosen at runtime via RAG_EMBED_BACKEND.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str) -> str:
    value = os.getenv(name)
    return value if value is not None and value.strip() else default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def split_csv(s: str) -> tuple[str, ...]:
    """Parse a comma-separated list into a tuple of non-empty trimmed fields."""
    return tuple(f.strip() for f in s.split(",") if f.strip())


# Default embedding model per backend. bge-m3 is strong on Chinese + long text.
# Default embedding model per backend.
# - local (fastembed) has no bge-m3; bge-small-zh-v1.5 is the safe Chinese option.
#   For higher quality stay local with intfloat/multilingual-e5-large or
#   jinaai/jina-embeddings-v3, or use ollama/openai for true bge-m3.
# - ollama / openai both serve real bge-m3 (best Chinese + long-text).
_DEFAULT_MODEL = {
    "local": "BAAI/bge-small-zh-v1.5",
    "ollama": "bge-m3",
    "openai": "BAAI/bge-m3",  # SiliconFlow hosts bge-m3 under this id
}

# Default chunk size per backend, sized to the model's input window so chunks
# aren't silently truncated at embed time. The local fastembed default
# (bge-small-zh-v1.5) caps at 512 tokens; tiktoken (o200k) over-counts CJK vs
# the model's tokenizer, so 384 leaves headroom. bge-m3 (ollama/openai) handles
# 8192, so the larger 800 keeps more context per chunk. Override via
# RAG_CHUNK_TOKENS regardless of backend.
_DEFAULT_CHUNK_TOKENS = {"local": 384, "ollama": 800, "openai": 800}


@dataclass(frozen=True)
class Config:
    # Which embedding backend: "local" (fastembed) | "ollama" | "openai" (openai-compatible)
    embed_backend: str
    embed_model: str

    # ollama
    ollama_url: str

    # openai-compatible (e.g. SiliconFlow)
    openai_base_url: str
    openai_api_key: str

    # storage
    data_dir: Path

    # chunking (token-based; bge-m3 handles up to 8192 tokens)
    chunk_tokens: int
    chunk_overlap: int

    # frontmatter fields to carry through as per-hit metadata (domain-agnostic).
    # Empty = none. The retriever does not interpret these; callers do.
    metadata_fields: tuple[str, ...] = ()

    # Max texts per request to the HTTP embedders (ollama/openai). A big document
    # can yield hundreds of chunks; batching keeps each request under provider
    # payload/timeout limits. Ignored by the local (fastembed) backend, which
    # batches internally.
    embed_batch_size: int = 64

    # chunking strategy: "structure" (heading/table/legal-marker aware) | "token"
    chunk_strategy: str = "structure"

    @classmethod
    def load(cls) -> "Config":
        backend = _env("RAG_EMBED_BACKEND", "local").lower()
        if backend not in _DEFAULT_MODEL:
            raise ValueError(
                f"RAG_EMBED_BACKEND must be one of {list(_DEFAULT_MODEL)}, got '{backend}'"
            )
        model = _env("RAG_EMBED_MODEL", _DEFAULT_MODEL[backend])
        data_dir = Path(_env("RAG_DATA_DIR", str(Path.home() / ".rag-retriever" / "data")))
        return cls(
            embed_backend=backend,
            embed_model=model,
            ollama_url=_env("RAG_OLLAMA_URL", "http://localhost:11434"),
            openai_base_url=_env("RAG_OPENAI_BASE_URL", "https://api.siliconflow.cn/v1"),
            openai_api_key=_env("RAG_OPENAI_API_KEY", ""),
            data_dir=data_dir,
            chunk_tokens=_env_int("RAG_CHUNK_TOKENS", _DEFAULT_CHUNK_TOKENS[backend]),
            chunk_overlap=_env_int("RAG_CHUNK_OVERLAP", 100),
            metadata_fields=split_csv(_env("RAG_METADATA_FIELDS", "")),
            embed_batch_size=_env_int("RAG_EMBED_BATCH_SIZE", 64),
            chunk_strategy=_env("RAG_CHUNK_STRATEGY", "structure").lower(),
        )
