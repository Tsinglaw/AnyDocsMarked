#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单文档结构导航层(outline，确定性，仅标准库）。

把 `_md/` 里每份 markdown 的 ATX 标题(`#`..`######`)解析成嵌套"目录树"，
作为介于 `_md/`(原文)与 `wiki/`(综合)之间的导航地图。用途:
① 提问时先按结构定位章节再取证(对抗"措辞不同→向量漏检"的遗漏类错误);
② 建库时逐节覆盖以减少遗漏;③ 没装 rag-retriever / 无 embedding 时的降级检索。

零依赖、零 LLM、零向量——故置于 lawiki 核心(与 lint 同级)，**始终可用**。

用法:
  python outline.py <案件根目录>   # 遍历 _md/，输出 [{source, outline}]
  python outline.py <某个.md文件>  # 输出该文件的标题树
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_HEADING = re.compile(r"^(#{1,6})(?:\s+(.*))?$")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _flat_headings(text: str) -> list[dict]:
    """逐行扫出标题(跳过围栏代码块)；先不算 line_end。"""
    out: list[dict] = []
    in_fence = False
    for i, line in enumerate(text.splitlines(), start=1):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if not m or not (m.group(2) or "").strip():
            continue  # 需 `# ` 后有标题文字；`#tag`(无空格)不算
        out.append({"title": m.group(2).strip(), "level": len(m.group(1)),
                    "line_start": i})
    return out


def parse_outline(text: str) -> list[dict]:
    """解析成嵌套树。node = {title, level, line_start, line_end, children}。
    line_end = 该节延伸到下一个同级或更高级标题前(或文末)。"""
    heads = _flat_headings(text)
    total = len(text.splitlines())
    # line_end:下一个 level<=自身 的标题之前;否则到文末
    for idx, h in enumerate(heads):
        end = total
        for nxt in heads[idx + 1:]:
            if nxt["level"] <= h["level"]:
                end = nxt["line_start"] - 1
                break
        h["line_end"] = end
        h["children"] = []
    # 按 level 用栈建嵌套
    roots: list[dict] = []
    stack: list[dict] = []
    for h in heads:
        while stack and stack[-1]["level"] >= h["level"]:
            stack.pop()
        (stack[-1]["children"] if stack else roots).append(h)
        stack.append(h)
    return roots


def build_case_outline(root: str | Path) -> list[dict]:
    """遍历 <root>/_md 下所有 .md，返回 [{source(相对POSIX), outline}]。"""
    root = Path(root)
    md_dir = root / "_md"
    out: list[dict] = []
    if not md_dir.is_dir():
        return out
    for f in sorted(md_dir.rglob("*.md")):
        text = f.read_text(encoding="utf-8", errors="replace")
        out.append({"source": f.relative_to(root).as_posix(),
                    "outline": parse_outline(text)})
    return out


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 重定向默认 GBK
    except Exception:
        pass
    if len(argv) != 2:
        print("用法:python outline.py <案件根目录|某个.md文件>", file=sys.stderr)
        return 2
    p = Path(argv[1])
    if p.is_file():
        result = parse_outline(p.read_text(encoding="utf-8", errors="replace"))
    else:
        result = build_case_outline(p)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
