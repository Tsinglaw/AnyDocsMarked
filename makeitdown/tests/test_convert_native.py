from pathlib import Path
import makeitdown.convert_native as cn
from makeitdown.models import ConversionResult


class _FakeResult:
    def __init__(self, text):
        self.text_content = text


class _FakeMarkItDown:
    def __init__(self, *a, **k):
        pass

    def convert(self, path):
        return _FakeResult(f"converted:{Path(path).name}")


def test_each_thread_gets_its_own_converter(monkeypatch):
    import threading

    class _CountingMarkItDown:
        instances = []

        def __init__(self, *a, **k):
            _CountingMarkItDown.instances.append(self)

        def convert(self, path):
            return _FakeResult("x")

    monkeypatch.setattr(cn, "MarkItDown", _CountingMarkItDown)
    monkeypatch.setattr(cn, "_local", __import__("threading").local())

    seen = {}

    def worker(name):
        seen[name] = id(cn._get_converter())
        # second call in same thread reuses the same instance
        assert id(cn._get_converter()) == seen[name]

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert seen["a"] != seen["b"]
    assert len(_CountingMarkItDown.instances) == 2


def test_convert_native_returns_conversion_result(tmp_path, monkeypatch):
    monkeypatch.setattr(cn, "MarkItDown", _FakeMarkItDown)
    p = tmp_path / "a.docx"
    p.write_text("x", encoding="utf-8")
    result = cn.convert(p)
    assert isinstance(result, ConversionResult)
    assert result.text == "converted:a.docx"
    assert result.engine == "markitdown"
    assert result.assets == {}
    assert result.pages is None  # non-PDF: no page count


def test_pdf_page_count_skips_non_pdf(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("x", encoding="utf-8")
    assert cn._pdf_page_count(p) is None


def test_pdf_page_count_best_effort_on_unreadable(tmp_path):
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4 not a real pdf")  # fitz can't parse -> None, no raise
    assert cn._pdf_page_count(p) is None


def test_convert_native_carries_pdf_page_count(tmp_path, monkeypatch):
    monkeypatch.setattr(cn, "MarkItDown", _FakeMarkItDown)
    monkeypatch.setattr(cn, "_pdf_page_count", lambda path: 3)
    p = tmp_path / "doc.pdf"
    p.write_bytes(b"%PDF-1.4")
    assert cn.convert(p).pages == 3
