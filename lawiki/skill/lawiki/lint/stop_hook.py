#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claude Code Stop hook（可选加硬）：校验最后一条回复里的引用锚点。

只跑零误报的两检——锚点逐字存在 + 闭世界（指向本案 _md/）。不跑「整篇兜底」：
hook 分不清案件问答与日常对话，无锚点的回复一律放行；「裸答必须明示未找到」
的密度约束由协议里的 `lint.py answer` 全量闸门负责。配置见 references/setup.md。

协议：stdin 收 hook JSON（transcript_path / cwd / stop_hook_active）；违规时
stdout 输出 {"decision":"block","reason":…} 让 agent 修复后重答。三条放行铁则：
stop_hook_active（防死循环）、cwd 无 _md/（不在案件目录）、transcript 读不到/
读不懂（hook 只能更严，不能误伤）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lint import ANCHOR_RE, check_answer_anchors  # noqa: E402


def last_assistant_text(transcript_path: str) -> str:
    """从 Claude Code 的 JSONL transcript 抽最后一条 assistant 文本。
    假设：Stop 触发时最终可见回复必含文本块，因此"取最后一条带文本的 assistant
    entry"是安全的兜底；即便遇到病态的纯工具调用尾巴，最坏也只是把上一条（较旧）
    回复再校验一遍——stop_hook_active 挡住了由此产生的死循环风险。"""
    p = Path(transcript_path)
    if not p.is_file():
        return ""
    text = ""
    with p.open(encoding="utf-8", errors="replace") as f:
        for line in f:  # 流式逐行：transcript 随会话线性膨胀到数 MB，每次 Stop 都要读，别整读进内存
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if entry.get("type") != "assistant":
                continue
            content = (entry.get("message") or {}).get("content")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = [b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") == "text"]
                if parts:
                    text = "\n".join(parts)
    return text


def decide(root: Path, reply: str) -> str | None:
    """返回 block 理由；None = 放行。无锚点直接放行（零误报边界）。
    用 lint 的 ANCHOR_RE 判"有无锚点"——手写字面量若与 lint 的格式漂移，
    hook 会静默永久放行（与坏掉不可区分），这正是它要防的失效模式。"""
    if not ANCHOR_RE.search(reply):
        return None
    _total, violations = check_answer_anchors(root, reply, "<回复>")
    if not violations:
        return None
    return ("回复中的引用锚点未通过确定性校验（须逐字存在且指向本案 _md/）。"
            "请修正后重新作答——只许把引用改真实、把断言改忠实，绝不为过校验编造：\n"
            + "\n".join(violations))


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    if not isinstance(data, dict):
        # 合法 JSON 但不是对象（如 `[1]`）——data.get(...) 会因 AttributeError 崩溃；
        # hook 的契约是"读不懂就放行、绝不因垃圾输入报错刷屏"，同垃圾 JSON 一视同仁。
        return 0
    if data.get("stop_hook_active"):
        return 0  # 已在 hook 触发的重答里，不再拦：防死循环
    root = Path(data.get("cwd") or ".")
    if not (root / "_md").is_dir():
        return 0  # 不在案件目录，与本 hook 无关
    reason = decide(root, last_assistant_text(data.get("transcript_path", "")))
    if reason:
        print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
