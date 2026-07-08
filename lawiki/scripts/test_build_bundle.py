# -*- coding: utf-8 -*-
"""build_bundle 回归测试（stdlib unittest，零依赖）。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import build_bundle  # noqa: E402


class IgnoreTests(unittest.TestCase):
    def test_excludes_vendored_by_default(self):
        ig = build_bundle._make_ignore(offline=False)
        out = ig("d", ["_models", "_tiktoken", "foo.py"])
        self.assertIn("_models", out)
        self.assertIn("_tiktoken", out)
        self.assertNotIn("foo.py", out)

    def test_keeps_vendored_when_offline(self):
        ig = build_bundle._make_ignore(offline=True)
        out = ig("d", ["_models", "_tiktoken", "foo.py"])
        self.assertNotIn("_models", out)
        self.assertNotIn("_tiktoken", out)
        # normal junk still excluded in both modes
        self.assertIn("__pycache__", ig("d", ["__pycache__"]))
        self.assertIn("x.pyc", ig("d", ["x.pyc"]))


class NameTests(unittest.TestCase):
    def test_zip_name(self):
        self.assertEqual(build_bundle._zip_name("1.1.2", False),
                         "anydocsmarked-v1.1.2.zip")
        self.assertEqual(build_bundle._zip_name("1.1.2", True),
                         "anydocsmarked-v1.1.2-offline.zip")


class VendoredCheckTests(unittest.TestCase):
    def test_absent(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(build_bundle._has_vendored_models(Path(d)))

    def test_present(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            m = Path(d) / "rag_retriever" / "_models" / "BAAI--x"
            m.mkdir(parents=True)
            (m / "model.onnx").write_bytes(b"x")
            self.assertTrue(build_bundle._has_vendored_models(Path(d)))


if __name__ == "__main__":
    unittest.main()
