# 设计：吸收 NexusRAG 优势 · 批次 1（rag-retriever 中文重排修复 + source 过滤）

日期：2026-07-05
状态：已批准设计，待写实现计划
范围：仅 `rag-retriever/`

## 背景与总路线图

对标 NexusRAG（LeDat98/NexusRAG）后，决定**选择性吸收其检索管线的优势，同时守住本项目"可溯源 / 可离线 / agent 工具形态"的内核**。不整体替换、不引入 Web UI。

吸收项拆成 3 批，每批独立 spec → plan → 实现：

- **批次 1（本 spec）**：A 修好中文重排 + B 按 source 过滤检索范围。落在 `rag-retriever` 内部，保守、向后兼容。
- 批次 2（未来 spec）：C 图/表 caption 化并纳入检索（makeitdown + rag）。caption 标 `INFERRED`，数字位仍走双 OCR/质检，绝不冒充原文。
- 批次 3（未来 spec）：D 在 wiki 之上建实体索引以支持多跳关系问答（lawiki）。借 KG 思路但**不做 LLM 自动抽取**，来源仍挂 wiki 逐字锚点、零幻觉。
- 已砍：E（Web UI 壳）。与"CLI / skill / MCP、零依赖、丢给 agent 就能跑"的形态冲突。

**贯穿约束**：一切以 CLI / JSON 契约 / MCP 暴露；默认本地可离线；不改变现有默认行为除非用户显式开启。

本 spec 只覆盖批次 1。成功标准取"保守增强"：改动小、零风险、完全向后兼容。

---

## Section A — 修好中文重排

### 问题
重排的**插槽设计本身是对的**：`RAG_RERANK` 默认 `none`，保持整条检索链零额外模型、离线（见 `rag_retriever/rerank.py` 头注）。唯一的缺陷是：当用户显式 `RAG_RERANK=local` 开启时，默认 cross-encoder 模型是 `Xenova/ms-marco-MiniLM-L-6-v2`——**纯英文 MS-MARCO 模型**，对中文法律术语基本无效。等于"给了开关，但开关后面接了错的模型"。

### 变更
仅改配置默认值 + 文档，不改控制流：

- `rag_retriever/config.py`：
  - `rerank_model` 默认 `Xenova/ms-marco-MiniLM-L-6-v2` → `BAAI/bge-reranker-v2-m3`（多语言，与本项目 `bge-m3` embedding 同族同分词）。
  - 对应的 `RAG_RERANK_MODEL` 环境变量默认同步更新。
  - `RAG_RERANK` 默认**保持 `none` 不变**——离线 / 零模型原则不破，无用户显式操作则行为零变化。
- `rag-retriever/README.md`：更新 `RAG_RERANK` 说明那一行，注明 `local` 重排现为多语言 / 中文可用。

### 硬依赖（必须在实现计划第一步先验证，不得假设）
fastembed 的 `TextCrossEncoder.list_supported_models()` 必须列出 `BAAI/bge-reranker-v2-m3`。
- 判断：较有把握其存在（fastembed ≥0.3.4 起支持），但**当前环境未装 fastembed，无法现场枚举**。
- 实现计划第一步：在装有 fastembed 的环境运行 `list_supported_models()` 确认。
- 回退：若不存在，改用 `jinaai/jina-reranker-v2-base-multilingual`（同为 fastembed 支持的多语言 cross-encoder）。
- **未经现场确认，不合并代码。**

### 测试
扩展 `tests/test_rerank.py`：
- 断言：`RAG_RERANK=none`（默认）→ `get_reranker()` 返回 `None`（不变，回归保护）。
- 断言：解析出的默认 `rerank_model` 是选定的多语言模型 id（`bge-reranker-v2-m3` 或回退 id）。
- 不下载模型、不做在线推理——只测配置解析，保持测试离线快速。

### 影响面
无破坏性。默认路径（`none`）行为完全不变；仅当用户主动 `RAG_RERANK=local` 时拿到一个对中文有效的模型。

---

## Section B — 按 source 限定检索范围

### 问题
多案卷场景需要"只在本案 / 本文件子树内检索"。当前 `search` 总是打全库索引，无法限定范围。

### 变更（新增、向后兼容）
- CLI：`rag-retriever search` 新增可选参数 `--filter <prefix>`。
- MCP：检索工具新增对应可选参数（与 CLI 语义一致）。
- 管线：`pipeline.search(query, k, source_prefix=None)`，向下透传到 `store.search(...)` 与 `store.search_text(...)`。
- 存储层：作为 LanceDB **prefilter** 应用于**向量路径和 FTS 路径两条**：`.where("source LIKE '<prefix>%'", prefilter=True)`（`source` 是顶层列，已有 `tbl.delete("source = ...")` 先例，可高效预过滤）。过滤发生在 top-k **之前**；RRF 融合与可选重排都在已过滤候选上进行。
- 不传 `--filter` → 行为与当前完全一致。

### 前缀值的转义
`prefix` 会拼进 LanceDB 的 SQL `where` 子句，**必须复用 `store.py` 现有的 `_escape()`** 处理单引号等，避免注入 / 语法破坏。`LIKE` 通配语义：实现时对用户传入 prefix 里的 `%`/`_` 是否转义要明确——本设计取"整体作为前缀，末尾自动补 `%`，用户 prefix 内的 `%`/`_` 按字面转义"，以免 `第X条` 之类含下划线的路径被误当通配。

### 为什么用 source 前缀
`source` 存的是文件路径且是真实列（预过滤高效）。因此 `--filter 案件A/` 天然限定到某案目录，`--filter 案件A/判决书` 按命名限定到文书类型。无需新 schema、不解析 frontmatter（`meta` 是 JSON 字符串列，非高效可过滤，保守版不碰）。

### 测试
- 过滤检索在**向量-only 和 hybrid 两条路径**都只返回匹配前缀的 chunk。
- 空 / 纯空白前缀：视为"未传 filter"，走全库检索（不是返回 `[]`）——避免误伤。
- 有前缀但无匹配：返回 `[]`。
- 不传 filter → 与当前行为逐位一致（回归保护）。
- 转义：含单引号 / `%` / `_` 的 prefix 不破坏查询、按字面匹配。

### 影响面
纯新增可选路径。默认（无 filter）零变化。

---

## 明确不在本批次范围内
- C（图/表 caption）、D（wiki 实体索引 / 多跳）——各自单独 spec。
- E（Web UI）——已砍。
- 重排默认开启、frontmatter 任意字段过滤、云端重排端点——属"激进版"，本保守批次不做。

## 交付定义
- `config.py` 两处默认值更新 + `README.md` 一行更新（A）。
- `--filter` 贯穿 CLI / MCP / pipeline / store，含转义（B）。
- `test_rerank.py` 扩展 + 新增 filter 测试（A、B）。
- fastembed 模型支持已现场确认（或已回退）。
- 全部现有测试仍通过。
