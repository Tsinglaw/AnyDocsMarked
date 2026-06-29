from pathlib import Path

from makeitdown.ocr_mineru import MinerULocal
from makeitdown.models import ConversionResult


def test_engine_label():
    assert MinerULocal().engine_label == "mineru"


def test_is_available_is_boolean():
    assert isinstance(MinerULocal.is_available(), bool)


def test_convert_wraps_runner_output(monkeypatch, tmp_path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    eng = MinerULocal()
    monkeypatch.setattr(eng, "_run_mineru", lambda p: ("# 标题\n\n正文内容", 3))
    result = eng.convert(f)
    assert isinstance(result, ConversionResult)
    assert result.text == "# 标题\n\n正文内容"
    assert result.pages == 3
    assert result.engine == "mineru"
