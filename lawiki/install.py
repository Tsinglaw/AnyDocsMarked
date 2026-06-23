#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lawiki bundle 安装器（仅标准库，跨平台）。

随 `lawiki-bundle` 发布，位于 bundle 根目录。agent 在 setup 阶段跑这一条即可
把三部分装好：lawiki skill（无需安装，加载即用）、makeitdown（转换器）、
rag-retriever（RAG 检索）。后两者从 bundle 内 `vendor/` 的源码本地安装。

用法：
  python install.py [--ocr local|cloud] [--dry-run]
                    [--skip-makeitdown] [--skip-rag]

- `--ocr local`（默认）：装本地 PaddleOCR（离线、不需 token、体积大）。
  `--ocr cloud`：装云端版（轻、需百度 AI Studio token，见 setup.md）。
- `--dry-run`：只打印将执行的命令，不真正安装。
- embedding 默认 local（fastembed，离线、无需 key）；换 ollama/openai 见 setup.md。

设计：每步独立、失败不致命（降级哲学）——makeitdown 装不上仍可用预转的 _md/；
rag-retriever 装不上则问答退化「仅 wiki」。退出码恒 0；失败项汇总打印。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent
VENDOR = BUNDLE / "vendor"
TSINGHUA = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _say(msg: str) -> None:
    print(f"[lawiki-install] {msg}", flush=True)


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def _uv_install(spec: str, dry: bool) -> bool:
    """uv tool install <spec>（钉 Python 3.12、走清华源加速）。返回是否成功。

    --python 3.12 同时满足三方约束：makeitdown(>=3.11,<3.13)、rag-retriever(>=3.10)、
    lawiki lint(>=3.11)；不钉则在 3.13+ 默认机器上 makeitdown 会装失败。
    """
    cmd = ["uv", "tool", "install", "--python", "3.12", "--index", TSINGHUA, spec]
    _say("将执行: " + " ".join(cmd))
    if dry:
        return True
    try:
        proc = subprocess.run(cmd, text=True)
        return proc.returncode == 0
    except FileNotFoundError:
        return False


def _verify(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 重定向默认 GBK
    except Exception:
        pass

    p = argparse.ArgumentParser(prog="install.py", description="lawiki bundle installer")
    p.add_argument("--ocr", choices=["local", "cloud"], default="local")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-makeitdown", action="store_true")
    p.add_argument("--skip-rag", action="store_true")
    args = p.parse_args(argv[1:])

    results: list[tuple[str, str]] = []  # (部件, 状态)

    # 0) 前置：Python 3.11+ / uv
    if sys.version_info < (3, 11):
        _say(f"⚠ 需要 Python 3.11+，当前 {sys.version.split()[0]}。请升级后重试。")
        return 0
    if not _have("uv"):
        _say("⚠ 未找到 uv（安装 makeitdown/rag-retriever 需要它）。")
        _say("  Windows: winget install astral-sh.uv ；macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh")
        _say("  装好 uv 后重跑本脚本；在此之前 RAG/转换不可用，但 lawiki 核心仍可用预转的 _md/。")
        return 0

    # 1) makeitdown（转换器）
    md_dir = VENDOR / "makeitdown"
    if args.skip_makeitdown:
        results.append(("makeitdown", "跳过"))
    elif not md_dir.is_dir():
        results.append(("makeitdown", "✗ bundle 内缺 vendor/makeitdown"))
    else:
        extra = "[local]" if args.ocr == "local" else ""
        spec = f"makeitdown{extra} @ {md_dir.as_uri()}"
        _say(f"正在安装 makeitdown（OCR={args.ocr}）……")
        ok = _uv_install(spec, args.dry_run)
        results.append(("makeitdown", "✓" if ok else "✗ 安装失败（可改用预转 _md/）"))

    # 2) rag-retriever（RAG 检索；embedding 默认 local）
    rag_dir = VENDOR / "rag-retriever"
    if args.skip_rag:
        results.append(("rag-retriever", "跳过"))
    elif not rag_dir.is_dir():
        results.append(("rag-retriever", "✗ bundle 内缺 vendor/rag-retriever"))
    else:
        _say("正在安装 rag-retriever（embedding 默认 local，离线）……")
        ok = _uv_install(f"rag-retriever @ {rag_dir.as_uri()}", args.dry_run)
        results.append(("rag-retriever", "✓" if ok else "✗ 安装失败（问答将退化仅 wiki）"))

    # 3) 验证
    if not args.dry_run:
        if _have("makeitdown") and _verify(["makeitdown", "--help"]):
            _say("✓ makeitdown 可用")
        if _have("rag-retriever") and _verify(["rag-retriever", "--help"]):
            _say("✓ rag-retriever 可用")

    # 汇总
    _say("—— 安装结果 ——")
    for part, status in results:
        _say(f"  {part}: {status}")
    _say("lawiki skill 无需安装：让 agent 加载 bundle 内 skill/lawiki 即可。")
    _say('就绪后把文件放进案件目录的 原始资料/，对 agent 说「整理案件资料」。')
    if args.ocr == "cloud":
        _say("云端 OCR 需设 PADDLEOCR_AISTUDIO_TOKEN，见 skill/lawiki/references/setup.md。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
