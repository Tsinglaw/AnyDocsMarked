import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "lint"))
import init_case as I  # noqa: E402
import lint as L  # noqa: E402


def test_creates_full_skeleton_and_anchors(tmp_path):
    I.init_case(tmp_path)
    for rel in ("AGENTS.md", "CLAUDE.md", "wiki/index.md", "wiki/log.md"):
        assert (tmp_path / rel).is_file(), rel
    for d in ("原始资料", "wiki", "wiki/案件主体", "wiki/法律关系",
              "wiki/法律事实", "wiki/时间线"):
        assert (tmp_path / d).is_dir(), d
    # 两个锚点内容相同且含 sentinel
    a = (tmp_path / "AGENTS.md").read_text(encoding="utf-8")
    c = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    assert a == c
    assert L.CASE_ANCHOR_SENTINEL in a


def test_generated_anchors_pass_lint_check(tmp_path):
    # 生成器产出必须过 lint 的锚点检查（generator ⟶ checker 一致性）
    I.init_case(tmp_path)
    assert L._check_case_files(tmp_path) == []


def test_idempotent_does_not_clobber(tmp_path):
    I.init_case(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# 我手工改过的\n答前必先检索\n", encoding="utf-8")
    created = I.init_case(tmp_path)          # 二次运行
    assert created == []                      # 什么都没重建
    assert "手工改过" in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


def test_force_overwrites(tmp_path):
    I.init_case(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# 被改脏\n", encoding="utf-8")
    I.init_case(tmp_path, force=True)
    assert L.CASE_ANCHOR_SENTINEL in (tmp_path / "AGENTS.md").read_text(encoding="utf-8")


def test_main_reports_and_exit_zero(tmp_path, capsys):
    assert I.main([str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "AGENTS.md" in out
    assert I.main([]) == 2                     # 缺参数
