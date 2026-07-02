from pathlib import Path
import io
import subprocess
import zipfile

import pytest

from makeitdown import ocr_mineru
from makeitdown.models import ConversionResult
from makeitdown.ocr_mineru import MinerULocal, read_mineru_markdown, MinerUCloud, _safe_extract_zip


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


def test_safe_extract_zip_rejects_zip_slip(tmp_path):
    # A crafted member escaping the destination via `..` must be rejected.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../evil.md", "pwned")
    buf.seek(0)
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(buf) as zf:
        with pytest.raises(RuntimeError, match="zip-slip"):
            _safe_extract_zip(zf, dest)
    assert not (tmp_path / "evil.md").exists()  # nothing written outside dest


def test_safe_extract_zip_allows_normal_members(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("scan/auto/scan.md", "ok")
    buf.seek(0)
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(buf) as zf:
        _safe_extract_zip(zf, dest)
    assert (dest / "scan" / "auto" / "scan.md").read_text() == "ok"


def test_local_run_surfaces_stderr(monkeypatch, tmp_path):
    def boom(*a, **k):
        raise subprocess.CalledProcessError(returncode=2, cmd="mineru", stderr="model not found detail")

    monkeypatch.setattr(ocr_mineru.subprocess, "run", boom)
    eng = MinerULocal()
    with pytest.raises(RuntimeError, match="model not found detail"):
        eng._run_mineru(tmp_path / "x.pdf", tmp_path)


def test_cloud_requires_token():
    with pytest.raises(ValueError):
        MinerUCloud(token="")


def test_cloud_convert_full_flow(monkeypatch, tmp_path):
    f = tmp_path / "scan.pdf"
    f.write_bytes(b"%PDF-1.4 fake")

    # Build a fake result zip containing one markdown file.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("scan/auto/scan.md", "# 合同\n\n金额八十万元")
    zip_bytes = buf.getvalue()

    eng = MinerUCloud(token="tok", poll_interval=0)

    monkeypatch.setattr(eng, "_request_upload", lambda name: ("batch1", "https://signed/put"))
    uploaded = {}
    monkeypatch.setattr(eng, "_upload", lambda url, path: uploaded.update(url=url))
    monkeypatch.setattr(eng, "_poll", lambda batch_id: "https://cdn/result.zip")

    class _Resp:
        status_code = 200
        content = zip_bytes
        def raise_for_status(self): pass

    monkeypatch.setattr("makeitdown.ocr_mineru.requests.get", lambda url, timeout=60: _Resp())

    result = eng.convert(f)
    assert "金额八十万元" in result.text
    assert result.engine == "mineru-cloud"
    assert uploaded["url"] == "https://signed/put"
