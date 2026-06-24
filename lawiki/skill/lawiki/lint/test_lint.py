# -*- coding: utf-8 -*-
"""lint 回归测试：锁住"归一化只消格式噪声、绝不放过真错"，覆盖 check 五类与 extract。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from lint import scan_case, get_pairs  # noqa: E402


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _anchor_case(tmp_path: Path, source: str, snippet: str, rel: str = "_md/a.md") -> Path:
    _write(tmp_path / rel, source)
    _write(tmp_path / "wiki" / "p.md", f"- 事实 〔来源: {rel}：「{snippet}」〕\n")
    return tmp_path


# ---- ① 锚点存在 ----

def test_exact_match_passes(tmp_path):
    _, viol, _ = scan_case(_anchor_case(tmp_path, "甲向乙借款500,000元。", "甲向乙借款500,000元"))
    assert viol == []


def test_wrong_number_is_flagged(tmp_path):
    _, viol, _ = scan_case(_anchor_case(tmp_path, "甲向乙借款500,000元。", "甲向乙借款500,001元"))
    assert len(viol) == 1


def test_formatting_noise_passes(tmp_path):
    src = '| **甲** 向乙\n借款 500，000 元 <td>（RMB）</td> |'
    _, viol, _ = scan_case(_anchor_case(tmp_path, src, "甲向乙借款500,000元（RMB）"))
    assert viol == []


def test_ellipsis_bridges_gap(tmp_path):
    _, viol, _ = scan_case(_anchor_case(tmp_path, "甲方……中间很多字……乙方签字。", "甲方…乙方签字"))
    assert viol == []


def test_out_of_order_fragments_flagged(tmp_path):
    _, viol, _ = scan_case(_anchor_case(tmp_path, "乙方在前，甲方在后。", "甲方…乙方"))
    assert len(viol) == 1


def test_missing_source_file_is_flagged(tmp_path):
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/missing.md：「随便」〕\n")
    _, viol, _ = scan_case(tmp_path)
    assert len(viol) == 1


# ---- ② 死链 ----

def test_dead_wikilink_flagged(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "甲.md", "见 [[不存在的页]]\n")
    _, viol, _ = scan_case(tmp_path)
    assert any("死链" in v for v in viol)


def test_wikilink_resolves_by_alias(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "无锡尚惟.md", "---\naliases: [尚惟]\n---\n# 无锡尚惟\n")
    _write(tmp_path / "wiki" / "p.md", "见 [[尚惟|尚惟]]\n")
    _, viol, _ = scan_case(tmp_path)
    assert viol == []


# ---- ③ 时间线顺序 ----

def test_timeline_out_of_order_flagged(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "时间线" / "总览.md",
           "# 时间线\n- 2022 年 6 月 9 日 甲\n- 2021 年 5 月 乙\n")
    _, viol, _ = scan_case(tmp_path)
    assert any("乱序" in v for v in viol)


def test_timeline_in_order_passes(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "时间线" / "总览.md",
           "# 时间线\n- 公司设立时 甲\n- 2021 年 5 月 乙\n- 2022 年 6 月 9 日 丙\n")
    _, viol, _ = scan_case(tmp_path)
    assert viol == []


def test_timeline_year_only_after_full_date_not_flagged(tmp_path):
    # A year-only entry is ambiguous within its year, so it must NOT be reported
    # as out-of-order after a same-year full date (the old 0-padding falsely did).
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "时间线" / "总览.md",
           "# 时间线\n- 2021 年 5 月 3 日 甲\n- 2021 年 乙\n")
    _, viol, _ = scan_case(tmp_path)
    assert viol == []


def test_timeline_year_regression_still_flagged_at_mixed_precision(tmp_path):
    # A genuine year regression must still be caught even when precision differs.
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "时间线" / "总览.md",
           "# 时间线\n- 2022 年 甲\n- 2021 年 5 月 乙\n")
    _, viol, _ = scan_case(tmp_path)
    assert any("乱序" in v for v in viol)


# ---- ④ 勾稽闭合 ----

def test_closure_ok_passes(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "p.md", "> [!check] 128,205 + 128,205 + 25,641 == 282,051\n")
    _, viol, _ = scan_case(tmp_path)
    assert viol == []


def test_closure_mismatch_flagged(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "p.md", "> [!check] 1,000 + 1 == 1,002\n")
    _, viol, _ = scan_case(tmp_path)
    assert any("勾稽不符" in v for v in viol)


def test_closure_ignores_trailing_comment(tmp_path):
    _write(tmp_path / "_md" / "a.md", "x")
    _write(tmp_path / "wiki" / "p.md",
           "> [!check] 1,749,287 + 53,824 == 1,803,111 （增资前+新增=增资后）\n")
    _, viol, _ = scan_case(tmp_path)
    assert viol == []


# ---- ⑤ 覆盖率（警告） ----

def test_uncited_source_warns(tmp_path):
    _write(tmp_path / "_md" / "cited.md", "甲乙")
    _write(tmp_path / "_md" / "draft.md", "草稿")
    _write(tmp_path / "wiki" / "p.md", "- 事实 〔来源: _md/cited.md：「甲乙」〕\n")
    _, viol, warn = scan_case(tmp_path)
    assert viol == [] and any("draft.md" in w for w in warn)


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
