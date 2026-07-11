#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AnyDocsMarked bundle 安装器（仅标准库，跨平台）。

随 `anydocsmarked` 发布包发布，位于 bundle 根目录。agent 在 setup 阶段跑这一条即可
把三部分装好：lawiki skill（无需安装，加载即用）、makeitdown（转换器）、
rag-retriever（RAG 检索）。后两者从 bundle 内 `vendor/` 的源码本地安装。

用法：
  python install.py [--ocr local|cloud] [--dry-run]
                    [--skip-makeitdown] [--skip-rag]

- `--ocr cloud`（默认）：装云端版（轻、需百度 AI Studio token，见 setup.md）。
  `--ocr local`：装本地 PaddleOCR（离线、不需 token、体积大）。
  两种都应先由 agent 向用户说明并让其选择，绝不静默替用户决定。
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


def _check_offline() -> None:
    """断网就绪自检：只查不装，逐项报告 ✓/✗ 与国内替代路径。退出码恒 0。
    ④⑤查的是 bundle 内 vendor 资产——安装即从此本地拷入已装包，故为"安装后
    是否离线"的忠实代理。"""
    _say("—— 离线就绪自检（--check-offline）——")
    ok_py = sys.version_info >= (3, 11)
    _say(f"  {'✓' if ok_py else '✗'} Python {sys.version.split()[0]}（需 3.11+）")
    if _have("uv"):
        _say("  ✓ uv 在 PATH")
    else:
        _say("  ✗ 未找到 uv —— pip install uv -i " + TSINGHUA)
    _say(f"  {'✓' if _verify(['makeitdown', '--help']) else '✗'} makeitdown 可用")
    _say(f"  {'✓' if _verify(['rag-retriever', '--help']) else '✗'} rag-retriever 可用")

    rag_pkg = VENDOR / "rag-retriever" / "rag_retriever"
    models = rag_pkg / "_models"
    if models.is_dir() and any(models.rglob("*.onnx")):
        _say("  ✓ embedding 模型离线就绪（vendor 内置 .onnx）")
    else:
        _say("  ✗ embedding 首次建索引将联网下载（境外 HuggingFace）——"
             "设 HF_ENDPOINT=https://hf-mirror.com，或改用 -offline 发布包")
    tk = rag_pkg / "_tiktoken"
    if tk.is_dir() and any(tk.iterdir()):
        _say("  ✓ 分词表离线就绪（vendor 内置 tiktoken BPE）")
    else:
        _say("  ✗ 分词首次将联网拉取（境外 blob，国内常慢）——用 -offline 发布包避免")

    _say("  提示：reranker（RAG_RERANK=local）默认关闭，开启需联网下载；")
    _say("        ollama 后端拉模型走境外 registry，国内建议 local（内置）或 openai（硅基流动）；")
    _say("        MinerU 互校默认已从 ModelScope（魔搭）拉权重，国内首用无需 HuggingFace。")


def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 重定向默认 GBK
    except Exception:
        pass

    p = argparse.ArgumentParser(prog="install.py", description="lawiki bundle installer")
    p.add_argument("--ocr", choices=["local", "cloud"], default="cloud")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-makeitdown", action="store_true")
    p.add_argument("--skip-rag", action="store_true")
    p.add_argument("--check-offline", action="store_true",
                   help="只查不装：报告离线就绪状态（Python/uv/命令/vendored 模型与分词表）")
    args = p.parse_args(argv[1:])

    if args.check_offline:
        _check_offline()
        return 0

    results: list[tuple[str, str]] = []  # (部件, 状态)

    # 0) 前置：Python 3.11+ / uv
    if sys.version_info < (3, 11):
        _say(f"⚠ 需要 Python 3.11+，当前 {sys.version.split()[0]}。请升级后重试。")
        return 0
    if not _have("uv"):
        _say("⚠ 未找到 uv（安装 makeitdown/rag-retriever 需要它）。")
        _say("  Windows: winget install astral-sh.uv ；macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh")
        _say("  或（国内推荐）: pip install uv -i " + TSINGHUA)
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
    # Mirrors rag_retriever.embed._BUNDLED_MODELS_DIR (stdlib-only installer can't
    # import it); keep the "_models" layout in sync if that package is renamed.
    offline_models = VENDOR / "rag-retriever" / "rag_retriever" / "_models"
    if offline_models.is_dir():
        _say("✓ 离线包：已内置 embedding 模型，首次建索引无需联网下载。")
    else:
        _say("提示：本包首次建索引会联网下载 embedding 模型（bge-small-zh-v1.5，境外 HuggingFace）。")
        _say("  国内如慢：设 HF_ENDPOINT=https://hf-mirror.com，或改用 -offline 版发布包。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
