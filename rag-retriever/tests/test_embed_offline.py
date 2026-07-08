import fastembed


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
