# Ingest 完整性闸门 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把本轮问题报告暴露的两个"靠自觉、无机器兜底"的口子变成确定性闸门——makeitdown 对非目录输入不再静默空跑；`_md/` 之外的转换失败/跳过源文件（lint 覆盖率账本结构上看不见的盲点）由一个独立收尾工具 `reconcile` 逼出显式处置。

**Architecture:** 两处互不 import 的改动，走各自项目的既有契约。(A) makeitdown 在 `cli.main` 入口加 `is_dir` 前置校验 + `convert_tree` 零文件告警，堵住"单文件/错路径 → rglob 返回空 → 全零 report、退出 0"的静默失败。(B) lawiki 新增 `tools/reconcile.py`：读 `_md/report.json`，复用 lint 的 `_load_skips` 解析 `wiki/log.md`，做一个和覆盖率账本对称的三态源级账本（已产出 / 已登记跳过 / 未处置源级遗漏），未处置 > 0 即退出码非 0；接进 SKILL.md 第二步作为收尾硬步骤，并把"ingest 完成定义"扩到含源级对账。

**Tech Stack:** Python 3.11+ 标准库（json / pathlib / re）。makeitdown 测试用 pytest（`uv run pytest`）；lawiki 测试用 pytest（零第三方依赖，`python -m pytest`）。

## Global Constraints

- **互不 import**：makeitdown 与 lawiki 是独立项目，只经 `report.json` / frontmatter 契约衔接；本计划不新增跨项目 import。
- **零第三方依赖（lawiki 侧）**：`reconcile.py` 只用标准库，与 `lint/`、`tools/` 现状一致（有 Python 即可跑）。
- **降级不阻塞核心**：`report.json` 缺失时 `reconcile` 明确报错退出 2（提示先跑 makeitdown），不抛裸栈。
- **不丢转换结果**：makeitdown 的改动只在**入口**拒绝非目录输入；进入 `convert_tree` 后的任何路径都不得因本改动丢弃已成功的产出。
- **路径命名空间对称**：源级 skip 用 `原始资料/<rel>` 登记，`_md` 级 skip 用 `_md/<rel>`；同一个 `wiki/log.md` 两账本各匹配各的前缀，互不冲突。
- **提交信息结尾**：`Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`。

---

## File Structure

- `makeitdown/src/makeitdown/cli.py` — 修改：`main()` 开头加输入目录校验。
- `makeitdown/src/makeitdown/pipeline.py` — 修改：`convert_tree` 里 `_iter_files` 后加零文件告警。
- `makeitdown/tests/test_cli.py` — 修改：新增非目录拒绝测试；把依赖假目录名的旧用例改为真实 tmp 目录。
- `makeitdown/tests/test_pipeline.py` — 视存在情况新增/追加：零文件告警测试（若无此文件则在 `test_cli.py` 或新建）。
- `lawiki/skill/lawiki/tools/reconcile.py` — 新建：源级对账工具（纯函数 `reconcile()` + `main()`）。
- `lawiki/skill/lawiki/tools/test_reconcile.py` — 新建：三态 + 退出码 + 计数兜底测试。
- `lawiki/skill/lawiki/SKILL.md` — 修改：第二步接入 reconcile；范围纪律的"ingest 完成定义"扩项。
- `lawiki/skill/lawiki/references/verification.md` — 修改：补一段源级对账说明 + `原始资料/<rel>` skip 约定。

---

## Task 1: makeitdown — 非目录输入前置校验 + 零文件告警

**Files:**
- Modify: `makeitdown/src/makeitdown/cli.py`（`main`，约 83–88 行）
- Modify: `makeitdown/src/makeitdown/pipeline.py`（`convert_tree`，约 175 行 `_iter_files` 之后）
- Test: `makeitdown/tests/test_cli.py`

**Interfaces:**
- Consumes: 现有 `cli.main(argv) -> int`、`convert_tree(input_dir, output_dir, **kw) -> dict`。
- Produces: `cli.main` 在 `input_dir` 非目录时返回 `2`（不调用 `convert_tree`）；`convert_tree` 在零文件时向 stderr 打印告警但照常写全零 report、返回该 report。

**背景（根因）：** `cli.main` 现在只做 `input_dir = Path(args.input)`，无存在性/类型校验；`pipeline._iter_files` 用 `input_dir.rglob("*")`，作用在**文件或不存在路径**上时静默返回 `[]`，于是产出全零 `report.json`、退出 0，看起来像"干净的空案子"。这正是问题报告 §3 根因 B 的"空跑一轮"。

- [ ] **Step 1: 写失败测试（非目录输入拒绝）**

在 `makeitdown/tests/test_cli.py` 末尾追加：

```python
def test_cli_rejects_non_directory_input(tmp_path, monkeypatch, capsys):
    # 单文件输入是本轮"空跑"的根因：必须在调用 convert_tree 前 fail-fast。
    called = {"n": 0}
    monkeypatch.setattr(cli, "convert_tree",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or _report())
    monkeypatch.delenv("PADDLEOCR_AISTUDIO_TOKEN", raising=False)

    a_file = tmp_path / "one.pdf"
    a_file.write_text("x", encoding="utf-8")
    rc_file = cli.main([str(a_file), "--ocr-engine", "local"])

    missing = tmp_path / "nope"
    rc_missing = cli.main([str(missing), "--ocr-engine", "local"])

    assert rc_file == 2 and rc_missing == 2
    assert called["n"] == 0                      # 从未进入转换
    assert "目录" in capsys.readouterr().err     # 给了可读原因
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd makeitdown && uv run pytest tests/test_cli.py::test_cli_rejects_non_directory_input -q`
Expected: FAIL（当前 `main` 不校验，`rc_file` 会是 0 且 `called["n"]==1`）。

- [ ] **Step 3: 实现 `cli.main` 前置校验**

在 `cli.py` 的 `main` 中，`input_dir = Path(args.input)` 之后、`output_dir = ...` 之前插入：

```python
    input_dir = Path(args.input)
    if not input_dir.is_dir():
        print(f"error: 输入路径不是目录：{input_dir}\n"
              f"makeitdown 只接受目录输入（递归转换其中所有文件）；"
              f"若要转单个文件，请把它放进一个目录再指向该目录。",
              file=sys.stderr)
        return 2
```

（`sys` 已在 `cli.py` 顶部导入，无需新增。）

- [ ] **Step 4: 跑新测试确认通过**

Run: `cd makeitdown && uv run pytest tests/test_cli.py::test_cli_rejects_non_directory_input -q`
Expected: PASS

- [ ] **Step 5: 跑整套 test_cli 暴露连带失败**

Run: `cd makeitdown && uv run pytest tests/test_cli.py -q`
Expected: 多个旧用例 FAIL——它们用 mock 的 `convert_tree` + 假目录名（`"./in"`、`"docs"`、`"in"`），现在被新校验在入口挡下。受影响用例：`test_cli_wires_args_to_convert_tree`、`test_cli_defaults_output_and_reads_token_from_env`、`test_cli_quality_defaults_and_flags_wired`、`test_cli_summary_includes_warned`、`test_cli_structure_headings_builds_structurer`、`test_cli_structure_headings_reads_llm_config_from_env`、`test_cli_structure_headings_fail_fast_without_config`、`test_cli_default_passes_no_structurer`、`test_cli_summary_includes_structured_when_enabled`、`test_cli_notes_actionable_skips`。

- [ ] **Step 6: 把依赖假目录的旧用例改为真实 tmp 目录**

对每个受影响用例，令其接收 `tmp_path`、建一个真实输入目录并传其绝对路径。逐个改法如下（只动"输入路径"参数与必要的断言，其余不变）：

`test_cli_wires_args_to_convert_tree` — 签名已有 `tmp_path`：
```python
    src = tmp_path / "in"; src.mkdir()
    rc = cli.main([str(src), "-o", "./out", "--ocr-engine", "cloud", "--cloud-consent",
                   "--cloud-token", "TKN", "--workers", "3", "--skip-existing"])
```
（`output_dir` 断言仍为 `Path("./out")`，不受影响。）

`test_cli_defaults_output_and_reads_token_from_env` — 输出默认名断言随输入目录变化：
```python
    src = tmp_path / "docs"; src.mkdir()
    rc = cli.main([str(src), "--cloud-consent"])
    assert rc == 0
    assert captured["output_dir"] == tmp_path / "docs_md"   # 原为 Path("docs_md")
    assert captured["cloud_token"] == "ENVTKN"
```

`test_cli_quality_defaults_and_flags_wired`、`test_cli_summary_includes_warned`、`test_cli_structure_headings_builds_structurer`、`test_cli_structure_headings_reads_llm_config_from_env`、`test_cli_default_passes_no_structurer`、`test_cli_summary_includes_structured_when_enabled`、`test_cli_notes_actionable_skips` — 均把 `"in"` 换成真实目录。给未带 `tmp_path` 的用例加该 fixture 参数，并在调用前：
```python
    src = tmp_path / "in"; src.mkdir()
```
把对应 `cli.main(["in", ...])` 改为 `cli.main([str(src), ...])`。这些用例的其余断言（quality 阈值、`warned=2`、`structured=4`、structurer 字段、`"1 file(s)"` 等）均与输入路径无关，保持不变。

`test_cli_structure_headings_fail_fast_without_config` — 该用例本意是"缺 LLM 配置时在调用 convert_tree 前 fail"。传真实目录让流程越过新的 is_dir 校验、仍在 structurer 校验处返回 2：
```python
    src = tmp_path / "in"; src.mkdir()
    rc = cli.main([str(src), "--structure-headings"])
    assert rc != 0
    assert called["n"] == 0
    assert "structure-headings" in capsys.readouterr().err
```
（`is_dir` 校验在前、structurer 校验在后；真实目录使其落到后者，断言不变。）

- [ ] **Step 7: 写零文件告警的失败测试**

追加到 `tests/test_cli.py`（用真实 `convert_tree`、真实空目录，跑通全链但期望一句 stderr 告警）：

```python
def test_convert_tree_warns_on_empty_dir(tmp_path, capsys):
    from makeitdown.pipeline import convert_tree
    empty = tmp_path / "empty"; empty.mkdir()
    report = convert_tree(empty, tmp_path / "out", ocr_engine="local",
                          ocr_model="PP-StructureV3", cloud_token=None,
                          workers=1, skip_existing=False, text_threshold=50,
                          report_path=tmp_path / "out" / "report.json",
                          quality_check=True, quality_thresholds=None,
                          keep_images=False, structurer=None,
                          cross_check=False, cross_check_ratio=0.0,
                          cross_check_mode="cloud", cloud_consent=False,
                          mineru_token=None)
    assert report["succeeded"] == 0
    assert "0" in capsys.readouterr().err   # 明说"找到 0 个文件"，而非静默
```

> 注：`convert_tree` 的完整参数以 `cli.py` 调用处（约 136–154 行）为准；实现时照抄参数名。若 `quality_thresholds=None` 会在 `assess` 前报错，则传一个 `QualityThresholds()` 默认实例。

- [ ] **Step 8: 跑测试确认失败**

Run: `cd makeitdown && uv run pytest tests/test_cli.py::test_convert_tree_warns_on_empty_dir -q`
Expected: FAIL（当前零文件无任何 stderr 输出）。

- [ ] **Step 9: 实现零文件告警**

在 `pipeline.py` 的 `convert_tree` 中，`files = _iter_files(input_dir)` 之后紧接：

```python
    files = _iter_files(input_dir)
    if not files:
        print(f"warning: {input_dir} 下没有找到任何文件——将产出空 report（0 个转换目标）。",
              file=sys.stderr, flush=True)
```

（`sys` 已在 `pipeline.py` 顶部导入。）

- [ ] **Step 10: 跑 makeitdown 全套确认全绿**

Run: `cd makeitdown && uv run pytest -q`
Expected: PASS（新测试通过，改写后的旧用例通过，无回归）。

- [ ] **Step 11: 提交**

```bash
git add makeitdown/src/makeitdown/cli.py makeitdown/src/makeitdown/pipeline.py makeitdown/tests/test_cli.py
git commit -m "$(cat <<'EOF'
fix(makeitdown): reject non-directory input instead of silently emitting an empty report

rglob() on a file or missing path returns [] silently, so single-file/typo
input produced an all-zero report.json with exit 0 — indistinguishable from a
clean empty case. main() now fails fast (exit 2) on non-directory input, and
convert_tree warns loudly when a valid dir yields zero files. Existing arg-wiring
tests that passed fake dir names are updated to real tmp dirs.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: lawiki — `reconcile.py` 源级对账工具

**Files:**
- Create: `lawiki/skill/lawiki/tools/reconcile.py`
- Test: `lawiki/skill/lawiki/tools/test_reconcile.py`

**Interfaces:**
- Consumes: lint 的 `_load_skips(root) -> dict[str, bool]` 与 `_posix(str) -> str`（`lawiki/skill/lawiki/lint/lint.py`，已被覆盖率账本使用、有测试）。`_md/report.json` 契约：`{succeeded,warned,failed,skipped_existing,skipped_unsupported:int, failures:[{file,error}], warnings:[{file,reasons}], skipped:[{file,reason}]}`，其中 `failures`/`skipped` 的 `file` 是**相对 `原始资料/` 的 POSIX 路径**。
- Produces: `reconcile(root: Path) -> tuple[list[str], dict[str,int]]`（`main` 与测试共用；stats 键：`produced/registered/unresolved/source_total/accounted`）；`main(argv) -> int`（未处置 > 0 或 report 缺失 → 非 0）。

- [ ] **Step 1: 写失败测试**

创建 `lawiki/skill/lawiki/tools/test_reconcile.py`：

```python
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import reconcile as R  # noqa: E402


def _case(tmp_path, report, log_md=None):
    (tmp_path / "_md").mkdir()
    (tmp_path / "_md" / "report.json").write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "wiki").mkdir()
    if log_md is not None:
        (tmp_path / "wiki" / "log.md").write_text(log_md, encoding="utf-8")
    (tmp_path / "原始资料").mkdir()
    return tmp_path


def _report(**over):
    base = {"succeeded": 0, "warned": 0, "failed": 0, "skipped_existing": 0,
            "skipped_unsupported": 0, "failures": [], "warnings": [], "skipped": []}
    base.update(over)
    return base


def test_unregistered_failure_is_unresolved(tmp_path):
    root = _case(tmp_path, _report(
        failed=1, failures=[{"file": "章程.doc", "error": "no LibreOffice"}]))
    unresolved, stats = R.reconcile(root)
    assert stats["unresolved"] == 1
    assert any("原始资料/章程.doc" in w for w in unresolved)


def test_registered_skip_resolves(tmp_path):
    log = ("# log\n\n"
           "## [2026-07-13] skip | 原始资料/章程.doc\n"
           "- 原因：环境无 LibreOffice，待装后补转\n")
    root = _case(tmp_path, _report(
        skipped_unsupported=1, skipped=[{"file": "章程.doc", "reason": "needs LibreOffice"}]),
        log_md=log)
    unresolved, stats = R.reconcile(root)
    assert stats["registered"] == 1 and stats["unresolved"] == 0
    assert unresolved == []


def test_all_produced_passes(tmp_path):
    root = _case(tmp_path, _report(succeeded=3))
    unresolved, stats = R.reconcile(root)
    assert stats["unresolved"] == 0 and stats["produced"] == 3


def test_missing_report_errors(tmp_path):
    (tmp_path / "wiki").mkdir()
    import pytest
    with pytest.raises(FileNotFoundError):
        R.reconcile(tmp_path)
    assert R.main([str(tmp_path)]) == 2


def test_source_more_than_accounted_flags(tmp_path):
    root = _case(tmp_path, _report(succeeded=1))
    # 原始资料/ 放 2 个文件，report 只记 1 个 → 源多于已处理
    (root / "原始资料" / "a.pdf").write_text("x", encoding="utf-8")
    (root / "原始资料" / "b.pdf").write_text("y", encoding="utf-8")
    unresolved, stats = R.reconcile(root)
    assert stats["source_total"] == 2 and stats["accounted"] == 1
    assert stats["unresolved"] >= 1
    assert any("源多于已处理" in w for w in unresolved)


def test_main_exit_codes(tmp_path):
    root = _case(tmp_path, _report(
        failed=1, failures=[{"file": "x.doc", "error": "boom"}]))
    assert R.main([str(root)]) == 1          # 未处置 → 非 0
    assert R.main([]) == 2                    # 缺参数
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd lawiki/skill/lawiki/tools && python -m pytest test_reconcile.py -q`
Expected: FAIL（`reconcile.py` 不存在，import 失败）。

- [ ] **Step 3: 实现 `reconcile.py`**

创建 `lawiki/skill/lawiki/tools/reconcile.py`：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""源级对账（ingest 收尾单独一步，独立于 lint check）。

把 原始资料/ 全量 与 _md/report.json 对齐，暴露"转换失败 / 跳过、从未进入
_md/"的源文件——这些正是 lint 覆盖率账本（只看 _md/）结构上看不见的盲点。
三态账本，镜像覆盖率：已产出 / 已登记跳过 / 未处置源级遗漏。

- 复用 lint._load_skips 解析 wiki/log.md 的 skip 条目（DRY，同一个账本文件）。
- 源级遗漏用 `原始资料/<rel>` 路径登记，与 _md 级用 `_md/<rel>` 对称、互不冲突：
  覆盖率只匹配 _md/*，reconcile 只匹配 原始资料/*，同一 log.md 各取所需。
- 未处置源级遗漏 > 0 → 退出码非 0（比覆盖率的 soft 警告更硬：这个盲点在别处
  完全不可见，必须逼一次显式处置——补转，或在 log.md 登记 skip 并告知用户）。

用法：python reconcile.py <案件根目录>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 复用 lint 的 log.md 解析与路径归一（DRY；两者同属本 skill、同一个覆盖率账本）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lint"))
from lint import _load_skips, _posix  # noqa: E402

SOURCE_DIR = "原始资料"


def reconcile(root: Path) -> tuple[list[str], dict[str, int]]:
    """返回 (未处置告警列表, 统计)。纯函数，便于测试。
    统计键：produced / registered / unresolved / source_total / accounted。"""
    stats = {"produced": 0, "registered": 0, "unresolved": 0,
             "source_total": 0, "accounted": 0}
    unresolved: list[str] = []

    report_path = root / "_md" / "report.json"
    if not report_path.is_file():
        raise FileNotFoundError(
            f"找不到 {report_path}（先在案件目录跑：makeitdown 原始资料 -o _md）")
    report = json.loads(report_path.read_text(encoding="utf-8"))

    # 源级非产出 = 转换失败 + 跳过（skipped_unsupported）：从未变成 _md 的源文件。
    non_produced = ([e["file"] for e in report.get("failures", [])] +
                    [e["file"] for e in report.get("skipped", [])])
    skips = _load_skips(root)  # {posix 路径: 是否带非空原因}
    for rel in non_produced:
        key = _posix(f"{SOURCE_DIR}/{rel}")
        if key in skips:
            stats["registered"] += 1
        else:
            stats["unresolved"] += 1
            unresolved.append(f"[未处置源级遗漏] {SOURCE_DIR}/{rel}")

    # 已产出 = 成功 + 质检警告 + 增量跳过（上轮已产出 _md，本轮 skip_existing）。
    stats["produced"] = (report.get("succeeded", 0) + report.get("warned", 0)
                         + report.get("skipped_existing", 0))
    stats["accounted"] = (stats["produced"] + report.get("failed", 0)
                          + report.get("skipped_unsupported", 0))

    # 计数兜底：原始资料/ 文件数应 == report 已处理总数；源多于已处理 → 有文件在
    # 上次转换后新增、makeitdown 从未见过（report 与覆盖率都看不见它）。
    src_dir = root / SOURCE_DIR
    if src_dir.is_dir():
        stats["source_total"] = sum(1 for p in src_dir.rglob("*") if p.is_file())
        if stats["source_total"] > stats["accounted"]:
            stats["unresolved"] += 1
            unresolved.append(
                f"[源多于已处理] 原始资料/ 有 {stats['source_total']} 个文件，"
                f"report.json 仅记录 {stats['accounted']} 个——请重跑 makeitdown 原始资料 -o _md。")
    return unresolved, stats


def main(argv: list[str]) -> int:
    if not argv:
        print("用法：python reconcile.py <案件根目录>", file=sys.stderr)
        return 2
    root = Path(argv[0])
    try:
        unresolved, stats = reconcile(root)
    except FileNotFoundError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    print(f"源级对账：{stats['source_total']} 源文件 | 已产出 {stats['produced']} | "
          f"已登记跳过 {stats['registered']} | 未处置 {stats['unresolved']}")
    for w in unresolved:
        print(w)
    return 1 if stats["unresolved"] else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

> 实现时先看一眼同目录既有工具（如 `outline.py`/`graph.py`）是否已有从 `lint` 导入的既定写法；若有，沿用其 import 风格以保持一致。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd lawiki/skill/lawiki/tools && python -m pytest test_reconcile.py -q`
Expected: PASS（全部用例）。

- [ ] **Step 5: 跑 lawiki 全套确认无回归**

Run: `cd lawiki && python -m pytest skill/lawiki -q`
Expected: PASS（含既有 lint / tools 测试）。

- [ ] **Step 6: 提交**

```bash
git add lawiki/skill/lawiki/tools/reconcile.py lawiki/skill/lawiki/tools/test_reconcile.py
git commit -m "$(cat <<'EOF'
feat(lawiki): reconcile tool — source-level coverage ledger over report.json

The coverage ledger only sees _md/, so source files that failed conversion or
were skipped (e.g. .doc with no LibreOffice) never enter _md and stay invisible.
reconcile reads _md/report.json, reuses lint._load_skips, and applies a
three-state ledger symmetric to coverage (produced / registered-skip / unresolved),
keyed on 原始资料/<rel>. Unresolved > 0 exits non-zero, forcing an explicit
disposition (convert or register a log.md skip).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: lawiki — 把 reconcile 接进协议 + 扩"完成定义"

**Files:**
- Modify: `lawiki/skill/lawiki/SKILL.md`（第二步 ~46 行；范围纪律"完成定义" ~77 行）
- Modify: `lawiki/skill/lawiki/references/verification.md`（覆盖率一节，补源级对账说明）

**Interfaces:**
- Consumes: Task 2 的 `tools/reconcile.py`（命令 `python <SKILL_DIR>/tools/reconcile.py <案件根目录>`）。
- Produces: 无代码接口；文档硬步骤 + 扩后的完成定义。

- [ ] **Step 1: 改 SKILL.md 第二步（转换后接入对账）**

将第二步（"## 第二步：转换（调 makeitdown）"）正文末尾那句"失败或跳过的文件不要凭空补内容，按缺失处理并告知用户。"之后追加一段：

```markdown

转换后**跑源级对账（确定性收尾）**：`python <SKILL_DIR>/tools/reconcile.py <案件根目录>`。它把 `原始资料/` 与 `_md/report.json` 对齐，把"转换失败 / 跳过、从未进入 `_md/`"的源文件（**lint 覆盖率账本看不见的盲点**，如无 LibreOffice 的 `.doc`）逼出来。退出码非 0 = 有**未处置源级遗漏**：要么装好外部转换器补转，要么在 `wiki/log.md` 登记 skip（路径写 `原始资料/<相对路径>` + 非空原因，格式同 `_md` 级 skip，见 `page-formats.md`）并**显式告知用户**；清零方可继续。
```

- [ ] **Step 2: 改 SKILL.md 范围纪律的完成定义**

把"**ingest 完成的定义 = lint 0 违规 且 覆盖率未处置 = 0**（每个源文件要么被引用、要么登记跳过）；待补清零前不得宣称 ingest 完成。"改为：

```markdown
- **ingest 完成的定义 = lint 0 违规 且 覆盖率未处置 = 0 且 源级对账未处置 = 0**（每个 `_md` 源文件要么被引用、要么登记跳过；每个转换失败/跳过、未进 `_md` 的源文件——lint 看不见——也要么补转、要么登记跳过）；三者未清零前不得宣称 ingest 完成。
```

- [ ] **Step 3: 改 verification.md 补源级对账说明**

在 `references/verification.md` 第 5 条覆盖率（三态账本）那段之后，新增一小节：

```markdown
### 源级对账（覆盖率的补盲，收尾单独跑）

覆盖率账本只统计 `_md/` 下的文件，**看不见转换失败/跳过、从未进入 `_md/` 的源文件**（如无 LibreOffice 的 `.doc`）——lint 通过 ≠ 全部源文件已处理。`python <SKILL_DIR>/tools/reconcile.py <案件根目录>` 补这个盲：读 `_md/report.json`，对 `failures` + `skipped`（源级非产出）做与覆盖率对称的三态账本——**已产出 / 已登记跳过（`wiki/log.md` 有 `原始资料/<rel>` 的 skip 条目）/ 未处置**，并兜底核对 `原始资料/` 文件数是否多于 report 已处理数（多则有文件 makeitdown 从未见过，需重跑）。**未处置 > 0 → 退出码非 0**，比覆盖率的 soft 警告更硬。源级 skip 与 `_md` 级 skip 共用同一个 `log.md`，靠路径前缀（`原始资料/` vs `_md/`）区分、互不干扰。**ingest 完成需三账本齐清零**（lint 0 违规 + 覆盖率未处置 0 + 源级对账未处置 0）。
```

- [ ] **Step 4: 跑 lawiki 全套确认文档改动未破坏任何断言**

Run: `cd lawiki && python -m pytest skill/lawiki -q`
Expected: PASS（文档改动不涉代码；确认无测试硬编码这些行）。

- [ ] **Step 5: 提交**

```bash
git add lawiki/skill/lawiki/SKILL.md lawiki/skill/lawiki/references/verification.md
git commit -m "$(cat <<'EOF'
docs(lawiki): wire reconcile into the pipeline and extend the completion definition

Second step now runs the deterministic source-level reconcile after makeitdown;
ingest completion now requires all three ledgers cleared (lint 0 violations +
coverage unresolved 0 + source-level reconcile unresolved 0), closing the
"lint passes but a .doc never entered _md" blind spot.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>
EOF
)"
```

---

## Part C（决定：不做，采方向乙 — 2026-07-13）：stop_hook 完成闸门

> **决定（2026-07-13，用户拍板）：暂不做，采方向乙。** 完成校验（"三账本齐清零才算完成"）留在协议层（L1），先让本轮的确定性工具（`reconcile`、`init_case` + lint 锚点闸门）发挥作用并观察。理由：本轮升成硬闸门的都是**能零误报判定**的性质（锚点、源级对账、锚点文件在场），稳赚；而 Part C 是**唯一**要牺牲"零误报"去换强制力的地方——"这条回复是不是在宣称完成"测不准，会误拦阶段汇报，与 `stop_hook.py` "只更严不误伤"的立身原则冲突；且 Stop hook 仅 Claude Code 生效、WorkBuddy 可能不跑。**重估触发条件**：光有确定性工具，仍反复出现"未验证就宣称完成"。下面存档当时的设计分析。

---


**动机：** 本轮最严重违规是"未达完成标准就宣称完成"，且该规则当时**已在 SKILL.md 主干、已在上下文**却被主动覆盖——纯文字约束挡不住"选择性遵守"。对称于问答的两端闸门（取证前闸门 + `lint answer` 后闸门），ingest 这一端目前**没有**对应的机器关卡。

**设想改法：** 扩 `lint/stop_hook.py`——当 `cwd` 有 `wiki/` 且回复文本出现"完成/搞定/处理完了"一类收尾措辞时，自动跑 `lint check`（含覆盖率）+ `reconcile`，任一未清零则 `block` 并把真实账本数字甩回，要求先清零或显式向用户交代再收尾。

**为何需要你先拍板（真实权衡）：** 现有 hook 只查"回复里的锚点是否属实"，能做到**零误报**（无锚点直接放行）。而"判断一句话是不是在宣称完成"天然是模糊匹配——做不到零误报，会有：① 误拦（agent 阶段性汇报进度、并未宣称全部完成，被拦）；② 漏拦（换种说法宣称完成）。若每轮无条件跑 check-并-拦，又会让合法的分批 ingest 中途寸步难行。这与 hook 现在恪守的"只更严、不误伤"原则有张力。

**两个可选方向（需你选）：**
- **方向甲**：接受"完成措辞检测"的误报风险，做文本触发的软 `block`（附一句"如只是阶段汇报可忽略本提示并继续"以缓冲误拦）。
- **方向乙**：不碰 stop_hook，改为把 reconcile + lint 的"三账本齐清零"作为收尾**协议硬步骤**（Task 3 已落一半），先只上确定性工具、把"宣称完成"的守门仍留给协议，观察一段时间再决定要不要上文本闸门。

我的倾向：**先方向乙**（Task 1–3），把 stop_hook 完成闸门单独留作后续、待观察 Task 1–3 的效果与误报容忍度后再定。

---

## Part D（案件层，非本仓库，需你给案件目录路径）

以下是问题报告 §8 里针对**炎凰数据案**那个案件文件夹的直接修复，不属于 AnyDocsMarked 代码库，需要该案件目录路径后单独执行（也可交由 skill 在该目录内自然跑）：

- 给 3 个 `quality: suspect` 源的 wiki 引用补「（未核验）」。
- 向用户显式上报 2 个 `.doc` 缺失，建议装 LibreOffice/Word 后补转（并在 `log.md` 以 `原始资料/<rel>` 登记 skip，正好被新的 reconcile 认作已处置）。
- 去重 `log.md` 的 11 条 skip（删重复的那遍）。
- 补建案件根的 `AGENTS.md` / `CLAUDE.md`（闭世界约束）。

---

## Self-Review

**Spec coverage：**
- 报告 §3 根因 B「单文件输入空跑」→ Task 1（is_dir 拒绝 + 零文件告警）。✓
- 报告 §4「lint 看不见 `_md` 之外的 `.doc` 真遗漏」+ §8.6「完成 = 全源清点一致（含 lint 看不见的 .doc）」→ Task 2（reconcile 三态）+ Task 3（完成定义扩项）。✓
- 报告 §8「stop_hook / 完成宣称」→ Part C（显式作为待决项，说明误报权衡）。✓
- 报告 §8 案件层直接修复 → Part D（标明在本仓库之外）。✓
- 上一轮讨论已**否决**的"强制读三件套 / 单文件试跑仪式"——不进计划（信息缺口由主干已有的铁律 + lint 硬闸门覆盖；锚点格式违规本就被 lint 抓到并逼返工）。✓

**Placeholder scan：** 无 TBD/TODO；每个代码步给了完整代码与命令。`convert_tree` 参数一处标注"以 cli 调用处为准"，因该函数签名较长且非本改动重点——实现者照抄调用处即可，非占位。✓

**Type consistency：** `reconcile(root)->(list[str],dict)`、`main(argv)->int`、stats 键 `produced/registered/unresolved/source_total/accounted` 在 Task 2 代码、测试、Task 3 文档间一致；复用的 `_load_skips`/`_posix` 名称与 lint.py 现状一致。✓
