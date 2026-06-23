# lawiki 扩展设计:单文档结构导航层(outline)

状态:设计已定稿,施工中。分支 `feat/rag-qa-extension`(lawiki)。

## 背景与定位

受 PageIndex(vectorless、reasoning-based RAG)启发。它给每份长文档建一棵"目录树",让 LLM 沿结构推理式导航,核心命题是 **"similarity ≠ relevance"**——语义相似 ≠ 真正相关,向量检索会漏掉"措辞不同但实际相关"的条款。

lawiki 已有两层:`_md/`(原文,忠实但无结构)与 `wiki/`(跨文档、按法律意义综合)。中间缺一层——**单文档自身的结构地图**。本扩展补上它:

```
原始资料 / _md        原文(忠实,无结构)
   ▲ outline          ★新增:每份 _md 的标题树(忠实于文档布局的导航地图)
wiki                  综合知识(跨文档,按法律意义)
```

## 解决什么(价值定位)

主要堵 **"遗漏类"错误(C)**——相关内容确在文档里,却没被检索到 / 没看到 / 没入 wiki。这是当前体系最弱的一类(判官只抓"漂移",`verification.md` 明确写"抓不了遗漏"),也是对律师最危险的一类(你不知道自己漏了什么)。

- **建库时**:agent 沿每份文档骨架逐节走,减少"整节被跳过"→ wiki 更完整。
- **提问时**:问法与原文用词不同(如问"违约责任",合同写"第八条 责任")时,向量会漏;outline 让 agent 直接导航到条款定位 → 减少"查不到→误判没有/矛盾"。
- **验证时**:溯源从扁平锚点升级为"可沿文档骨架导航",并能发现同文档内的相反条款。
- **降级时**:没装 rag-retriever / 无 embedding 时,outline 当结构检索兜底。

不重复降低已覆盖的编造(A)/漂移(B)——那是锚点+lint+判官+交叉验证的活。

## 落点(关键:只动 lawiki)

| 项目 | 改动 | 理由 |
|---|---|---|
| **lawiki** | 新增 `tools/outline.py`(stdlib) + qa.md/SKILL.md 接线 | outline 要当"无 RAG 兜底",必须在零依赖核心里,故**不**放 rag-retriever |
| makeitdown | 不动 | 已在 `_md` 输出 `#` 标题,解析即可 |
| rag-retriever | 不动 | 纯新增、不碰其契约;outline 与向量检索互补 |

**不破坏联动**:纯新增、零新依赖、不改任何现有 CLI/JSON 契约,旧 bundle 兼容;反而增强降级能力。

## `tools/outline.py`(stdlib,零依赖)

解析 markdown ATX 标题(`#`..`######`)成嵌套树。

- `parse_outline(text) -> list[node]`,`node = {title, level, line_start, line_end, children:[…]}`;`line_end` = 该节延伸到下一个同级或更高级标题前(或文末)。
- 跳过围栏代码块(``` ```)内的 `#`,避免误判。
- CLI:`python outline.py <案件根|文件>` → 输出 JSON。目录则遍历 `_md/`,每项 `{source: "_md/相对POSIX路径", outline: [树]}`(source 形式与锚点一致)。
- 仅标准库;Python 3.11+(与 lint 一致)。

## 接线

- `qa.md`:第一步取证加"**先查 outline 定位章节,再 RAG/grep 取证**";并写明"RAG 不可用时 outline 作降级检索"。
- `SKILL.md`:references 指针加 `outline`;ingest 可用它做"逐节覆盖"以减少遗漏。
- 不改 lint 核心;outline 是旁路工具,不进 lint 闸门。

## 边界(诚实)

- 质量取决于 makeitdown 的标题识别:扫描件/OCR 乱的文档标题可能不准 → outline 退化;**无结构文本(聊天记录/口供流水)无标题骨架,帮不上**。价值集中在结构良好的高价值法律文书(合同/判决书/章程/决议)。
- 它是导航/兜底,不替代 wiki 的综合,也不替代 RAG 的语义召回。

## 验证

stdlib unittest(`tools/test_outline.py`):标题嵌套、行区间、代码围栏跳过、多文件遍历、相对路径形式。
