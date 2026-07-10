# -*- coding: utf-8 -*-
"""Stop hook 回归测试：transcript 解析 + 两检决策（零误报边界）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stop_hook import decide, last_assistant_text  # noqa: E402


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _transcript(tmp_path: Path, *entries: dict) -> str:
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries),
                 encoding="utf-8")
    return str(p)


def _assistant(text: str) -> dict:
    return {"type": "assistant",
            "message": {"content": [{"type": "text", "text": text}]}}


# ---- transcript 解析 ----

def test_last_assistant_wins_and_junk_ignored(tmp_path):
    path = _transcript(
        tmp_path,
        _assistant("第一条回复"),
        {"type": "user", "message": {"content": "提问"}},
        _assistant("最后一条回复"))
    # 混入坏行也不崩
    with open(path, "a", encoding="utf-8") as f:
        f.write("\nnot json at all")
    assert last_assistant_text(path) == "最后一条回复"


def test_missing_transcript_returns_empty(tmp_path):
    assert last_assistant_text(str(tmp_path / "无此文件.jsonl")) == ""


# ---- 决策（root 需有 _md/ 源文件） ----

def _root(tmp_path: Path) -> Path:
    _write(tmp_path / "_md" / "借条.md", "本案欠款金额为人民币50,000元。")
    return tmp_path


def test_no_anchor_reply_never_blocked(tmp_path):
    assert decide(_root(tmp_path), "帮你把文件改好了。") is None


def test_valid_anchor_passes(tmp_path):
    reply = "欠款 5 万元。〔来源: _md/借条.md：「欠款金额为人民币50,000元」〕"
    assert decide(_root(tmp_path), reply) is None


def test_broken_anchor_blocked(tmp_path):
    reply = "欠款。〔来源: _md/借条.md：「欠款金额为人民币50,001元」〕"
    reason = decide(_root(tmp_path), reply)
    assert reason and "片段不符" in reason


def test_outside_md_anchor_blocked(tmp_path):
    root = _root(tmp_path)
    _write(root / "wiki" / "页.md", "已有结论")
    reason = decide(root, "结论。〔来源: wiki/页.md：「已有结论」〕")
    assert reason and "闭世界" in reason
