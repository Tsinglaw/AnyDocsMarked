# AnyDocsMarked 修订方案：结构感知分块 + 混合检索 + 双 OCR 互校

日期：2026-06-28
状态：已确认，待实现

## 背景与目标

对标 RAGFlow（开源 RAG 引擎）后，确认其解四类文件处理痛点靠的是"更强视觉解析 +
版面感知分块 + rerank"主线；其短板恰是"静默失败无自动告警"，而那正是我们 makeitdown
质检已覆盖的强项。对比后定位出我们真正的空白与该补的能力：

| 痛点 | 现状 | 本次修订 |
|---|---|---|
| ① OCR 失败 | 单引擎单路径（Paddle PP-StructureV3 / VL） | 工作流 C：旋转纠正 + 双 OCR 互校 |
| ② 无提示 | makeitdown 已有质检标记（基本已解决） | 工作流 C 顺手强化（互校分歧入质检） |
| ③ 信息碎片化 | `chunk.py` 裸文本 token 盲切 | 工作流 A：结构感知分块 |
| ④ LLM 注意力漂移 | `search()` 纯向量 top-k | 工作流 B：BM25+向量 RRF 混合检索 |

本 spec 为**总设计**，含三条相对独立的工作流，跨 `rag-retriever` 与 `makeitdown`
两个子项目。实现阶段可拆成三段独立推进，但共享本设计的全局一致性约束。

## 范围与默认行为（明确边界）

经确认的默认与边界：

- **A 结构感知分块**：默认开（`RAG_CHUNK_STRATEGY=structure`），可一键退回旧的纯
  token 切分（`token`）。
- **B 混合检索**：默认开（`RAG_HYBRID=1`），纯程序零模型；cross-encoder rerank
  默认**关**（`RAG_RERANK=none`），是唯一会引入模型的可选增强。
- **C 双 OCR 互校**：默认**关**（`--ocr-cross-check`，opt-in），法律高危件手动开；
  默认仍以 Paddle 为准、MinerU 作校验方。
- **不写死文书解析器**：A 用"markdown 结构 + 法律标记兜底"的通用结构信号，不为
  判决书/合同/法条各写一个脆弱的专用 parser。

非目标（明确不做）：重平台化中间件（ES/MinIO/Redis/MySQL）；页级 salvage；
OCR 自动纠错改写正文；把生成/回答塞进 retriever（坚持"前半段 RAG"形态）。

---

## 工作流 A — 结构感知分块（rag-retriever `chunk.py`）

### 目标
治碎片化：按文档结构切，每块携带"它出自哪一节"的元数据，避免把条款、表格、
"本院认为"从中切断。

### 两层切分策略

不硬编码专用解析器（脆弱）；用结构信号，对法律文书天然有效。

1. **Markdown 结构层（主）**：`_md` 来自 makeitdown，原生文档本就带 `#` 标题，
   OCR 件可经 `--structure-headings` 重建标题。
   - 先按标题层级切成 section，每 section 记录**标题面包屑**（如
     `民事判决书 > 本院认为`）。
   - section 内再跑现有 token 打包逻辑（保留 overlap、`_hard_split` 兜底）。
   - **表格不切开**：连续 `|` 行构成的 markdown 表格块整块保留；超预算时按行切，
     但**每片重复表头行**，使每块表格自洽可检索。

2. **法律标记兜底层**：扫描件 OCR 常是扁平无标题文本。当一段内**无 `#` 标题**时，
   识别法律枚举/段落标记作**软边界**，避免从条款中间切断：
   - `第X条` / `第X款` / `第X项`
   - 行首 `一、二、三、…`（含 `（一）（二）`）
   - 关键段落词：`本院认为`、`审理终结`、`如不服本判决`、`事实和理由` 等
   - 软边界仅用于"优先在此处断"，不改变 token 预算上限。

### 接口与数据变化

- `chunk_text()` 返回值由 `list[str]` 改为 `list[Chunk]`：
  ```python
  @dataclass(frozen=True)
  class Chunk:
      text: str            # 用于存储/返回的正文
      heading_path: str    # 面包屑，如 "民事判决书 > 本院认为"；无则空串
  ```
- **嵌入增强**：嵌入时把 `heading_path` 前置进被嵌入文本（让向量带上下文），
  存储的 `text` 也含该前缀，命中即可告诉 agent 出处。
- 涟漪改动：
  - `pipeline.index_file`：消费 `Chunk` 列表；把 `heading_path` 并入传给
    `store.add` 的 per-chunk 元数据。
  - `store.add`：每行 `meta` JSON 增加 `heading_path` 字段（**schema 不变**，
    沿用现有 `meta` 列，零迁移；老库无此字段读出为空）。
  - `store.search`：返回的 `metadata` 自然带出 `heading_path`。
- 配置：`RAG_CHUNK_STRATEGY=structure|token`（默认 structure）。

### 设计决定
- 面包屑前置进**被嵌入文本**而非仅存元数据——上下文进入向量，召回更准。
- 表格超预算重复表头，不丢列含义；其余文本块沿用现有 overlap 不变。
- 软边界是"优先断点"，不是硬性 section，避免在无明显结构的材料上过度切碎。

---

## 工作流 B — 混合检索 BM25+向量 RRF（rag-retriever `store.py` / `pipeline.py`）

### 目标
治注意力漂移：给现有向量检索并联一条关键词（BM25）通道，再用 RRF 合并名次，
"少喂、喂准"。**默认零模型、纯程序、可离线。**

### store.py
- 索引时对 `text` 列建 LanceDB **FTS 全文索引**；`add()` 后确保索引存在/更新。
- 新增 `search_text(query, k) -> list[dict]`（BM25），与现有
  `search(vector, k)`（向量）并列，返回结构一致。
- **中文分词风险（已识别，实现期必须验证）**：LanceDB FTS 默认分词对中文不友好。
  设计为**可配置分词器**：优先 `jieba`（若 LanceDB 版本支持），不可用则退回 ngram
  分词。spec 实现期第一步即写一个最小验证：对中文短语建索引并断言可召回；不通过
  则锁定 ngram 方案。
- 向后兼容：老索引无 FTS 索引时，`search_text` 返回空，`pipeline` 自动退回纯向量。

### pipeline.py
- `search()`：向量与 BM25 各召回 top-N（默认各 50）→ **RRF 融合**
  （每文档 `score = Σ 1/(rrf_k + rank)`，`rrf_k=60`）→ 返回 top-k。
  纯算术，作用于已召回的候选集，耗时可忽略。
- **可选 cross-encoder rerank**（默认关，模型只在此出现）：
  `RAG_RERANK=none|local|cloud`，与 embedding 后端同构——
  `local`=fastembed reranker，`cloud`=SiliconFlow bge-reranker。
  开启时对 RRF 后的候选重排取 top-k。

### config.py
- `RAG_HYBRID`（默认 `1`）：关则退回纯向量。
- `RAG_RRF_K`（默认 60）、`RAG_HYBRID_CANDIDATES`（默认 50，每路召回数）。
- `RAG_RERANK`（默认 `none`）；rerank 模型相关复用现有 openai/local 配置。

### 设计决定
- 默认路径不碰任何模型——治法律近义词漂移的主力是 BM25 关键词通道，不是
  cross-encoder；后者仅锦上添花、按需开启。
- RRF 而非加权求和：无需调权重、对两路分数量纲不敏感，鲁棒且可解释。

---

## 工作流 C — 旋转纠正 → 双 OCR 互校（makeitdown）

### 目标
治 OCR 失败（①）并强化无提示（②）。两个独立技术栈引擎交叉验证，分歧即暴露。
引擎选 **Paddle + MinerU**：均开源可本地部署、国内均有 API。

### 新增 MinerU 后端
- 新模块 `src/makeitdown/ocr_mineru.py`：`MinerULocal` / `MinerUCloud`，
  与 `LocalOCR`/`CloudOCR` **同接口**（`is_available()`、`convert(path) ->
  ConversionResult`、`engine_label`）。`ConversionResult` 结构不变
  （`text/engine/pages/assets/confidences`）。
- 重依赖（mineru 包）惰性导入，遵循现有"首次转换才加载"惯例。

### 旋转纠正阶段（一次性、便宜）
- 用单引擎（Paddle，能给 `rec_scores`）对 0/90/180/270 快速试，挑 OCR 置信度
  最高角 → 得到"摆正的页"。≈ 不显著增加成本（仅定角用一个引擎）。
- 两个引擎随后都在**同一张正页**上识别——这是互校有意义的前提（排除方向这一
  已知变量，剩余分歧才反映识别错误）。

### 互校阶段（`--ocr-cross-check`，默认关）
- Paddle 与 MinerU 在正页上各跑一次。
- **归一化后逐行 diff**：复用一套与 lawiki lint 同源的归一化（空白/全半角/标点/
  千分位逗号/markdown），但 makeitdown **自带一份**以保持子项目解耦。
- **法律高危聚焦**：重点比对**数字/金额/日期 token**——OCR 改一位最致命处；
  数字位不一致单独计为高危分歧。
- **主输出**：默认以 Paddle 为准（保持现行默认），MinerU 作校验方；
  分歧**不改正文，只标记**。
- 引擎标签：`engine` 记为 `local:pp-structurev3 × mineru`。

### 接入现有质检
- `quality.assess()` 新增一条理由（由互校结果驱动），例如
  `12 处双OCR分歧，含 3 处金额/日期位不一致`；写进 `report.json.warnings` +
  `.md` frontmatter `quality: suspect`（沿用现有落地机制）。
- `QualityThresholds` 增 `cross_check_disagreement_ratio`（默认保守，偏少误报）。

### CLI
- `--ocr-cross-check`（默认关）：开启双 OCR 互校。
- `--cross-check-engine {mineru}`（默认 mineru）：校验方引擎，预留扩展。
- `--ocr-rotate`（默认随互校自动）：旋转纠正开关。

---

## 横切关注点

### 错误处理
- **C 绝不因互校失败丢转换结果**：MinerU 不可用 / 校验方异常 / diff 出错 →
  降级为单引擎正常产出 + 记一条 warning，沿用"单文件错不中断整批"铁律。
- **B 退回安全**：FTS 缺失/分词异常 → 退回纯向量，检索不中断。
- **A 退回安全**：结构解析异常 → 退回 token 策略，分块不中断。

### 测试（按 TDD，先写测试）
- A `test_chunk.py`：判决书/合同/带表格的构造 md → 断言切块边界、`heading_path`、
  表格不被切断且超大表重复表头；扁平无标题文本走法律标记软边界；`token` 策略
  回退等价旧行为。
- B `test_store.py` / `test_pipeline.py`：RRF 名次正确；中文短语经 FTS 可召回
  （分词验证）；老库无 FTS 自动退回纯向量；`RAG_HYBRID=0` 关闭生效；
  rerank=none 时不加载任何模型。
- C `test_ocr_mineru.py` / `test_quality.py` / `test_pipeline.py`：两段人造
  分歧 OCR 文本 → diff 命中、数字/日期位分歧单独计高危、归一化不误报换行差异；
  MinerU 不可用 → 降级单引擎 + warning、仍产出；互校理由进 frontmatter。
- 全部纯本地、可离线跑。

### 向后兼容
- A 默认开但可退 token；B 默认开但可关、老索引自动退回；C 默认关（opt-in）。
  老索引、老转换流程均不破坏。

## 受影响文件

**rag-retriever**
- 修改：`rag_retriever/chunk.py`（结构感知 + `Chunk` 数据类）、
  `rag_retriever/pipeline.py`（消费 Chunk、RRF 融合、可选 rerank）、
  `rag_retriever/store.py`（FTS 索引、`search_text`、meta 带 heading_path）、
  `rag_retriever/config.py`（A/B 新配置项）。
- 测试：`tests/test_chunk.py`、`tests/test_store.py`、`tests/test_pipeline.py`、
  `tests/test_config.py`。
- 文档：`rag-retriever/README.md`（混合检索、结构分块、rerank 开关）。

**makeitdown**
- 新增：`src/makeitdown/ocr_mineru.py`、`src/makeitdown/ocr_crosscheck.py`
  （旋转纠正 + 归一化 diff，纯函数化便于测试）、`tests/test_ocr_mineru.py`、
  `tests/test_ocr_crosscheck.py`。
- 修改：`src/makeitdown/convert_ocr.py`（接入校验方与互校流程）、
  `src/makeitdown/quality.py`（互校分歧规则 + 阈值）、
  `src/makeitdown/pipeline.py`（互校结果接入 report/frontmatter）、
  `src/makeitdown/cli.py`（`--ocr-cross-check` 等开关）。
- 文档：`makeitdown/README.md`（双 OCR 互校、MinerU 安装）。

**仓库根**
- 本 spec：`docs/superpowers/specs/2026-06-28-rag-ocr-revision-design.md`。
- `README.md`（技术特点处提一句混合检索 + 双 OCR 互校）。
