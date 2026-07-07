# Lawiki graph.py Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, zero-dependency `graph.py` tool to lawiki that traverses the wiki's existing `[[wikilinks]]` to answer `neighbors` and shortest-`path` (multi-hop) relationship questions.

**Architecture:** One new stdlib-only module `tools/graph.py` (sibling to `outline.py`), agent-invoked via qa.md. Task 1 builds the library (parsers + `build_graph` + `neighbors` + `find_path`) with function-level unittest coverage. Task 2 adds the CLI `main()` (exit codes, arg validation) and the qa.md integration note, with CLI-level tests.

**Tech Stack:** Python 3.11, standard library only (`re`, `json`, `collections.deque`, `pathlib`, `unittest`). No third-party deps, no LLM, no network. Tests run with plain `python` (unittest), no pytest required.

## Global Constraints

- Zero dependency, zero LLM, zero network. `graph.py` must import only the standard library (mirroring `outline.py`).
- Zero fabrication: edges are existing `[[wikilinks]]`; the tool reports connectivity only, never asserts a relationship.
- Nodes = entity pages only: frontmatter `类型 ∈ {案件主体, 法律关系, 法律事实}`. `index.md`/`log.md`/`时间线` must be excluded (they would short-circuit paths).
- Edges are UNDIRECTED. Links whose target (after alias resolution) is not an entity node are ignored (lint owns deadlink policing).
- Link target normalization matches lint: strip `|display` and `#anchor` (`m.group(1).split("|")[0].split("#")[0].strip()`).
- Node id = filename stem. Alias resolution from frontmatter `aliases: [...]`.
- Output is JSON (`ensure_ascii=False, indent=2`). Unknown page → `{"error": ...}` + non-zero exit; disconnected pair is a valid answer (`connected: false`, exit 0).
- Do not modify `lint.py`, `outline.py`, `rag.py`, rag-retriever, or makeitdown.
- Work on branch `feat/absorb-nexusrag-batch3`. Tests run with `python` from `lawiki/skill/lawiki/tools/`.

---

### Task 1: graph.py library — parsers, build_graph, neighbors, find_path

**Files:**
- Create: `lawiki/skill/lawiki/tools/graph.py`
- Create: `lawiki/skill/lawiki/tools/test_graph.py`

**Interfaces:**
- Consumes: standard library only.
- Produces:
  - `ENTITY_TYPES = {"案件主体", "法律关系", "法律事实"}`
  - `build_graph(root) -> dict | None` — returns `{"nodes": {stem: 类型}, "adj": {stem: [sorted neighbor stems]}, "alias": {name_or_alias: stem}}`, or `None` if `<root>/wiki` is missing.
  - `neighbors(graph: dict, name: str) -> dict`
  - `find_path(graph: dict, a: str, b: str) -> dict`
  - helpers `_frontmatter`, `_page_type`, `_aliases`.

- [ ] **Step 1: Write the failing tests**

Create `lawiki/skill/lawiki/tools/test_graph.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `lawiki/skill/lawiki/tools/`): `python test_graph.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'graph'` (module not created yet).

- [ ] **Step 3: Implement the library**

Create `lawiki/skill/lawiki/tools/graph.py`:

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wiki 关系图谱导航层（graph，确定性，仅标准库）。

把 `wiki/` 里的实体页（案件主体/法律关系/法律事实）当节点、页面间的
`[[wikilink]]` 当无向边，构成一张确定性关系图。导航页（index.md/log.md/时间线）
不入图，避免把任意两点短接。用于 qa.md「wiki 路」回答关系/多跳问题：
① neighbors —— 某页直接关联哪些实体页；② path —— 两页之间的最短连通路径。

零依赖、零 LLM——每条边都是 wiki 里已存在、且被 lint 校验过的 `[[wikilink]]`；
本工具只报连通路径，不断言法律关系（含义仍由 agent/人读沿途页锚点确认）。

用法:
  python graph.py <案件根目录> neighbors "<页名或别名>"
  python graph.py <案件根目录> path "<A>" "<B>"
"""
from __future__ import annotations

import json
import re
import sys
from collections import deque
from pathlib import Path

ENTITY_TYPES = {"案件主体", "法律关系", "法律事实"}

WIKILINK_RE = re.compile(r"\[\[([^\]\n]+?)\]\]")
ALIASES_RE = re.compile(r"aliases:\s*\[(.*?)\]")
TYPE_RE = re.compile(r"^\s*类型:\s*(\S+)", re.M)


def _frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def _page_type(text: str) -> str:
    m = TYPE_RE.search(_frontmatter(text))
    return m.group(1).strip() if m else ""


def _aliases(text: str) -> list[str]:
    m = ALIASES_RE.search(_frontmatter(text))
    if not m:
        return []
    return [a.strip() for a in m.group(1).split(",") if a.strip()]


def build_graph(root) -> dict | None:
    """构图。返回 {"nodes": {stem: 类型}, "adj": {stem: [邻居stem,已排序]},
    "alias": {名或别名: stem}}；缺 <root>/wiki 目录返回 None。"""
    wiki = Path(root) / "wiki"
    if not wiki.is_dir():
        return None
    nodes: dict[str, str] = {}
    raw: dict[str, str] = {}
    alias: dict[str, str] = {}
    for md in sorted(wiki.rglob("*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        t = _page_type(text)
        if t in ENTITY_TYPES:
            stem = md.stem
            nodes[stem] = t
            raw[stem] = text
            alias[stem] = stem
            for a in _aliases(text):
                alias[a] = stem
    adj: dict[str, set] = {s: set() for s in nodes}
    for stem, text in raw.items():
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).split("|")[0].split("#")[0].strip()
            if not target:
                continue
            tstem = alias.get(target)
            if tstem and tstem in nodes and tstem != stem:
                adj[stem].add(tstem)
                adj[tstem].add(stem)  # 无向
    return {"nodes": nodes,
            "adj": {s: sorted(v) for s, v in adj.items()},
            "alias": alias}


def neighbors(graph: dict, name: str) -> dict:
    stem = graph["alias"].get(name)
    if not stem or stem not in graph["nodes"]:
        return {"error": f"未找到页面: {name}"}
    return {"node": stem, "类型": graph["nodes"][stem],
            "neighbors": [{"page": n, "类型": graph["nodes"][n]}
                          for n in graph["adj"][stem]]}


def find_path(graph: dict, a: str, b: str) -> dict:
    sa = graph["alias"].get(a)
    sb = graph["alias"].get(b)
    if not sa or sa not in graph["nodes"]:
        return {"error": f"未找到页面: {a}"}
    if not sb or sb not in graph["nodes"]:
        return {"error": f"未找到页面: {b}"}
    prev: dict[str, str | None] = {sa: None}
    q = deque([sa])
    while q:
        cur = q.popleft()
        if cur == sb:
            break
        for nb in graph["adj"][cur]:  # 已排序 → 确定性
            if nb not in prev:
                prev[nb] = cur
                q.append(nb)
    if sb not in prev:
        return {"from": sa, "to": sb, "connected": False}
    seq: list[str] = []
    node: str | None = sb
    while node is not None:
        seq.append(node)
        node = prev[node]
    seq.reverse()
    return {"from": sa, "to": sb, "connected": True, "hops": len(seq) - 1,
            "path": [{"page": s, "类型": graph["nodes"][s]} for s in seq]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `lawiki/skill/lawiki/tools/`): `python test_graph.py -v`
Expected: PASS — all BuildGraph/Neighbors/Path tests pass.

- [ ] **Step 5: Commit**

```bash
git add lawiki/skill/lawiki/tools/graph.py lawiki/skill/lawiki/tools/test_graph.py
git commit -m "feat(lawiki): graph.py library — deterministic wiki relationship traversal

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: CLI entrypoint + qa.md integration

**Files:**
- Modify: `lawiki/skill/lawiki/tools/graph.py` (append `main()` + `__main__` guard)
- Modify: `lawiki/skill/lawiki/tools/test_graph.py` (add CLI-level tests)
- Modify: `lawiki/skill/lawiki/references/qa.md` (add graph.py to the "wiki 路" step)

**Interfaces:**
- Consumes: `build_graph`, `neighbors`, `find_path` from Task 1.
- Produces: `main(argv: list[str]) -> int` — exit 0 on success (including `connected: false`), 1 on `{"error": ...}` / missing wiki, 2 on bad arguments.

- [ ] **Step 1: Write the failing CLI tests**

Append to `lawiki/skill/lawiki/tools/test_graph.py` (before the `if __name__` guard):

```python
import io
import contextlib


class MainTests(unittest.TestCase):
    def _root(self):
        d = tempfile.mkdtemp()
        _make_wiki(Path(d))
        return d

    def _run(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = graph.main(argv)
        return code, buf.getvalue()

    def test_cli_path_ok_exit_zero(self):
        code, out = self._run(["graph.py", self._root(), "path", "甲", "丙"])
        self.assertEqual(code, 0)
        self.assertIn('"connected": true', out)

    def test_cli_neighbors_ok(self):
        code, out = self._run(["graph.py", self._root(), "neighbors", "晨山"])
        self.assertEqual(code, 0)
        self.assertIn("借款事实", out)

    def test_cli_disconnected_is_exit_zero(self):
        code, out = self._run(["graph.py", self._root(), "path", "甲", "北京晨山"])
        self.assertEqual(code, 0)               # a valid answer, not an error
        self.assertIn('"connected": false', out)

    def test_cli_unknown_page_exit_nonzero(self):
        code, out = self._run(["graph.py", self._root(), "neighbors", "没有这页"])
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_cli_missing_wiki_exit_one(self):
        d = tempfile.mkdtemp()                  # no wiki/
        code, out = self._run(["graph.py", d, "neighbors", "甲"])
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_cli_bad_args_exit_two(self):
        code, _ = self._run(["graph.py", self._root(), "path", "甲"])  # path needs 2
        self.assertEqual(code, 2)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python test_graph.py -v`
Expected: FAIL — `AttributeError: module 'graph' has no attribute 'main'`.

- [ ] **Step 3: Implement the CLI**

Append to `lawiki/skill/lawiki/tools/graph.py`:

```python
def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 重定向默认 GBK
    except Exception:
        pass
    usage = ('用法:\n'
             '  python graph.py <案件根> neighbors "<页名或别名>"\n'
             '  python graph.py <案件根> path "<A>" "<B>"')
    if len(argv) < 3:
        print(usage, file=sys.stderr)
        return 2
    root, cmd = argv[1], argv[2]
    g = build_graph(root)
    if g is None:
        print(json.dumps({"error": f"未找到 wiki 目录: {root}/wiki"},
                         ensure_ascii=False, indent=2))
        return 1
    if cmd == "neighbors" and len(argv) == 4:
        result = neighbors(g, argv[3])
    elif cmd == "path" and len(argv) == 5:
        result = find_path(g, argv[3], argv[4])
    else:
        print(usage, file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python test_graph.py -v`
Expected: PASS — all Task 1 + Task 2 tests pass.

- [ ] **Step 5: Add the qa.md integration note**

In `lawiki/skill/lawiki/references/qa.md`, in "## 第一步 · 多路并行取证", extend the "1. wiki 路" bullet (currently: `**wiki 路**：读 wiki/index.md → 顺 [[wikilink]]/grep 找相关页 → 取其已综合结论 + 既有锚点。`) by appending:

```markdown
   - **关系/多跳问题**（"X 与 Y 有无关系""X 牵涉哪些事实/关系"）用确定性图工具，替代人肉追链：
     ```
     python <SKILL_DIR>/tools/graph.py <案件根> neighbors "<页名>"
     python <SKILL_DIR>/tools/graph.py <案件根> path "<A>" "<B>"
     ```
     只走 wiki 中已存在、经 lint 校验的 `[[wikilink]]`（导航页/时间线不入图），返回邻居或最短连通路径；拿到路径后仍读沿途页锚点取证。零依赖、始终可用。
```

- [ ] **Step 6: Run tests once more**

Run: `python test_graph.py -v`
Expected: PASS (unchanged — the qa.md edit is docs-only).

- [ ] **Step 7: Commit**

```bash
git add lawiki/skill/lawiki/tools/graph.py lawiki/skill/lawiki/tools/test_graph.py lawiki/skill/lawiki/references/qa.md
git commit -m "feat(lawiki): graph.py CLI + qa.md wiki-path integration

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- Run tests with plain `python` (they use stdlib `unittest`; no pytest needed): from `lawiki/skill/lawiki/tools/`, `python test_graph.py -v`.
- Also run the sibling suites once at the end to confirm no collateral damage: `python test_outline.py` and `python ../lint/test_lint.py` should still pass.
- `build_graph` returning `None` (missing `wiki/`) is distinct from an empty graph; `main` maps `None` → error JSON + exit 1.
- Determinism: `adj` lists are sorted, so BFS in `find_path` breaks ties lexicographically — the same wiki yields the same path every run.
