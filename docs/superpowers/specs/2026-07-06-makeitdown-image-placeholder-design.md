# 设计：吸收 NexusRAG 优势 · 批次 2（makeitdown 图像不再静默丢弃 → 可溯源占位标记）

日期：2026-07-06
状态：已批准设计，待写实现计划
范围：仅 `makeitdown/`（rag-retriever、lawiki 均不改）

## 背景与来龙去脉

对标 NexusRAG 的 "vision-LLM 给图/表生成 caption 并向量化" 能力后，经与用户讨论**主动收敛了范围**：

- **表格无需处理**：makeitdown 已把表格以文本形式保留在 `_md`（`_strip_images` 仅删图、保留表格包裹 div 的内容），rag-retriever 也专门不切开表格 —— 表格已可检索。
- **拒绝外接视觉 LLM**：给图片自动生成 caption 必须外接一个 VLM，且"读图"走云端等于上传敏感证据图，与本项目"隐私优先、宁可查不出也不编造"冲突；且对**有文字的图**（扫描件、聊天/转账截图）OCR 已覆盖，caption 多余。自动 caption 仅对**非文字类证据图**（现场照、印章、签名、图表）有意义，属少数，性价比低。**LLM 自动描述整体延后**，不在本批次。
- **真问题**：makeitdown 默认路径把图片**静默双删**——既删 `_md` 里的图片引用，又丢弃图片字节（`result.assets = {}`），`_md` 中连"此处曾有一张图"都不留。哪怕一张关键现场照，读者/agent 都无从知其存在。

因此批次 2 收敛为一个**零依赖、零上传、零编造**的改动：**默认路径不再静默删除图片引用，改为留一个中性、可溯源的占位标记**，如实记录"此处原本有一张图"。检索层（rag）无需改动即可命中标记及其上下文，把人领到证据位置，再由人打开原图核对。

## 现状（精确）

`makeitdown/src/makeitdown/pipeline.py`：
- `_strip_images(text)`（:30）用正则删除 `<img …>`（`_IMG_HTML_RE`）与 `![alt](path)`（`_IMG_MD_RE`），并折叠因此变空的 `<div>`。
- `handle()`（:174-176）在 `not keep_images` 时执行 `result.text = _strip_images(result.text)` **且** `result.assets = {}`。
- `--keep-images` 为真时：图片引用原样保留，`result.assets` 随 `_write_output`（:66-71）写出图片文件。

## 变更

### 1. 用可溯源标记替换静默删除（核心）

新增 `_mark_images(text: str) -> tuple[str, int]`，替代默认路径中的 `_strip_images`：

- 对每个 `![alt](path)`：替换为 `〔图像：<name> —— 已省略未保留，请查原件〕`。
- 对每个 `<img … src="path" …>`：同样替换（从 `src` 取 name）。
- `<name>` 取值优先级：图片路径的 basename（如 `image_007.png`）→ 若无 path 则用 alt 文本 → 都没有则 `未命名`。即便图片字节被丢弃，引用字符串里仍带着这个文件名句柄，保留下来便于与原文件对应。
- 因替换而变空的 `<div>` 仍按原逻辑折叠。
- 返回被标记的图片数量（供 report 计数）。

`handle()` 默认路径改为：`result.text, n_omitted = _mark_images(result.text)`（仍 `result.assets = {}`，默认不写图片文件，输出保持轻量）。

**标记约定**：用 `〔图像：…〕`，与 lawiki 的 `〔来源：…〕` 视觉语言一致，但**关键词为"图像"而非"来源"**，不会被 lawiki 的锚点 lint 误当来源锚点校验。标记内容**只陈述"曾有一张图 + 文件名"这一事实**，不描述图的内容，无编造。

### 2. report.json 增加省略计数

report 字典新增 `"images_omitted": 0`。`handle()` 返回值携带本文件的 `n_omitted`，主聚合循环累加。让 agent 能回话"本次省略了 N 张图，如需提取请加 `--keep-images`" —— 契合"暴露、不隐藏"。仅在默认（非 keep-images）路径计数。

### 3. SKILL.md 文档

在 `--keep-images` 说明处补一句：默认输出现在会为每张图片留 `〔图像：…〕` 占位标记（记录其存在与位置，不含内容），`--keep-images` 才提取真实图片文件并保留标准 `![]()` 引用。

## 明确不在范围内（范围边界）

- **`--keep-images` 路径完全不变**：仍保留 `![](path)` 引用 + 写出图片文件。回归测试锁定其行为不变。
- **rag-retriever 不改**：标记只是 `_md` 文本，rag 自动索引；靠标记及其上下文句子被检索命中。
- **不引入任何模型、不上传任何数据**。
- **不改质量标记**：图片被省略不触发 `quality: suspect`（那是 OCR 质量信号）。
- **LLM 自动图像描述**：延后，不在本批次（未来若确有非文字证据图需求再单独立项）。
- **表格**：已可检索，不动。

## 测试

`makeitdown/tests/`（沿用现有测试布局）：
- `_mark_images`：
  - `![alt](img/x.png)` → 标记含 `x.png`，且不再有 `![]()`。
  - `<img src="a/b.png">` → 标记含 `b.png`。
  - `![说明]()`（有 alt 无 path）→ 标记回退用 alt 文本。
  - `<img>`（无 src）→ 标记用 `未命名`，不抛错。
  - 多张图 → 返回计数等于图片数。
  - 因删图变空的 `<div></div>` 仍被折叠；含表格内容的 `<div>` 不受影响（保留）。
- 管线级：
  - 默认路径产出的 `_md` 含 `〔图像：…〕` 标记而非空缺；`images_omitted` 计数正确进 report.json。
  - `--keep-images` 路径：`_md` 仍是 `![]()`、图片文件写出、`images_omitted` 不计数（保持 0）—— 回归保护。

## 交付定义

- `pipeline.py`：新增 `_mark_images`；默认路径改用它；`handle()`/report 聚合新增 `images_omitted`。（`_strip_images` 可保留为内部实现细节或被 `_mark_images` 取代，实现计划定夺。）
- `SKILL.md`：一句文档更新。
- 新增 `_mark_images` 单元测试 + 管线级测试；`--keep-images` 回归测试。
- 现有测试全部通过。
