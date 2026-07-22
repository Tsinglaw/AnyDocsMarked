# Changelog

本项目遵循语义化版本。尚未发布的变化记录在 `Unreleased`。

## Unreleased

## 1.7.0 - 2026-07-21

### Security / trust boundaries

- `lint answer` 改为逐行拒绝未锚定事实；普通 blockquote 不再被当作分析豁免。
- Wiki 页面同样逐行拒绝未锚定事实；导航 wikilink 与确定性勾稽行保留结构豁免。
- 来源 frontmatter 引入 `provenance_version: 1`；缺版本的旧转换可读但不能通过完成闸门。
- wiki 与回答锚点统一限制在案件 `_md/` 内，拒绝 `..` 与符号链接逃逸。
- makeitdown 为原件与转换正文写入 SHA-256；lawiki 在 ingest 闸门复核两者。
- OCR 与标题重建 LLM 共用显式外部处理同意。
- 兼容 OpenAI 的远端 embedding 需要 `RAG_CLOUD_CONSENT=1`，且报错明确说明外传范围。
- JSON sidecar 与 Markdown/report 输出改为原子替换，降低崩溃截断风险。
- 有效锚点不再豁免其后同一行夹带的无来源事实；Wiki 首标题只豁免页面身份或固定结构标题。
- 源级对账不再把缺少非空理由的 skip 视为已处置。

### Changed

- 覆盖率未处置或 skip 无理由时，`lint check` 返回非零。
- 安装器在环境或组件安装失败时返回非零。
- `RAG_MIN_SCORE` 拒绝非有限值和 `[0,1]` 外数值。
- 搜索结果稳定包含 `parent_text`，关闭或旧索引时为 `null`。
- 删除未实现却被公开配置的 `cloud` rerank；端到端验收覆盖原始资料到带锚点回答。
- bundle 构建强制校验顶层版本与两个 Python 包版本一致。
- 依赖锁将 Pillow 提升到 12.3.0+、MCP 提升到 1.28.1+，修复发布前漏洞审计发现的已知安全问题。

## 1.6.0 - 2026-07-17

- 增加 parent-context（small-to-big）检索。
- 增加向量通道相关度阈值 `RAG_MIN_SCORE`。
