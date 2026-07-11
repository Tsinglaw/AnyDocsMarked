# 断外网加固 + 长任务可观测性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 makeitdown 批量转换过程逐文件可见（agent 才能感知长任务完成），并给出可验证的"离线就绪"自检 + 国内断网/内网部署指引。

**Architecture:** Part B 在 `pipeline.py` 的 `as_completed` 主线程消费循环里打印逐文件进度行到 stderr（stdout 保持干净），由 `convert_tree` 的 `progress: bool` 参数门控。Part A 给 `install.py` 加 `--check-offline` 只查不装的自检，并补国内 Python/uv 指引到 `setup.md`。协议层在 `SKILL.md` 加"长任务模式"。

**Tech Stack:** Python 标准库（`sys`/`time` 已是 makeitdown 依赖面内；`install.py` 全程 stdlib）。makeitdown 测试：`cd makeitdown && uv run pytest`。

**Spec:** `docs/superpowers/specs/2026-07-10-offline-hardening-and-long-task-observability-design.md`

## Global Constraints

- 不引入任何第三方依赖；不改 rag-retriever 本体；不改三层架构。
- `lawiki/install.py` 全程仅标准库（它是零依赖安装器）。
- makeitdown 进度行打到 **stderr** 且 `flush=True`（与 `cli.py` 现有通知同流；stdout 保持干净供管道消费）。
- 进度默认开（`convert_tree(progress=True)`）；不新增 CLI flag（`progress` 仅供测试关）。
- **Gitee 发行/镜像本次不做**（用户明确暂缓）。
- 不实现 PushNotification / Notification hook（协议文字可提一句 Claude Code 可自配，不展开）。
- 状态字形固定：✓ succeeded / ⚠ warned / ✗ failed / = skipped_existing / → skipped_unsupported。
- 进度序号 `k` 为**完成序号**（并发下完成顺序 ≠ 提交顺序，如实即可）。
- 测试运行命令（本机已验证）：`cd "D:\Vibe Coding Items\AnyDocsMarked\makeitdown" && uv run pytest -q`
- **工作树前置状态**：控制器在 Task 1 前已把三个既有未提交改动（`lawiki/README.md`、`makeitdown/README.md` 的百度 URL 修正 + `ocr_mineru.py` 的 MinerU→ModelScope 国内默认）作为一个 housekeeping 提交清掉，工作树从 Task 1 起是干净的。**每次 commit 只 `git add` 本任务的文件，绝不 `git add -A`。**
- 分支：`feat/offline-hardening`（控制器在执行阶段创建）。

---

### Task 1: makeitdown 逐文件进度行（`pipeline.py`）

在批量消费循环里打印 `[k/N] <字形> <相对路径> ...` 到 stderr，由 `progress` 参数门控。计时用 `time.monotonic()`，在 `handle` 外层包一层记录 elapsed，避免动 `handle` 的 6 个 return 点。

**Files:**
- Modify: `makeitdown/src/makeitdown/pipeline.py`（导入区 + `convert_tree` 签名 + 消费循环；新增模块级 `_STATUS_GLYPH`）
- Test: `makeitdown/tests/test_pipeline.py`（追加 3 个测试）

**Interfaces:**
- Consumes: 既有 `convert_tree(...)` 与其内部 `handle(src) -> tuple`（5 元组：`(status, rel, detail, structured, images_omitted)`）。
- Produces: `convert_tree(..., progress: bool = True)`——新增**关键字**参数，默认 True，放在参数列表末尾（`mineru_token` 之后），既有位置/关键字调用不受影响。stderr 进度行格式 `[k/N] ✓ 相对路径 (8.2s)` 等。返回值与 `report` 结构**不变**。

- [ ] **Step 1: 写失败测试**

在 `makeitdown/tests/test_pipeline.py` 末尾追加（复用文件顶部已 import 的 `pl` / `ConversionResult`）：

```python
def _one_native_file(tmp_path, monkeypatch, content="正常的文档内容" * 5):
    src = tmp_path / "in"
    src.mkdir()
    (src / "a.docx").write_text("x", encoding="utf-8")
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    monkeypatch.setattr(pl, "convert_native",
                        lambda p: ConversionResult(text="# ok\n\n" + content, engine="markitdown"))
    return src


def test_progress_lines_printed_to_stderr_by_default(tmp_path, monkeypatch, capsys):
    src = _one_native_file(tmp_path, monkeypatch)
    pl.convert_tree(src, tmp_path / "out", ocr_engine="auto", ocr_model="x",
                    cloud_token=None, workers=1, skip_existing=False,
                    text_threshold=50, report_path=tmp_path / "out" / "report.json")
    err = capsys.readouterr().err
    assert "[1/1] ✓ a.docx" in err


def test_progress_can_be_silenced(tmp_path, monkeypatch, capsys):
    src = _one_native_file(tmp_path, monkeypatch)
    pl.convert_tree(src, tmp_path / "out", ocr_engine="auto", ocr_model="x",
                    cloud_token=None, workers=1, skip_existing=False,
                    text_threshold=50, report_path=tmp_path / "out" / "report.json",
                    progress=False)
    err = capsys.readouterr().err
    assert "[1/" not in err


def test_progress_marks_failure_with_error(tmp_path, monkeypatch, capsys):
    src = tmp_path / "in"
    src.mkdir()
    (src / "bad.docx").write_text("x", encoding="utf-8")
    monkeypatch.setattr(pl, "classify", lambda p, text_threshold=50: "native")
    def boom(p): raise ValueError("broken file")
    monkeypatch.setattr(pl, "convert_native", boom)
    pl.convert_tree(src, tmp_path / "out", ocr_engine="auto", ocr_model="x",
                    cloud_token=None, workers=1, skip_existing=False,
                    text_threshold=50, report_path=tmp_path / "out" / "report.json")
    err = capsys.readouterr().err
    assert "[1/1] ✗ bad.docx" in err and "broken file" in err
```

- [ ] **Step 2: 跑测试确认失败**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\makeitdown" && uv run pytest -q tests/test_pipeline.py -k progress
```
预期：3 个测试 FAIL——`test_progress_can_be_silenced` 报 `TypeError: convert_tree() got an unexpected keyword argument 'progress'`；另两个 FAIL（stderr 无 `[1/1]` 行）。

- [ ] **Step 3: 实现——导入 + 字形表**

`makeitdown/src/makeitdown/pipeline.py` 顶部导入区：

```python
import json
import re
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
```

在 `_iter_files` 定义之前（模块级）加字形表：

```python
# 进度行状态字形（打到 stderr，供长任务时人/agent 感知进度；见 SKILL.md 长任务模式）。
_STATUS_GLYPH = {
    "succeeded": "✓", "warned": "⚠", "failed": "✗",
    "skipped_existing": "=", "skipped_unsupported": "→",
}


def _progress_line(k: int, total: int, status: str, rel: Path,
                   detail, elapsed: float) -> str:
    line = f"[{k}/{total}] {_STATUS_GLYPH.get(status, '?')} {rel.as_posix()}"
    if status == "failed":
        return line + f" — {detail}"
    if status == "skipped_existing":
        return line + "（已最新，跳过）"
    if status == "skipped_unsupported":
        return line + "（需外部转换器，见 report）"
    return line + f" ({elapsed:.1f}s)"  # succeeded / warned
```

- [ ] **Step 4: 实现——签名 + 计时包装 + 消费循环**

`convert_tree` 签名：在最后一个参数 `mineru_token: str | None = None,` 之后加一行：

```python
    mineru_token: str | None = None,
    progress: bool = True,
) -> dict:
```

把提交与消费循环（现为）：

```python
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for future in as_completed(pool.submit(handle, src) for src in files):
            status, rel, detail, structured, images_omitted = future.result()
```

替换为（外层包 `_timed` 记 elapsed，不动 `handle` 的 6 个 return 点；主线程串行消费，打印线程安全）：

```python
    total = len(files)

    def _timed(src):
        start = time.monotonic()
        result = handle(src)  # 5-tuple
        return (*result, time.monotonic() - start)

    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for future in as_completed(pool.submit(_timed, src) for src in files):
            status, rel, detail, structured, images_omitted, elapsed = future.result()
            completed += 1
            if progress:
                print(_progress_line(completed, total, status, rel, detail, elapsed),
                      file=sys.stderr, flush=True)
```

（循环体内后续 `report[status] += 1` 等统计逻辑**保持原样不变**。）

- [ ] **Step 5: 跑测试确认通过**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\makeitdown" && uv run pytest -q tests/test_pipeline.py
```
预期：全绿（既有 + 新 3 个）。既有测试不 assert stderr，故默认打印进度不破坏它们。

- [ ] **Step 6: 全套回归 + 提交**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\makeitdown" && uv run pytest -q
```
预期：全绿（原 152 + 新 3 = 155）。然后：

```
git add makeitdown/src/makeitdown/pipeline.py makeitdown/tests/test_pipeline.py
git commit -m "feat(makeitdown): per-file progress lines during batch convert

Batch conversion was silent; a few-hundred-file cloud-OCR run gave no
signal until report.json. Print [k/N] <glyph> <path> to stderr as each
file completes (stdout stays clean), gated by convert_tree(progress=True).
Lets an agent tail a long run and know when it finished."
```

---

### Task 2: `install.py --check-offline` 自检 + 国内 uv 提示

只查不装的离线就绪自检；补 uv 缺失时的国内安装路径。install.py 现无测试文件，本任务按 spec 以 E2E 手工验证为准（Task 4 覆盖），不新建测试框架。

**Files:**
- Modify: `lawiki/install.py`（新增 `_check_offline()`；`main` 加 `--check-offline` 分支；uv 缺失提示补一行）

**Interfaces:**
- Consumes: 既有 `_say(msg)`、`_have(cmd)`、`_verify(cmd_list)`、常量 `VENDOR`、`TSINGHUA`。
- Produces: `--check-offline` flag → 打印自检报告，退出码恒 0，不安装。

- [ ] **Step 1: 加自检函数**

在 `lawiki/install.py` 的 `_verify` 定义之后、`main` 之前插入：

```python
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
```

- [ ] **Step 2: `main` 加分支 + uv 提示补一行**

`main` 中，在 `args = p.parse_args(argv[1:])` 之后、`results: list...` 之前插入：

```python
    p.add_argument("--check-offline", action="store_true",
                   help="只查不装：报告离线就绪状态（Python/uv/命令/vendored 模型与分词表）")
```

（注意：该 `add_argument` 要放在 `p.add_argument("--skip-rag", ...)` 之后、`args = p.parse_args(...)` 之前——与其他 flag 定义在一起。）然后在 `args = p.parse_args(argv[1:])` 之后立即：

```python
    if args.check_offline:
        _check_offline()
        return 0
```

uv 缺失提示：把现有

```python
        _say("  Windows: winget install astral-sh.uv ；macOS/Linux: curl -LsSf https://astral.sh/uv/install.sh | sh")
```

其后补一行：

```python
        _say("  或（国内推荐）: pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple")
```

- [ ] **Step 3: 冒烟验证（裸目录，vendor 不存在）**

```
cd "D:\Vibe Coding Items\AnyDocsMarked" && PYTHONIOENCODING=utf-8 python lawiki/install.py --check-offline; echo "exit=$?"
```
预期：打印自检 6 项；因 `lawiki/vendor/` 不存在，embedding/分词表两项报 ✗（并给国内替代路径）；`exit=0`。（真实 bundle 布局的 ✓ 路径在 Task 4 用合成 vendor 结构验证。）

- [ ] **Step 4: 提交**

```
git add lawiki/install.py
git commit -m "feat(lawiki): install.py --check-offline self-check + domestic uv hint

Query-only readiness report (Python/uv/commands/vendored embedding ONNX
and tiktoken BPE) so a mainland user can verify offline-readiness instead
of trial-and-error; adds the Tsinghua pip route to the uv-missing hint."
```

---

### Task 3: 文档接线（`setup.md` + `SKILL.md` + makeitdown `README.md`）

三处文档，命令拼写须与 Task 1/2 的实现一致。

**Files:**
- Modify: `lawiki/skill/lawiki/references/setup.md`（「两种发布包」小节之后插入「断网 / 内网部署（国内）」）
- Modify: `lawiki/skill/lawiki/SKILL.md`（「第二步：转换」小节末尾加「长任务模式」段）
- Modify: `makeitdown/README.md`（「使用」节加进度行说明一行）

**Interfaces:**
- Consumes: Task 2 的 `python install.py --check-offline`；Task 1 的 `[k/N]` 进度行格式与后台运行方式。

- [ ] **Step 1: `setup.md` 插入断网小节**

在 `lawiki/skill/lawiki/references/setup.md` 的「## 两种发布包（Release 二选一）」小节**末尾之后**、「## 第 1 步 · 检测（并把结果告诉用户）」**之前**插入：

```markdown
## 断网 / 内网部署（国内）

大陆断外网使用，按此保证不触国际互联网（offline 发布包已内置 embedding 模型与
tiktoken，云端选项全是国内服务）：

- **提前自备 Python 3.11/3.12**：别依赖 uv 自动下载（那走 GitHub 的
  python-build-standalone，国内常断）。uv 本体用
  `pip install uv -i https://pypi.tuna.tsinghua.edu.cn/simple` 装；若必须让 uv 管
  Python，设环境变量 `UV_PYTHON_INSTALL_MIRROR` 指向国内镜像。
- **内网/涉密三件套**（联网机备好带入内网）：Python 官方安装包 + uv wheel
  （`pip download uv`）+ `-offline` 发布包。进内网后 `python install.py --ocr local`，
  装完 `python install.py --check-offline` 逐项核验离线就绪。
- **如实提示**：`RAG_EMBED_BACKEND=ollama` 拉模型走境外 registry，国内建议 `local`
  （内置）或 `openai`（硅基流动）；reranker（`RAG_RERANK=local`）开启需联网下载。
  MinerU 互校默认已从 ModelScope（魔搭）取权重，国内首用无需 HuggingFace。
- **边界**：agent 本身（如 Claude）的联网需求超出本项目范围；skill 跨 agent 可用，
  配国产 agent 可做到全链路国内。
```

- [ ] **Step 2: `SKILL.md` 加长任务模式**

在 `lawiki/skill/lawiki/SKILL.md` 的「## 第二步：转换（调 makeitdown）」小节，把这句：

```
在案件目录执行 `makeitdown 原始资料 -o _md`。转换后读 `_md/report.json`，留意 `warned`/`failed`/`skipped`。失败或跳过的文件**不要凭空补内容**，按缺失处理并告知用户。
```

其后另起一段追加：

```

**长任务模式（批量含扫描件 / 走云端 OCR）**：文件多于 ~20 个或含大量扫描件时，转换可能几十分钟。makeitdown 会逐文件把进度打到 stderr（`[k/N] ✓/⚠/✗ 路径`）。**后台运行并落日志**（Claude Code 用 Bash 的后台模式——完成时 harness 会自动唤醒你；其他 agent 用 `nohup makeitdown 原始资料 -o _md > convert.log 2>&1 &` 等价形式），期间可 tail 日志按进度向用户播报；**以进程退出 + `_md/report.json` 出现为完成信号**，完成后读 report.json 向用户汇总 succeeded/warned/failed/skipped 四类计数与需注意项。中断或掉线后加 `--skip-existing` 重跑即断点续传。
```

- [ ] **Step 3: makeitdown `README.md` 加进度说明**

在 `makeitdown/README.md` 的「## 使用」节，`makeitdown <输入目录> -o <输出目录>` 代码块与其后「输出目录默认…」说明之后、`### OCR 后端` 之前，插入一行：

```markdown
转换过程逐文件把进度打到 stderr（`[k/N] ✓ 文件路径 (耗时)`，✗ 为失败并附错误）。批量大或走云端 OCR 时建议后台运行并 tail 日志；**完成以 `report.json` 为准**。
```

- [ ] **Step 4: 一致性核对**

```
cd "D:\Vibe Coding Items\AnyDocsMarked" && PYTHONIOENCODING=utf-8 grep -rn "check-offline\|skip-existing\|\[k/N\]" lawiki/skill/lawiki/references/setup.md lawiki/skill/lawiki/SKILL.md makeitdown/README.md
```
预期：setup.md 出现 `--check-offline`（1 处）；SKILL.md 与 makeitdown/README.md 出现 `[k/N]` 进度格式描述；`--skip-existing` 在 SKILL.md 长任务段出现。命令拼写与 Task 1/2 实现一致。

- [ ] **Step 5: 提交**

```
git add lawiki/skill/lawiki/references/setup.md lawiki/skill/lawiki/SKILL.md makeitdown/README.md
git commit -m "docs: domestic offline deployment + long-task mode

setup.md: mainland/air-gapped deployment section (self-fetch Python, uv
mirror, three-piece air-gap kit, --check-offline verification, honest
ollama/reranker/MinerU-ModelScope notes). SKILL.md: long-task mode for
batch/cloud-OCR conversion (background + tail + report.json as done
signal). makeitdown README: progress-line note."
```

---

### Task 4: 端到端验证

验证进度行、`--check-offline` 两种布局、全套测试回归。无新代码、无提交（除非发现回归缺陷回相应 Task 修）。

**Files:** 无（临时目录用后即弃）。

- [ ] **Step 1: 进度行端到端（合成目录，含一个坏文件）**

```bash
cd "D:\Vibe Coding Items\AnyDocsMarked\makeitdown"
uv run python - <<'PY'
import sys, tempfile, os
from pathlib import Path
import makeitdown.pipeline as pl
from makeitdown.models import ConversionResult
d = Path(tempfile.mkdtemp()); src = d/"in"; (src/"sub").mkdir(parents=True)
(src/"a.docx").write_text("x", encoding="utf-8")
(src/"sub"/"bad.docx").write_text("x", encoding="utf-8")
pl.classify = lambda p, text_threshold=50: "native"
_orig = pl.convert_native
def cn(p):
    if p.name == "bad.docx": raise ValueError("broken file")
    return ConversionResult(text="# ok\n\n"+"正常的文档内容"*5, engine="markitdown")
pl.convert_native = cn
pl.convert_tree(src, d/"out", ocr_engine="auto", ocr_model="x", cloud_token=None,
                workers=2, skip_existing=False, text_threshold=50,
                report_path=d/"out"/"report.json")
PY
```
预期 stderr：两行 `[1/2] …` 与 `[2/2] …`；其一为 `✓ a.docx (…s)`，另一为 `✗ sub/bad.docx — ValueError: broken file`（完成序号顺序视并发而定，两行都在即可）。

- [ ] **Step 2: `--check-offline` 裸布局（✗ 路径）**

```bash
cd "D:\Vibe Coding Items\AnyDocsMarked" && PYTHONIOENCODING=utf-8 python lawiki/install.py --check-offline; echo "exit=$?"
```
预期：6 项自检；embedding/分词表报 ✗ 并给国内替代路径；`exit=0`。

- [ ] **Step 3: `--check-offline` 模拟 offline 布局（✓ 路径）**

```bash
cd "D:\Vibe Coding Items\AnyDocsMarked" && \
MODELS="lawiki/vendor/rag-retriever/rag_retriever/_models" && \
TK="lawiki/vendor/rag-retriever/rag_retriever/_tiktoken" && \
mkdir -p "$MODELS" "$TK" && : > "$MODELS/model.onnx" && : > "$TK/o200k_base.tiktoken" && \
PYTHONIOENCODING=utf-8 python lawiki/install.py --check-offline; \
rm -rf lawiki/vendor
```
预期：embedding 与分词表两项报 ✓（vendor 内置）；`exit=0`。**清理**：命令尾 `rm -rf lawiki/vendor` 已删除临时 vendor；跑完 `git status --short` 确认无 `lawiki/vendor/` 残留。

- [ ] **Step 4: 全套回归**

```bash
cd "D:\Vibe Coding Items\AnyDocsMarked\makeitdown" && uv run pytest -q
cd "D:\Vibe Coding Items\AnyDocsMarked\rag-retriever" && uv run pytest -q
cd "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki" && python -m unittest discover -s tools -p "test_*.py" 2>&1 | tail -2
```
预期：makeitdown 155 passed（152 + 3）；rag-retriever 71 passed；lawiki tools 43（本计划未动 lawiki 核心，应不变）。

- [ ] **Step 5: 收尾确认**

```bash
cd "D:\Vibe Coding Items\AnyDocsMarked" && git status --short
```
预期：工作树干净（Task 1 前的 housekeeping 提交已清掉三个既有改动；本计划文件均已提交；无 `lawiki/vendor/` 残留）。若发现回归，回相应 Task 修（先补失败测试再改码），修完重跑本 Task。
