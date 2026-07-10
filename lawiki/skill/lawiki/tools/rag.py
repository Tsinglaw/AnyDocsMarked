#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lawiki ↔ rag-retriever 薄 wrapper（确定性，仅标准库）。

lawiki 经此**单一入口**消费 RAG：subprocess 调 rag-retriever CLI、只认其
JSON 契约，不 import 其 Python、不用其 MCP server（与 lawiki 对接 makeitdown 同构）。
所有 lawiki/法律专属约定收在这里——rag-retriever 本体保持通用。

子命令：
  python rag.py index  <案件根目录>            # 索引 _md/ → .rag/（确定性，转换后跑）
  python rag.py search <案件根目录> "<问题>" [-k 8]
                                               # 检索 → 每条命中拼成 lawiki 锚点 + quality

职责（对应设计 3.2）：① 绑 _md/ 根、.rag/ 目录 ② 把命中拼成 lawiki 锚点
③ 把 quality:suspect 翻成「（未核验）」 ④ 降级检测（未装/未建索引/模型不一致 →
明确返回「无 RAG」，问答退化仅 wiki）⑤ 增量重索引交给 rag-retriever 的 delete+add。

输出恒为 JSON：search 成功 → {"rag_available": true, "hits": [...]}；
任何降级 → {"rag_available": false, "reason": "..."}（退出码 0，供 agent 分流）。
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

# lawiki 固定透传的 frontmatter 字段（makeitdown 写的质量信号）。
_METADATA_FIELDS = "quality"


# ───────────────────────── 纯逻辑（可单测） ─────────────────────────

def build_anchor(source: str, snippet: str, quality: str | None = None) -> str:
    """拼成 lawiki 锚点；可疑来源追加「（未核验）」（铁律 AMBIGUOUS）。"""
    anchor = f"〔来源: {source}：「{snippet}」〕"
    if quality == "suspect":
        anchor += "（未核验）"
    return anchor


def default_snippet(text: str) -> str:
    """从 chunk 逐字 text 取一个**单行**默认片段：去掉前导 frontmatter 块、
    折叠空白。lint 锚点是单行（ANCHOR_RE 的 . 不跨行），且归一化丢弃空白——故
    折叠后的片段既单行、又能在源文件逐字定位。agent 通常会进一步缩到具体支撑句。
    """
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":  # 去前导 frontmatter 块（含闭合 fence 行尾空格）
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1:]
                break
    return " ".join(" ".join(lines).split())


def enrich_hit(hit: dict) -> dict:
    """给一条检索命中补上 lawiki 锚点（单行默认片段）与 unverified 标记。

    片段取自命中的逐字 text（rag-retriever 从 _md/ 逐字切出），但要先剥掉
    结构分块拼在前面的标题面包屑（"标题A > 标题B\\n\\n正文"）——面包屑在源
    文件里不是连续文本，进锚点必挂 lint。剥完的片段经 lint 归一化后必能在
    源文件定位。`text` 保持原样（含面包屑/frontmatter/换行）供 agent 通读
    并自行挑选更精确的支撑句。
    """
    meta = hit.get("metadata") or {}
    quality = meta.get("quality")
    text = hit["text"]
    heading = meta.get("heading_path")
    if heading and text.startswith(heading + "\n\n"):
        text = text[len(heading) + 2:]
    return {
        **hit,
        "anchor": build_anchor(hit["source"], default_snippet(text), quality),
        "unverified": quality == "suspect",
    }


def model_status(stats: dict) -> tuple[bool, str]:
    """比对索引时模型与当前查询模型——两者都由 rag-retriever `stats` 提供，
    本 wrapper 不自行推导模型（避免镜像其默认表）。返回 (ok, 不一致原因)。"""
    idx = (stats.get("index_backend"), stats.get("index_model"))
    qry = (stats.get("query_backend"), stats.get("query_model"))
    if idx[1] is None:
        return False, "尚未建索引（.rag 为空或无效），先运行：rag.py index <案件>"
    if idx != qry:
        return False, (
            f"索引模型({idx[0]}/{idx[1]}) 与当前查询模型({qry[0]}/{qry[1]}) "
            f"不一致——相似度会失真，须用同一模型重建索引（rebuild）")
    return True, ""


# ───────────────────────── subprocess 层（调 rag-retriever CLI） ─────────────────────────

def _rag_base() -> list[str]:
    """rag-retriever 调用前缀。默认 `rag-retriever`；可用 LAWIKI_RAG_CMD 覆盖
    （如 `uv run --project D:/.../rag-retriever rag-retriever`）。"""
    return shlex.split(os.environ.get("LAWIKI_RAG_CMD", "rag-retriever"))


def _run_rag(data_dir: Path, args: list[str]) -> subprocess.CompletedProcess | None:
    """跑一条 rag-retriever 子命令。未装（命令找不到）→ None（触发降级）。"""
    cmd = [*_rag_base(), "--data-dir", str(data_dir), *args]
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except FileNotFoundError:
        return None


def _paths(case: Path) -> tuple[Path, Path]:
    return case / "_md", case / ".rag"


def _degrade(reason: str) -> dict:
    return {"rag_available": False, "reason": f"{reason}——问答退化为仅 wiki。"}


def index_case(case: Path) -> dict:
    md_dir, data_dir = _paths(case)
    if not md_dir.is_dir():
        return {"ok": False, "reason": f"找不到 {md_dir}（先用 makeitdown 转换）"}
    proc = _run_rag(data_dir, [
        "index", str(md_dir), "--source-root", str(case),
        "--metadata-fields", _METADATA_FIELDS,
        "--exclude", "report.json",  # makeitdown 的台账，非源文，别进 RAG
    ])
    if proc is None:
        return {"ok": False, "reason": "未安装 rag-retriever（或不在 PATH / LAWIKI_RAG_CMD）"}
    if proc.returncode != 0:
        return {"ok": False, "reason": (proc.stderr or proc.stdout).strip()}
    try:
        return {"ok": True, **json.loads(proc.stdout)}
    except ValueError:
        return {"ok": True, "raw": proc.stdout.strip()}


def search_case(case: Path, query: str, k: int = 8) -> dict:
    """降级检测 → 检索 → 拼锚点。恒返回带 rag_available 的 dict。"""
    _md_dir, data_dir = _paths(case)

    if not data_dir.is_dir():
        return _degrade("尚未建索引（无 .rag/）")

    # 模型一致性闸门：stats 同时报索引时模型与当前查询模型，本 wrapper 只比对
    stats_proc = _run_rag(data_dir, ["stats"])
    if stats_proc is None:
        return _degrade("未安装 rag-retriever")
    try:
        stats = json.loads(stats_proc.stdout)
    except ValueError:
        stats = {}
    ok, reason = model_status(stats)
    if not ok:
        return {"rag_available": False, "reason": reason}

    proc = _run_rag(data_dir, ["search", query, "-k", str(k), "--json"])
    if proc is None:
        return _degrade("未安装 rag-retriever")
    if proc.returncode != 0:
        return _degrade((proc.stderr or proc.stdout).strip())
    try:
        hits = json.loads(proc.stdout)
    except ValueError:
        hits = []
    return {"rag_available": True, "hits": [enrich_hit(h) for h in hits]}


# ───────────────────────── CLI ─────────────────────────

def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 重定向默认 GBK
    except Exception:
        pass
    p = argparse.ArgumentParser(prog="rag.py", description="lawiki RAG wrapper")
    sub = p.add_subparsers(dest="cmd", required=True)
    pi = sub.add_parser("index", help="索引 _md/ → .rag/")
    pi.add_argument("case")
    ps = sub.add_parser("search", help="检索并拼成 lawiki 锚点")
    ps.add_argument("case")
    ps.add_argument("query")
    ps.add_argument("-k", type=int, default=8)
    args = p.parse_args(argv[1:])

    case = Path(args.case)
    if args.cmd == "index":
        result = index_case(case)
    else:
        result = search_case(case, args.query, k=args.k)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
