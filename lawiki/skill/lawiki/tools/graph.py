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
