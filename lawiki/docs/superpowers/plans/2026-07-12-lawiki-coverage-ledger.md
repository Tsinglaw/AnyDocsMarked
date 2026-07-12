# lawiki 覆盖率三态账本 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 lint 覆盖率从两态（引用/未引用）升级为三态（已引用/登记跳过/未处置），跳过决策以 `wiki/log.md` skip 条目显式留痕，警告可收敛到 0，并在 SKILL.md 中钉死"ingest 完成 = 0 违规 且 未处置 = 0"。

**Architecture:** 账本复用既有 append-only `wiki/log.md`（新增 `skip` 操作条目），lint 新增一个解析函数 `_load_skips`，`_check_coverage` 按三态分类并返回统计，`scan_case` 返回值从 3 元组变 4 元组，CLI `check` 恒输出覆盖率汇总行。随后同步三份文档（verification.md / page-formats.md / SKILL.md）。

**Tech Stack:** Python 3.11+ 标准库（lint 铁规：零第三方依赖）、pytest（既有测试风格：tmp_path + `_write` helper）。

**Spec:** `lawiki/docs/superpowers/specs/2026-07-12-lawiki-coverage-ledger-design.md`

## Global Constraints

- lint 仅标准库，Python 3.11+，不新增任何依赖。
- 覆盖率保持 **soft**：所有新增输出/警告均不影响退出码（`check` 仍是 `1 if violations else 0`）。
- wiki 固定结构零新增文件；账本只写在 `wiki/log.md`。
- 所有新判据必须是确定性格式判据（零误报原则）。
- 警告文案精确采用：`[未处置] <路径>`、`[跳过无原因] <路径>`；汇总行精确采用：`覆盖率：<N> 源文件 | 已引用 <a> | 登记跳过 <b> | 未处置 <c>`。
- 引用优先于登记：文件被任何锚点引用即归"已引用"，其 skip 条目冗余无害（即使缺原因也不警告）。
- skip 条目路径在 `_md/` 中不存在 → 静默忽略。
- 所有文件 UTF-8；测试命令在**仓库根目录**执行。

---

### Task 1: `_load_skips` — log.md skip 条目解析器

**Files:**
- Modify: `lawiki/skill/lawiki/lint/lint.py`（在 `_check_closures` 之后、`_check_coverage` 之前插入）
- Test: `lawiki/skill/lawiki/lint/test_lint.py`

**Interfaces:**
- Consumes: 无（纯新增）。
- Produces: `_load_skips(root: Path) -> dict[str, bool]` —— 键为 skip 条目里的 POSIX 路径（反斜杠已归一化），值为"是否带非空原因"。Task 2 的 `_check_coverage` 依赖此签名。

- [ ] **Step 1: Write the failing tests**

在 `test_lint.py` 的 `# ---- ⑤ 覆盖率（警告） ----` 小节之前插入新小节（import 行加在文件顶部 `from lint import ...` 处，把 `_load_skips` 补进去）：

```python
# ---- ⑤ 覆盖率：log.md skip 条目解析 ----

def test_load_skips_basic(tmp_path):
    _write(tmp_path / "wiki" / "log.md",
           "# 操作日志\n\n## [2026-07-12] skip | _md/a.md\n- 原因：红线对比版\n")
    assert _load_skips(tmp_path) == {"_md/a.md": True}


def test_load_skips_missing_reason(tmp_path):
    _write(tmp_path / "wiki" / "log.md", "## [2026-07-12] skip | _md/a.md\n")
    assert _load_skips(tmp_path) == {"_md/a.md": False}


def test_load_skips_empty_reason_is_missing(tmp_path):
    _write(tmp_path / "wiki" / "log.md", "## [2026-07-12] skip | _md/a.md\n- 原因：  \n")
    assert _load_skips(tmp_path) == {"_md/a.md": False}


def test_load_skips_reason_scoped_to_entry(tmp_path):
    # 原因行只归属其上方最近的 skip 条目；隔了别的 ## 条目不得串账。
    _write(tmp_path / "wiki" / "log.md",
           "## [2026-07-12] skip | _md/a.md\n"
           "## [2026-07-12] ingest | b.md\n- 原因：不该算到 a 头上\n")
    assert _load_skips(tmp_path) == {"_md/a.md": False}


def test_load_skips_duplicate_entries_reason_or(tmp_path):
    # 同一路径多条登记：任一条带原因即视为有原因（append-only 下补一条即修复缺原因）。
    _write(tmp_path / "wiki" / "log.md",
           "## [2026-07-10] skip | _md/a.md\n"
           "## [2026-07-12] skip | _md/a.md\n- 原因：补充原因\n")
    assert _load_skips(tmp_path) == {"_md/a.md": True}


def test_load_skips_no_log(tmp_path):
    assert _load_skips(tmp_path) == {}


def test_load_skips_backslash_normalized(tmp_path):
    _write(tmp_path / "wiki" / "log.md",
           "## [2026-07-12] skip | _md\\a.md\n- 原因：x\n")
    assert _load_skips(tmp_path) == {"_md/a.md": True}


def test_load_skips_halfwidth_colon_accepted(tmp_path):
    _write(tmp_path / "wiki" / "log.md",
           "## [2026-07-12] skip | _md/a.md\n- 原因: 半角冒号也行\n")
    assert _load_skips(tmp_path) == {"_md/a.md": True}
```

同时把 test_lint.py 第 7 行的 import 改为：

```python
from lint import scan_case, get_pairs, scan_answer, _load_skips  # noqa: E402
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest lawiki/skill/lawiki/lint/test_lint.py -k load_skips -v`
Expected: FAIL / ERROR with `ImportError: cannot import name '_load_skips'`

- [ ] **Step 3: Write the implementation**

在 `lint.py` 中 `_check_closures` 函数之后、`_check_coverage` 之前插入：

```python
SKIP_RE = re.compile(r"^##\s*\[\d{4}-\d{2}-\d{2}\]\s*skip\s*\|\s*(.+?)\s*$")
REASON_RE = re.compile(r"^\s*-\s*原因[:：](.*)$")


def _load_skips(root: Path) -> dict[str, bool]:
    """解析 wiki/log.md 的 skip 条目（覆盖率账本）：
    `## [YYYY-MM-DD] skip | <路径>` + 条目正文 `- 原因：<非空理由>`。
    返回 {POSIX 路径: 是否带非空原因}。同一路径多条登记取"任一条带原因"
    （append-only 下补登记即可修复缺原因）；原因行只归属其上方最近的 skip 条目。"""
    skips: dict[str, bool] = {}
    log = root / "wiki" / "log.md"
    if not log.is_file():
        return skips
    cur: str | None = None
    for line in log.read_text(encoding="utf-8").splitlines():
        m = SKIP_RE.match(line)
        if m:
            cur = m.group(1).replace("\\", "/")
            skips.setdefault(cur, False)
            continue
        if line.startswith("#"):  # 任何其他标题都结束当前 skip 条目的正文
            cur = None
            continue
        if cur is not None:
            r = REASON_RE.match(line)
            if r and r.group(1).strip():
                skips[cur] = True
    return skips
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest lawiki/skill/lawiki/lint/test_lint.py -v`
Expected: 全部 PASS（新增 8 个 + 既有全部）

- [ ] **Step 5: Commit**

```bash
git add lawiki/skill/lawiki/lint/lint.py lawiki/skill/lawiki/lint/test_lint.py
git commit -m "feat(lawiki-lint): parse log.md skip entries (coverage ledger)"
```

---

### Task 2: 三态 `_check_coverage` + `scan_case` 返回覆盖率统计

**Files:**
- Modify: `lawiki/skill/lawiki/lint/lint.py`（`_check_coverage`、`scan_case`；`main` 的解包在本任务一并改，汇总行输出留给 Task 3）
- Modify: `lawiki/skill/lawiki/lint/test_lint.py`（既有解包点 + 新用例）
- Modify: `lawiki/skill/lawiki/tools/test_evidence.py:39,82`（解包点）
- Modify: `lawiki/skill/lawiki/tools/test_rag.py:54,77,108`（解包点）

**Interfaces:**
- Consumes: Task 1 的 `_load_skips(root) -> dict[str, bool]`。
- Produces: `_check_coverage(root: Path, cited: set[str]) -> tuple[list[str], dict[str, int]]`，统计 dict 固定四键 `{"total", "cited", "skipped", "unresolved"}`；`scan_case(root) -> tuple[int, list[str], list[str], dict[str, int]]`（第 4 元即该统计）。Task 3 的 CLI 依赖 `scan_case` 的 4 元组形状与统计键名。

- [ ] **Step 1: Write the failing tests**

替换 test_lint.py 中既有的 `test_uncited_source_warns`（整个函数删除），在同小节写入：

```python
def test_unresolved_source_warns(tmp_path):
    _write(tmp_path / "_md" / "cited.md", "甲乙")
    _write(tmp_path / "_md" / "draft.md", "草稿")
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/cited.md：「甲乙」〕\n")
    _, viol, warn, cov = scan_case(tmp_path)
    assert viol == []
    assert warn == ["[未处置] _md/draft.md"]
    assert cov == {"total": 2, "cited": 1, "skipped": 0, "unresolved": 1}


def test_registered_skip_silences_warning(tmp_path):
    _write(tmp_path / "_md" / "cited.md", "甲乙")
    _write(tmp_path / "_md" / "draft.md", "草稿")
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/cited.md：「甲乙」〕\n")
    _write(tmp_path / "wiki" / "log.md",
           "## [2026-07-12] skip | _md/draft.md\n- 原因：红线对比版\n")
    _, viol, warn, cov = scan_case(tmp_path)
    assert viol == [] and warn == []
    assert cov == {"total": 2, "cited": 1, "skipped": 1, "unresolved": 0}


def test_skip_without_reason_warns(tmp_path):
    _write(tmp_path / "_md" / "draft.md", "草稿")
    _write(tmp_path / "wiki" / "p.md", "占位页\n")
    _write(tmp_path / "wiki" / "log.md", "## [2026-07-12] skip | _md/draft.md\n")
    _, viol, warn, cov = scan_case(tmp_path)
    assert viol == []
    assert warn == ["[跳过无原因] _md/draft.md"]
    assert cov == {"total": 1, "cited": 0, "skipped": 1, "unresolved": 0}


def test_cited_wins_over_registration(tmp_path):
    # 已引用文件即使被登记跳过（且无原因）也归"已引用"，零警告——引用优先，登记冗余无害。
    _write(tmp_path / "_md" / "cited.md", "甲乙")
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/cited.md：「甲乙」〕\n")
    _write(tmp_path / "wiki" / "log.md", "## [2026-07-12] skip | _md/cited.md\n")
    _, viol, warn, cov = scan_case(tmp_path)
    assert viol == [] and warn == []
    assert cov == {"total": 1, "cited": 1, "skipped": 0, "unresolved": 0}


def test_stale_skip_entry_silently_ignored(tmp_path):
    # 登记路径在 _md/ 中不存在：不发警告、不进统计（真正漏网的文件仍会以未处置暴露）。
    _write(tmp_path / "_md" / "a.md", "甲乙")
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/a.md：「甲乙」〕\n")
    _write(tmp_path / "wiki" / "log.md",
           "## [2026-07-12] skip | _md/早已删除.md\n- 原因：x\n")
    _, viol, warn, cov = scan_case(tmp_path)
    assert viol == [] and warn == []
    assert cov == {"total": 1, "cited": 1, "skipped": 0, "unresolved": 0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest lawiki/skill/lawiki/lint/test_lint.py -k "unresolved or registered or skip_without or cited_wins or stale" -v`
Expected: FAIL with `ValueError: not enough values to unpack (expected 4, got 3)`

- [ ] **Step 3: Write the implementation**

lint.py 中整体替换 `_check_coverage`：

```python
def _check_coverage(root: Path, cited: set[str]) -> tuple[list[str], dict[str, int]]:
    """⑤ 覆盖率（警告，三态账本）：已引用 / 登记跳过（wiki/log.md skip 条目）/ 未处置。
    仅未处置发 `[未处置]`；登记但缺非空原因发 `[跳过无原因]`。引用优先于登记；
    登记路径不在 _md/ 中的静默忽略。返回 (警告, 统计)。"""
    stats = {"total": 0, "cited": 0, "skipped": 0, "unresolved": 0}
    warnings: list[str] = []
    md_dir = root / "_md"
    if not md_dir.is_dir():
        return warnings, stats
    cited_norm = {c.replace("\\", "/") for c in cited}
    skips = _load_skips(root)
    for f in sorted(md_dir.rglob("*.md")):
        rel = f.relative_to(root).as_posix()
        stats["total"] += 1
        if rel in cited_norm:
            stats["cited"] += 1
        elif rel in skips:
            stats["skipped"] += 1
            if not skips[rel]:
                warnings.append(f"[跳过无原因] {rel}")
        else:
            stats["unresolved"] += 1
            warnings.append(f"[未处置] {rel}")
    return warnings, stats
```

整体替换 `scan_case`：

```python
def scan_case(root: Path) -> tuple[int, list[str], list[str], dict[str, int]]:
    """返回 (锚点总数, 违规列表, 警告列表, 覆盖率统计)。纯函数，便于测试。"""
    wiki = root / "wiki"
    if not wiki.is_dir():
        raise FileNotFoundError(f"找不到 {wiki}")
    pages = _load_pages(wiki, root)
    names = _page_names(pages)
    violations, cited, total = _check_anchors(root, pages)
    violations += _check_deadlinks(pages, names)
    violations += _check_timeline_order(pages)
    violations += _check_closures(pages)
    warnings, coverage = _check_coverage(root, cited)
    return total, violations, warnings, coverage
```

`main()` 中 `total, violations, warnings = scan_case(root)` 改为：

```python
            total, violations, warnings, cov = scan_case(root)
```

（`cov` 的输出在 Task 3 加；本任务只保证解包不炸。）

同步全部既有解包点（机械改动，逐处执行）：

- test_lint.py：所有 `_, viol, _ = scan_case(` → `_, viol, *_ = scan_case(`；所有 `total, viol, _ = scan_case(` → `total, viol, *_ = scan_case(`（共约 20 处，含 `_anchor_case` 各用例与死链/时间线/勾稽小节）。
- `lawiki/skill/lawiki/tools/test_evidence.py` 两处 `total, violations, _ = scan_case(root)` → `total, violations, *_ = scan_case(root)`。
- `lawiki/skill/lawiki/tools/test_rag.py`：`total, violations, warnings = scan_case(root)` → `total, violations, warnings, _ = scan_case(root)`；两处 `total, violations, _ = scan_case(root)` → `total, violations, *_ = scan_case(root)`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest lawiki/skill/lawiki/lint/test_lint.py lawiki/skill/lawiki/tools/test_evidence.py lawiki/skill/lawiki/tools/test_rag.py -v`
Expected: 全部 PASS（tools 侧若有依赖缺失的 skip 属正常，不得有 FAIL/ERROR）

- [ ] **Step 5: Commit**

```bash
git add lawiki/skill/lawiki/lint/lint.py lawiki/skill/lawiki/lint/test_lint.py lawiki/skill/lawiki/tools/test_evidence.py lawiki/skill/lawiki/tools/test_rag.py
git commit -m "feat(lawiki-lint): three-state coverage (cited/skipped/unresolved) with ledger"
```

---

### Task 3: CLI 覆盖率汇总行 + 模块 docstring

**Files:**
- Modify: `lawiki/skill/lawiki/lint/lint.py`（`main` 的 check 分支、模块 docstring 第 9–11 行）
- Test: `lawiki/skill/lawiki/lint/test_lint.py`

**Interfaces:**
- Consumes: Task 2 的 `scan_case` 4 元组（第 4 元统计 dict，键 `total/cited/skipped/unresolved`）。
- Produces: `check` 子命令 stdout 恒含一行 `覆盖率：<N> 源文件 | 已引用 <a> | 登记跳过 <b> | 未处置 <c>`（列在"扫描锚点…"行之后、逐条违规/警告之前）。退出码语义不变。

- [ ] **Step 1: Write the failing test**

在 test_lint.py 覆盖率小节末尾追加：

```python
def test_check_cli_prints_coverage_summary(tmp_path, capsys):
    from lint import main
    _write(tmp_path / "_md" / "cited.md", "甲乙")
    _write(tmp_path / "_md" / "draft.md", "草稿")
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/cited.md：「甲乙」〕\n")
    assert main(["lint.py", "check", str(tmp_path)]) == 0  # 仅覆盖率警告不影响退出码
    out = capsys.readouterr().out
    assert "覆盖率：2 源文件 | 已引用 1 | 登记跳过 0 | 未处置 1" in out
    assert "[未处置] _md/draft.md" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest lawiki/skill/lawiki/lint/test_lint.py::test_check_cli_prints_coverage_summary -v`
Expected: FAIL（assert `覆盖率：…` not in out）

- [ ] **Step 3: Write the implementation**

`main()` check 分支中，`print(f"扫描锚点 {total} 个；违规 {len(violations)} 处；警告 {len(warnings)} 处。")` 之后插入：

```python
        print(f"覆盖率：{cov['total']} 源文件 | 已引用 {cov['cited']} | "
              f"登记跳过 {cov['skipped']} | 未处置 {cov['unresolved']}")
```

模块 docstring 第 9–11 行改为：

```python
check 五类：① 锚点存在（EXTRACTED 硬底线）② 死链 ③ 时间线顺序 ④ 勾稽闭合
（`> [!check] a+b==c`）⑤ 覆盖率（警告，三态：已引用/登记跳过/未处置，账本为
wiki/log.md 的 skip 条目）。只消格式噪声、数字与文字精确——
"数字写错/张冠李戴"必被抓、"换行差异"不误报。详见 SKILL.md / references/verification.md。
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest lawiki/skill/lawiki/lint/test_lint.py -v`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add lawiki/skill/lawiki/lint/lint.py lawiki/skill/lawiki/lint/test_lint.py
git commit -m "feat(lawiki-lint): always print coverage summary line in check output"
```

---

### Task 4: 文档同步（verification.md / page-formats.md / SKILL.md）

**Files:**
- Modify: `lawiki/skill/lawiki/references/verification.md`（第 17–18 行的警告小节）
- Modify: `lawiki/skill/lawiki/references/page-formats.md`（log.md 初始内容之后）
- Modify: `lawiki/skill/lawiki/SKILL.md`（第三步，第 60–74 行区域）

**Interfaces:**
- Consumes: Task 1–3 定下的条目格式、警告文案、汇总行（文档措辞必须与代码输出逐字一致）。
- Produces: 无代码接口；三份文档为 agent 的行为规范。

- [ ] **Step 1: 改 verification.md**

将：

```markdown
**警告（soft，不影响退出码，交人判断）：**
5. **覆盖率**：`_md/` 下从未被任何锚点引用的源文件——可能漏 ingest 的实质文件，也可能是有意跳过的草稿/红线版。
```

替换为：

```markdown
**警告（soft，不影响退出码）：**
5. **覆盖率（三态账本）**：`_md/` 下每个源文件必居其一——**已引用**（被任何锚点引用，优先级最高）、**登记跳过**（`wiki/log.md` 中有其 skip 条目，格式见 `page-formats.md`）、**未处置**（两者皆非 → `[未处置]` 警告）。skip 条目缺非空「原因」→ `[跳过无原因]` 警告；条目路径在 `_md/` 中不存在 → 静默忽略（路径写错时目标文件仍以未处置暴露）。`check` 恒输出汇总行 `覆盖率：N 源文件 | 已引用 a | 登记跳过 b | 未处置 c`。**不设"待补"登记态**——未处置警告本身就是 backlog 信号；**ingest 完成 = 0 违规 且 未处置 = 0**。
```

- [ ] **Step 2: 改 page-formats.md**

在 log.md 初始内容代码块之后（第 67 行后）插入：

````markdown
**skip 条目（覆盖率账本）**：决定不 ingest 某个源文件时，必须在 `log.md` 追加一条：

```markdown
## [YYYY-MM-DD] skip | _md/<相对案件根的 POSIX 路径>
- 原因：<一句非空理由，如"红线对比版，签署版已 ingest">
```

lint 据此把该文件计入「登记跳过」、不再发未处置警告；缺非空原因会得到 `[跳过无原因]` 警告。撤销跳过无需删条目——文件一旦被锚点引用即归「已引用」（引用优先于登记），append-only 不破。
````

- [ ] **Step 3: 改 SKILL.md 第三步**

在第三步编号列表（1–9）之后、"第 8、9 步细节见…"一句之前插入：

```markdown
**范围纪律**：默认目标就是上面的"每个 `.md`"。允许分批 / 先做案件主干，但必须：
- 每轮向用户申报范围——「本轮 ingest n / 登记跳过 m / 待补 k」；
- 决定跳过的文件（草稿/红线版等）在 `log.md` 写 skip 条目并附原因（格式见 `page-formats.md`），不许以"记入 backlog"等形式静默降格；
- **ingest 完成的定义 = lint 0 违规 且 覆盖率未处置 = 0**（每个源文件要么被引用、要么登记跳过）；待补清零前不得宣称 ingest 完成。
```

- [ ] **Step 4: 全量回归**

Run: `python -m pytest lawiki/skill/lawiki -v`
Expected: 全部 PASS（环境性 skip 允许，不得有 FAIL/ERROR）

- [ ] **Step 5: Commit**

```bash
git add lawiki/skill/lawiki/references/verification.md lawiki/skill/lawiki/references/page-formats.md lawiki/skill/lawiki/SKILL.md
git commit -m "docs(lawiki): three-state coverage ledger — skip entry format, scope discipline, done definition"
```
