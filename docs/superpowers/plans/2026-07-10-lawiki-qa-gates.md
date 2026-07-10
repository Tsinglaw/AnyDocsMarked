# lawiki 问答确定性闸门（QA Gates）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 lawiki 的确定性校验从建库时延伸到问答时：前闸门（evidence 一条命令三路取证）+ 后闸门（`lint.py answer` 交付校验）+ 可选 Stop hook，外加面包屑默认锚点 bug 的前置修复。

**Architecture:** 过程保持 agent 自由（wiki 路自由导航），产物过机器闸门。evidence.py 组合既有工具（rag.py 是 rag-retriever 唯一边界、outline.py 结构树、新增纯 Python grep）；answer 闸门复用 lint.py 的 `_check_anchors`；Stop hook 复用 answer 闸门的两检子集。**不改 rag-retriever 本体。**

**Tech Stack:** 纯 Python 标准库（skill 内 tools/lint 零第三方依赖）。测试：`tools/` 用 stdlib unittest，`lint/` 用 pytest 风格（跑在 rag-retriever 的 uv 环境里）。

**Spec:** `docs/superpowers/specs/2026-07-10-lawiki-qa-gates-design.md`

## Global Constraints

- `lawiki/skill/lawiki/` 下的 tools 与 lint **只用标准库**（含 `unittest.mock`），任何 Python 3.11+ 可跑。
- **不修改 `rag-retriever/` 任何文件**（面包屑前缀是好设计，保留）。
- 所有新 CLI 入口沿用既有模式：`sys.stdout.reconfigure(encoding="utf-8")` 包 try/except（Windows 重定向默认 GBK）。
- 注释/docstring 风格与现有文件一致：中文、说"为什么"。
- 违规输出格式沿用 lint 现有风格：`[类别] 位置\n          详情`。
- 测试运行命令（本机已验证可用）：
  - tools（unittest）：`cd "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\tools" && python -m unittest <模块名> -v`
  - lint（pytest）：`cd "D:\Vibe Coding Items\AnyDocsMarked\rag-retriever" && uv run pytest -q "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\lint\<测试文件>"`
- 工作区里已有三个与本计划无关的未提交改动（`lawiki/README.md`、`makeitdown/README.md`、`makeitdown/src/makeitdown/ocr_mineru.py`）——**每次 commit 只 add 本任务的文件，绝不 `git add -A`**。

---

### Task 1: 面包屑默认锚点修复（`tools/rag.py`）

rag-retriever 结构分块把标题面包屑拼在存储文本前（`"民事判决书 > 本院认为\n\n正文"`）。`enrich_hit` 直接拿它取默认锚点片段，而面包屑在源文件里不连续（标题之间隔着正文），锚点过不了 lint（2026-07-10 实测确认）。修法：按 `metadata.heading_path` 剥前缀后再取片段。

**Files:**
- Modify: `lawiki/skill/lawiki/tools/rag.py`（`enrich_hit`，约 59-71 行）
- Test: `lawiki/skill/lawiki/tools/test_rag.py`（`EnrichHitTests` 类内追加）

**Interfaces:**
- Consumes: 既有 `build_anchor(source, snippet, quality)`、`default_snippet(text)`、lint 的 `scan_case(root)`（测试用）。
- Produces: `enrich_hit(hit: dict) -> dict` 行为变化——当 `hit["metadata"]["heading_path"]` 存在且 `hit["text"]` 以 `heading_path + "\n\n"` 开头时，默认锚点片段不含面包屑。返回 dict 结构不变（`anchor`/`unverified` 键）。Task 3 的 evidence.py 依赖此函数经 `search_case` 产出的 `anchor`。

- [ ] **Step 1: 写失败测试**

在 `lawiki/skill/lawiki/tools/test_rag.py` 的 `EnrichHitTests` 类内追加三个方法：

```python
    def test_breadcrumb_prefix_stripped_anchor_passes_real_lint(self):
        # 结构分块的命中 text 带标题面包屑前缀（rag-retriever pipeline._compose），
        # 而源文件里两个标题之间隔着正文——面包屑不是连续文本，直接进锚点必挂 lint。
        # enrich_hit 须按 metadata.heading_path 剥前缀后再取默认片段。
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = ("---\nsource: 判决书.pdf\n---\n# 民事判决书\n\n"
                   "（2023）京0105民初12345号\n\n## 本院认为\n\n"
                   "本院认为，被告应向原告偿还借款本金人民币 50000 元。\n")
            (root / "_md").mkdir(parents=True)
            (root / "_md" / "判决书.md").write_text(src, encoding="utf-8")

            hit = {"source": "_md/判决书.md",
                   "text": ("民事判决书 > 本院认为\n\n"
                            "本院认为，被告应向原告偿还借款本金人民币 50000 元。"),
                   "metadata": {"heading_path": "民事判决书 > 本院认为"}}
            enriched = rag.enrich_hit(hit)

            (root / "wiki").mkdir()
            (root / "wiki" / "p.md").write_text(
                f"- 事实 {enriched['anchor']}\n", encoding="utf-8")
            total, violations, _ = scan_case(root)
            self.assertEqual(total, 1)
            self.assertEqual(violations, [], msg=str(violations))

    def test_heading_path_absent_snippet_unchanged(self):
        hit = {"source": "_md/a.md", "text": "正文片段", "metadata": {}}
        self.assertIn("「正文片段」", rag.enrich_hit(hit)["anchor"])

    def test_heading_path_prefix_mismatch_not_stripped(self):
        # heading_path 存在但 text 不以它开头（防御边界）——不剥、不崩。
        hit = {"source": "_md/a.md", "text": "正文片段",
               "metadata": {"heading_path": "别的标题"}}
        self.assertIn("「正文片段」", rag.enrich_hit(hit)["anchor"])
```

- [ ] **Step 2: 跑测试确认失败**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\tools" && python -m unittest test_rag.EnrichHitTests -v
```

预期：`test_breadcrumb_prefix_stripped_anchor_passes_real_lint` FAIL（violations 含「片段不符」）；另两个新测试 PASS（现有行为本就如此，它们是防回归锁）。

- [ ] **Step 3: 实现最小修复**

`lawiki/skill/lawiki/tools/rag.py` 中整体替换 `enrich_hit`：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\tools" && python -m unittest test_rag -v
```

预期：全部 PASS（含既有测试——frontmatter 剥离、suspect 后缀等不回归）。

- [ ] **Step 5: 跑全部 tools 测试防外溢**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki" && python -m unittest discover -s tools -p "test_*.py"
```

预期：OK（原 32 个 + 新 3 个 = 35 个）。

- [ ] **Step 6: Commit**

```
git add lawiki/skill/lawiki/tools/rag.py lawiki/skill/lawiki/tools/test_rag.py
git commit -m "fix(lawiki): strip heading breadcrumb from RAG default anchors

Structure-aware chunks (2026-06-28) prefix stored text with the heading
breadcrumb; the wrapper's default anchor snippet included it, so anchors
from any structured document failed lint (breadcrumb isn't contiguous in
the source). Strip the metadata.heading_path prefix before snippeting;
hit text itself keeps the breadcrumb for agent context."
```

---

### Task 2: `lint.py answer` 交付闸门

对回答草稿做三项确定性检查：① 锚点全验（复用 `_check_anchors`）② 闭世界（锚点须指向 `_md/`）③ 整篇兜底（零锚点须明示「未在本案材料中找到」或全篇为标注分析/标题，否则裸答打回）。严格度档位经用户拍板：零误报，不猜哪句是事实。

**Files:**
- Modify: `lawiki/skill/lawiki/lint/lint.py`（新增 answer 区段 + 重写 `main`）
- Test: `lawiki/skill/lawiki/lint/test_lint.py`（文件尾追加）

**Interfaces:**
- Consumes: 既有 `_check_anchors(root, pages)`（pages 为 `[(Path, where 标签, 正文)]`，只用后两项）、`ANCHOR_RE`。
- Produces:
  - `NOT_FOUND_PHRASE = "未在本案材料中找到"`（模块常量）
  - `check_answer_anchors(root: Path, text: str, where: str) -> tuple[int, list[str]]`——检查①②，返回（锚点总数, 违规）。**Task 4 的 Stop hook 复用它。**
  - `scan_answer(root: Path, draft: Path) -> tuple[int, list[str]]`——三检全跑。
  - CLI：`python lint.py answer <案件根> <草稿.md>`，退出码 0 过 / 1 违规 / 2 用法或文件错。

- [ ] **Step 1: 写失败测试**

`lawiki/skill/lawiki/lint/test_lint.py` 顶部 import 行改为：

```python
from lint import scan_case, get_pairs, scan_answer  # noqa: E402
```

文件尾追加：

```python
# ---- answer 交付闸门（问答铁规：锚点全验 + 闭世界 + 整篇兜底） ----

def _draft_case(tmp_path: Path, draft_text: str,
                src: str = "本案欠款金额为人民币50,000元，借款人为张三。") -> tuple[Path, Path]:
    _write(tmp_path / "_md" / "借条.md", src)
    draft = tmp_path / "draft.md"
    _write(draft, draft_text)
    return tmp_path, draft


def test_answer_valid_anchor_passes(tmp_path):
    root, draft = _draft_case(
        tmp_path, "欠款本金为 5 万元。〔来源: _md/借条.md：「欠款金额为人民币50,000元」〕\n")
    total, viol = scan_answer(root, draft)
    assert total == 1 and viol == []


def test_answer_wrong_snippet_flagged(tmp_path):
    root, draft = _draft_case(
        tmp_path, "欠款本金。〔来源: _md/借条.md：「欠款金额为人民币50,001元」〕\n")
    _, viol = scan_answer(root, draft)
    assert len(viol) == 1 and "片段不符" in viol[0]


def test_answer_missing_source_flagged(tmp_path):
    root, draft = _draft_case(tmp_path, "欠款本金。〔来源: _md/不存在.md：「任意」〕\n")
    _, viol = scan_answer(root, draft)
    assert len(viol) == 1 and "缺文件" in viol[0]


def test_answer_non_md_anchor_flagged_closed_world(tmp_path):
    # 引用真实存在、但在证据宇宙（_md/）之外的文件——锚点存在性过，闭世界抓。
    root, draft = _draft_case(tmp_path, "结论。〔来源: wiki/页.md：「已有结论」〕\n")
    _write(root / "wiki" / "页.md", "已有结论")
    _, viol = scan_answer(root, draft)
    assert len(viol) == 1 and "闭世界" in viol[0]


def test_answer_bare_prose_flagged(tmp_path):
    # 整篇裸答：有实质内容、零锚点、未明示「未找到」——打回。
    root, draft = _draft_case(tmp_path, "被告应偿还 5 万元。\n")
    _, viol = scan_answer(root, draft)
    assert len(viol) == 1 and "裸答" in viol[0]


def test_answer_not_found_phrase_passes(tmp_path):
    root, draft = _draft_case(tmp_path, "未在本案材料中找到相关约定。\n")
    total, viol = scan_answer(root, draft)
    assert total == 0 and viol == []


def test_answer_pure_analysis_passes(tmp_path):
    # 全篇只有标题 + callout（显式标注的分析）——不是事实陈述，放行。
    root, draft = _draft_case(
        tmp_path, "## 分析\n\n> [!note] 分析（非本案证据）\n> 通常此类合同适用总价包干。\n")
    total, viol = scan_answer(root, draft)
    assert total == 0 and viol == []


def test_answer_cli_exit_codes(tmp_path):
    from lint import main
    root, draft = _draft_case(tmp_path, "未在本案材料中找到相关约定。\n")
    assert main(["lint.py", "answer", str(root), str(draft)]) == 0
    bad = tmp_path / "bad.md"
    _write(bad, "裸答无锚点。\n")
    assert main(["lint.py", "answer", str(root), str(bad)]) == 1
    assert main(["lint.py", "answer", str(root), str(tmp_path / "无此文件.md")]) == 2
```

- [ ] **Step 2: 跑测试确认失败**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\rag-retriever" && uv run pytest -q "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\lint\test_lint.py"
```

预期：collection error（`ImportError: cannot import name 'scan_answer'`）。

- [ ] **Step 3: 实现**

`lawiki/skill/lawiki/lint/lint.py`——在 `scan_case` 定义之后、`# ───── extract` 区段之前插入：

```python
# ───────────────────────── answer：问答交付闸门 ─────────────────────────

NOT_FOUND_PHRASE = "未在本案材料中找到"


def check_answer_anchors(root: Path, text: str, where: str) -> tuple[int, list[str]]:
    """① 锚点全验（复用 _check_anchors）② 闭世界（锚点须指向本案 _md/）。
    供 answer 闸门与 Stop hook 共用——hook 只跑这两项（零误报，无锚点不拦），
    「整篇兜底」归 scan_answer。返回 (锚点总数, 违规)。"""
    violations, _cited, total = _check_anchors(root, [(root / where, where, text)])
    for m in ANCHOR_RE.finditer(text):
        rel = m.group(1).strip().replace("\\", "/")
        if not rel.startswith("_md/"):
            violations.append(
                f"[闭世界] {where}\n          锚点指向本案 _md/ 之外: {rel}")
    return total, violations


def _has_substantive_prose(text: str) -> bool:
    """存在 callout(`>`)/标题(`#`)/空行之外的实质内容行？（前导 frontmatter 跳过）"""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1:]
                break
    for line in lines:
        s = line.strip()
        if s and not s.startswith(">") and not s.startswith("#"):
            return True
    return False


def scan_answer(root: Path, draft: Path) -> tuple[int, list[str]]:
    """交付闸门三检：锚点全验 + 闭世界 + 整篇兜底。兜底只在零锚点时触发：
    有实质内容却零锚点、又未明示「未在本案材料中找到」→ 裸答打回。
    不猜哪句是事实陈述（那是蕴含判官的活），误报率设计为 ~0。"""
    text = draft.read_text(encoding="utf-8")
    total, violations = check_answer_anchors(root, text, draft.name)
    if total == 0 and NOT_FOUND_PHRASE not in text and _has_substantive_prose(text):
        violations.append(
            f"[裸答] {draft.name}\n          零锚点、未明示「{NOT_FOUND_PHRASE}」，"
            f"且含分析标注之外的实质内容——事实必须挂锚点")
    return total, violations
```

整体替换 `main`：

```python
def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 重定向默认 GBK
    except Exception:
        pass
    usage = ("用法：python lint.py check|extract <案件根目录>\n"
             "      python lint.py answer <案件根目录> <回答草稿.md>")
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd in ("check", "extract") and len(argv) == 3:
        root = Path(argv[2])
        if cmd == "extract":
            print(json.dumps(get_pairs(root), ensure_ascii=False, indent=2))
            return 0
        try:
            total, violations, warnings = scan_case(root)
        except FileNotFoundError as e:
            print(e, file=sys.stderr)
            return 2
        print(f"扫描锚点 {total} 个；违规 {len(violations)} 处；警告 {len(warnings)} 处。")
        for v in violations:
            print("  ✗ " + v)
        for w in warnings:
            print("  ! " + w)
        return 1 if violations else 0
    if cmd == "answer" and len(argv) == 4:
        root, draft = Path(argv[2]), Path(argv[3])
        if not draft.is_file():
            print(f"找不到回答草稿：{draft}", file=sys.stderr)
            return 2
        total, violations = scan_answer(root, draft)
        print(f"回答锚点 {total} 个；违规 {len(violations)} 处。")
        for v in violations:
            print("  ✗ " + v)
        return 1 if violations else 0
    print(usage, file=sys.stderr)
    return 2
```

同时更新文件头 docstring 的子命令列表（把 `answer` 补进两条子命令的描述后）：

```python
"""lawiki 校验工具（确定性，仅标准库）。三条子命令：

  python lint.py check   <案件根目录>            # 五类确定性检查，违规则退出码非 0
  python lint.py extract <案件根目录>            # 抽 claim↔引文清单(JSON)，供换实例判官做蕴含校验
  python lint.py answer  <案件根目录> <草稿.md>  # 问答交付闸门：锚点全验+闭世界+整篇兜底

check 五类：① 锚点存在（EXTRACTED 硬底线）② 死链 ③ 时间线顺序 ④ 勾稽闭合
（`> [!check] a+b==c`）⑤ 覆盖率（警告）。只消格式噪声、数字与文字精确——
"数字写错/张冠李戴"必被抓、"换行差异"不误报。详见 SKILL.md / references/verification.md。
"""
```

- [ ] **Step 4: 跑测试确认通过**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\rag-retriever" && uv run pytest -q "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\lint\test_lint.py"
```

预期：全部 PASS（既有 24 个 + 新 8 个）。

- [ ] **Step 5: Commit**

```
git add lawiki/skill/lawiki/lint/lint.py lawiki/skill/lawiki/lint/test_lint.py
git commit -m "feat(lawiki): lint.py answer — QA delivery gate

Three deterministic checks on an answer draft: every anchor verbatim in
its source (reuses _check_anchors), closed world (anchors must point
under _md/), and a whole-answer fallback (zero anchors requires the
canonical not-found phrase or pure flagged analysis). Zero false
positives by design: no guessing which sentence is a factual claim."
```

---

### Task 3: `tools/evidence.py` 前闸门

一条命令代码执行三路取证（RAG / 精确词 grep / outline 结构树），输出统一 JSON 证据包。wiki 路刻意不进（agent 自由导航）。RAG 降级时 grep + outline 照常——证据包绝不空手。

**Files:**
- Create: `lawiki/skill/lawiki/tools/evidence.py`
- Test: `lawiki/skill/lawiki/tools/test_evidence.py`

**Interfaces:**
- Consumes: `rag.search_case(case, query, k)`（含 Task 1 修复后的 `anchor`）、`rag.build_anchor(source, snippet, quality)`、`outline.build_case_outline(root) -> [{source, outline}]`、lint 的 `norm(s)`（grep 归一化匹配用——"50000" 要能命中原文的 "50,000"，与 lint「只消格式噪声」同一套归一化）。同目录 `import rag` / `import outline` + `../lint` 的 `norm`（脚本运行时手动 `sys.path.insert`；测试里同样处理）。
- Produces:
  - `grep_terms(case: Path, terms: list[str]) -> dict`——`{"hits": [...], "truncated": [超上限的词]}`；命中项 `{term, source, text, anchor, unverified}`，查无的词产出 `{term, source: None, text: None, anchor: None, not_found: True}`。
  - `gather(case: Path, question: str, terms: list[str], k: int) -> dict`——`{"question", "rag", "grep", "outline"}`。
  - CLI：`python evidence.py <案件根> "<问题>" [--terms "a,b"] [-k 8]`，恒输出 JSON、退出码 0。

- [ ] **Step 1: 写失败测试**

新建 `lawiki/skill/lawiki/tools/test_evidence.py`：

```python
# -*- coding: utf-8 -*-
"""evidence wrapper 回归测试（stdlib unittest，零依赖）。

锁住：grep 精确命中 + 现成锚点过真实 lint、quality→未核验、查无留痕(not_found)、
每词命中上限、RAG 降级时证据包仍有 grep/outline、gather 输出结构。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "lint"))

import evidence  # noqa: E402
import rag  # noqa: E402
from lint import scan_case  # noqa: E402


def _case(root: Path, name: str, text: str) -> None:
    (root / "_md").mkdir(parents=True, exist_ok=True)
    (root / "_md" / name).write_text(text, encoding="utf-8")


class GrepTermsTests(unittest.TestCase):
    def test_hit_carries_anchor_that_passes_real_lint(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "合同.md", "# 合同\n\n第八条 违约方应支付违约金 50000 元。\n")
            result = evidence.grep_terms(root, ["第八条"])
            hits = [h for h in result["hits"] if not h.get("not_found")]
            self.assertEqual(len(hits), 1)
            self.assertEqual(hits[0]["source"], "_md/合同.md")

            (root / "wiki").mkdir()
            (root / "wiki" / "p.md").write_text(
                f"- 事实 {hits[0]['anchor']}\n", encoding="utf-8")
            total, violations, _ = scan_case(root)
            self.assertEqual((total, violations), (1, []), msg=str(violations))

    def test_suspect_source_marks_unverified(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "扫描件.md",
                  "---\nquality: suspect\n---\n借款金额为 88888 元。\n")
            result = evidence.grep_terms(root, ["88888"])
            hit = [h for h in result["hits"] if not h.get("not_found")][0]
            self.assertTrue(hit["unverified"])
            self.assertTrue(hit["anchor"].endswith("（未核验）"))

    def test_absent_term_recorded_as_not_found(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "a.md", "无关内容。\n")
            result = evidence.grep_terms(root, ["李四"])
            self.assertEqual(result["hits"],
                             [{"term": "李四", "source": None, "text": None,
                               "anchor": None, "not_found": True}])

    def test_per_term_cap_marks_truncated(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "长文.md", "\n".join(f"第{i}行 甲方" for i in range(50)))
            result = evidence.grep_terms(root, ["甲方"])
            self.assertEqual(len(result["hits"]), evidence._MAX_HITS_PER_TERM)
            self.assertEqual(result["truncated"], ["甲方"])

    def test_normalized_match_hits_thousands_separator(self):
        # 金额精确词按 lint 归一化匹配："50000" 须命中原文的 "50,000"
        # （逗号是格式噪声）；锚点片段仍取原始行逐字，故必过 lint。
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "借条.md", "借款人民币50,000元整。\n")
            result = evidence.grep_terms(root, ["50000"])
            hits = [h for h in result["hits"] if not h.get("not_found")]
            self.assertEqual(len(hits), 1)
            self.assertIn("50,000", hits[0]["text"])

            (root / "wiki").mkdir()
            (root / "wiki" / "p.md").write_text(
                f"- 事实 {hits[0]['anchor']}\n", encoding="utf-8")
            total, violations, _ = scan_case(root)
            self.assertEqual((total, violations), (1, []), msg=str(violations))


class GatherTests(unittest.TestCase):
    def test_rag_degraded_bundle_still_has_grep_and_outline(self):
        # 无 .rag/ → search_case 走真实降级路径（不起子进程），grep/outline 照常。
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "合同.md", "# 第一章\n\n甲方为某公司。\n")
            bundle = evidence.gather(root, "甲方是谁", ["甲方"], k=8)
            self.assertFalse(bundle["rag"]["rag_available"])
            self.assertEqual(
                len([h for h in bundle["grep"]["hits"] if not h.get("not_found")]), 1)
            self.assertEqual(bundle["outline"][0]["source"], "_md/合同.md")
            self.assertEqual(bundle["question"], "甲方是谁")

    def test_rag_available_hits_passed_through(self):
        fake = {"rag_available": True,
                "hits": [{"source": "_md/a.md", "text": "x", "score": 0.9,
                          "anchor": "〔来源: _md/a.md：「x」〕", "unverified": False}]}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            _case(root, "a.md", "x\n")
            with mock.patch.object(rag, "search_case", return_value=fake) as m:
                bundle = evidence.gather(root, "问题", [], k=5)
            m.assert_called_once_with(root, "问题", k=5)
            self.assertTrue(bundle["rag"]["rag_available"])
            self.assertEqual(bundle["grep"], {"hits": [], "truncated": []})


class TermSplitTests(unittest.TestCase):
    def test_split_on_ascii_and_chinese_comma(self):
        self.assertEqual(evidence.split_terms("50万, 张三，第八条"),
                         ["50万", "张三", "第八条"])
        self.assertEqual(evidence.split_terms(""), [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\tools" && python -m unittest test_evidence -v
```

预期：`ModuleNotFoundError: No module named 'evidence'`。

- [ ] **Step 3: 实现**

新建 `lawiki/skill/lawiki/tools/evidence.py`：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""三路取证一条命令（确定性，仅标准库）——问答的前闸门。

把 qa.md「多路并行取证」中可脚本化的三路压成一条命令，agent 答题前必跑：
  python evidence.py <案件根目录> "<问题>" [--terms "50万,张三,第八条"] [-k 8]

三路（wiki 路是 agent 自由导航——index → wikilink → graph.py——刻意不在此内）：
  rag     语义检索（经 rag.py 单一入口；不可用时 rag_available:false，其余照常）
  grep    --terms 各精确词在 _md/ 的逐行命中（向量按语义检索常漏的法条号/
          姓名/金额/案号；查无的词以 not_found 留痕——「grep 也没有」≠「忘了查」）
  outline 每份 _md/ 的标题树（问题措辞与原文用词不同时按结构导航）

恒输出 JSON；RAG 降级也绝不空手（grep + outline 零依赖始终可用）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))  # 同目录兄弟模块
sys.path.insert(0, str(_HERE.parent / "lint"))  # lint.norm：与锚点校验同一套归一化
import outline  # noqa: E402
import rag  # noqa: E402
from lint import norm  # noqa: E402

# 每个精确词最多带回的命中行数：常用字撞进高频词（如"元"）时防证据包爆炸。
_MAX_HITS_PER_TERM = 20
_QUALITY_RE = re.compile(r"^quality:\s*(\S+)", re.MULTILINE)


def _source_quality(text: str) -> str | None:
    """从 _md 文件的前导 frontmatter 读 quality 字段（轻量，不解析 YAML）。"""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    m = _QUALITY_RE.search(text[3:end])
    return m.group(1) if m else None


def split_terms(raw: str) -> list[str]:
    """逗号分隔的精确词（中英文逗号都认）。"""
    return [t.strip() for t in re.split(r"[,，]", raw) if t.strip()]


def grep_terms(case: Path, terms: list[str]) -> dict:
    """对每个精确词逐行扫 _md/**/*.md。匹配按 lint 的 norm 归一化做（中文无需
    分词；"50000" 命中原文 "50,000"——逗号/空白/全半角是格式噪声，与锚点校验
    同一套标准）；锚点片段取**原始行逐字**（折叠空白成单行），故必过 lint。
    命中带来源 quality 的「未核验」标注。"""
    md_dir = case / "_md"
    files: list[tuple[str, list[tuple[str, str]], str | None]] = []  # (相对路径, [(原始行,归一行)], quality)
    if md_dir.is_dir():
        for f in sorted(md_dir.rglob("*.md")):
            text = f.read_text(encoding="utf-8", errors="replace")
            lines = [(ln, norm(ln)) for ln in text.splitlines()]
            files.append((f.relative_to(case).as_posix(), lines, _source_quality(text)))
    hits: list[dict] = []
    truncated: list[str] = []
    for term in terms:
        nterm = norm(term)
        found = 0
        if nterm:  # 归一化后为空的词（纯标点）没有可匹配的实质，直接记查无
            for rel, lines, quality in files:
                for raw, nline in lines:
                    if nterm not in nline:
                        continue
                    found += 1
                    if found > _MAX_HITS_PER_TERM:
                        break
                    snippet = " ".join(raw.split())
                    hits.append({"term": term, "source": rel, "text": snippet,
                                 "anchor": rag.build_anchor(rel, snippet, quality),
                                 "unverified": quality == "suspect"})
                if found > _MAX_HITS_PER_TERM:
                    truncated.append(term)
                    break
        if found == 0:
            hits.append({"term": term, "source": None, "text": None,
                         "anchor": None, "not_found": True})
    return {"hits": hits, "truncated": truncated}


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
```

- [ ] **Step 4: 跑测试确认通过**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\tools" && python -m unittest test_evidence -v
```

预期：8 个测试全 PASS。

- [ ] **Step 5: 跑全部 tools 测试防外溢**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki" && python -m unittest discover -s tools -p "test_*.py"
```

预期：OK（35 + 8 = 43 个）。

- [ ] **Step 6: Commit**

```
git add lawiki/skill/lawiki/tools/evidence.py lawiki/skill/lawiki/tools/test_evidence.py
git commit -m "feat(lawiki): tools/evidence.py — one-command three-path retrieval

Front gate for QA: RAG (via rag.py, degrades cleanly), exact-term grep
over _md/ (with ready-made lint-valid anchors and not-found traces), and
the per-file outline tree. The wiki path stays agent-navigated by design.
The bundle is never empty: grep + outline are stdlib-only and always run."
```

---

### Task 4: Stop hook（可选加硬）+ `setup.md` 文档

Claude Code 专属、opt-in。只跑 `check_answer_anchors` 两检（零误报）；无锚点回复不拦（hook 分不清案件问答与日常对话）；`stop_hook_active` 时放行防死循环；transcript 读不到一律放行（hook 只能更严，不能误伤）。

**Files:**
- Create: `lawiki/skill/lawiki/lint/stop_hook.py`
- Create: `lawiki/skill/lawiki/lint/test_stop_hook.py`
- Modify: `lawiki/skill/lawiki/references/setup.md`（「第 3 步补」之后插入新小节）

**Interfaces:**
- Consumes: Task 2 的 `check_answer_anchors(root, text, where)`。Claude Code Stop hook 协议：stdin 收 JSON（`transcript_path`/`cwd`/`stop_hook_active`），transcript 为 JSONL（每行一个 entry，assistant 行形如 `{"type":"assistant","message":{"content":[{"type":"text","text":"…"}]}}`）；阻止交付 = stdout 输出 `{"decision":"block","reason":"…"}`。
- Produces: `last_assistant_text(transcript_path) -> str`、`decide(root, reply) -> str | None`（返回 block 理由或 None）。

- [ ] **Step 1: 写失败测试**

新建 `lawiki/skill/lawiki/lint/test_stop_hook.py`（pytest 风格，与 `test_lint.py` 同式）：

```python
# -*- coding: utf-8 -*-
"""Stop hook 回归测试：transcript 解析 + 两检决策（零误报边界）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stop_hook import decide, last_assistant_text  # noqa: E402


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _transcript(tmp_path: Path, *entries: dict) -> str:
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries),
                 encoding="utf-8")
    return str(p)


def _assistant(text: str) -> dict:
    return {"type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]}}


# ---- transcript 解析 ----

def test_last_assistant_wins_and_junk_ignored(tmp_path):
    path = _transcript(
        tmp_path,
        _assistant("第一条回复"),
        {"type": "user", "message": {"content": "提问"}},
        _assistant("最后一条回复"))
    # 混入坏行也不崩
    with open(path, "a", encoding="utf-8") as f:
        f.write("\nnot json at all")
    assert last_assistant_text(path) == "最后一条回复"


def test_missing_transcript_returns_empty(tmp_path):
    assert last_assistant_text(str(tmp_path / "无此文件.jsonl")) == ""


# ---- 决策（root 需有 _md/ 源文件） ----

def _root(tmp_path: Path) -> Path:
    _write(tmp_path / "_md" / "借条.md", "本案欠款金额为人民币50,000元。")
    return tmp_path


def test_no_anchor_reply_never_blocked(tmp_path):
    assert decide(_root(tmp_path), "帮你把文件改好了。") is None


def test_valid_anchor_passes(tmp_path):
    reply = "欠款 5 万元。〔来源: _md/借条.md：「欠款金额为人民币50,000元」〕"
    assert decide(_root(tmp_path), reply) is None


def test_broken_anchor_blocked(tmp_path):
    reply = "欠款。〔来源: _md/借条.md：「欠款金额为人民币50,001元」〕"
    reason = decide(_root(tmp_path), reply)
    assert reason and "片段不符" in reason


def test_outside_md_anchor_blocked(tmp_path):
    root = _root(tmp_path)
    _write(root / "wiki" / "页.md", "已有结论")
    reason = decide(root, "结论。〔来源: wiki/页.md：「已有结论」〕")
    assert reason and "闭世界" in reason
```

- [ ] **Step 2: 跑测试确认失败**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\rag-retriever" && uv run pytest -q "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\lint\test_stop_hook.py"
```

预期：collection error（`ModuleNotFoundError: No module named 'stop_hook'`）。

- [ ] **Step 3: 实现**

新建 `lawiki/skill/lawiki/lint/stop_hook.py`：

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude Code Stop hook（可选加硬）：校验最后一条回复里的引用锚点。

只跑零误报的两检——锚点逐字存在 + 闭世界（指向本案 _md/）。不跑「整篇兜底」：
hook 分不清案件问答与日常对话，无锚点的回复一律放行；「裸答必须明示未找到」
的密度约束由协议里的 `lint.py answer` 全量闸门负责。配置见 references/setup.md。

协议：stdin 收 hook JSON（transcript_path / cwd / stop_hook_active）；违规时
stdout 输出 {"decision":"block","reason":…} 让 agent 修复后重答。三条放行铁则：
stop_hook_active（防死循环）、cwd 无 _md/（不在案件目录）、transcript 读不到/
读不懂（hook 只能更严，不能误伤）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lint import check_answer_anchors  # noqa: E402


def last_assistant_text(transcript_path: str) -> str:
    """从 Claude Code 的 JSONL transcript 抽最后一条 assistant 文本。"""
    p = Path(transcript_path)
    if not p.is_file():
        return ""
    text = ""
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            if parts:
                text = "\n".join(parts)
    return text


def decide(root: Path, reply: str) -> str | None:
    """返回 block 理由；None = 放行。无锚点直接放行（零误报边界）。"""
    if "〔来源:" not in reply:
        return None
    _total, violations = check_answer_anchors(root, reply, "<回复>")
    if not violations:
        return None
    return ("回复中的引用锚点未通过确定性校验（须逐字存在且指向本案 _md/）。"
            "请修正后重新作答——只许把引用改真实、把断言改忠实，绝不为过校验编造：\n"
            + "\n".join(violations))


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if data.get("stop_hook_active"):
        return 0  # 已在 hook 触发的重答里，不再拦：防死循环
    root = Path(data.get("cwd") or ".")
    if not (root / "_md").is_dir():
        return 0  # 不在案件目录，与本 hook 无关
    reason = decide(root, last_assistant_text(data.get("transcript_path", "")))
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

```
cd "D:\Vibe Coding Items\AnyDocsMarked\rag-retriever" && uv run pytest -q "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\lint\test_stop_hook.py"
```

预期：6 个测试全 PASS。

- [ ] **Step 5: 增补 `setup.md`**

在 `lawiki/skill/lawiki/references/setup.md` 的「## 第 3 步补 · RAG 检索（可选，可降级）」小节**末尾之后、「## 第 4 步 · 优雅降级」之前**插入：

```markdown
## 第 3 步再补 · 问答交付闸门加硬（可选，仅 Claude Code）

问答协议已要求 agent 交付前自跑 `lint.py answer`（见 `qa.md` 第四步）。用 Claude Code 时可再加一道 harness 级保险：**Stop hook** 在每次回复结束时自动校验回复中的锚点（逐字存在 + 指向本案 `_md/`），违规自动打回重答。在**案件目录**建 `.claude/settings.json`（`<SKILL_DIR>` 换成本 skill 的绝对路径）：

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python \"<SKILL_DIR>/lint/stop_hook.py\""
          }
        ]
      }
    ]
  }
}
```

边界（如实告诉用户）：hook 只做零误报的两检；**无锚点的回复不拦**（它分不清案件问答与日常闲聊），「裸答必须明示未找到」的兜底仍靠协议里的 answer 闸门。其他 agent（Codex / Copilot 等）无此机制，靠协议约束；闸门工具本身零依赖、随 skill 走。
```

- [ ] **Step 6: Commit**

```
git add lawiki/skill/lawiki/lint/stop_hook.py lawiki/skill/lawiki/lint/test_stop_hook.py lawiki/skill/lawiki/references/setup.md
git commit -m "feat(lawiki): optional Claude Code Stop hook for QA anchors

Runs only the zero-false-positive subset (anchor verbatim existence +
closed world) on the last assistant reply; replies without anchors pass
(the hook cannot tell case QA from ordinary chat — the bare-answer rule
stays with lint.py answer). Never blocks on stop_hook_active, outside a
case dir, or when the transcript is unreadable."
```

---

### Task 5: 协议接线（`qa.md` + `SKILL.md`）

把两道闸门和情形④倾向写进问答协议。改动全是文档，但措辞是协议本体——须逐字按下面的块落。

**Files:**
- Modify: `lawiki/skill/lawiki/references/qa.md`
- Modify: `lawiki/skill/lawiki/SKILL.md`

**Interfaces:**
- Consumes: Task 2 的 CLI（`lint.py answer <案件根> <草稿.md>`）、Task 3 的 CLI（`evidence.py <案件根> "<问题>" --terms … -k 8`）。命令行拼写必须与实现一致。

- [ ] **Step 1: `qa.md` — 头部降级说明**

将：

```
> RAG 是可降级旁路：检索经 `<SKILL_DIR>/tools/rag.py`（见 `rag.md`）。没装 / 没建索引 / 模型不一致 → 退化为「仅 wiki」，并明确告知用户「当前无 RAG 交叉验证」。
```

替换为：

```
> RAG 是可降级旁路：取证经 `<SKILL_DIR>/tools/evidence.py` 一条命令（内部经 `tools/rag.py` 调 RAG，见 `rag.md`）。没装 / 没建索引 / 模型不一致 → 证据包中 `rag_available:false`，但 grep 与 outline 两路照常返回；须明确告知用户「当前无 RAG 交叉验证」。
```

- [ ] **Step 2: `qa.md` — 第一步取证改为 evidence 命令**

将「## 第一步 · 多路并行取证」小节中第 2、3、4 项（`2. **RAG 路**：…` 到该小节末尾）整体替换为：

```markdown
2. **证据包路（一条命令，三路代码执行）**：
   ```
   python <SKILL_DIR>/tools/evidence.py <案件根> "<问题>" --terms "<金额,姓名,法条号,案号>" -k 8
   ```
   先从问题里抽出**精确词**（金额/当事人姓名/法条号/案号——向量按语义检索常漏的）填进 `--terms`；金额写成不带千分位逗号的形式（如 `50000`），匹配按归一化做、照样命中原文的「50,000」。返回统一 JSON：
   - `rag`：语义检索命中（相对路径 + 逐字 `text` + `score` + `quality` + 现成 `anchor`）。`k≥8` 防假冲突：先凑够上下文再判，别把「RAG 没检索到」误判成「矛盾」。`rag_available:false` 时其余两路照常返回——证据包绝不空手，但须告知用户「当前无 RAG 交叉验证」。
   - `grep`：各精确词在 `_md/` 的逐行命中（带现成 `anchor`；来源可疑的带「（未核验）」）。**一个词向量没召回 ≠ 不存在；grep 结果为准**——查无的词带 `not_found`，这时才可以放心说「未找到」。
   - `outline`：每份 `_md/` 的标题树。**当问题措辞与原文用词不同**（如问「违约责任」而合同写「第八条 责任」），按结构导航到相关文件的相关章节，读该节原文取证、拼锚点。
```

（保留第 1 项 wiki 路与 graph 工具原文不动。）

- [ ] **Step 3: `qa.md` — 情形④升级**

将：

```
- **④ 不一致，查不出因**（安全阀）→ **不许静默取舍**：把【wiki 答案 + 锚点】与【RAG 答案 + `_md/` 锚点】**并列**，各附相对路径 + 逐字片段，明说「**无法判定，请人工溯源裁决**」→ 交用户。
```

替换为：

```
- **④ 不一致，查不出因**（安全阀）→ **不许静默取舍**：把【wiki 答案 + 锚点】与【RAG 答案 + `_md/` 锚点】**并列**，各附相对路径 + 逐字片段，明说「**无法判定，请人工溯源裁决**」→ 交用户。
  并列之后，**应当**附上你的倾向（`> [!note] 分析` 标注，属 INFERRED）：倾向哪版 + 支撑理由 + **你排除不掉的反方证据** + 「最终由你裁决」。措辞取"请你来反驳我"（摆足理由与反证供人推翻），不取"建议照此办"（诱导盖章）。倾向**绝不替代、绝不压缩**两边原文的完整并列——递交裁决权 ≠ 放弃分析。
```

- [ ] **Step 4: `qa.md` — 新增第四步交付闸门**

在「## 第三步 · 引用纪律（继承铁律）」小节之后、「## 共同盲区与降级」之前插入：

```markdown
## 第四步 · 交付闸门（确定性，必过）

回答**发给用户前**：写入草稿文件（任意临时路径均可）→ 跑

```
python <SKILL_DIR>/lint/lint.py answer <案件根> <草稿.md>
```

→ **0 违规（退出码 0）才允许交付**。它确定性校验三件事：① 每个锚点的逐字片段确在所指源文件 ② 锚点全部指向本案 `_md/`（闭世界的机器兜底）③ 整篇零锚点时必须明示「未在本案材料中找到」或全篇为标注的分析——**裸答直接打回**。

违规 → **有界修复**：只许把引用改真实、把断言改忠实，**绝不为过闸门编造锚点**（同蕴含判官的纪律）→ 重跑；反复不过时把违规原样告知用户，绝不静默交付。
```

- [ ] **Step 5: `SKILL.md` — 问答小节同步**

将「## 案件问答（交叉验证）」小节中的流程段：

```
流程：**多路并行取证**（wiki 已综合结论 + RAG 原文 `python <SKILL_DIR>/tools/rag.py search <案件根> "<问题>" -k 8` + outline 结构 + 精确词 grep `_md`）→ **四情形分流**：一致则答、wiki 沉默用原文答、不一致能定因则以原文为准并指出 wiki 待修处、查不出因则把两套答案 + 各自锚点并列交用户裁决。完整协议见 **`references/qa.md`**。RAG 不可用时退化「仅 wiki」并告知用户。
```

替换为：

```
流程：**取证**（wiki 路自由导航 + 一条命令跑齐其余三路：`python <SKILL_DIR>/tools/evidence.py <案件根> "<问题>" --terms "<精确词>" -k 8`，RAG/精确词 grep/outline）→ **四情形分流**：一致则答、wiki 沉默用原文答、不一致能定因则以原文为准并指出 wiki 待修处、查不出因则两套答案 + 各自锚点并列、附标注为分析的倾向、交用户裁决 → **交付闸门（铁规）**：回答先写草稿过 `python <SKILL_DIR>/lint/lint.py answer <案件根> <草稿.md>`，**0 违规才发**。完整协议见 **`references/qa.md`**。RAG 不可用时证据包自动降级（grep + outline 仍在）并告知用户。
```

同时把 SKILL.md 第 10 行工具列表句中的 `工具在 tools/：rag.py（RAG 包装）、outline.py（…）` 更新为：

```
工具在 `tools/`：`evidence.py`（问答取证一条命令：RAG+精确词+outline）、`rag.py`（RAG 包装）、`outline.py`（`_md` 标题树导航，零依赖、对抗遗漏、亦作无 RAG 降级）。
```

- [ ] **Step 6: 一致性检查**

逐条核对文档中的命令与实现签名一致：

```
cd "D:\Vibe Coding Items\AnyDocsMarked" && grep -rn "evidence.py" lawiki/skill/lawiki --include="*.md" && grep -rn "lint.py answer" lawiki/skill/lawiki --include="*.md"
```

预期：qa.md（2 处 evidence + 1 处 answer）、SKILL.md（1 处 evidence + 1 处 answer）、setup.md（1 处 answer 提及），命令拼写与 Task 2/3 的 CLI 完全一致。

- [ ] **Step 7: Commit**

```
git add lawiki/skill/lawiki/references/qa.md lawiki/skill/lawiki/SKILL.md
git commit -m "docs(lawiki): wire QA gates into the protocol

qa.md: evidence command replaces the three prose retrieval paths; new
mandatory delivery gate step (lint.py answer, zero violations to ship);
case-4 now requires the agent's flagged-as-analysis leaning alongside
the intact side-by-side presentation. SKILL.md summary kept in sync."
```

---

### Task 6: 端到端验证

全链路走一遍：合成案件 → evidence（降级态）→ 好/坏草稿过 answer 闸门 → 全部测试套件。

**Files:** 无新文件（临时案件建在系统临时目录，用后即弃）。

- [ ] **Step 1: 建合成案件并跑 evidence**

（bash；`$TMPDIR` 用系统临时目录或 scratchpad）

```bash
CASE="$(mktemp -d)/案件A" && mkdir -p "$CASE/_md"
cat > "$CASE/_md/借条.md" <<'EOF'
---
source: 借条.pdf
---
# 借条

张三于2023年1月5日向李四借款人民币50,000元，约定第八条按月息1%计息。
EOF
PYTHONIOENCODING=utf-8 python "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\tools\evidence.py" "$CASE" "借款金额是多少" --terms "50000,第八条,王五"
```

预期 JSON：`rag.rag_available` 为 `false`（无 `.rag/`）；`grep.hits` 含 `50000`（归一化命中原文「50,000」的行）与 `第八条` 的命中（带 `anchor`）、`王五` 的 `not_found:true`；`outline` 含 `_md/借条.md` 的 `# 借条` 标题树。

- [ ] **Step 2: 好草稿过闸门（退出码 0）**

```bash
cat > "$CASE/draft_good.md" <<'EOF'
借款金额为人民币 5 万元。〔来源: _md/借条.md：「向李四借款人民币50,000元」〕
EOF
PYTHONIOENCODING=utf-8 python "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\lint\lint.py" answer "$CASE" "$CASE/draft_good.md"; echo "exit=$?"
```

预期：`回答锚点 1 个；违规 0 处。` + `exit=0`。

- [ ] **Step 3: 坏草稿被打回（退出码 1，三类违规各现形）**

```bash
cat > "$CASE/draft_bad.md" <<'EOF'
借款金额为人民币 6 万元。〔来源: _md/借条.md：「借款人民币60,000元」〕
另据判例。〔来源: wiki/判例.md：「任意」〕
EOF
PYTHONIOENCODING=utf-8 python "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\lint\lint.py" answer "$CASE" "$CASE/draft_bad.md"; echo "exit=$?"
cat > "$CASE/draft_bare.md" <<'EOF'
借款金额为 5 万元，没有异议。
EOF
PYTHONIOENCODING=utf-8 python "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\lint\lint.py" answer "$CASE" "$CASE/draft_bare.md"; echo "exit=$?"
```

预期：第一跑报「片段不符」+「缺文件」（或闭世界，`wiki/判例.md` 不存在时缺文件与闭世界同时报——两条违规均可接受，须 `exit=1`）；第二跑报「裸答」+ `exit=1`。

- [ ] **Step 4: 全套测试回归**

```bash
cd "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki" && python -m unittest discover -s tools -p "test_*.py"
cd "D:\Vibe Coding Items\AnyDocsMarked\rag-retriever" && uv run pytest -q "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\lint\test_lint.py" "D:\Vibe Coding Items\AnyDocsMarked\lawiki\skill\lawiki\lint\test_stop_hook.py" "D:\Vibe Coding Items\AnyDocsMarked\lawiki\scripts\test_build_bundle.py"
cd "D:\Vibe Coding Items\AnyDocsMarked\rag-retriever" && uv run pytest -q
cd "D:\Vibe Coding Items\AnyDocsMarked\makeitdown" && uv run pytest -q
```

预期：tools OK（43 个）；lint/hook/bundle 全 PASS；rag-retriever 71 passed；makeitdown 152 passed。

- [ ] **Step 5: 收尾确认**

```bash
cd "D:\Vibe Coding Items\AnyDocsMarked" && git status --short
```

预期：只剩计划开始前就存在的三个无关改动（`lawiki/README.md`、`makeitdown/README.md`、`ocr_mineru.py`）；本计划的文件全部已提交。若 Step 1-3 发现问题，回相应 Task 修复（先补失败测试再改码），修完重跑本 Task。
