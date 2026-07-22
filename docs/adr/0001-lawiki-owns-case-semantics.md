---
status: accepted
---

# 案件语义整理归 Lawiki 所有

来源文本同时供 Lawiki 组织案件知识和 RAG Retriever 独立召回；不在来源文本与二者之间增加独立的结构化抽取层，因为它会重复 Lawiki 的语义所有权、增加需要同步的派生状态，并把 Agent 工作推向固定流水线。若未来出现真实的机器消费需求，只在通过校验的 Wiki 下游提供可选的确定性导出，不让导出参与 build 或 answer 主链路。
