# -*- coding: utf-8 -*-
"""lint 回归测试：锁住"归一化只消格式噪声、绝不放过真错"，覆盖 check 五类与 extract。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lint import scan_case, get_pairs, scan_answer, _load_skips  # noqa: E402


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _anchor_case(tmp_path: Path, source: str, snippet: str, rel: str = "_md/a.md") -> Path:
    _write(tmp_path / rel, source)
    _write(tmp_path / "wiki" / "p.md", f"- 事实 〔来源: {rel}：「{snippet}」〕\n")
    return tmp_path


# ---- ① 锚点存在 ----

def test_exact_match_passes(tmp_path):
    _, viol, *_ = scan_case(_anchor_case(tmp_path, "甲向乙借款500,000元。", "甲向乙借款500,000元"))
    assert viol == []


def test_wrong_number_is_flagged(tmp_path):
    _, viol, *_ = scan_case(_anchor_case(tmp_path, "甲向乙借款500,000元。", "甲向乙借款500,001元"))
    assert len(viol) == 1


def test_formatting_noise_passes(tmp_path):
    src = '| **甲** 向乙\n借款 500，000 元 <td>（RMB）</td> |'
    _, viol, *_ = scan_case(_anchor_case(tmp_path, src, "甲向乙借款500,000元（RMB）"))
    assert viol == []


def test_ellipsis_bridges_gap(tmp_path):
    _, viol, *_ = scan_case(_anchor_case(tmp_path, "甲方……中间很多字……乙方签字。", "甲方…乙方签字"))
    assert viol == []


def test_out_of_order_fragments_flagged(tmp_path):
    _, viol, *_ = scan_case(_anchor_case(tmp_path, "乙方在前，甲方在后。", "甲方…乙方"))
    assert len(viol) == 1


def test_missing_source_file_is_flagged(tmp_path):
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/missing.md：「随便」〕\n")
    _, viol, *_ = scan_case(tmp_path)
    assert len(viol) == 1


def test_nested_quotes_in_snippet_pass(tmp_path):
    # 判决书常引当事人陈述：片段内嵌套「」。锚点闭合符是两字符序列「」〕」，
    # 片段中不跟〕的孤立」不得提前截断——锁住此行为，防未来改正则时退化。
    src = "被告到庭后称「我不会还款」并当场离开。"
    total, viol, *_ = scan_case(_anchor_case(tmp_path, src, "被告到庭后称「我不会还款」并当场离开"))
    assert total == 1 and viol == []


def test_nested_quotes_at_snippet_end_pass(tmp_path):
    # 片段以嵌套引号收尾（……」」〕）：首个」后跟」而非〕，不得在此截断。
    src = "被告到庭后称「我不会还款」。"
    total, viol, *_ = scan_case(_anchor_case(tmp_path, src, "被告到庭后称「我不会还款」"))
    assert total == 1 and viol == []


def test_two_anchors_same_line_parsed_separately(tmp_path):
    # 同一行两个锚点必须各自独立匹配（非贪婪不吞到行尾）：好锚点过、坏锚点抓。
    _write(tmp_path / "_md" / "a.md", "甲借款50万元。乙提供担保。")
    _write(tmp_path / "wiki" / "p.md",
           "- 甲借款〔来源: _md/a.md：「甲借款50万元」〕，乙担保〔来源: _md/a.md：「丙提供担保」〕\n")
    total, viol, *_ = scan_case(tmp_path)
    assert total == 2 and len(viol) == 1 and "丙提供担保" in viol[0]


# ---- ② 死链 ----

def test_dead_wikilink_flagged(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "甲.md", "见 [[不存在的页]]\n")
    _, viol, *_ = scan_case(tmp_path)
    assert any("死链" in v for v in viol)


def test_wikilink_resolves_by_alias(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "无锡尚惟.md", "---\naliases: [尚惟]\n---\n# 无锡尚惟\n")
    _write(tmp_path / "wiki" / "p.md", "见 [[尚惟|尚惟]]\n")
    _, viol, *_ = scan_case(tmp_path)
    assert viol == []


# ---- ③ 时间线顺序 ----

def test_timeline_out_of_order_flagged(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "时间线" / "总览.md",
           "# 时间线\n- 2022 年 6 月 9 日 甲\n- 2021 年 5 月 乙\n")
    _, viol, *_ = scan_case(tmp_path)
    assert any("乱序" in v for v in viol)


def test_timeline_in_order_passes(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "时间线" / "总览.md",
           "# 时间线\n- 公司设立时 甲\n- 2021 年 5 月 乙\n- 2022 年 6 月 9 日 丙\n")
    _, viol, *_ = scan_case(tmp_path)
    assert viol == []


def test_timeline_year_only_after_full_date_not_flagged(tmp_path):
    # A year-only entry is ambiguous within its year, so it must NOT be reported
    # as out-of-order after a same-year full date (the old 0-padding falsely did).
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "时间线" / "总览.md",
           "# 时间线\n- 2021 年 5 月 3 日 甲\n- 2021 年 乙\n")
    _, viol, *_ = scan_case(tmp_path)
    assert viol == []


def test_timeline_year_regression_still_flagged_at_mixed_precision(tmp_path):
    # A genuine year regression must still be caught even when precision differs.
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "时间线" / "总览.md",
           "# 时间线\n- 2022 年 甲\n- 2021 年 5 月 乙\n")
    _, viol, *_ = scan_case(tmp_path)
    assert any("乱序" in v for v in viol)


# ---- ④ 勾稽闭合 ----

def test_closure_ok_passes(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "p.md", "> [!check] 128,205 + 128,205 + 25,641 == 282,051\n")
    _, viol, *_ = scan_case(tmp_path)
    assert viol == []


def test_closure_mismatch_flagged(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "p.md", "> [!check] 1,000 + 1 == 1,002\n")
    _, viol, *_ = scan_case(tmp_path)
    assert any("勾稽不符" in v for v in viol)


def test_closure_ignores_trailing_comment(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "p.md",
           "> [!check] 1,749,287 + 53,824 == 1,803,111 （增资前+新增=增资后）\n")
    _, viol, *_ = scan_case(tmp_path)
    assert viol == []


# ---- ⑤ 覆盖率：log.md skip 条目解析 ----

def test_load_skips_basic(tmp_path):
    _write(tmp_path / "wiki" / "log.md",
           "# 操作日志\n\n## [2026-07-12] skip | _md/a.md\n- 原因：红线对比版\n")
    assert _load_skips(tmp_path) == {"_md/a.md": True}


def test_load_skips_missing_reason(tmp_path):
    _write(tmp_path / "wiki" / "log.md", "## [2026-07-12] skip | _md/a.md\n")
    assert _load_skips(tmp_path) == {"_md/a.md": False}


def test_load_skips_empty_reason_is_missing(tmp_path):
    _write(tmp_path / "wiki" / "log.md", "## [2026-07-12] skip | _md/a.md\n- 原因：  \n")
    assert _load_skips(tmp_path) == {"_md/a.md": False}


def test_load_skips_reason_scoped_to_entry(tmp_path):
    # 原因行只归属其上方最近的 skip 条目；隔了别的 ## 条目不得串账。
    _write(tmp_path / "wiki" / "log.md",
           "## [2026-07-12] skip | _md/a.md\n"
           "## [2026-07-12] ingest | b.md\n- 原因：不该算到 a 头上\n")
    assert _load_skips(tmp_path) == {"_md/a.md": False}


def test_load_skips_duplicate_entries_reason_or(tmp_path):
    # 同一路径多条登记：任一条带原因即视为有原因（append-only 下补一条即修复缺原因）。
    _write(tmp_path / "wiki" / "log.md",
           "## [2026-07-10] skip | _md/a.md\n"
           "## [2026-07-12] skip | _md/a.md\n- 原因：补充原因\n")
    assert _load_skips(tmp_path) == {"_md/a.md": True}


def test_load_skips_no_log(tmp_path):
    assert _load_skips(tmp_path) == {}


def test_load_skips_backslash_normalized(tmp_path):
    _write(tmp_path / "wiki" / "log.md",
           "## [2026-07-12] skip | _md\\a.md\n- 原因：x\n")
    assert _load_skips(tmp_path) == {"_md/a.md": True}


def test_load_skips_halfwidth_colon_accepted(tmp_path):
    _write(tmp_path / "wiki" / "log.md",
           "## [2026-07-12] skip | _md/a.md\n- 原因: 半角冒号也行\n")
    assert _load_skips(tmp_path) == {"_md/a.md": True}


# ---- ⑤ 覆盖率（警告） ----

def test_unresolved_source_warns(tmp_path):
    _write(tmp_path / "_md" / "cited.md", "甲乙")
    _write(tmp_path / "_md" / "draft.md", "草稿")
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/cited.md：「甲乙」〕\n")
    _, viol, warn, cov = scan_case(tmp_path)
    assert viol == []
    assert warn == ["[未处置] _md/draft.md"]
    assert cov == {"total": 2, "cited": 1, "skipped": 0, "unresolved": 1}


def test_registered_skip_silences_warning(tmp_path):
    _write(tmp_path / "_md" / "cited.md", "甲乙")
    _write(tmp_path / "_md" / "draft.md", "草稿")
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/cited.md：「甲乙」〕\n")
    _write(tmp_path / "wiki" / "log.md",
           "## [2026-07-12] skip | _md/draft.md\n- 原因：红线对比版\n")
    _, viol, warn, cov = scan_case(tmp_path)
    assert viol == [] and warn == []
    assert cov == {"total": 2, "cited": 1, "skipped": 1, "unresolved": 0}


def test_skip_without_reason_warns(tmp_path):
    _write(tmp_path / "_md" / "draft.md", "草稿")
    _write(tmp_path / "wiki" / "p.md", "占位页\n")
    _write(tmp_path / "wiki" / "log.md", "## [2026-07-12] skip | _md/draft.md\n")
    _, viol, warn, cov = scan_case(tmp_path)
    assert viol == []
    assert warn == ["[跳过无原因] _md/draft.md"]
    assert cov == {"total": 1, "cited": 0, "skipped": 1, "unresolved": 0}


def test_cited_wins_over_registration(tmp_path):
    # 已引用文件即使被登记跳过（且无原因）也归"已引用"，零警告——引用优先，登记冗余无害。
    _write(tmp_path / "_md" / "cited.md", "甲乙")
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/cited.md：「甲乙」〕\n")
    _write(tmp_path / "wiki" / "log.md", "## [2026-07-12] skip | _md/cited.md\n")
    _, viol, warn, cov = scan_case(tmp_path)
    assert viol == [] and warn == []
    assert cov == {"total": 1, "cited": 1, "skipped": 0, "unresolved": 0}


def test_check_cli_prints_coverage_summary(tmp_path, capsys):
    from lint import main
    _write(tmp_path / "_md" / "cited.md", "甲乙")
    _write(tmp_path / "_md" / "draft.md", "草稿")
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/cited.md：「甲乙」〕\n")
    assert main(["lint.py", "check", str(tmp_path)]) == 0  # 仅覆盖率警告不影响退出码
    out = capsys.readouterr().out
    assert "覆盖率：2 源文件 | 已引用 1 | 登记跳过 0 | 未处置 1" in out
    assert "[未处置] _md/draft.md" in out


def test_stale_skip_entry_silently_ignored(tmp_path):
    # 登记路径在 _md/ 中不存在：不发警告、不进统计（真正漏网的文件仍会以未处置暴露）。
    _write(tmp_path / "_md" / "a.md", "甲乙")
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/a.md：「甲乙」〕\n")
    _write(tmp_path / "wiki" / "log.md",
           "## [2026-07-12] skip | _md/早已删除.md\n- 原因：x\n")
    _, viol, warn, cov = scan_case(tmp_path)
    assert viol == [] and warn == []
    assert cov == {"total": 1, "cited": 1, "skipped": 0, "unresolved": 0}


# ---- extract ----

def test_extract_basic_pair(tmp_path):
    _write(tmp_path / "wiki" / "p.md", "- 蓝驰增资 3000 万 〔来源: _md/a.md：「蓝驰以叁仟万元」〕\n")
    pairs = get_pairs(tmp_path)
    assert len(pairs) == 1
    assert pairs[0]["claim"] == "蓝驰增资 3000 万"
    assert pairs[0]["source"] == "_md/a.md" and pairs[0]["quote"] == "蓝驰以叁仟万元"


def test_extract_skips_heading_and_analysis(tmp_path):
    body = ("# 标题 〔来源: _md/a.md：「不该抽」〕\n"
            "> [!note] 分析\n"
            "> 推断如此 〔来源: _md/a.md：「也不该抽」〕\n"
            "- 真事实 〔来源: _md/a.md：「该抽」〕\n")
    _write(tmp_path / "wiki" / "p.md", body)
    pairs = get_pairs(tmp_path)
    assert len(pairs) == 1 and pairs[0]["quote"] == "该抽"


def test_extract_per_anchor_subclaim(tmp_path):
    _write(tmp_path / "wiki" / "p.md",
           "- 增资前 X 〔来源: _md/a.md：「X」〕；增资后 Y 〔来源: _md/b.md：「Y」〕\n")
    pairs = get_pairs(tmp_path)
    by_quote = {p["quote"]: p["claim"] for p in pairs}
    assert by_quote["X"] == "增资前 X" and by_quote["Y"] == "增资后 Y"


# ---- answer 交付闸门（问答铁规：锚点全验 + 闭世界 + 整篇兜底） ----

def _draft_case(tmp_path: Path, draft_text: str,
                src: str = "本案欠款金额为人民币50,000元，借款人为张三。") -> tuple[Path, Path]:
    _write(tmp_path / "_md" / "借条.md", src)
    draft = tmp_path / "draft.md"
    _write(draft, draft_text)
    return tmp_path, draft


def test_answer_valid_anchor_passes(tmp_path):
    root, draft = _draft_case(
        tmp_path, "欠款本金为 5 万元。〔来源: _md/借条.md：「欠款金额为人民币50,000元」〕\n")
    total, viol = scan_answer(root, draft)
    assert total == 1 and viol == []


def test_answer_wrong_snippet_flagged(tmp_path):
    root, draft = _draft_case(
        tmp_path, "欠款本金。〔来源: _md/借条.md：「欠款金额为人民币50,001元」〕\n")
    _, viol = scan_answer(root, draft)
    assert len(viol) == 1 and "片段不符" in viol[0]


def test_answer_missing_source_flagged(tmp_path):
    root, draft = _draft_case(tmp_path, "欠款本金。〔来源: _md/不存在.md：「任意」〕\n")
    _, viol = scan_answer(root, draft)
    assert len(viol) == 1 and "缺文件" in viol[0]


def test_answer_non_md_anchor_flagged_closed_world(tmp_path):
    # 引用真实存在、但在证据宇宙（_md/）之外的文件——锚点存在性过，闭世界抓。
    root, draft = _draft_case(tmp_path, "结论。〔来源: wiki/页.md：「已有结论」〕\n")
    _write(root / "wiki" / "页.md", "已有结论")
    _, viol = scan_answer(root, draft)
    assert len(viol) == 1 and "闭世界" in viol[0]


def test_answer_traversal_anchor_flagged_closed_world(tmp_path):
    # `_md/../wiki/页.md` 用文件系统解析能穿到 _md/ 之外并命中真实存在的文件——
    # 旧的纯前缀串检查会被这种穿越骗过（以 "_md/" 开头就放行）；闭世界必须抓住它。
    root, draft = _draft_case(tmp_path, "结论。〔来源: _md/../wiki/页.md：「已有结论」〕\n")
    _write(root / "wiki" / "页.md", "已有结论")
    _, viol = scan_answer(root, draft)
    assert len(viol) == 1 and "闭世界" in viol[0]


def test_answer_bare_prose_flagged(tmp_path):
    # 整篇裸答：有实质内容、零锚点、未明示「未找到」——打回。
    root, draft = _draft_case(tmp_path, "被告应偿还 5 万元。\n")
    _, viol = scan_answer(root, draft)
    assert len(viol) == 1 and "裸答" in viol[0]


def test_answer_not_found_phrase_passes(tmp_path):
    root, draft = _draft_case(tmp_path, "未在本案材料中找到相关约定。\n")
    total, viol = scan_answer(root, draft)
    assert total == 0 and viol == []


def test_answer_pure_analysis_passes(tmp_path):
    # 全篇只有标题 + callout（显式标注的分析）——不是事实陈述，放行。
    root, draft = _draft_case(
        tmp_path, "## 分析\n\n> [!note] 分析（非本案证据）\n> 通常此类合同适用总价包干。\n")
    total, viol = scan_answer(root, draft)
    assert total == 0 and viol == []


def test_answer_cli_exit_codes(tmp_path):
    from lint import main
    root, draft = _draft_case(tmp_path, "未在本案材料中找到相关约定。\n")
    assert main(["lint.py", "answer", str(root), str(draft)]) == 0
    bad = tmp_path / "bad.md"
    _write(bad, "裸答无锚点。\n")
    assert main(["lint.py", "answer", str(root), str(bad)]) == 1
    assert main(["lint.py", "answer", str(root), str(tmp_path / "无此文件.md")]) == 2


def test_answer_undecodable_draft_returns_2_not_crash(tmp_path):
    # 无法解码的草稿应是"读取失败"（退出码 2），而不是让 UnicodeDecodeError
    # 冒出去、被协议误读成"违规打回"（退出码 1）。
    from lint import main
    root, _draft = _draft_case(tmp_path, "未在本案材料中找到相关约定。\n")
    bad = tmp_path / "bad_encoding.md"
    bad.write_bytes(b"\xff\xfe\x00bad")
    assert main(["lint.py", "answer", str(root), str(bad)]) == 2
