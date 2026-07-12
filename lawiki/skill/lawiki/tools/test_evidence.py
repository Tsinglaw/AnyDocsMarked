# -*- coding: utf-8 -*-
"""evidence wrapper 回归测试（stdlib unittest，零依赖）。

锁住：grep 精确命中 + 现成锚点过真实 lint、quality→未核验、查无列入 not_found、
每词命中上限、RAG 降级时证据包仍有 grep/outline、gather 输出结构。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "lint"))

import evidence  # noqa: E402
import rag  # noqa: E402
from lint import scan_case  # noqa: E402


def _case(root: Path, name: str, text: str) -> None:
    (root / "_md").mkdir(parents=True, exist_ok=True)
    (root / "_md" / name).write_text(text, encoding="utf-8")


class GrepTermsTests(unittest.TestCase):
    def test_hit_carries_anchor_that_passes_real_lint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "合同.md", "# 合同\n\n第八条 违约方应支付违约金 50000 元。\n")
            result = evidence.grep_terms(root, ["第八条"])
            hits = result["hits"]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["source"], "_md/合同.md")

            (root / "wiki").mkdir()
            (root / "wiki" / "p.md").write_text(
                f"- 事实 {hits[0]['anchor']}\n", encoding="utf-8")
            total, violations, *_ = scan_case(root)
            self.assertEqual((total, violations), (1, []), msg=str(violations))

    def test_suspect_source_marks_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "扫描件.md",
                  "---\nquality: suspect\n---\n借款金额为 88888 元。\n")
            result = evidence.grep_terms(root, ["88888"])
            hit = result["hits"][0]
            self.assertTrue(hit["unverified"])
            self.assertTrue(hit["anchor"].endswith("（未核验）"))

    def test_absent_term_recorded_as_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "a.md", "无关内容。\n")
            result = evidence.grep_terms(root, ["李四"])
            self.assertEqual(result["hits"], [])
            self.assertEqual(result["not_found"], ["李四"])

    def test_per_term_cap_marks_truncated(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "长文.md", "\n".join(f"第{i}行 甲方" for i in range(50)))
            result = evidence.grep_terms(root, ["甲方"])
            self.assertEqual(len(result["hits"]), evidence._MAX_HITS_PER_TERM)
            self.assertEqual(result["truncated"], ["甲方"])

    def test_normalized_match_hits_thousands_separator(self):
        # 金额精确词按 lint 归一化匹配："50000" 须命中原文的 "50,000"
        # （逗号是格式噪声）；锚点片段仍取原始行逐字，故必过 lint。
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "借条.md", "借款人民币50,000元整。\n")
            result = evidence.grep_terms(root, ["50000"])
            hits = result["hits"]
            self.assertEqual(len(hits), 1)
            self.assertIn("50,000", hits[0]["text"])

            (root / "wiki").mkdir()
            (root / "wiki" / "p.md").write_text(
                f"- 事实 {hits[0]['anchor']}\n", encoding="utf-8")
            total, violations, *_ = scan_case(root)
            self.assertEqual((total, violations), (1, []), msg=str(violations))


class GatherTests(unittest.TestCase):
    def test_rag_degraded_bundle_still_has_grep_and_outline(self):
        # 无 .rag/ → search_case 走真实降级路径（不起子进程），grep/outline 照常。
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "合同.md", "# 第一章\n\n甲方为某公司。\n")
            bundle = evidence.gather(root, "甲方是谁", ["甲方"], k=8)
            self.assertFalse(bundle["rag"]["rag_available"])
            self.assertEqual(len(bundle["grep"]["hits"]), 1)
            self.assertEqual(bundle["outline"][0]["source"], "_md/合同.md")
            self.assertEqual(bundle["question"], "甲方是谁")

    def test_rag_available_hits_passed_through(self):
        fake = {"rag_available": True,
                "hits": [{"source": "_md/a.md", "text": "x", "score": 0.9,
                          "anchor": "〔来源: _md/a.md：「x」〕", "unverified": False}]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "a.md", "x\n")
            with mock.patch.object(rag, "search_case", return_value=fake) as m:
                bundle = evidence.gather(root, "问题", [], k=5)
            m.assert_called_once_with(root, "问题", k=5)
            self.assertEqual(bundle["rag"], fake)
            self.assertEqual(bundle["grep"],
                             {"hits": [], "not_found": [], "truncated": []})


class TermSplitTests(unittest.TestCase):
    def test_split_on_ascii_and_chinese_comma(self):
        self.assertEqual(evidence.split_terms("50万, 张三，第八条"),
                         ["50万", "张三", "第八条"])
        self.assertEqual(evidence.split_terms(""), [])


if __name__ == "__main__":
    unittest.main()
