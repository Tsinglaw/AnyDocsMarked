# lawiki 扩展设计:RAG + 交叉验证问答

状态：**已实现并端到端验证**。分支 `feat/rag-qa-extension`（lawiki）+ `feat/lawiki-adaptations`（rag-retriever）。

> **落地小结（2026-06-21）**：① rag-retriever 本体加齐五项通用能力（`--source-root` 相对 POSIX 路径 / `--data-dir` / `search --json` / 通用 frontmatter 透传 / 索引模型持久化 + `stats` 报索引时模型）+ UTF-8 stdout 修复，12 个 pytest 全绿。② lawiki 加 `tools/rag.py` 薄 wrapper（subprocess 调 CLI、不 import、不用 MCP）承担五职责，11 个 stdlib unittest 全绿（含「wrapper 锚点过真实 lint」）。③ `references/qa.md`（四情形协议）+ `rag.md` + `setup.md`（RAG 安装/后端/一致性）+ `SKILL.md`（索引步、问答小节、触发词）。④ 合成四情形案件实测：index→search 返回带相对锚点 + quality 的证据；`lint check` 锚点有效且覆盖率警告抓出漏引的冲突源；`lint extract` 抽出失真断言供交叉验证。修掉一个真 bug：默认锚片段原为多行（含 frontmatter），lint 单行正则会静默漏检 → 改为单行去 frontmatter 片段（先写复现测试）。

> **架构锐化（2026-06-21 修订）**：在初稿基础上钉死了 lawiki ↔ rag-retriever 的**集成边界**——两者用 **CLI/JSON 契约**连接（与 makeitdown 对称），lawiki 经一个**薄 wrapper `tools/rag.py`（subprocess 调 CLI，不 import 其 Python、不用其 MCP server）**单点消费。并给出**功能切分判据**（第 3 节）、把初稿"存 quality"修正为**通用 frontmatter 字段透传**、补上**索引模型持久化 + `stats --json`**（一致性校验的依据）与**冷启动延迟**的取舍说明。受影响的主要是第 3、7、8 节。

## 背景与目标

lawiki 当前流水线 `原始资料/ → _md/(makeitdown) → wiki/`，已经做到「转换 + 严谨可溯源的 wiki 化」。
本扩展补上其缺失的两块能力，使其覆盖完整的「案件资料整理 + 问答」愿景：

1. **RAG**：对 `_md/` 建向量索引，提供语义检索。
2. **交叉验证问答**：用户提问时，同时取 wiki 结论与 RAG 原文证据，**一致则答；不一致则查因；查不出则把两套答案 + 原始依据并列交用户裁决**。

复用既有项目：检索引擎用 `rag-retriever`（`D:\Vibe Coding Items\rag-retriever`），转换仍用 makeitdown。不新建 skill，扩展 lawiki 本体。

## 设计原则（尊重 lawiki 既有哲学）

1. `原始资料/`、`_md/` **永不可变**。RAG 索引是从 `_md/` 派生的衍生物（与 `wiki/` 平级）。
2. **锚点是唯一引用语言**：RAG 命中必须能翻译成 lawiki 锚点 `〔来源: _md/<相对路径>：「逐字原文」〕`，且 lint 可验。**不用页码**。
3. **RAG 是可降级的外部能力**（与 makeitdown 同等地位）：装了就用，没装则问答退化为「仅 wiki」。lawiki 核心（wiki + lint）保持零依赖。
4. **问答继承三类标注纪律**：EXTRACTED / INFERRED / AMBIGUOUS；事实挂锚点，推断标分析，存疑显式标注。
5. **集成靠 CLI/JSON 契约，不靠 import（与 makeitdown 完全对称）**：lawiki 对 makeitdown 是「shell 调 CLI + 只认其稳定输出契约（frontmatter/`report.json`），不 import 其 Python」；对 rag-retriever 一模一样——shell 调 CLI、只认 `search --json` 输出契约、不 import 其 Python、**也不用它的 MCP server**。两项目只靠契约连接，各自独立演进。中间这层就是 lawiki 的 `tools/rag.py`。
6. **功能切分判据**（决定每个功能放本体还是 wrapper）：**「换一个非法律的 RAG 用户，他还想不想要这个功能？」** 想要 → 通用能力 → 改进 **rag-retriever 本体**（本体不出现任何法律/lawiki 字眼）；这是 lawiki/法律/makeitdown 的约定 → 放 **lawiki 的 `tools/rag.py`**。

## 1. 目录结构变化

```
案件目录/
  原始资料/        # 不可变源
  _md/             # makeitdown 产物，不可变，RAG 输入
  wiki/            # 编译知识（人读 + LLM 读）
  .rag/            # ★新增：rag-retriever 向量库（LanceDB + manifest），从 _md/ 派生，隐藏，可重建，可 gitignore
```

每案件一个独立 `.rag/` → 天然按案件隔离。

## 2. 流水线变化：转换后增加「索引」步

```
原始资料/ ──makeitdown──▶ _md/ ──┬──ingest──▶ wiki/   （既有：Agent 综合）
                                  └──index───▶ .rag/   （★新增：确定性脚本）
```

- 时机：第二步（转换）完成后即索引 `_md/`；后续新增来源时增量重索引（rag-retriever 按文件 delete+add）。
- 性质：确定性，跑命令即可，不需 Agent 判断。

## 3. 功能切分：rag-retriever 本体 vs lawiki wrapper

按第 6 条判据切分。**rag-retriever 本体只加通用能力、不出现任何法律字眼；所有 lawiki/法律黑话归 `tools/rag.py`。**

### 3.1 rag-retriever 本体改动（全部通用，配最小测试）

> 动手前先给 rag-retriever **`git init`**——它现在没有版本控制，改之前要有回退点。

| 改动 | 位置 | 说明 | 为何通用 |
|---|---|---|---|
| ① `--source-root`（存相对路径） | `pipeline.py` / `store.py` / `cli.py` | 索引时传入根目录；`source` 存**相对该根**的路径（如 `_md/合同/采购.md`），替代现在的 `str(path.resolve())` 绝对路径 | "相对某根存路径"让索引可整体搬迁、不绑绝对机器路径 |
| ② `--data-dir`（per-case 库） | `cli.py`（现仅 `RAG_DATA_DIR` 环境变量半支持） | 指向 `<案件>/.rag` | 任何人都想指定库在哪 |
| ③ `search --json` | `cli.py` | 返回 `[{source(相对), text(逐字), score, ord, metadata{…}}]` | 任何程序化消费者都要 JSON |
| ④ frontmatter 字段透传 | 索引时按**配置指定**抽取 YAML 字段当 metadata、随命中返回 | 不教它认识 `quality:suspect`，只给它「抽这些字段当 metadata」的通用能力 | 任何 RAG 用户都想按文档元数据过滤/带回 |
| ⑤ 索引模型持久化 + `stats` 同时报两模型 | `store.py`（写 sidecar）/ `pipeline.py` | 把**索引时** `embed_model`/`embed_backend` 写进 `.rag/index_meta.json`；`stats` **同时报索引时模型（`index_*`）与当前查询模型（`query_*`，来自 live config）** | 任何人都怕"用模型 X 建库、用模型 Y 查"——消费者据这两组判定，无需自己推导模型 |

**关键转化（④）**：初稿写"把 `quality:suspect` 存进库"——那是让 rag-retriever 认识 makeitdown/法律黑话，违反判据。改为**通用 frontmatter 字段透传**：本体只负责"索引时抽取配置指定的 YAML 字段、检索时随 JSON 带回"，至于要 `quality` 这个字段、`suspect` 这个值意味"未核验"，由 lawiki wrapper 去消化。

### 3.2 lawiki `tools/rag.py`（薄 wrapper，subprocess 调上面的 CLI）

集中承担所有 lawiki 专属逻辑 + 降级判断，是 `SKILL.md`/`qa.md` 调用 RAG 的**唯一入口**：

1. **绑根**：固定以案件 `_md/` 为 `--source-root`、`.rag/` 为 `--data-dir`；管理这两个路径。
2. **拼锚点**：把 JSON 命中的 `{相对路径, 逐字文本}` 拼成 lawiki 锚点 `〔来源: _md/…：「逐字」〕`，使问答引用也 lint 可验。
3. **翻黑话**：从透传回来的 `metadata.quality` 读到 `suspect` → 引用后缀「（未核验）」。
4. **降级检测（一处集中）**：rag-retriever 装了吗？`.rag/` 建了吗？`stats --json` 报的索引模型与当前查询模型一致吗？——任一不满足，返回明确的"无 RAG"，问答自动退化「仅 wiki」并告知用户"当前无交叉验证"。集中在此比散落 `SKILL.md` prose 里可靠。
5. **增量重索引**：新增来源时按文件 delete+add（rag-retriever 支持），根目录恒为同一 `_md/`，保证相对路径键不漂移。

### 3.3 为什么走 subprocess、不 import、不用 MCP（取舍说明）

- **不 import 其 Python**：`import` 会耦合到 rag-retriever 的类签名/返回结构/依赖环境，比 CLI/JSON 契约更紧更脆，且破坏与 makeitdown 的对称。subprocess + JSON 契约下，rag-retriever 内部随便改，只要契约不变 lawiki 不受影响。
- **不用其自带 MCP server**（其代码注明 MCP 才是"agent-facing entry"）：MCP 是 agent **直连**的常驻服务，agent 会绕过 `tools/rag.py` 直接调它 → "单一入口集中降级 + 拼锚点"的控制点就散了。为 lawiki 的"一个入口、集中管控"，**故意不用 MCP、走 wrapper→CLI**。
- **代价 — 冷启动延迟（分后端）**：subprocess 每次 search 冷启 Python。`local`（fastembed 离线）要重载 embedding 模型，约 1–2s/次，交互问答下可感知；`ollama`/`openai` 模型常驻服务端，只付 import + HTTP，便宜。即"每次 shell 调一次 search"的税主要落在离线模式——接受并写进设计；若难忍，给 local 模式留个 warm 进程余地（不在本期范围）。

## 4. 问答协议（核心新增，落到 `references/qa.md`）

### 触发
「问本案…」「关于这个案子…」「本案里 X 是什么」等（与「整理/建库」触发词区分）。

### 第一步：双路并行取证
1. **wiki 路**：读 `wiki/index.md` → 顺 wikilink/grep 找相关页 → 已综合结论 + 锚点。
2. **RAG 路**：`rag search "<问题>" --json -k 8` over `.rag/` → 原始 `_md/` 片段 + 相对路径 + 逐字文本 + quality。
   - k≥8 是防假冲突：先凑够上下文再判，避免「RAG 没检索到」被误判为「矛盾」。

### 第二步：比对，四情形分流

- **一致** → 直接回答；同时引用 wiki 页 + `_md/` 锚点，标注「wiki 与原文互证」。
- **wiki 沉默/未覆盖**（≠冲突）→ 用 RAG 原文回答，标 INFERRED「该点尚未入 wiki」；可建议用户是否 ingest 进 wiki。
- **不一致，能定因** → 查因三连：①wiki 总结拔高/转述失真？（比对 wiki 断言 vs RAG 逐字原文）②RAG 漏上下文？（提高 k 或换检索词）③源文件本身冲突？（看 wiki 是否已有 ⚠冲突 callout）。定因后以 EXTRACTED 原文为准作答，并指出 wiki 何处需修。
- **不一致，查不出因**（安全阀）→ 不许静默取舍：把【wiki 答案 + 锚点】与【RAG 答案 + `_md/` 锚点】并列，附两边相对路径 + 逐字片段，明说「无法判定，请人工溯源裁决」→ 交用户。

### 第三步：引用纪律（继承铁律）
- 每个事实陈述挂 `〔来源: _md/…：「逐字原文」〕`，逐字片段取自 RAG 返回原文。
- 来源 `quality: suspect` 的，引用后缀「（未核验）」。
- 推断标 `> [!note] 分析`；冲突/存疑标 AMBIGUOUS。
- 「一致/不一致」是 LLM 判断、会错 → 规定永远把两边原文摆出，不只给「一致」二字，供用户复核。

## 5. 锚点桥接（为什么 RAG 与 lawiki 无缝对接）

- lint 校验锚点 = 「逐字片段确在所指 `_md/` 文件里」。
- rag-retriever 的 chunk 文本就是从 `_md/` 逐字切出。
- 故：RAG 命中 → Agent 取支撑句的精确片段 → 拼成 lawiki 锚点 → **lint 可验**。
- 推论：问答引用也机器可校验；用户认可的问答结论已是 wiki 格式，可直接回填 wiki。

## 6. RAG 与 wiki 互为校验的依据

| | wiki | RAG over `_md/` |
|---|---|---|
| 本质 | 已综合、已锚定的子集 | 全量原文的无过滤语义检索 |
| 强项 | 关系/时间线/已判定 | 覆盖全，catch wiki 漏掉的 |
| 弱点 | 可能漏或总结漂移 | 无综合，可能检索噪声 |

两者从同一 `_md/` 独立派生 → 一致=高可信，不一致=要抓的信号。
共同盲区：都吃 `_md/`，抓不出 `_md/` 的 OCR 错 → 靠 `quality: suspect` + 最终回溯 `原始资料/` 兜底。

## 7. setup 变化（`references/setup.md` 增补）

新增一项（与 OCR 同样的「本地 vs 云端」框架）：
- 装 rag-retriever（本地路径依赖或打包）。
- 选 embedding 后端：`local`(fastembed，离线) / `ollama`(bge-m3，本地最佳中文) / `openai`(硅基流动 bge-m3，需 key)。
- 优雅降级：没装 RAG → 问答退化「仅 wiki」，明确告知用户「当前无交叉验证」。
- 一致性铁规：索引与查询须同一 embedding 模型；换模型须 `rag rebuild`。落地靠第 3.1 节 ⑤——索引时把模型写进 `.rag/manifest`，`tools/rag.py` 启动经 `stats --json` 读回**索引时**模型与当前 config 比对（不是比 live config，否则测不出不一致）。

## 8. 要动的文件清单

**lawiki skill 内：**
1. `SKILL.md` — 流水线加「第二步半：索引 `_md/` → `.rag/`」；新增顶层小节「案件问答」指向 `references/qa.md`；补问答触发词。**调 RAG 只经 `tools/rag.py` 一个入口，不直接碰 rag-retriever。**
2. `references/qa.md` — 新建：第 4 节完整问答协议。
3. `references/setup.md` — 增补第 7 节。
4. `references/rag.md` — 新建：索引建/刷新、`tools/rag.py` 命令、锚点映射规则、降级与模型一致性校验。
5. `tools/rag.py` — **薄 wrapper（不再是可选）**：subprocess 调 rag-retriever CLI，承担第 3.2 节五项职责（绑 `_md/` 根、拼锚点、翻 quality→「未核验」、降级检测、增量重索引）。

**rag-retriever 内（全部通用，先 `git init`）：**
6. `pipeline.py` / `store.py` / `cli.py` — `--source-root`（存相对路径，替代绝对路径）。
7. `cli.py` — `--data-dir` + `search --json`。
8. 索引时按配置抽取 frontmatter 字段当 metadata、随命中返回（**通用透传，不认识 `quality:suspect`**）。
9. `store.py` 写 manifest 时持久化**索引时** `embed_model`/`embed_backend`；`cli.py` 加 `stats --json` 报回。

## 9. 边界与未来

- 不改 lint 核心（零依赖保持）；RAG 是旁路，不进 lint 闸门。
- 未来可选：用 RAG 给 lawiki「覆盖率警告」加强版（抽 wiki 断言反向用 RAG 验，发现「wiki 说了但原文找不到」）。
- 未来可选：问答结论一键回填 wiki。

## 10. 落地顺序（每段可验证）

1. rag-retriever：先 `git init` → 加 `--source-root`/`--data-dir`/`search --json`/frontmatter 透传/模型持久化+`stats --json`（各配最小测试）→ 用某案件 `_md/` 实测「索引→检索→JSON→相对路径正确」。
2. 写 `tools/rag.py` + `references/rag.md` → 实测「检索→拼锚点→过 lint」全链路，并验降级与模型一致性校验。
3. 写 `references/qa.md` 问答协议 → 拿真实问题走决策树。
4. 改 `SKILL.md` + `setup.md`，接入索引步与问答入口（只经 `tools/rag.py`）。
5. 端到端验证：真实案件 `原始资料/` → 整理（转换+wiki+索引）→ 问「一致 / wiki 沉默 / 不一致可定因 / 不一致查不出」四类问题，看四情形分流是否正确。
