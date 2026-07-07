from rag_retriever.config import Config
from rag_retriever.rerank import get_reranker


def test_rerank_none_returns_no_reranker(monkeypatch):
    monkeypatch.setenv("RAG_RERANK", "none")
    assert get_reranker(Config.load()) is None


def test_default_rerank_is_none(monkeypatch):
    monkeypatch.delenv("RAG_RERANK", raising=False)
    cfg = Config.load()
    assert cfg.rerank == "none"
    assert get_reranker(cfg) is None


def test_default_rerank_model_is_multilingual(monkeypatch):
    monkeypatch.delenv("RAG_RERANK_MODEL", raising=False)
    cfg = Config.load()
    assert cfg.rerank_model == "BAAI/bge-reranker-v2-m3"
