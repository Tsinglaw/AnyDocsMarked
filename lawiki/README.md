# lawiki

把一个法律案件的资料整合成**可控、可溯源**的案件 wiki。成果是一个**可被不同 agent 使用的 skill**（`skill/lawiki/SKILL.md`），不是 CLI 工具。

## 流水线

```
原始资料/ ──makeitdown──▶ _md/ ──lawiki skill──▶ wiki/
```

- `原始资料/`：你丢入的法律文件（不可变）。
- `_md/`：makeitdown 转换出的 markdown（按工作流只读；原件与正文 SHA-256 由 lint 校验，带 frontmatter 与 report.json）。
- `wiki/`：LLM 按 skill 构建维护的案件 wiki：案件主体 / 法律关系 / 法律事实 / 时间线 + index/log。产出**以 Obsidian 为基准**（wikilink `[[]]` 驱动图谱反链、callout 标注分析/冲突、frontmatter 即 properties），在非 Obsidian 查看器中亦为合法 markdown。

## 上游依赖

[makeitdown](https://github.com/Tsinglaw/AnyDocsMarked/tree/main/makeitdown)（独立工具）：把各式文件转成 LLM 可读的 md。lawiki 通过其稳定输出契约（frontmatter 的 `source` / `quality: suspect` 等）对接。

## 首次使用（自动配环境）

第一次用时，agent 会照 `skill/lawiki/references/setup.md` 引导你配好环境：检测 Python / makeitdown → **让你选 OCR 方式（本地 vs 云端，附优缺点对比）** → 云端给出[百度 AI Studio](https://aistudio.baidu.com/paddleocr)申请 token 的网址 → 安装（并明确告诉你"正在安装环境…"）→ 最后告诉你用哪些话激活它。lawiki 自带的校验工具（`lint/`）零第三方依赖，只要有 Python 即可。

## 结构（一个自包含 skill）

```
skill/lawiki/
  SKILL.md          # 短主干：触发 / 流水线 / 铁律 / 锚点 / ingest 步骤 / references 指针
  references/       # 按需加载：setup / page-formats / obsidian / verification
  lint/             # 自带校验代码（零第三方依赖）
```

## 在不同 agent 下挂载

- **Claude Code / Copilot**：把整个 `skill/lawiki/`（含 `references/`、`lint/`）放入对应的 skills 目录，agent 会按 `description` 自动触发。
- **Codex 等**：把 `skill/lawiki/SKILL.md` 内容作为系统指令喂给 agent，并带上 `references/`、`lint/`；或放入案件目录作 `AGENTS.md`。

## 用法

把文件放进某案件的 `原始资料/`，让 agent "处理 / 整理 / 建库"。agent 会调 makeitdown 转换、再按 skill 把来源归档进 `wiki/`，无需手动跑中间步骤。

## 可控性（为何可信）

skill 内嵌不可违反的铁律：写进 wiki 的每句话必须归入 **EXTRACTED（原文直取）/ INFERRED（推断）/ AMBIGUOUS（存疑）** 三类之一并各自打标，无法归类者不写。一条硬底线：凡作为事实陈述的（EXTRACTED）必须挂固定格式逐字来源锚点 `〔来源: _md/…：「逐字原文」〕`，可回溯、可机检；推断须标为分析，存疑（可疑来源/冲突/无法确证）须显式标注。

## 校验（lint，确定性闸门）

`skill/lawiki/lint/lint.py` 把铁律从"自觉"变成"可机检"。`python <SKILL_DIR>/lint/lint.py check <案件根目录>`：

- **违规（退出码非 0，必修）**：① 锚点逐字存在且解析后仍位于本案 `_md/` ② 原件/正文 SHA-256 未变化 ③ 死链 ④ 时间线顺序 ⑤ 勾稽闭合。
- **完整性警告（同样返回非 0，未清零不得宣称完成）**：`_md/` 下未引用且未登记跳过的文件，或跳过却没有理由的文件。

`lint answer` 还会逐行拒绝未锚定事实；只有明确的 `> [!note] 分析` callout 可豁免。校验只消除格式噪声，数字与文字保持精确。

**蕴含校验（每次 ingest 收尾自动跑）**：`python <SKILL_DIR>/lint/lint.py extract <案件根目录>` 把每条 `(断言, 引文, 来源, 源文上下文)` 拆出（每锚点配其紧前子断言），交一个**换实例的 LLM 判官**三分判"引文是否支持断言"（支持/不支持/信息不足），抓"引文真但断言被脑补/拔高/歪曲"。判官只判不改；ingest agent 据判官**有界修复**（只许把断言改忠实，绝不编造），三轮仍判不过的**显著上报用户**。协议见 `skill/lawiki/references/verification.md`。法律定性正确性归人。

## 设计

`docs/superpowers/specs/2026-06-16-lawiki-design.md`
