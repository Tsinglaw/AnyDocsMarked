# -*- coding: utf-8 -*-
"""rag wrapper 回归测试（stdlib unittest，零依赖，任何 python 可跑）。

锁住纯逻辑：锚点拼装、quality→未核验、模型一致性判定、当前模型解析。
关键一条：wrapper 拼出的锚点喂给**真实 lint** 必须 0 违规——证明问答引用机器可校验。
"""
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "lint"))

import rag  # noqa: E402
from lint import scan_case  # noqa: E402


class BuildAnchorTests(unittest.TestCase):
    def test_plain_anchor(self):
        self.assertEqual(
            rag.build_anchor("_md/a.md", "双方于 2021 年签约"),
            "〔来源: _md/a.md：「双方于 2021 年签约」〕",
        )

    def test_suspect_appends_unverified(self):
        a = rag.build_anchor("_md/a.md", "金额 5 万元", quality="suspect")
        self.assertTrue(a.endswith("」〕（未核验）"))

    def test_non_suspect_quality_no_suffix(self):
        a = rag.build_anchor("_md/a.md", "x", quality="clean")
        self.assertFalse(a.endswith("（未核验）"))


class EnrichHitTests(unittest.TestCase):
    def test_enriched_anchor_passes_real_lint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = "本案欠款金额为人民币 50000 元，借款人为张三。"
            (root / "_md").mkdir(parents=True)
            (root / "_md" / "借条.md").write_text(src, encoding="utf-8")

            hit = {"source": "_md/借条.md",
                   "text": "欠款金额为人民币 50000 元", "metadata": {}}
            enriched = rag.enrich_hit(hit)

            (root / "wiki").mkdir()
            (root / "wiki" / "p.md").write_text(
                f"- 事实 {enriched['anchor']}\n", encoding="utf-8")

            total, violations, warnings = scan_case(root)
            self.assertEqual(violations, [], msg=str(violations))
            self.assertEqual(total, 1)

    def test_anchor_from_multiline_chunk_is_single_line_and_lint_recognized(self):
        # rag chunks carry the whole file (frontmatter + newlines). lint anchors
        # are single-line (ANCHOR_RE's . does not cross \n), so a multi-line
        # snippet would be silently UN-recognized. The default anchor must be a
        # single line and still locate verbatim in the source.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = "---\nquality: suspect\n---\n本案欠款金额为人民币 50000 元，借款人为张三。"
            (root / "_md").mkdir(parents=True)
            (root / "_md" / "借条.md").write_text(src, encoding="utf-8")

            hit = {"source": "_md/借条.md", "text": src,
                   "metadata": {"quality": "suspect"}}
            enriched = rag.enrich_hit(hit)
            self.assertNotIn("\n", enriched["anchor"])

            (root / "wiki").mkdir()
            (root / "wiki" / "p.md").write_text(
                f"- 事实 {enriched['anchor']}\n", encoding="utf-8")
            total, violations, _ = scan_case(root)
            self.assertEqual(total, 1, "锚点未被 lint 识别（多行？）")
            self.assertEqual(violations, [], msg=str(violations))

    def test_suspect_hit_flagged_unverified(self):
        hit = {"source": "_md/a.md", "text": "x", "metadata": {"quality": "suspect"}}
        enriched = rag.enrich_hit(hit)
        self.assertTrue(enriched["unverified"])
        self.assertTrue(enriched["anchor"].endswith("（未核验）"))

    def test_breadcrumb_prefix_stripped_anchor_passes_real_lint(self):
        # 结构分块的命中 text 带标题面包屑前缀（rag-retriever pipeline._compose），
        # 而源文件里两个标题之间隔着正文——面包屑不是连续文本，直接进锚点必挂 lint。
        # enrich_hit 须按 metadata.heading_path 剥前缀后再取默认片段。
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = ("---\nsource: 判决书.pdf\n---\n# 民事判决书\n\n"
                   "（2023）京0105民初12345号\n\n## 本院认为\n\n"
                   "本院认为，被告应向原告偿还借款本金人民币 50000 元。\n")
            (root / "_md").mkdir(parents=True)
            (root / "_md" / "判决书.md").write_text(src, encoding="utf-8")

            hit = {"source": "_md/判决书.md",
                   "text": ("民事判决书 > 本院认为\n\n"
                            "本院认为，被告应向原告偿还借款本金人民币 50000 元。"),
                   "metadata": {"heading_path": "民事判决书 > 本院认为"}}
            enriched = rag.enrich_hit(hit)

            (root / "wiki").mkdir()
            (root / "wiki" / "p.md").write_text(
                f"- 事实 {enriched['anchor']}\n", encoding="utf-8")
            total, violations, _ = scan_case(root)
            self.assertEqual(total, 1)
            self.assertEqual(violations, [], msg=str(violations))

    def test_heading_path_absent_snippet_unchanged(self):
        hit = {"source": "_md/a.md", "text": "正文片段", "metadata": {}}
        self.assertIn("「正文片段」", rag.enrich_hit(hit)["anchor"])

    def test_heading_path_prefix_mismatch_not_stripped(self):
        # heading_path 存在但 text 不以它开头（防御边界）——不剥、不崩。
        hit = {"source": "_md/a.md", "text": "正文片段",
               "metadata": {"heading_path": "别的标题"}}
        self.assertIn("「正文片段」", rag.enrich_hit(hit)["anchor"])


class ModelStatusTests(unittest.TestCase):
    # stats supplies BOTH index-time and live query model; wrapper only compares.
    def test_ok_when_models_match(self):
        ok, _ = rag.model_status({
            "index_backend": "local", "index_model": "m",
            "query_backend": "local", "query_model": "m"})
        self.assertTrue(ok)

    def test_not_indexed_when_index_model_none(self):
        ok, reason = rag.model_status({
            "index_backend": None, "index_model": None,
            "query_backend": "local", "query_model": "m"})
        self.assertFalse(ok)
        self.assertIn("索引", reason)

    def test_mismatch_detected(self):
        ok, reason = rag.model_status({
            "index_backend": "local", "index_model": "old",
            "query_backend": "ollama", "query_model": "new"})
        self.assertFalse(ok)
        self.assertIn("不一致", reason)


if __name__ == "__main__":
    unittest.main()
