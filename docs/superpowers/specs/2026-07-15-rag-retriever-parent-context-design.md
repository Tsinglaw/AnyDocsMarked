# 设计：父块上下文检索（small-to-big）· rag-retriever

日期：2026-07-15
状态：已批准设计，待写实现计划
范围：仅 `rag-retriever/`

## 背景

对标一套通用全栈 RAG 工程（RAG-Pro）后，逐条比对其"值得借鉴点"与本项目现状，结论是：
混合检索（BM25+向量 RRF）、结构感知分块（表格原子、`第X条`软边界、面包屑）、可选中文
cross-encoder 重排（默认 `bge-reranker-v2-m3`）——**我们均已具备或已刻意做出更合理的取舍**。
唯一的真实空白是 **parent_child / small-to-big（父块上下文）检索**：用细粒度子块保召回精度，
返回时附上子块所在的更大父块补上下文。

本 spec 只覆盖这一项。成功标准取"保守增强"：改动小、无新依赖、无新模型、不联网、
默认行为**逐字节不变**、完全向后兼容，且不触碰本项目"可溯源 / 可离线 / agent 工具形态"内核。

**贯穿约束**：一切以 CLI / JSON 契约 / MCP 暴露；父块是纯文本，离线承诺不破；
RAG 仍是"召回 / 验证层"，逐字锚点仍由上游指向 `_md`——本特性只给上下文，不改变锚点来源，
与 lawiki 三类标注铁律零冲突。

---

## 核心思路

"进"求准、"出"求全：

- **索引**：把每个 section 先打包成**父块**（较大、块间不重叠），再把每个父块内部打包成
  **子块**（较小、带 overlap）。只有**子块**进向量索引与 BM25 索引。
- **检索**：召回 / RRF 融合 / 重排**全部在子块上做**（精度不打折）→ 最终 top-k 每条
  回查其父块，作为 `parent_text` 附上返回。

父块永不跨 section，故 `heading_path` 面包屑语义保持一致。

---

## Section 1 — 配置（`config.py`，新增，默认关）

`Config` 新增两个字段，均可 env 覆盖：

- `parent_context: bool = False`（`RAG_PARENT_CONTEXT`）——总开关。**关闭 = 现状**。
- `parent_tokens: int = 1600`（`RAG_PARENT_TOKENS`）——父块目标 token 数。
  子块继续用现有 `chunk_tokens`；父块打包用 overlap=0。

约束/校验：`parent_tokens` 应显著大于 `chunk_tokens`，否则父块≈子块、退化无意义。
实现时若 `parent_context=True` 且 `parent_tokens <= chunk_tokens`，取
`max(parent_tokens, chunk_tokens*2)` 兜底（不报错、写日志说明）。

---

## Section 2 — 分层分块（`chunk.py`，新增，单层路径不动）

- `Chunk` dataclass 增加可选字段 `parent_ord: int | None = None`（默认 None → 现有构造与
  测试不受影响；`frozen=True` 保持）。
- 新增函数（**不改** `chunk_document` 签名，其单层路径原样保留）：

  ```
  chunk_document_hierarchical(
      text, child_tokens, overlap, parent_tokens, strategy="structure"
  ) -> tuple[list[Chunk], list[str]]
  ```

  返回 `(children, parents)`：
  - `parents`：`list[str]`，每个是父块原文（不加面包屑前缀——面包屑通过子块 meta 的
    `heading_path` 已带出；父块存原始文本，供 agent 读上下文时干净）。
  - `children`：`list[Chunk]`，每个 `parent_ord` 指向 `parents` 的下标；`heading_path`
    与所属 section 一致。

  实现：对每个 `parse_sections` 出的 section，复用 `_split_structured_units(body, ...)`
  得到 units（**表格原子、legal marker 软边界在此层已保证**）；
  - 先用 `_pack_units(units, parent_tokens, overlap=0)` 得父块列表（在本 section 内、
    追加到全局 `parents`，记住本 section 父块的全局下标区间）；
  - 对每个父块再 `_split_structured_units(parent_text, child_tokens)` →
    `_pack_units(..., child_tokens, overlap)` 得子块，`parent_ord` 指向该父块全局下标。

  设计取舍：子块由**父块文本二次切分**得到，保证"子块 ⊂ 父块"（拼接可覆盖），
  便于测试断言与逐字核对；不采用"父/子各自独立切原文再按包含匹配"（易因边界不齐产生
  孤儿子块）。

- 单层 `chunk_document` / `chunk_text` 路径**一行不改**，`parent_context=False` 时全程不触及本函数。

---

## Section 3 — 存储（`store.py`，新增 sidecar，表 schema 不动）

- 新 sidecar `parents.json`：`{source: [父块原文, ...]}`（list 下标即 `parent_ord`）。
  与现有 `manifest.json` / `index_meta.json` 同一套路（`_read_json` + 写盘）。
- 新方法：
  - `set_parents(source: str, parents: list[str]) -> None`——写/覆盖某源的父块列表并落盘。
  - `get_parent(source: str, ord: int) -> str | None`——越界 / 源不存在 / 无 sidecar 均返回 `None`。
  - `delete_source(source)` **扩展**：同时移除该源的父块条目（与现有 manifest 清理并列）。
- 子块的 `parent_ord` 写进**现有 `meta` JSON 字段**（`add()` 已支持 per-row `metas`），
  **不新增表列**——legacy 索引、`text_tokens` 兼容逻辑零影响。

兼容：老索引没有 `parents.json` → `get_parent` 恒返回 `None`；`_manifest` 一样不含新键。

---

## Section 4 — 建库 / 检索（`pipeline.py`）

### 建库（`index_file`）
`cfg.parent_context` 为真时：
- 调 `chunk_document_hierarchical` 得 `(children, parents)`；
- 子块照常 `_compose`（面包屑前缀）→ 向量 + BM25 索引；每个子块 meta 合入
  `{"parent_ord": c.parent_ord}`（沿用现有 `metas` 机制）；
- `store.set_parents(source, parents)`。

为假时：**与现状逐字节一致**（现有 `chunk_document` 分支）。

注意 `delete_source` 先行（现有逻辑）已覆盖父块清理（见 Section 3），重建索引干净。

### 检索（`search`）
- 召回 / RRF / 重排**全不改**，仍作用于子块 `text`（精度保持）。
- 得到最终 top-k 后，遍历每条 hit：
  `parent_text = store.get_parent(hit["source"], hit["metadata"].get("parent_ord"))`，
  附为 `hit["parent_text"]`（`None` 当关闭 / legacy / 无 parent_ord）。
- **不做父块去重**（v1 保持 k 语义简单）；多个高分子块共享同一父块时 `parent_text` 会重复，
  留作可能的 follow-up（按父块 dedup 或合并上下文）。

---

## Section 5 — 返回形状 / 契约

hit dict **新增一个 key `parent_text: str | None`**；`text` 仍是子块原文，其余 key 全不动。

- 非破坏：lawiki 的 `tools/evidence.py`、MCP server、CLI JSON 输出等现有消费方
  **零改动照常工作**（多一个它们可忽略的 key）。
- 消费方**可选**用 `parent_text` 做更宽上下文，而逐字锚点仍指向 `_md`——
  RAG 是验证/召回层，不是锚点来源，**不与铁律冲突**。
- MCP / CLI：默认把 `parent_text` 一并透出（存在即带）。是否在 CLI 人读输出里显示父块，
  取"默认不显示、`--show-parent` 显示"，避免刷屏（实现细节，计划阶段定）。

---

## 硬依赖（实现计划须先验证，不得假设）

1. `_pack_units` / `_split_structured_units` 对"已经是父块的文本"再切分行为正确
   （父块本身可能含表格 / legal marker）——须有测试覆盖父块内二次切分。
2. LanceDB per-row `meta` 已能承载 `parent_ord` 并在 `search` / `search_text` 结果里
   经 `_parse_meta` 原样取回——**当前 `add()` 的 `metas` 路径已支持**，计划第一步用测试坐实。
3. 无新第三方依赖、无模型、无网络——父块纯文本，须由离线测试保证（不加载 embedder 也能测分块/存储）。

---

## 测试

**chunk（`tests/test_chunk.py` 扩展）**
- 分层：每个子块 `parent_ord` 有效且落在 `parents` 范围内；同一父块的子块拼接可覆盖父块文本
  （去 overlap 后无内容缺失）。
- 父块不跨 section（父块文本不含来自另一 heading_path 的内容）。
- 表格：跨父/子两层都保持原子（表头重复规则不被二次切分破坏）。
- **回归**：`parent_context` 关闭路径与改动前 `chunk_document` 输出逐字节一致。

**store（`tests/test_store.py` 扩展）**
- `set_parents` / `get_parent` 往返正确；越界 / 缺 sidecar / 源不存在 → `None`。
- `delete_source` 同时清掉父块条目。
- legacy 索引（无 `parents.json`）→ `get_parent` 恒 `None`，无异常。

**pipeline（`tests/test_pipeline.py` 扩展）**
- 开启父块：`search` 结果每条带正确 `parent_text`；子块 `text` 确 ⊂ 其 `parent_text`。
- 关闭 / legacy：`parent_text` 恒 `None`；其余输出与现状一致（回归）。
- 重排仍作用于子块（开启父块不改变 top-k 排序）。
- 离线：全程不加载 embedder 模型可跑（用 dummy / mock 向量）。

---

## 明确不在本次范围内
- 父块去重 / 上下文合并（v1 不做，留 follow-up）。
- bge-m3 学习式稀疏向量替代 BM25——**刻意不做**（我们零模型 BM25 对法律硬词更稳）。
- QA 成对分块、图形界面、模型分数置信度——与既有取舍冲突，不做。
- 默认开启父块——本批次默认关、opt-in。

## 交付定义
- `config.py`：新增 `parent_context` / `parent_tokens` 两字段 + env 解析 + 兜底校验。
- `chunk.py`：`Chunk.parent_ord` 字段 + `chunk_document_hierarchical`；单层路径不变。
- `store.py`：`parents.json` sidecar + `set_parents` / `get_parent` + `delete_source` 扩展。
- `pipeline.py`：`index_file` 分层建库分支 + `search` 附 `parent_text`。
- CLI / MCP：透出 `parent_text`（+ 可选 `--show-parent` 人读显示）。
- `README.md`：新增 `RAG_PARENT_CONTEXT` / `RAG_PARENT_TOKENS` 说明。
- 上述各测试新增 / 扩展；全部现有测试仍通过。
- 默认（`parent_context=False`）行为与当前逐字节一致。
