# 设计：向量通道相关性下限（min-score cutoff）· rag-retriever

日期：2026-07-15
状态：已批准设计，待写实现计划
范围：仅 `rag-retriever/`

## 背景

吸收前沿"带归属问答"工作（RAGentA, arXiv 2506.16988；MIKA Dual-Channel, RS rs-9142139）的一条经验：
**不要把"勉强沾边"的召回块塞给作答方**——弱相关证据正是诱发"事后合理化 / 脑补引用"
（citation post-rationalization，见 arXiv 2412.18004）的温床。RAGentA 用一个**每 query 自适应的
相关性阈值**丢掉弱相关文档；我们要吸收的是这个"设相关性下限、宁缺毋滥"的思路，而非它的
四 agent 重型编排（其论文自承开销大、收益递减，与本项目"锁两端、中段单 agent 自由"取舍冲突）。

本 spec 只做这一件事，且刻意做成最小、可选、默认不改变行为。成功标准取"保守增强"：
默认 `RAG_MIN_SCORE=0` 时行为与当前**逐字节一致**，完全向后兼容。

**贯穿约束**：CLI / env / MCP 契约不破；本地可离线、无新依赖、无新模型；
"宁可查不出，也不编造"——下限之下宁可返回更少甚至零条，也不拿弱相关块凑数。

---

## 核心决策：阈值加在哪个分数上

hit 的 `score` 含义随检索路径而变，不能用单一绝对阈值一刀切：

| 路径 | `score` 是 | 量纲 |
|---|---|---|
| 纯向量 | 余弦相似度（`1 - cosine_distance`） | 有界 ~[-1,1]，可解释 |
| 混合（RRF） | 倒数排名和 | 极小(~0.01–0.03)，是排名不是相关性幅度 |
| 重排开启 | cross-encoder logit | 无界、带符号、模型相关 |

因此**只在唯一有界、可解释、每条 hit 必然算出的相关性数字——余弦相似度——上设下限**，
且只作用于**向量通道**。这样：
- 绕开 RRF / reranker 分数量纲不可比的问题；
- **保留 BM25/关键词通道作为硬约束**：法律硬词（金额、法条号）即便向量相似度低，
  只要关键词命中仍应召回——所以下限**不过滤 BM25 命中**，只剥离"语义离题的向量噪声"。

---

## Section 1 — 配置（`config.py`，新增，默认关）

- 新增 `Config` 字段 `min_score: float = 0.0`（env `RAG_MIN_SCORE`）。
- 新增 `_env_float(name, default)` 辅助（与现有 `_env_int` / `_env_bool` 同构，
  解析失败回退默认值）。
- 语义：**仅当 `min_score > 0.0` 时启用过滤**；`0.0`（默认）= 关闭 = 行为零变化。
  （余弦相似度理论上可为负，用"> 0 才启用"保证默认不误删；需要丢负相关的用户设 `0.01` 等即可。）
- 有意义取值范围写进注释/README：`(0, 1]` 的余弦相似度；不做强制 clamp（越界只是过滤更多/更少，不崩）。

---

## Section 2 — 检索过滤（`pipeline.py`）

新增模块级小辅助：

```
def _above_floor(hits: list[dict], floor: float) -> list[dict]:
    """Drop hits whose (cosine) score is below floor. No-op when floor <= 0."""
    if floor <= 0.0:
        return hits
    return [h for h in hits if h["score"] >= floor]
```

在 `search` 两个应用点接入（其余逻辑不动）：

- **纯向量路径**（`not self.cfg.hybrid`）：对 `store.search(...)` 返回的 hits 直接过滤
  （这些 score 就是余弦相似度）。
- **混合路径**：对 `vector_hits` 过滤**后**再进 RRF / rerank；`text_hits`（BM25）**不动**。
  → 一个只在 BM25 命中、向量相似度低于下限的文档**仍会**经关键词通道进入融合（关键词硬约束保住）；
  一个向量相似度低于下限且未被关键词命中的文档被剔除（语义噪声被剥离）。

不设 reranker-score 二次闸门（那是另一套量纲问题，YAGNI，留作独立特性）。
过滤在 `_attach_parents` **之前**发生，parent-context 逻辑不受影响。

**结果可能少于 k 甚至为 0**：这是**预期行为**（"宁可查不出"），不做回填、不报错。

---

## Section 3 — 契约 / 表层

- 无新 CLI flag、无新 MCP 参数——与其它检索旋钮（`RAG_HYBRID` / `RAG_RRF_K` /
  `RAG_HYBRID_CANDIDATES` / `RAG_RERANK`）一致，纯 env 驱动。
- `README.md`：在检索配置小节新增 `RAG_MIN_SCORE` 说明（默认 0=关；作用于向量通道；
  不影响关键词命中；启用后可能返回更少/零条，符合闭世界"未找到"语义）。

---

## 硬依赖（实现计划须先验证）

1. `store.search` 返回的 `score` 确为余弦相似度 `round(1 - distance, 4)`（现状如此，用测试坐实）。
2. `store.search_text` 的 BM25 hits 不被本过滤触碰——由"混合路径只过滤 vector_hits"保证，须有测试。
3. `_env_float` 对非法值回退默认，且 `min_score=0` 全程 no-op（默认字节级不变）。

---

## 测试

**config（`tests/test_config.py` 扩展）**
- 默认 `min_score == 0.0`；`RAG_MIN_SCORE=0.3` → `0.3`；非法值（如 `abc`）→ 回退 `0.0`。

**pipeline（`tests/test_pipeline.py` 扩展，用现有 `_FakeEmbedder` / 真 `VectorStore` 或 fake store）**
- 默认（`min_score=0`）：`search` 结果与过滤前逐位一致（回归）。
- 纯向量 + 设下限：低于下限的 hit 被剔除，等于/高于的保留。
- 混合 + 设下限：**只在 BM25 命中、向量分低于下限的文档仍返回**（关键词通道保住——核心测试）；
  向量分低于下限且无关键词命中的文档被剔除。
- 下限高到滤掉全部 → 返回 `[]`（不崩、不回填）。
- 过滤发生在 parent-context 附加之前：开启 parent_context 时被保留的 hit 仍带 `parent_text`。

---

## 明确不做
- 每-query 自适应阈值（RAGentA 的 `τ_q − nσ`）——我们的分数非校准 log-odds，自适应无从算起；固定余弦下限足够。
- reranker-score / RRF-score 二次闸门——量纲问题，独立特性，YAGNI。
- 相对"距 top X%"截断——RRF 值过密、logit 带符号，破。
- 新 CLI flag / MCP 参数、多 agent 编排、LLM relevance judge——超出"最小吸收"范围。

## 交付定义
- `config.py`：`_env_float` + `min_score` 字段 + 解析（默认 0）。
- `pipeline.py`：`_above_floor` + `search` 两处接入（纯向量 hits、混合的 vector_hits）。
- `README.md`：`RAG_MIN_SCORE` 一条说明。
- 上述 config / pipeline 测试新增；全部现有测试仍通过。
- 默认（`min_score=0`）行为与当前逐字节一致。
