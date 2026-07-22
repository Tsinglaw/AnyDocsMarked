---
name: lawiki
description: Use when building, maintaining, OR answering questions about a Chinese legal-case knowledge wiki from a folder of case materials — drives makeitdown to convert raw documents to markdown, files them into a controlled, source-anchored case wiki (案件主体/法律关系/法律事实/时间线), and answers case questions by cross-checking the wiki against RAG evidence over the source files. Triggers on 整理案件资料、把案件资料建成 wiki、建案件库、处理这个案子、ingest case files, build a case wiki; and on 问本案、关于这个案子、本案里 X 是什么/多少/是谁, 以及就本案出简报/汇报/分析/梳理 (ask about, or produce any briefing / report / analysis over, this case).
---

# lawiki — 法律案件 wiki 构建

把一个案件的原始资料整合成**可控、可溯源**的 wiki。你（agent）负责全部归档与维护。法律工作不接受模糊和混乱——下面的**铁律（三类标注 + 逐字锚点硬底线）不可违反**。

细节按需读本 skill 的 `references/`（`setup.md` 首次配环境、`page-formats.md` 页面格式+Obsidian 约定、`verification.md` 校验、`rag.md` RAG 索引检索、`qa.md` 案件问答协议——**answer 模式必读、非按需**，见下「何时用」）；build 相关的其余不必一次全装进注意力。工具在 `tools/`：`init_case.py`（确定性建案脚手架 + 闭世界锚点）、`evidence.py`（问答取证一条命令：RAG+精确词+outline）、`rag.py`（RAG 包装）、`outline.py`（`_md` 标题树导航，零依赖、对抗遗漏、亦作无 RAG 降级）、`reconcile.py`（源级对账，补覆盖率账本看不见 `_md` 之外的盲点）。`<SKILL_DIR>` 指本 skill 实际所在目录。

## 何时用 / 怎么激活（两种模式，动手前先判哪种）

本 skill 覆盖两种任务，**入手先判是 build 还是 answer**——切错模式是最常见的失守：构建做完切到问答，最易忘记重走取证接地，直接啃 `_md` 写结论（曾因此把未验证的断言当事实交付）。

- **build（构建）**：用户把文件放进 `原始资料/`，说「整理案件资料」「把案件资料建成 wiki」「建案件库」「处理这个案子」「build a case wiki」等 → 走下面的流水线。
- **answer（问答）**：用户就本案内容要**任何产出**——「问本案」「关于这个案子」「本案里 X 是什么/多少/是谁」，以及**简报 / 汇报 / 分析 / 梳理**等 → 走下方「案件问答」协议。**answer 模式：先读 `references/qa.md` 全文**（不能只凭本 SKILL.md 摘要作答）→ 跑 `evidence.py` 多路取证 → 过 `lint answer` 交付闸门。**每次进入 answer（哪怕紧接在 build 之后）都要重走这套，别把上半程的状态当全程有效。**

## 流水线

```
原始资料/ ──makeitdown──▶ _md/ ──┬── ingest ──▶ wiki/   （你综合归档）
                                  └── index ──▶ .rag/   （确定性脚本，可选）
```

三层结构（前两层不可变，你只写第三层）：
- `原始资料/`：用户丢入的原件，真相之源，**永不修改**。
- `_md/`：makeitdown 转换产物，来源层，**永不修改**。
- `wiki/`：你拥有并维护的案件 wiki。
- `.rag/`：RAG 向量库，从 `_md/` 派生、与 `wiki/` 平级，隐藏、可重建、可选（见 `rag.md`）。

## 第〇步：首次配环境

第一次在某机器上用、或缺 Python/makeitdown 时，照 **`references/setup.md`** 走：检测环境 → 让用户选 OCR 方式（本地/云端，附优缺点对比与 token 申请网址）→ 安装（并明确告诉用户"正在安装环境…"）→ 告知激活语。环境就绪可跳过本步。

## 第一步：确保案件结构存在

**跑确定性建案脚手架**（别手写——手写会被跳过，问题报告 §10 里 `AGENTS.md`/`CLAUDE.md` 就一直没被建）：

```
python <SKILL_DIR>/tools/init_case.py <案件根目录>
```

它幂等地建固定结构（`wiki/` + `案件主体/法律关系/法律事实/时间线` 四子目录 + `index.md` + `log.md` + `原始资料/`），并**盖章写入闭世界锚点 `AGENTS.md` + 同内容 `CLAUDE.md`**（自描述 + "只用本案数据、答前必检索"约束——harness 自动加载本文件、**即便 skill 未触发也在场**，是唯一不依赖触发的护栏）。已存在的文件不覆盖；被掏空需复原时加 `--force`。

**这两个锚点由闸门守**：`lint check` 把案件根缺 `AGENTS.md`/`CLAUDE.md`（或被掏空）判为**硬违规**，故它们并入"ingest 完成 = lint 0 违规"。模板内容见 `references/page-formats.md`。

## 第二步：转换（调 makeitdown）

在案件目录执行 `makeitdown 原始资料 -o _md`。新产物 frontmatter 带 `provenance_version: 1`、`source_sha256` 与 `content_sha256`，后续 lint 会 fail-closed 核对原件和转换正文是否变化；旧产物可读但必须重转后才能宣称 ingest 完成。转换后读 `_md/report.json`，留意 `warned`/`failed`/`skipped`。失败或跳过的文件**不要凭空补内容**，按缺失处理并告知用户。

转换后**跑源级对账（确定性收尾）**：`python <SKILL_DIR>/tools/reconcile.py <案件根目录>`。它把 `原始资料/` 与 `_md/report.json` 对齐，把"转换失败 / 跳过、从未进入 `_md/`"的源文件（**lint 覆盖率账本看不见的盲点**，如无 LibreOffice 的 `.doc`）逼出来。退出码非 0 = 有**未处置源级遗漏**：要么装好外部转换器补转，要么在 `wiki/log.md` 登记 skip（路径写 `原始资料/<相对路径>` + 非空原因，格式同 `_md` 级 skip，见 `page-formats.md`）并**显式告知用户**；清零方可继续。

**长任务模式（批量含扫描件 / 走云端 OCR）**：文件多于 ~20 个或含大量扫描件时，转换可能几十分钟。makeitdown 会逐文件把进度打到 stderr（`[k/N] ✓/⚠/✗ 路径`）。**后台运行并落日志**，期间可 tail 日志按进度向用户播报；**以进程退出 + `_md/report.json` 出现为完成信号**，完成后读 report.json 汇总。中断或掉线后加 `--skip-existing` 重跑；它会同时核对 mtime 与原件 SHA-256，不会因复制时间戳而静默跳过已变化原件。

## 第二步半：索引 `_md/` → `.rag/`（确定性，可选可降级）

装了 rag-retriever 就建索引，支撑后续交叉验证问答：

```
python <SKILL_DIR>/tools/rag.py index <案件根目录>
```

确定性、跑命令即可，无需判断。新增来源后重跑同一条命令增量刷新。没装 / 装不上**不阻塞核心**——问答会退化「仅 wiki」。细节与降级见 `rag.md`，首次安装见 `setup.md`。

## 第三步：ingest（逐个来源归档进 wiki）

对 `_md/` 下每个 `.md`：

1. 读其正文与 frontmatter。
2. 若含 `quality: suspect` → 该来源所有引用在锚点后追加「（未核验）」。
3. 抽主体信息 → `wiki/案件主体/<主体名>.md`。
4. 提炼有法律意义的事实点及证据 → `wiki/法律事实/<事实名>.md`。
5. 判定/更新法律关系 → `wiki/法律关系/<关系名>.md`。
6. 把事实按时序并入 `wiki/时间线/总览.md`。
7. 维护交叉引用，更新 `index.md`，向 `log.md` 追加 `## [YYYY-MM-DD] ingest | <来源文件名>`。
8. **确定性校验（lint）**：`python <SKILL_DIR>/lint/lint.py check <案件根目录>`，修到**退出码 0**；违规、未处置与无理由 skip 都会返回非零。
9. **蕴含校验（换实例判官）**：抽取 claim↔引文 → 派全新子代理三分判 → 有界修复 ≤3 轮 → 仍判不过的显著上报用户。

**范围纪律**：默认目标就是上面的"每个 `.md`"。允许分批 / 先做案件主干，但必须：
- 每轮向用户申报范围——「本轮 ingest n / 登记跳过 m / 待补 k」；
- 决定跳过的文件（草稿/红线版等）在 `log.md` 写 skip 条目并附原因（格式见 `page-formats.md`），不许以"记入 backlog"等形式静默降格；
- **ingest 完成的定义 = lint 0 违规 且 覆盖率未处置 = 0 且 源级对账未处置 = 0**（每个 `_md` 源文件要么被引用、要么登记跳过；每个转换失败/跳过、未进 `_md` 的源文件——lint 看不见——也要么补转、要么登记跳过）；三者未清零前不得宣称 ingest 完成。

第 8、9 步细节见 **`references/verification.md`**；页面格式与 Obsidian 约定见 **`references/page-formats.md`**。

## 案件问答（交叉验证）

案件建好后，用户**就内容提问或要产出**——「问本案…」「关于这个案子…」「本案里 X 是什么/多少/是谁」，以及就本案出**简报 / 汇报 / 分析 / 梳理**等（与「整理/建库/ingest」这类**构建**触发词区分）。**这些都算 answer 模式，一律先读 `qa.md` 全文再动手**——写"报告/汇报"最容易滑成通用文档写作、跳过本协议的取证接地。

**闭世界铁规（先检索后答）**：本案事实的唯一来源 = 本案 `原始资料/_md/wiki/.rag`；**答前必先检索，严禁凭记忆/通用法律知识直接回答**；查不到就明说「未在本案材料中找到」，绝不脑补；每个事实挂逐字锚点。通用分析须标 `> [!note] 分析（非本案证据）`。

流程：**取证**（wiki 路自由导航 + 一条命令跑齐其余三路：`python <SKILL_DIR>/tools/evidence.py <案件根> "<问题>" --terms "<精确词>" -k 8`，RAG/精确词 grep/outline）→ **四情形分流**：一致则答、wiki 沉默用原文答、不一致能定因则以原文为准并指出 wiki 待修处、查不出因则两套答案 + 各自锚点并列、附标注为分析的倾向、交用户裁决 → **交付闸门（铁规）**：回答先写草稿过 `python <SKILL_DIR>/lint/lint.py answer <案件根> <草稿.md>`，**0 违规才发**。完整协议见 **`references/qa.md`**。RAG 不可用时证据包自动降级（grep + outline 仍在）并告知用户。

## 铁律：三类标注 + 一条硬底线（不可违反）

写进 wiki 的每一句话，必须先归入且仅归入三类之一，并按该类规矩处理。

**硬底线**：凡作为「事实」陈述的（EXTRACTED），必须挂逐字来源锚点 `〔来源: _md/…：「逐字原文」〕`；挂不上锚点的，不许当事实写出。

1. **EXTRACTED（原文直取）**：源文档白纸黑字写的。必挂逐字锚点；法律要害（日期/金额/当事人名/条款原文）逐字照录、不转述。**唯一能当事实直述的一类。**
2. **INFERRED（推断）**：你的分析/推论/归纳，非任一来源明文。必须显式标注（`> [!note] 分析`），与事实物理隔离，并写明推断依据。**绝不伪装成事实。**
3. **AMBIGUOUS（存疑）**：拿不准的——来源可疑（`quality: suspect` / OCR 乱码）、多源冲突、或数值无法确证。必须显式打标：可疑引用后缀「（未核验）」；冲突用 `> [!warning] ⚠ 冲突` callout 并列各方与锚点。**绝不静默取舍、绝不当既定事实。**

> 写每句前先自问：这是 EXTRACTED / INFERRED / AMBIGUOUS？三者必居其一、各有其标。**无法归类（无来源、无依据）→ 不写。**

## 引用锚点（机器可校验）

固定格式，**不使用页码**——指明来自哪份文件的哪一部分，带逐字上下文片段：

```
〔来源: _md/<相对路径>：「<逐字上下文片段>」〕
```

- 片段取自源 md 原文、逐字；可含 `…` 表略去（lint 按 `…` 分段、按序匹配）。
- 案件主体每条属性、每个法律事实、每条时间线，都必须挂锚点。
- **一条断言 ← 一条能完整支持它的引文**：别把多个事实塞一条锚点下。
- **别引 OCR 打乱的表头**：扫描件表头常被 OCR 打散、乱序，拼不出连续引文；改引该表里干净、连续的数据行或"总计"行。

## 跨 agent

- **Claude Code / Copilot**：本 `SKILL.md` 按 `description` 自动触发。
- **Codex 等**：把本文件内容作为系统指令喂给 agent，或放入案件目录作 `AGENTS.md`；`references/` 与 `lint/` 随本 skill 一起带上。
