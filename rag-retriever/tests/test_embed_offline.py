import fastembed
import pytest


class _FakeTE:
    """Records the kwargs LocalEmbedder passes to fastembed.TextEmbedding."""
    last_kwargs: dict = {}

    @staticmethod
    def list_supported_models():
        return [{"model": "BAAI/bge-small-zh-v1.5"}]

    def __init__(self, **kwargs):
        _FakeTE.last_kwargs = dict(kwargs)


def test_local_embedder_offline_when_model_path_given(monkeypatch, tmp_path):
    monkeypatch.setattr(fastembed, "TextEmbedding", _FakeTE)
    from rag_retriever.embed import LocalEmbedder
    LocalEmbedder("BAAI/bge-small-zh-v1.5", model_path=str(tmp_path))  # tmp_path exists
    assert _FakeTE.last_kwargs.get("specific_model_path") == str(tmp_path)
    assert _FakeTE.last_kwargs.get("local_files_only") is True


def test_local_embedder_uses_bundled_dir_when_present(monkeypatch, tmp_path):
    import rag_retriever.embed as e
    monkeypatch.setattr(fastembed, "TextEmbedding", _FakeTE)
    vendored = tmp_path / "BAAI--bge-small-zh-v1.5"
    vendored.mkdir()
    monkeypatch.setattr(e, "_BUNDLED_MODELS_DIR", tmp_path)
    e.LocalEmbedder("BAAI/bge-small-zh-v1.5")  # no model_path -> resolves bundled dir
    assert _FakeTE.last_kwargs.get("specific_model_path") == str(vendored)
    assert _FakeTE.last_kwargs.get("local_files_only") is True


def test_local_embedder_downloads_when_no_vendored(monkeypatch, tmp_path):
    import rag_retriever.embed as e
    monkeypatch.setattr(fastembed, "TextEmbedding", _FakeTE)
    monkeypatch.setattr(e, "_BUNDLED_MODELS_DIR", tmp_path / "nonexistent")
    e.LocalEmbedder("BAAI/bge-small-zh-v1.5")  # no vendored dir -> download branch
    assert "specific_model_path" not in _FakeTE.last_kwargs
    assert _FakeTE.last_kwargs.get("model_name") == "BAAI/bge-small-zh-v1.5"


def test_local_embedder_download_failure_gets_actionable_message(monkeypatch, tmp_path):
    # Real incident (LAWIKI-RAG-001): a sandboxed machine can't reach HuggingFace
    # (or its mirror), so the raw fastembed/huggingface_hub call blows up with a
    # bare ConnectTimeout deep in a stack trace. LocalEmbedder must turn that into
    # a RuntimeError whose message alone tells the user what to do next.
    import rag_retriever.embed as e

    class _BoomTE:
        @staticmethod
        def list_supported_models():
            return [{"model": "BAAI/bge-small-zh-v1.5"}]

        def __init__(self, **kwargs):
            raise ConnectionError("[WinError 10060] connection attempt timed out")

    monkeypatch.setattr(fastembed, "TextEmbedding", _BoomTE)
    monkeypatch.setattr(e, "_BUNDLED_MODELS_DIR", tmp_path / "nonexistent")
    with pytest.raises(RuntimeError) as exc:
        e.LocalEmbedder("BAAI/bge-small-zh-v1.5")
    msg = str(exc.value)
    assert "无法下载 embedding 模型" in msg
    assert "offline" in msg
    assert "HF_ENDPOINT" in msg
    assert "RAG_EMBED_BACKEND" in msg
    assert exc.value.__cause__ is not None  # original exception preserved via `from e`


def test_local_embedder_logs_heads_up_before_download_attempt(monkeypatch, tmp_path, caplog):
    # The notice must fire BEFORE the (possibly slow/hanging) network call, not
    # only after it fails — that's the whole point of the fix (understand a
    # stall immediately instead of diagnosing a timeout after the fact). It's a
    # log record (not a direct print to stderr) so a caller embedding this
    # class as a library — cli.py, the MCP server — can suppress/redirect it;
    # the default "handler of last resort" still surfaces WARNING+ on stderr.
    import rag_retriever.embed as e
    monkeypatch.setattr(fastembed, "TextEmbedding", _FakeTE)
    monkeypatch.setattr(e, "_BUNDLED_MODELS_DIR", tmp_path / "nonexistent")

    with caplog.at_level("WARNING", logger="rag_retriever.embed"):
        e.LocalEmbedder("BAAI/bge-small-zh-v1.5")

    assert len(caplog.records) == 1
    msg = caplog.records[0].getMessage()
    assert "未检测到内置 embedding 模型" in msg
    assert "offline" in msg


def test_local_embedder_no_notice_when_vendored_copy_present(monkeypatch, tmp_path, caplog):
    import rag_retriever.embed as e
    monkeypatch.setattr(fastembed, "TextEmbedding", _FakeTE)
    vendored = tmp_path / "BAAI--bge-small-zh-v1.5"
    vendored.mkdir()
    monkeypatch.setattr(e, "_BUNDLED_MODELS_DIR", tmp_path)

    with caplog.at_level("WARNING", logger="rag_retriever.embed"):
        e.LocalEmbedder("BAAI/bge-small-zh-v1.5")

    assert caplog.records == []
