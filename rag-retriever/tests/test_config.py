"""Config defaults — chunk size must match each backend's model window.

bge-small-zh-v1.5 (local fastembed default) caps at 512 tokens, so the default
chunk budget must stay under it; bge-m3 (ollama/openai) handles 8192, so it can
be larger. An explicit RAG_CHUNK_TOKENS always wins.
"""

from __future__ import annotations

from rag_retriever.config import Config


def _clear(monkeypatch):
    for var in ("RAG_EMBED_BACKEND", "RAG_CHUNK_TOKENS", "RAG_EMBED_MODEL"):
        monkeypatch.delenv(var, raising=False)


def test_local_default_chunk_fits_512_window(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RAG_EMBED_BACKEND", "local")
    assert Config.load().chunk_tokens == 384


def test_remote_default_chunk_is_larger(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RAG_EMBED_BACKEND", "ollama")
    assert Config.load().chunk_tokens == 800


def test_explicit_chunk_tokens_overrides_backend_default(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("RAG_EMBED_BACKEND", "local")
    monkeypatch.setenv("RAG_CHUNK_TOKENS", "1234")
    assert Config.load().chunk_tokens == 1234


def test_chunk_strategy_defaults_to_structure(monkeypatch):
    monkeypatch.delenv("RAG_CHUNK_STRATEGY", raising=False)
    assert Config.load().chunk_strategy == "structure"


def test_chunk_strategy_env_override(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_STRATEGY", "token")
    assert Config.load().chunk_strategy == "token"


def test_hybrid_defaults(monkeypatch):
    for var in ("RAG_HYBRID", "RAG_RRF_K", "RAG_HYBRID_CANDIDATES"):
        monkeypatch.delenv(var, raising=False)
    cfg = Config.load()
    assert cfg.hybrid is True
    assert cfg.rrf_k == 60
    assert cfg.hybrid_candidates == 50


def test_hybrid_can_be_disabled(monkeypatch):
    monkeypatch.setenv("RAG_HYBRID", "0")
    assert Config.load().hybrid is False


def test_parent_context_defaults_off(monkeypatch):
    monkeypatch.delenv("RAG_PARENT_CONTEXT", raising=False)
    monkeypatch.delenv("RAG_PARENT_TOKENS", raising=False)
    from rag_retriever.config import Config
    cfg = Config.load()
    assert cfg.parent_context is False
    assert cfg.parent_tokens == 1600


def test_parent_context_env_on(monkeypatch):
    monkeypatch.setenv("RAG_PARENT_CONTEXT", "1")
    from rag_retriever.config import Config
    assert Config.load().parent_context is True


def test_parent_tokens_floored_to_twice_child(monkeypatch):
    # A parent must be materially larger than a child; a too-small value is floored.
    monkeypatch.setenv("RAG_CHUNK_TOKENS", "800")
    monkeypatch.setenv("RAG_PARENT_TOKENS", "500")
    from rag_retriever.config import Config
    assert Config.load().parent_tokens == 1600  # max(500, 800*2)
