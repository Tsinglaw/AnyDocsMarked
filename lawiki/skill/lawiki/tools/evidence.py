#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三路取证一条命令（确定性，仅标准库）——问答的前闸门。

把 qa.md「多路并行取证」中可脚本化的三路压成一条命令，agent 答题前必跑：
  python evidence.py <案件根目录> "<问题>" [--terms "50万,张三,第八条"] [-k 8]

三路（wiki 路是 agent 自由导航——index → wikilink → graph.py——刻意不在此内）：
  rag     语义检索（经 rag.py 单一入口；不可用时 rag_available:false，其余照常）
  grep    --terms 各精确词在 _md/ 的逐行命中（向量按语义检索常漏的法条号/
          姓名/金额/案号；查无的词列在 not_found——「grep 也没有」≠「忘了查」）
  outline 每份 _md/ 的标题树（问题措辞与原文用词不同时按结构导航）

恒输出 JSON；RAG 降级也绝不空手（grep + outline 零依赖始终可用）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import islice
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))  # 同目录兄弟模块
sys.path.insert(0, str(_HERE.parent / "lint"))  # 与锚点校验同一套归一化/frontmatter 解析
import outline  # noqa: E402
import rag  # noqa: E402
from lint import _frontmatter, norm  # noqa: E402

# 每个精确词最多带回的命中行数：常用字撞进高频词（如"元"）时防证据包爆炸。
_MAX_HITS_PER_TERM = 20
_QUALITY_RE = re.compile(r"^quality:\s*(\S+)", re.MULTILINE)


def _source_quality(text: str) -> str | None:
    """从 _md 文件的前导 frontmatter 读 quality 字段（轻量，不解析 YAML）。"""
    m = _QUALITY_RE.search(_frontmatter(text))
    return m.group(1) if m else None


def split_terms(raw: str) -> list[str]:
    """逗号分隔的精确词（中英文逗号都认）。"""
    return [t.strip() for t in re.split(r"[,，]", raw) if t.strip()]


def _term_hits(files: list[tuple[str, list[tuple[str, str]], str | None]],
               term: str, nterm: str):
    """逐文件逐行产出 term 的命中项（惰性——调用方用 islice 封顶即可早停）。"""
    for rel, lines, quality in files:
        for raw, nline in lines:
            if nterm in nline:
                snippet = " ".join(raw.split())
                yield {"term": term, "source": rel, "text": snippet,
                       "anchor": rag.build_anchor(rel, snippet, quality),
                       "unverified": quality == "suspect"}


def grep_terms(case: Path, terms: list[str]) -> dict:
    """对每个精确词逐行扫 _md/**/*.md。匹配按 lint 的 norm 归一化做（中文无需
    分词；"50000" 命中原文 "50,000"——逗号/空白/全半角是格式噪声，与锚点校验
    同一套标准）；锚点片段取**原始行逐字**（折叠空白成单行），故必过 lint。
    命中带来源 quality 的「未核验」标注；查无的词列在 not_found（显式的
    「查过且没有」，agent 才可放心说未找到）；超上限的词列在 truncated。"""
    result: dict = {"hits": [], "not_found": [], "truncated": []}
    if not terms:
        return result  # 没有精确词就不必扫盘
    files: list[tuple[str, list[tuple[str, str]], str | None]] = []  # (相对路径, [(原始行,归一行)], quality)
    md_dir = case / "_md"
    if md_dir.is_dir():
        for f in sorted(md_dir.rglob("*.md")):
            text = f.read_text(encoding="utf-8", errors="replace")
            lines = [(ln, norm(ln)) for ln in text.splitlines()]
            files.append((f.relative_to(case).as_posix(), lines, _source_quality(text)))
    for term in terms:
        nterm = norm(term)
        # 归一化后为空的词（纯标点）没有可匹配的实质，直接记查无
        term_hits = (list(islice(_term_hits(files, term, nterm), _MAX_HITS_PER_TERM + 1))
                     if nterm else [])
        if len(term_hits) > _MAX_HITS_PER_TERM:
            result["truncated"].append(term)
            del term_hits[_MAX_HITS_PER_TERM:]
        if term_hits:
            result["hits"].extend(term_hits)
        else:
            result["not_found"].append(term)
    return result


def gather(case: Path, question: str, terms: list[str], k: int) -> dict:
    """三路证据包。rag 字段自带 rag_available 供 agent 分流（见 rag.py）。"""
    return {
        "question": question,
        "rag": rag.search_case(case, question, k=k),
        "grep": grep_terms(case, terms),
        "outline": outline.build_case_outline(case),
    }


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 重定向默认 GBK
    except Exception:
        pass
    p = argparse.ArgumentParser(prog="evidence.py", description="lawiki 三路取证（前闸门）")
    p.add_argument("case")
    p.add_argument("question")
    p.add_argument("--terms", default="",
                   help="逗号分隔的精确词（金额/姓名/法条号/案号——向量常漏的）")
    p.add_argument("-k", type=int, default=8)
    args = p.parse_args(argv[1:])
    bundle = gather(Path(args.case), args.question, split_terms(args.terms), args.k)
    print(json.dumps(bundle, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
