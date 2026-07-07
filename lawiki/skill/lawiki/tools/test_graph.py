# -*- coding: utf-8 -*-
"""graph.py 回归测试（stdlib unittest，零依赖，任何 python 可跑）。"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import graph  # noqa: E402


def _make_wiki(root: Path):
    """两个连通分量 + 导航页（应被排除）。
    分量1: 甲 —[某合同关系]— 丙 ; 分量2: 北京晨山 —— 借款事实。
    index.md / log.md / 时间线 链接一切，但必须被排除，不得桥接两分量。"""
    w = root / "wiki"
    (w / "案件主体").mkdir(parents=True)
    (w / "法律关系").mkdir(parents=True)
    (w / "法律事实").mkdir(parents=True)
    (w / "时间线").mkdir(parents=True)

    (w / "index.md").write_text(
        "# 索引\n[[甲]] [[丙]] [[北京晨山]] [[借款事实]]\n", encoding="utf-8")
    (w / "log.md").write_text("# 操作日志\n", encoding="utf-8")
    (w / "时间线" / "总览.md").write_text(
        "---\n类型: 时间线\n---\n# 时间线\n[[借款事实]] [[甲]]\n", encoding="utf-8")

    (w / "案件主体" / "甲.md").write_text(
        "---\n类型: 案件主体\naliases: [甲总]\n---\n# 甲\n身份信息。\n", encoding="utf-8")
    (w / "案件主体" / "丙.md").write_text(
        "---\n类型: 案件主体\n---\n# 丙\n身份信息。\n", encoding="utf-8")
    (w / "案件主体" / "北京晨山.md").write_text(
        "---\n类型: 案件主体\naliases: [晨山]\n---\n# 北京晨山\n相关法律事实：[[借款事实]]\n",
        encoding="utf-8")
    # 关系页用 |显示 和 #标题 形式，验证归一
    (w / "法律关系" / "某合同关系.md").write_text(
        "---\n类型: 法律关系\n---\n# 某合同关系\n主体：[[甲|甲方]]、[[丙#基本信息]]\n",
        encoding="utf-8")
    (w / "法律事实" / "借款事实.md").write_text(
        "---\n类型: 法律事实\n---\n# 借款事实\n事实：借款。链接时间线[[总览]]\n", encoding="utf-8")


class BuildGraphTests(unittest.TestCase):
    def _graph(self):
        d = tempfile.mkdtemp()
        _make_wiki(Path(d))
        return graph.build_graph(Path(d))

    def test_only_entity_pages_are_nodes(self):
        g = self._graph()
        self.assertEqual(
            set(g["nodes"]),
            {"甲", "丙", "北京晨山", "某合同关系", "借款事实"})
        # navigation pages excluded
        self.assertNotIn("index", g["nodes"])
        self.assertNotIn("log", g["nodes"])
        self.assertNotIn("总览", g["nodes"])

    def test_node_types(self):
        g = self._graph()
        self.assertEqual(g["nodes"]["甲"], "案件主体")
        self.assertEqual(g["nodes"]["某合同关系"], "法律关系")
        self.assertEqual(g["nodes"]["借款事实"], "法律事实")

    def test_edges_undirected_and_display_anchor_stripped(self):
        g = self._graph()
        # 某合同关系 —[[甲|甲方]]/[[丙#..]]— both directions
        self.assertIn("某合同关系", g["adj"]["甲"])
        self.assertIn("甲", g["adj"]["某合同关系"])
        self.assertIn("丙", g["adj"]["某合同关系"])
        # 北京晨山 —[[借款事实]]— undirected
        self.assertIn("借款事实", g["adj"]["北京晨山"])
        self.assertIn("北京晨山", g["adj"]["借款事实"])

    def test_links_to_excluded_pages_make_no_edge(self):
        g = self._graph()
        # 借款事实 links [[总览]] (timeline, excluded) -> no such neighbor
        self.assertNotIn("总览", g["adj"]["借款事实"])
        # index.md links everything but is excluded -> does not bridge components
        for nbrs in g["adj"].values():
            self.assertNotIn("index", nbrs)

    def test_missing_wiki_returns_none(self):
        d = tempfile.mkdtemp()  # no wiki/ inside
        self.assertIsNone(graph.build_graph(Path(d)))


class NeighborsTests(unittest.TestCase):
    def _graph(self):
        d = tempfile.mkdtemp()
        _make_wiki(Path(d))
        return graph.build_graph(Path(d))

    def test_neighbors_by_alias(self):
        g = self._graph()
        r = graph.neighbors(g, "晨山")           # alias of 北京晨山
        self.assertEqual(r["node"], "北京晨山")
        self.assertEqual(r["类型"], "案件主体")
        self.assertEqual([n["page"] for n in r["neighbors"]], ["借款事实"])

    def test_neighbors_sorted_and_typed(self):
        g = self._graph()
        r = graph.neighbors(g, "某合同关系")
        self.assertEqual([n["page"] for n in r["neighbors"]], ["丙", "甲"])  # sorted
        self.assertEqual(r["neighbors"][0]["类型"], "案件主体")

    def test_neighbors_unknown_page_errors(self):
        g = self._graph()
        self.assertIn("error", graph.neighbors(g, "不存在的页"))


class PathTests(unittest.TestCase):
    def _graph(self):
        d = tempfile.mkdtemp()
        _make_wiki(Path(d))
        return graph.build_graph(Path(d))

    def test_multi_hop_path(self):
        g = self._graph()
        r = graph.find_path(g, "甲", "丙")
        self.assertTrue(r["connected"])
        self.assertEqual(r["hops"], 2)
        self.assertEqual([n["page"] for n in r["path"]], ["甲", "某合同关系", "丙"])

    def test_path_via_alias(self):
        g = self._graph()
        r = graph.find_path(g, "甲总", "丙")       # 甲总 is alias of 甲
        self.assertTrue(r["connected"])
        self.assertEqual(r["path"][0]["page"], "甲")

    def test_disconnected_components(self):
        g = self._graph()
        # 甲-cluster and 北京晨山-cluster are only "joined" via index/timeline,
        # which are excluded -> no real path.
        r = graph.find_path(g, "甲", "北京晨山")
        self.assertFalse(r["connected"])

    def test_path_unknown_page_errors(self):
        g = self._graph()
        self.assertIn("error", graph.find_path(g, "甲", "不存在"))


if __name__ == "__main__":
    unittest.main()
