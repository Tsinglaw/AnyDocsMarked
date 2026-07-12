# RAG 索引与检索（可降级外部能力）

> RAG 是**可降级**的外部能力（与 makeitdown 同等地位）：装了就用，没装则问答退化为「仅 wiki」。lawiki 核心（wiki + lint）始终零依赖。
>
> lawiki 经**唯一入口** `<SKILL_DIR>/tools/rag.py`（仅标准库）消费 RAG：它 subprocess 调外部 `rag-retriever` CLI、只认其 JSON 契约，**不 import 其 Python、不用其 MCP server**（与对接 makeitdown 同构）。所有法律/lawiki 专属约定都收在这层；`rag-retriever` 本体保持通用。`<SKILL_DIR>` 指本 skill 实际所在目录。

## 目录与派生关系

```
案件根/
  _md/      # makeitdown 产物，不可变，RAG 的输入
  .rag/     # rag-retriever 向量库（LanceDB + manifest），从 _md/ 派生，隐藏，可重建，可 gitignore
  wiki/     # 你维护的 wiki
```

`.rag/` 与 `wiki/` 平级，都是从同一份不可变 `_md/` 独立派生 → 一致=高可信，不一致=要抓的信号。每案件一个 `.rag/`，天然按案件隔离。

## 建索引（转换后、确定性，无需判断）

```
python <SKILL_DIR>/tools/rag.py index <案件根>
```

内部跑 `rag-retriever --data-dir <案件根>/.rag index <案件根>/_md --source-root <案件根> --metadata-fields quality`：
- `--source-root <案件根>` → 入库的 `source` 是相对 POSIX 路径（如 `_md/合同/采购.md`），与 lawiki 锚点一致。
- `--metadata-fields quality` → 把 makeitdown 写的 `quality` frontmatter 随命中带回（用于「未核验」标注）。

**刷新**：新增/替换来源后重跑同一条命令即可——rag-retriever 按文件 delete+add 增量重索引。

## 检索（问答第一步的 RAG 路）

```
python <SKILL_DIR>/tools/rag.py search <案件根> "<问题>" -k 8
```

输出恒为 JSON。成功：

```json
{ "rag_available": true, "hits": [
  { "source": "_md/借条.md", "text": "<逐字原文>", "score": 0.70,
    "metadata": {"quality": "suspect"},
    "anchor": "〔来源: _md/借条.md：「<单行逐字片段>」〕（未核验）",
    "unverified": true }
]}
```

- `text`：命中 chunk 的逐字原文（含 frontmatter/换行），供你通读、挑出更精确的支撑句。
- `anchor`：现成的 lawiki 锚点（**单行**、已去前导 frontmatter、可疑来源带「（未核验）」）——**lint 可验**，可直接落进 wiki。通常你应把片段缩到具体支撑句（一条断言 ← 一条能完整支持它的引文）。
- `k≥8` 防假冲突：先凑够上下文再判，别把「RAG 没检索到」误判成「矛盾」。

## 提示（`notice`，成功路径也可能出现，务必转告用户）

`index`/`search` 成功时若带 `"notice": "..."`，是 rag-retriever 打到 stderr 的**非致命**提示——
最常见的是「未检测到内置 embedding 模型，将联网下载」（源码包首次索引、embedding
后端选了 `local` 但没走 `-offline` 发布包时触发）。**看到就转告用户**：断网/受限
网络环境下这条提示往往就是随后失败的前兆，此时改用 `-offline` 发布包能让索引
零下载完全离线，比等下载超时后再排障快得多。

## 降级（任一不满足即退化「仅 wiki」，并明确告知用户）

`search` 返回 `{"rag_available": false, "reason": "..."}`（退出码仍 0，供分流）的三种情形：
- **未装 rag-retriever**（命令不在 PATH，且未设 `LAWIKI_RAG_CMD`）。
- **未建索引**（无 `.rag/`）→ 先 `index`。
- **模型不一致**：索引时模型 ≠ 当前查询模型（见下）。

降级时照常用 wiki 作答，并对用户挑明「当前无 RAG 交叉验证」。

## embedding 模型一致性（铁规）

索引与查询**必须同一 embedding 模型**，否则相似度失真。机制：
- `rag-retriever` 索引时把模型写进 `.rag/`（`index_meta.json`）；`stats` **同时报索引时模型与当前查询模型**（后者由 rag-retriever 自己的 config 解析，wrapper 不再镜像其默认表）。
- wrapper `search` 只比对 `stats` 给的这两组模型——不一致即降级，提示 rebuild。
- 换模型 → 删 `.rag/` 重新 `index`。

## 调用方式（`LAWIKI_RAG_CMD`）

wrapper 默认调 `rag-retriever`（需其在 PATH，如 `uv tool install` 后）。否则用环境变量覆盖前缀，例如开发期指向其 uv 项目：

```
LAWIKI_RAG_CMD='uv run --project "<rag-retriever 路径>" rag-retriever'
```

embedding 后端选择（local / ollama / openai）见 `setup.md`。
