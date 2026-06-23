# AnyDocsMarked

把一堆案件材料，变成**可控、可溯源**的案件知识库——再就案情交叉验证问答。

本仓库收录三个**各自独立、通过稳定 CLI/JSON 契约协作**的项目：

| 子项目 | 职责 | 形态 |
|---|---|---|
| [`lawiki/`](lawiki) | 法律案件 wiki 构建 skill：把 `_md` 归档成可溯源的案件 wiki（案件主体/法律关系/法律事实/时间线），并做 wiki×RAG 交叉验证问答 | agent skill（含确定性 lint + outline 导航，零依赖核心） |
| [`makeitdown/`](makeitdown) | 把各式文件（PDF/Word/扫描件…）转成带质量标记的 LLM 可读 markdown | CLI（可选 OCR） |
| [`rag-retriever/`](rag-retriever) | 本地优先的语义检索引擎（fastembed/LanceDB，local/ollama/openai） | CLI / MCP |

数据流：`原始资料/ ──makeitdown──▶ _md/ ──┬─ lawiki ingest ─▶ wiki/`
                                       `└─ rag-retriever index ─▶ .rag/`

## 一键上手（推荐：下载 Release bundle）

到 **Releases** 下载 `lawiki-bundle-*.zip`，解压后让 AI agent 加载 `skill/lawiki`，按提示跑 `python install.py` 即可自动安装 makeitdown 与 rag-retriever。然后把文件放进案件目录的 `原始资料/`，对 agent 说「整理案件资料」。

## 从源码安装（非 bundle）

三者仍是独立可装的包（子目录语法）：
```
uv tool install --python 3.12 "makeitdown[local] @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=makeitdown"
uv tool install --python 3.12 "rag-retriever @ git+https://github.com/Tsinglaw/AnyDocsMarked.git#subdirectory=rag-retriever"
```
lawiki 是 skill、无需安装（加载 `lawiki/skill/lawiki` 即可）。详见 [`lawiki/skill/lawiki/references/setup.md`](lawiki/skill/lawiki/references/setup.md)。

## 技术特点

- **零来源不可写 + 逐字锚点（机器可校验）**：wiki 每句事实挂 `〔来源: _md/…：「逐字原文」〕`，确定性 lint 把关（锚点存在/死链/时间线/勾稽闭合）。
- **三类标注铁律**：EXTRACTED（原文）/ INFERRED（分析）/ AMBIGUOUS（存疑）物理隔离，换实例 LLM 判官查"引文真但断言被拔高"。
- **三层互补**：`_md`（原文）/ outline（结构导航，防漏检）/ wiki（综合）+ RAG；wiki×RAG 交叉验证，不一致即暴露、查不出交人裁决。
- **松耦合 + 可降级**：CLI/JSON 契约连接、互不 import；RAG/转换为可插拔外部能力，lawiki 核心零依赖、离线可用。

> 各子项目保留各自的 README 与许可证；它们也可独立使用。
