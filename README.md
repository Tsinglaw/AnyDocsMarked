# AnyDocsMarked

**把一堆杂乱的案件材料，变成可控、可溯源、经得起交叉验证的案件知识库——再就案情做可回溯的问答。**

为**法律工作**而生：律师、法务、法官助理面对的是成百上千页判决书、合同、笔录、票据、聊天记录，
其中**每一个金额、日期、当事人、法条都不能错**，而且**任何一句结论都要能指回原文**。
AnyDocsMarked 不追求"看起来很懂"，而是把「可信」与「可溯源」做成第一性原则——
**宁可查不出，也不编造**。

---

## 为什么法律场景需要它（它解决的真问题）

| 法律工作的痛点 | 通用 AI / 普通 RAG 的问题 | AnyDocsMarked 的做法 |
|---|---|---|
| 结论必须能指回原文 | 生成式回答无法逐字溯源，易"看似有据实则脑补" | 每句事实挂**逐字来源锚点** `〔来源: _md/…：「逐字原文」〕`，**确定性 lint 机器校验** |
| 金额/日期错一位就是事故 | OCR 静默出错、模型幻觉数字 | **双 OCR 互校**（Paddle×MinerU）+ 质检标记 + 数字/日期位专项比对 |
| 近义法律术语必须分清 | 纯向量检索把"表见代理/无权代理"混为一谈 | **BM25 关键词 + 向量混合检索**（RRF 融合），术语精确召回 |
| 推断不能冒充事实 | 模型把分析当事实陈述 | **三类标注铁律**：原文 / 推断 / 存疑物理隔离，越界即拦 |
| 敏感案卷不能随便外传 | 云服务默认上传 | **本地优先可离线**；用云端需**显式同意**，绝不静默上传 |
| 漏检等于没做 | 一次检索命不中就当没有 | **三层互补** + **wiki×RAG 交叉验证**，不一致立即暴露 |

---

## 仓库构成

三个**各自独立、通过稳定 CLI/JSON 契约协作、互不 import** 的子项目：

| 子项目 | 职责 | 形态 |
|---|---|---|
| [`makeitdown/`](makeitdown) | 把各式文件（PDF / Word / 扫描件 / 图片…）转成**带质量标记**的 LLM 可读 markdown | CLI（可选 OCR，云端默认 + 本地可选） |
| [`rag-retriever/`](rag-retriever) | 本地优先的语义检索引擎：**结构感知分块 + BM25/向量混合检索** | CLI / MCP 工具 |
| [`lawiki/`](lawiki) | 法律案件 wiki 构建 skill：把 `_md` 归档成**可溯源**的案件 wiki（案件主体 / 法律关系 / 法律事实 / 时间线），并做 **wiki×RAG 交叉验证问答** | agent skill（确定性 lint + outline 导航，零依赖核心） |

---

## 工作方式与数据流

```
原始资料/                     不可变的原始案卷（PDF/Word/扫描件…）
   │
   │  makeitdown（转换 + 质检 + 可选双 OCR 互校）
   ▼
_md/                          带 frontmatter 的高保真 markdown（不可变来源层）
   │                          每份记录 source / engine / quality:suspect / warnings
   ├─ lawiki ingest ───────▶  wiki/   案件主体·法律关系·法律事实·时间线（每句挂逐字锚点）
   │
   └─ rag-retriever index ─▶  .rag/   结构感知分块 + 向量 + BM25 全文索引
                                 │
      问答时：lawiki（综合结论）  ◄── wiki×RAG 交叉验证 ──►  rag-retriever（原文召回）
                                 不一致 → 暴露给人裁决
```

**三层互补，各司其职、互为校验：**
- **`_md`（原文层）**：makeitdown 的忠实转换，不可变、可回溯的事实底座。
- **`wiki`（综合层）**：lawiki 归纳出的结构化结论，每句挂逐字锚点、可机器校验。
- **`.rag`（召回层）**：rag-retriever 的混合检索，防漏检的安全网。
- 三者**独立**产生，问答时 **wiki×RAG 交叉验证**：两条独立路径得同一答案才可信，不一致即报警。

---

## 使用流程

1. **建案件目录**，把原始文件放进 `原始资料/`。
2. **转换**：`makeitdown 原始资料 -o _md`——递归转换，自动质检；扫描件走 OCR，
   法律高危件可加 `--ocr-cross-check` 做双 OCR 互校。可疑产出被标 `quality: suspect`
   并随文件流入下游。
3. **建库**：
   - lawiki skill 读 `_md`，按铁律归档进 `wiki/`（每句事实挂逐字锚点，收尾自动跑 lint + 蕴含校验）。
   - rag-retriever `index _md` 建结构感知分块 + 向量/BM25 索引。
4. **问答**：对 agent 提问。agent 综合 wiki 结论并用 rag-retriever 召回原文交叉验证，
   给出**带逐字来源**的回答；wiki 与 RAG 冲突时显著上报，交人裁决。

> 面向非技术用户：把整个仓库（或 Release bundle）交给 AI 助手，说「整理案件资料」，
> 助手会自动安装并跑完上面的流程。

---

## 架构设计（为何可信、可维护）

- **松耦合 + 稳定契约**：三个子项目互不 import，仅通过 **CLI / JSON / frontmatter 契约**衔接
  （如 `source` / `quality: suspect`）。任一环可单独使用、单独替换、单独测试。
- **可降级 + 离线可用**：lawiki 核心**零第三方依赖**，只要有 Python 就能跑校验；
  RAG 与转换是**可插拔的外部能力**；本地后端全程离线。
- **确定性闸门优先于模型判断**：能用规则机器校验的（锚点存在、死链、时间线顺序、勾稽算术、
  数字位比对），一律用确定性代码把关，不依赖模型"自觉"。
- **默认隐私、显式同意**：默认本地；用云端 OCR 需 `--cloud-consent`（或环境变量），
  **非交互也绝不静默上传**，本地始终是可选退路。

---

## 强项（对法律工作的直接价值）

**1. 机器可校验的逐字溯源（护城河）**
wiki 每句作为事实的陈述必须挂 `〔来源: _md/…：「逐字原文」〕`，`lawiki/lint` 确定性核验：
锚点逐字确在源文件、`[[]]` 无死链、时间线日期非递减、`> [!check] a+b==c` 勾稽算术成立
（安全求值不 eval）。**"数字写错/张冠李戴"必被抓，"换行/标点差异"不误报。**

**2. 三类标注铁律 + 蕴含校验判官**
写进 wiki 的每句话必归入 **EXTRACTED（原文直取）/ INFERRED（推断）/ AMBIGUOUS（存疑）**
之一并打标，无法归类者不写。每次 ingest 收尾，**换实例 LLM 判官**三分判"引文是否支持断言"，
专抓"引文是真的、但断言被拔高/脑补/歪曲"。判官只判不改，三轮仍不过则显著上报。

**3. 双 OCR 互校，专治法律高危盲点**
`--ocr-cross-check` 用 **Paddle + MinerU 两个独立引擎**在同一张（先摆正的）页面上各识别一次，
归一化后比对，**重点盯金额/日期/数字位不一致**——OCR 改一位最致命处。分歧标 `quality: suspect`，
随文件进知识库；互校失败也绝不丢转换结果。

**4. 结构感知分块 + 混合检索，术语召回更准**
rag-retriever 按**法律文书结构**切块（标题面包屑、表格不切开、`第X条`/`本院认为`等法律标记做软边界），
每块带"出自哪一节"。检索默认 **BM25 关键词（jieba 分词，离线）+ 向量**双路 **RRF 融合**——
纯向量会把"表见代理 vs 无权代理"混淆，关键词通道把术语当硬约束，召回更精确。

**5. 三层互补 + wiki×RAG 交叉验证**
原文 / 综合结论 / 召回三层独立产生；问答时两条独立路径交叉印证，不一致立即暴露、交人裁决。
**纵深防御**——对错一处代价极高的法律场景，"两条路得同一答案"才敢信。

**6. 国内可用、隐私可控**
本地版离线免费、文档不出本机；需要轻快时可用云端（需显式同意）。转换与检索的本地后端
全程无需海外服务。

---

## 安装

### 一键上手（推荐：下载 Release bundle）
到 **Releases** 下载 `anydocsmarked-*.zip`，解压后让 AI agent 加载 `skill/lawiki`，
按提示跑 `python install.py` 即可自动安装 makeitdown 与 rag-retriever。
然后把文件放进案件目录的 `原始资料/`，对 agent 说「整理案件资料」。

### 从源码安装（非 bundle）
三者仍是独立可装的包（子目录语法）：
```
uv tool install --python 3.12 "makeitdown[local] @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown"
uv tool install --python 3.12 "rag-retriever @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=rag-retriever"
```
lawiki 是 skill、无需安装（加载 `lawiki/skill/lawiki` 即可）。
详见 [`lawiki/skill/lawiki/references/setup.md`](lawiki/skill/lawiki/references/setup.md)。

> 各子项目保留各自的 README 与许可证；它们也可独立使用。
> 每个子项目的 README 有更细的选项与用法：
> [makeitdown](makeitdown/README.md) · [rag-retriever](rag-retriever/README.md) · [lawiki](lawiki/README.md)。
