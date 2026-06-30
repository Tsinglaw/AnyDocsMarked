from pathlib import Path

import pytest

from makeitdown.models import ConversionResult
from makeitdown.ocr_mineru import MinerULocal, read_mineru_markdown


def test_read_markdown_concatenates_md_files(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "doc.md").write_text("# 标题\n\n正文一", encoding="utf-8")
    (tmp_path / "b.md").write_text("正文二", encoding="utf-8")
    text, pages = read_mineru_markdown(tmp_path)
    assert "正文一" in text and "正文二" in text
    assert pages is None


def test_read_markdown_raises_when_empty(tmp_path):
    with pytest.raises(RuntimeError):
        read_mineru_markdown(tmp_path)


def test_engine_label_and_availability():
    assert MinerULocal().engine_label == "mineru"
    assert isinstance(MinerULocal.is_available(), bool)


def test_convert_runs_cli_then_reads_markdown(monkeypatch, tmp_path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    eng = MinerULocal()

    def fake_run(path, out_dir):
        # emulate the mineru CLI writing markdown into the output dir
        (Path(out_dir) / "scan.md").write_text("# 合同\n\n金额五十万元", encoding="utf-8")

    monkeypatch.setattr(eng, "_run_mineru", fake_run)
    result = eng.convert(f)
    assert isinstance(result, ConversionResult)
    assert "金额五十万元" in result.text
    assert result.engine == "mineru"
