# -*- coding: utf-8 -*-
"""outline 解析回归测试(stdlib unittest,零依赖,任何 python 可跑)。

锁住:标题嵌套、节区间(line_start/line_end)、围栏代码块内 # 不误判、
按案件遍历 _md 输出相对 POSIX source。
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import outline  # noqa: E402


class ParseOutlineTests(unittest.TestCase):
    def test_nesting_and_ranges(self):
        text = "# A\ntext\n## B\nb text\n## C\n# D\n"
        tree = outline.parse_outline(text)
        self.assertEqual([n["title"] for n in tree], ["A", "D"])
        a = tree[0]
        self.assertEqual((a["level"], a["line_start"], a["line_end"]), (1, 1, 5))
        self.assertEqual([c["title"] for c in a["children"]], ["B", "C"])
        b, c = a["children"]
        self.assertEqual((b["line_start"], b["line_end"]), (3, 4))
        self.assertEqual((c["line_start"], c["line_end"]), (5, 5))
        d = tree[1]
        self.assertEqual((d["line_start"], d["line_end"]), (6, 6))
        self.assertEqual(d["children"], [])

    def test_fenced_code_hash_is_not_heading(self):
        text = "# Real\n```\n# NotHeading\n```\n## Sub\n"
        tree = outline.parse_outline(text)
        self.assertEqual([n["title"] for n in tree], ["Real"])
        self.assertEqual([c["title"] for c in tree[0]["children"]], ["Sub"])

    def test_hash_without_space_is_not_heading(self):
        # `#tag` (no space) is not an ATX heading
        tree = outline.parse_outline("#nothing\n# Yes\n")
        self.assertEqual([n["title"] for n in tree], ["Yes"])

    def test_empty_or_headingless(self):
        self.assertEqual(outline.parse_outline(""), [])
        self.assertEqual(outline.parse_outline("just text\nmore\n"), [])


class BuildCaseOutlineTests(unittest.TestCase):
    def test_walks_md_with_relative_posix_source(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            f = root / "_md" / "合同" / "采购.md"
            f.parent.mkdir(parents=True)
            f.write_text("# 采购框架协议\n## 第八条 责任\n违约责任…\n", encoding="utf-8")
            (root / "_md" / "note.md").write_text("正文无标题\n", encoding="utf-8")

            result = outline.build_case_outline(root)
            by_src = {r["source"]: r for r in result}
            self.assertIn("_md/合同/采购.md", by_src)          # 相对 POSIX
            titles = [n["title"] for n in by_src["_md/合同/采购.md"]["outline"]]
            self.assertEqual(titles, ["采购框架协议"])
            self.assertEqual(
                by_src["_md/合同/采购.md"]["outline"][0]["children"][0]["title"],
                "第八条 责任")
            self.assertEqual(by_src["_md/note.md"]["outline"], [])  # 无标题→空


if __name__ == "__main__":
    unittest.main()
