#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""组装 lawiki-bundle 发布包（仅标准库）。

把三部分的**源码**当场从本地权威仓库复制进一个自包含 zip（每次发版重新
vendor 最新源码，避免副本陈旧）：

  lawiki-bundle-v<版本>.zip
  ├── skill/lawiki/          # agent 加载的 skill
  ├── vendor/rag-retriever/  # 源码 + LICENSE
  ├── vendor/makeitdown/     # 源码 + LICENSE
  ├── install.py             # 安装器
  ├── MANIFEST.txt           # 三部分各自的 commit 哈希
  └── README.txt

排除 .git/.venv/缓存/构建产物/案件数据/大模型——bundle 是纯源码（几 MB）。
OCR/embedding 模型在安装或首次运行时下载。

用法：python scripts/build_bundle.py [--version 1.0.0]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

LAWIKI = Path(__file__).resolve().parent.parent          # lawiki/ 子项目目录
REPO = LAWIKI.parent                                      # 单仓库根（AnyDocsMarked）
RAG_SRC = REPO / "rag-retriever"                          # 同仓库的姊妹子项目
MD_SRC = REPO / "makeitdown"

# 复制时跳过的目录/文件名（按名匹配，任意层级）
_EXCLUDE = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    "dist", "build", "node_modules", ".rag", ".rag-retriever",
    "原始资料", "_md", ".mypy_cache",
}
_EXCLUDE_SUFFIX = {".pyc", ".pyo", ".zip"}


def _ignore(_dir: str, names: list[str]) -> set[str]:
    out = set()
    for n in names:
        if n in _EXCLUDE or any(n.endswith(s) for s in _EXCLUDE_SUFFIX) or n.endswith(".egg-info"):
            out.add(n)
    return out


def _copy_tree(src: Path, dst: Path) -> None:
    if not src.is_dir():
        sys.exit(f"找不到源目录：{src}")
    shutil.copytree(src, dst, ignore=_ignore)


def _git_head(repo: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             capture_output=True, text=True)
        return out.stdout.strip() or "(无 git)"
    except Exception:
        return "(无 git)"


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK
    except Exception:
        pass
    ap = argparse.ArgumentParser(prog="build_bundle.py")
    ap.add_argument("--version", default="1.0.0")
    args = ap.parse_args(argv[1:])

    out_zip = LAWIKI / "dist" / f"lawiki-bundle-v{args.version}.zip"
    out_zip.parent.mkdir(exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / f"lawiki-bundle-v{args.version}"
        root.mkdir()

        # 1) skill
        _copy_tree(LAWIKI / "skill" / "lawiki", root / "skill" / "lawiki")
        # 2) vendor 全部三个（除 skill 外的两个外部项目）
        _copy_tree(RAG_SRC, root / "vendor" / "rag-retriever")
        _copy_tree(MD_SRC, root / "vendor" / "makeitdown")
        # 3) 安装器
        shutil.copy2(LAWIKI / "install.py", root / "install.py")

        # 4) MANIFEST（溯源三部分的 commit）
        (root / "MANIFEST.txt").write_text(
            f"lawiki-bundle v{args.version}\n"
            f"lawiki        {_git_head(LAWIKI)}\n"
            f"rag-retriever {_git_head(RAG_SRC)}\n"
            f"makeitdown    {_git_head(MD_SRC)}\n",
            encoding="utf-8")

        # 5) README
        (root / "README.txt").write_text(
            "lawiki bundle —— 法律案件资料整理 + 交叉验证问答\n\n"
            "用法：\n"
            "1. 解压本包。\n"
            "2. 让你的 AI agent 加载 skill/lawiki（Claude Code/Copilot 自动识别 SKILL.md；\n"
            "   Codex 等把 skill/lawiki 内容作系统指令或置入案件目录作 AGENTS.md）。\n"
            "3. 首次使用：运行 `python install.py`（agent 会按 setup.md 自动跑）安装\n"
            "   makeitdown 与 rag-retriever；按提示选 OCR 方式。\n"
            "4. 把法律文件放进案件目录的 原始资料/，对 agent 说「整理案件资料」。\n"
            "5. 之后可就案件提问，agent 会用 wiki 与 RAG 原文交叉验证作答。\n\n"
            "vendor/ 下为 rag-retriever、makeitdown 源码（含各自 LICENSE）。\n",
            encoding="utf-8")

        # 打包
        if out_zip.exists():
            out_zip.unlink()
        with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(root.rglob("*")):
                if f.is_file():
                    zf.write(f, f.relative_to(root.parent).as_posix())

    size_mb = out_zip.stat().st_size / 1e6
    print(f"✓ 已生成 {out_zip}  ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
