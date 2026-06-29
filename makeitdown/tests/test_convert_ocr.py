from pathlib import Path
import makeitdown.convert_ocr as co
from makeitdown.models import ConversionResult, OCRUnavailableError


class _FakeBackend:
    def __init__(self, label):
        self._label = label

    def convert(self, path):
        return ConversionResult(text=f"md from {self._label}", engine=self._label)


def test_auto_prefers_local_when_available(monkeypatch):
    monkeypatch.setattr(co.LocalOCR, "is_available", staticmethod(lambda: True))
    monkeypatch.setattr(co, "LocalOCR", lambda **k: _FakeBackend("local:pp-structurev3"))
    d = co.OCRDispatcher(engine="auto", token=None)
    r = d.convert(Path("x.png"))
    assert r.engine == "local:pp-structurev3"


def test_auto_falls_back_to_cloud_when_local_missing(monkeypatch):
    monkeypatch.setattr(co.LocalOCR, "is_available", staticmethod(lambda: False))
    monkeypatch.setattr(co, "CloudOCR", lambda **k: _FakeBackend("cloud:paddleocr-vl-1.6"))
    d = co.OCRDispatcher(engine="auto", token="TKN")
    r = d.convert(Path("x.png"))
    assert r.engine == "cloud:paddleocr-vl-1.6"


def test_auto_raises_clear_error_when_neither(monkeypatch):
    monkeypatch.setattr(co.LocalOCR, "is_available", staticmethod(lambda: False))
    d = co.OCRDispatcher(engine="auto", token=None)
    try:
        d.convert(Path("x.png"))
        assert False, "expected OCRUnavailableError"
    except OCRUnavailableError as e:
        msg = str(e)
        assert "makeitdown[local]" in msg
        assert "PADDLEOCR_AISTUDIO_TOKEN" in msg


def test_explicit_cloud_without_token_raises(monkeypatch):
    d = co.OCRDispatcher(engine="cloud", token=None)
    try:
        d.convert(Path("x.png"))
        assert False, "expected OCRUnavailableError"
    except OCRUnavailableError as e:
        assert "PADDLEOCR_AISTUDIO_TOKEN" in str(e)


# ---------------------------------------------------------------------------
# Cross-check orchestration tests (Task 5)
# ---------------------------------------------------------------------------

class _FakePrimary:
    def convert(self, path):
        return ConversionResult(text="金额为500000元", engine="local:pp-structurev3", pages=1)


def _dispatcher_with(monkeypatch, primary, verifier_text):
    d = co.OCRDispatcher(engine="local", cross_check=True, cross_check_ratio=0.1)
    monkeypatch.setattr(d, "_resolve_backend", lambda: primary)
    # verifier returns a ConversionResult with the given text
    class _V:
        @staticmethod
        def is_available():
            return True
        def convert(self, path):
            return ConversionResult(text=verifier_text, engine="mineru", pages=1)
    monkeypatch.setattr(d, "_make_verifier", lambda: _V())
    return d


def test_crosscheck_flags_digit_mismatch(monkeypatch, tmp_path):
    d = _dispatcher_with(monkeypatch, _FakePrimary(), verifier_text="金额为800000元")
    result = d.convert(tmp_path / "x.pdf")
    assert result.text == "金额为500000元"          # primary content untouched
    assert result.cross_check_reasons              # disagreement flagged
    assert "mineru" in result.engine


def test_crosscheck_clean_when_engines_agree(monkeypatch, tmp_path):
    d = _dispatcher_with(monkeypatch, _FakePrimary(), verifier_text="金额为500000元")
    result = d.convert(tmp_path / "x.pdf")
    assert not result.cross_check_reasons


def test_crosscheck_degrades_when_verifier_unavailable(monkeypatch, tmp_path):
    d = co.OCRDispatcher(engine="local", cross_check=True)
    monkeypatch.setattr(d, "_resolve_backend", lambda: _FakePrimary())
    monkeypatch.setattr(d, "_make_verifier", lambda: None)  # no verifier
    result = d.convert(tmp_path / "x.pdf")
    assert result.text == "金额为500000元"          # never lose the conversion
    assert result.cross_check_reasons == ["双OCR互校跳过：校验引擎 MinerU 不可用"]
