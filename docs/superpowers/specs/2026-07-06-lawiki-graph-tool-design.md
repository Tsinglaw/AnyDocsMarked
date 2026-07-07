# 设计：吸收 NexusRAG 优势 · 批次 3（lawiki 关系图谱工具 graph.py → 多跳关系问答）

日期：2026-07-06
状态：已批准设计，待写实现计划
范围：仅 `lawiki/`（rag-retriever、makeitdown 均不改）

## 背景与来龙去脉

对标 NexusRAG 的 "知识图谱 + Local/Global-KG 多跳查询" 能力后，主动收敛为贴合本项目内核的版本：

- NexusRAG 用 LightRAG **让 LLM 自动抽取实体-关系**建图。这与本项目 "宁可查不出也不编造、每条边都要可溯源" 冲突，**拒绝**。
- **本项目的 wiki 本身已是一张更可信的手工知识图谱**：`案件主体`/`法律关系`/`法律事实` 页面是节点，页面间的 `[[wikilink]]` 是边，且这些链接已被 `lint` 的死链检查校验过。
- **真缺口**：qa.md 的 "wiki 路" 目前靠 agent **人肉追 `[[wikilink]]` / grep** 找相关页，容易漏掉多跳路径；没有工具能确定性地"走"这张图。像 "甲与丙有无间接关系" 这类多跳问题答不好。

因此批次 3 = 新增一个**确定性、零依赖、零 LLM** 的图遍历工具 `graph.py`，与 `outline.py` 并排，作为 `tools/` 里 agent 取证工具的一员。它遍历 wiki 中**已存在的** wikilink，回答连通性/多跳问题；**只报连通路径，不断言法律关系**——具体含义仍由 agent/人读相关页的逐字锚点确认。

**谁用**：agent，不是用户。用户只用自然语言提问（"甲和丙有关系吗"），agent 在 qa.md 取证流程里自行调用 `graph.py`；用户永不敲命令，仅在安全阀 ④（无法判定）时复核 agent 给出的路径。

## 复用现有解析（与 lint 保持一致）

`graph.py` 镜像 `lint.py` 对链接/别名的理解（同样的正则，保证 graph 与 lint 对"边"的认定一致），但保持 `graph.py` 为独立 stdlib 模块（像 `outline.py` 一样自包含，不 import lint）：

- `WIKILINK_RE = re.compile(r"\[\[([^\]\n]+?)\]\]")`
- 目标归一：`m.group(1).split("|")[0].split("#")[0].strip()`（剥离 `|显示文字` 与 `#页内标题`）
- `_frontmatter(text)`：取 `---...---` 之间的 frontmatter 文本
- `ALIASES_RE = re.compile(r"aliases:\s*\[(.*?)\]")`：解析别名
- 新增 `类型` 解析：`TYPE_RE = re.compile(r"^\s*类型:\s*(\S+)", re.M)`（在 frontmatter 内匹配）

## 图模型

- **节点** = 实体页，即 frontmatter `类型 ∈ {案件主体, 法律关系, 法律事实}`。
  - `index.md`/`log.md` 无 `类型` → 天然排除；`时间线/总览`（`类型: 时间线`）排除。二者会链接几乎所有页，若入图会把任意两点短接成 2 跳，令多跳查询失效——故必须排除。
- **节点 id** = 文件名 stem（wikilink 按 stem 全局解析，与 Obsidian、lint 一致）。
- **别名映射** `alias_to_stem: dict[str, str]`：由每个实体页的 stem 与其 `aliases` frontmatter 构建（stem→自身，各别名→该 stem）。用于把 `[[晨山]]` 解析到 `北京晨山`。
- **边** = 对每个实体页，扫其正文所有 `[[wikilink]]`，归一并经别名映射解析到目标 stem；**若目标也是实体节点且 ≠ 自身**，加一条**无向**边。
  - 指向被排除页（index/log/时间线）或不存在页的链接：直接忽略（真死链由 lint 单独负责，graph 不重复报错）。
  - 无向：因为 `主体`/`法律关系` 页大多是出边来源、`法律事实` 页常是入边汇点，只有无向才能让路径穿过事实节点连通两个主体。

## 两个命令（输出 JSON，风格对齐 outline.py）

调用形式 `python graph.py <案件根> <子命令> ...`（`<案件根>` 下的 `wiki/` 为图源）：

1. `neighbors "<页名或别名>"` → 该节点的直接邻居：
   ```json
   {"node": "北京晨山", "类型": "案件主体",
    "neighbors": [{"page": "民间借贷纠纷", "类型": "法律关系"},
                  {"page": "甲向乙借款50万元", "类型": "法律事实"}]}
   ```
   邻居按 `page` 名排序，保证输出确定。

2. `path "<A>" "<B>"` → 无向 BFS 最短连通路径：
   ```json
   {"from": "甲", "to": "丙", "connected": true, "hops": 2,
    "path": [{"page": "甲", "类型": "案件主体"},
             {"page": "某合同关系", "类型": "法律关系"},
             {"page": "丙", "类型": "案件主体"}]}
   ```
   无连通路径 → `{"from": "甲", "to": "丙", "connected": false}`。
   返回**一条**最短路径（确定连通性 + 给出可核链条即可）；多路径/子图导出**不做**（YAGNI）。

3. 错误处理：页名（经别名解析后）不在图中 → `{"error": "未找到页面: 晨山"}`，退出码非 0，**不抛栈**。缺 `wiki/` 目录同样返回清晰 error JSON。

BFS 决定性：邻接表按节点名排序遍历，最短路径在并列时取字典序最小，保证同一 wiki 多次运行结果一致。

## 集成

- **qa.md**：在"第一步 · 多路并行取证"的"1. wiki 路"里补一句——遇到**关系/多跳类**问题（"X 与 Y 有无关系""X 牵涉哪些事实"），用 `python <SKILL_DIR>/tools/graph.py <案件根> path/neighbors ...` 确定性地走 wiki 图，替代人肉追链；拿到路径后仍读沿途页的锚点取证。零依赖、始终可用。
- **不改** lint、rag、outline，不改 rag-retriever/makeitdown，不加任何依赖。

## 测试（`tools/test_graph.py`，镜像 `test_outline.py` 布局）

用 `tmp_path` 搭一个最小 wiki 夹具（含 index.md、时间线、两个主体、一个法律关系、一个法律事实，带 aliases），断言：

- 只有实体页入图：index.md / log.md / 时间线 **不是**节点（不出现在任何 neighbors/path）。
- 别名解析：`neighbors 晨山` 命中 `北京晨山`（别名→canonical）。
- 无向邻居：主体页 `[[事实]]` 使"主体—事实"互为邻居。
- 多跳 `path`：构造 甲—关系—丙，`path 甲 丙` 返回 `connected:true, hops:2` 且路径含中间关系页。
- 不连通：孤立的两页 → `connected:false`。
- 链接归一：`[[北京晨山|晨山]]` 与 `[[北京晨山#某节]]` 都解析到 `北京晨山`。
- 指向 index/时间线的链接被忽略（不产生边、不短接路径）。
- 未知页 → `{"error": ...}` 且退出码非 0；缺 `wiki/` → error JSON。

## 交付定义

- 新增 `lawiki/skill/lawiki/tools/graph.py`（stdlib-only，含 `neighbors`/`path` 两子命令 + CLI）。
- 新增 `lawiki/skill/lawiki/tools/test_graph.py`。
- `lawiki/skill/lawiki/references/qa.md` 补一句 graph.py 用法。
- 现有 lawiki 测试（lint/outline/rag）全部仍通过。
