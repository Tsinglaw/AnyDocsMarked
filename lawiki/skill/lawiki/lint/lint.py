#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lawiki 校验工具（确定性，仅标准库）。三条子命令：

  python lint.py check   <案件根目录>            # 五类确定性检查，违规则退出码非 0
  python lint.py extract <案件根目录>            # 抽 claim↔引文清单(JSON)，供换实例判官做蕴含校验
  python lint.py answer  <案件根目录> <草稿.md>  # 问答交付闸门：锚点全验+闭世界+整篇兜底

check 五类：① 锚点存在（EXTRACTED 硬底线）② 死链 ③ 时间线顺序 ④ 勾稽闭合
（`> [!check] a+b==c`）⑤ 覆盖率（警告，三态：已引用/登记跳过/未处置，账本为
wiki/log.md 的 skip 条目）。只消格式噪声、数字与文字精确——
"数字写错/张冠李戴"必被抓、"换行差异"不误报。详见 SKILL.md / references/verification.md。
"""
import ast
import json
import os
import re
import sys
from pathlib import Path

# ───────────────────────── 归一化（只消格式噪声，保留数字文字精确） ─────────────────────────

_PUNCT = {
    "，": ",", "（": "(", "）": ")", "：": ":", "；": ";",
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "－": "-", "—": "-", "–": "-", "　": "",
}
_DROP_CHARS = set(" \t\r\n,|*#>`~_")


def norm_with_map(s: str) -> tuple[str, list[int]]:
    """返回 (归一化串, 索引映射)，map[i] = 归一化第 i 字符在原串 s 的下标。"""
    out: list[str] = []
    idx: list[int] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "<":  # 跳过成对 HTML 标签；孤立 '<' 当普通字符
            j = s.find(">", i)
            if j != -1:
                i = j + 1
                continue
        c2 = _PUNCT.get(c, c)
        if c2 == "" or c2 in _DROP_CHARS:
            i += 1
            continue
        out.append(c2)
        idx.append(i)
        i += 1
    return "".join(out), idx


def norm(s: str) -> str:
    return norm_with_map(s)[0]


def _posix(s: str) -> str:
    """路径统一到 POSIX 形态——锚点、跳过账本、覆盖率共用同一坐标系。"""
    return s.replace("\\", "/")


# ───────────────────────── 公共正则 ─────────────────────────

ANCHOR_RE = re.compile(r"〔来源:\s*(.+?)：「(.+?)」〕")
WIKILINK_RE = re.compile(r"\[\[([^\]\n]+?)\]\]")
SPLIT_RE = re.compile(r"…+|\.\.\.+")
DATE_RE = re.compile(r"(\d{4})\s*年(?:\s*(\d{1,2})\s*月)?(?:\s*(\d{1,2})\s*日)?")
ALIASES_RE = re.compile(r"aliases:\s*\[(.*?)\]")
CHECK_RE = re.compile(r">\s*\[!check\]\s*(.+)")
_NUM_PUNCT = str.maketrans({"，": ",", "＋": "+", "－": "-", "×": "*", "＝": "="})
_LEAD = re.compile(r"^\s*(?:[-*+]\s+)?")  # 列表项前导符
SKIP_RE = re.compile(r"^##\s*\[\d{4}-\d{2}-\d{2}\]\s*skip\s*\|\s*(.+?)\s*$")  # log.md 跳过条目
REASON_RE = re.compile(r"^\s*-\s*原因[:：](.*)$")


def _fragments(snippet: str) -> list[str]:
    return [s.strip() for s in SPLIT_RE.split(snippet) if s.strip()]


def _frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    return text[3:end] if end != -1 else ""


def _load_pages(wiki: Path, root: Path) -> list[tuple[Path, str, str]]:
    """一次性读入所有 wiki 页面：(路径, 相对根的 posix 路径, 正文)。各检查共用。"""
    return [(md, md.relative_to(root).as_posix(), md.read_text(encoding="utf-8"))
            for md in sorted(wiki.rglob("*.md"))]


# ───────────────────────── check：五类确定性检查 ─────────────────────────

def _page_names(pages: list[tuple[Path, str, str]]) -> set[str]:
    names: set[str] = set()
    for md, _where, text in pages:
        names.add(md.stem)
        m = ALIASES_RE.search(_frontmatter(text))
        if m:
            names.update(a.strip() for a in m.group(1).split(",") if a.strip())
    return names


def _check_anchors(root: Path, pages: list[tuple[Path, str, str]]
                   ) -> tuple[list[str], set[str], int]:
    """① 锚点存在。返回 (违规, 被引用源文件集合, 锚点总数)。"""
    cache: dict[Path, str] = {}
    violations: list[str] = []
    cited: set[str] = set()
    total = 0
    for _md, where, text in pages:
        for m in ANCHOR_RE.finditer(text):
            total += 1
            rel, snippet = m.group(1).strip(), m.group(2)
            cited.add(rel)
            src = root / rel
            if not src.is_file():
                violations.append(f"[缺文件] {where}\n          所指来源不存在: {rel}")
                continue
            if src not in cache:
                cache[src] = norm(src.read_text(encoding="utf-8"))
            body = cache[src]
            pos, missing = 0, None
            for frag in _fragments(snippet):
                nf = norm(frag)
                idx = body.find(nf, pos)
                if idx < 0:
                    missing = frag
                    break
                pos = idx + len(nf)
            if missing is not None:
                violations.append(
                    f"[片段不符] {where}\n          来源: {rel}\n          找不到片段: 「{missing}」")
    return violations, cited, total


def _check_deadlinks(pages: list[tuple[Path, str, str]], names: set[str]) -> list[str]:
    """② 死链。"""
    violations: list[str] = []
    for _md, where, text in pages:
        for m in WIKILINK_RE.finditer(text):
            target = m.group(1).split("|")[0].split("#")[0].strip()
            if not target:  # [[#同页标题]]
                continue
            if target not in names:
                violations.append(f"[死链] {where}\n          指向不存在的页面: [[{target}]]")
    return violations


def _date_key(m: re.Match) -> tuple[int, ...]:
    """日期取**实际写出**的精度：(年[, 月[, 日]])。保留精度（而非用 0 补位）——
    这样"只写年份"的条目不会与同年的完整日期被误判成乱序。"""
    key = [int(m.group(1))]
    if m.group(2):
        key.append(int(m.group(2)))
        if m.group(3):
            key.append(int(m.group(3)))
    return tuple(key)


def _check_timeline_order(pages: list[tuple[Path, str, str]]) -> list[str]:
    """③ 时间线顺序。"""
    violations: list[str] = []
    for _md, where, text in pages:
        if "时间线" not in where.split("/"):
            continue
        prev = None
        for line in text.splitlines():
            if not line.lstrip().startswith("-"):
                continue
            m = DATE_RE.search(line)
            if not m:
                continue
            cur = _date_key(m)
            # 只比较两个日期共有的精度：只写年份的条目不与同年完整日期冲突，
            # 但"年/月"层面的倒退仍会被抓。
            if prev is not None:
                n = min(len(prev), len(cur))
                if cur[:n] < prev[:n]:
                    violations.append(f"[时间线乱序] {where}\n          {cur} 出现在 {prev} 之后")
            prev = cur
    return violations


def _ev(n):  # 受限算术求值（只许 + - * 与数字，绝不 eval）
    if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
        return n.value
    if isinstance(n, ast.BinOp) and isinstance(n.op, (ast.Add, ast.Sub, ast.Mult)):
        l, r = _ev(n.left), _ev(n.right)
        return l + r if isinstance(n.op, ast.Add) else (l - r if isinstance(n.op, ast.Sub) else l * r)
    if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
        v = _ev(n.operand)
        return v if isinstance(n.op, ast.UAdd) else -v
    raise ValueError("不允许的表达式")


def _safe_eval(expr: str) -> float:
    return _ev(ast.parse(expr.replace(",", "").strip(), mode="eval").body)


def _check_closures(pages: list[tuple[Path, str, str]]) -> list[str]:
    """④ 勾稽闭合：`> [!check] a + b == c`。"""
    violations: list[str] = []
    for _md, where, text in pages:
        for line in text.splitlines():
            m = CHECK_RE.search(line)
            if not m:
                continue
            raw = m.group(1).strip()
            m2 = re.match(r"\s*([0-9,\s+\-*().]+==[0-9,\s+\-*().]+)",
                          raw.translate(_NUM_PUNCT))
            if not m2:
                violations.append(f"[勾稽无法解析] {where}\n          {raw}")
                continue
            try:
                left, right = (_safe_eval(p) for p in m2.group(1).split("=="))
            except Exception as e:
                violations.append(f"[勾稽无法解析] {where}\n          {raw}  （{e}）")
                continue
            if abs(left - right) > 1e-6:
                violations.append(
                    f"[勾稽不符] {where}\n          {raw}\n          左={left:g} ≠ 右={right:g}")
    return violations


def _load_skips(root: Path) -> dict[str, bool]:
    """解析 wiki/log.md 的 skip 条目（覆盖率账本）：
    `## [YYYY-MM-DD] skip | <路径>` + 条目正文 `- 原因：<非空理由>`。
    返回 {POSIX 路径: 是否带非空原因}。同一路径多条登记取"任一条带原因"
    （append-only 下补登记即可修复缺原因）；原因行只归属其上方最近的 skip 条目。"""
    skips: dict[str, bool] = {}
    log = root / "wiki" / "log.md"
    if not log.is_file():
        return skips
    cur: str | None = None
    for line in log.read_text(encoding="utf-8").splitlines():
        m = SKIP_RE.match(line)
        if m:
            cur = _posix(m.group(1))
            skips.setdefault(cur, False)
            continue
        if line.startswith("#"):  # 任何其他标题都结束当前 skip 条目的正文
            cur = None
            continue
        if cur is not None:
            r = REASON_RE.match(line)
            if r and r.group(1).strip():
                skips[cur] = True
    return skips


def _check_coverage(root: Path, cited: set[str]) -> tuple[list[str], dict[str, int]]:
    """⑤ 覆盖率（警告，三态账本）：已引用 / 登记跳过（wiki/log.md skip 条目）/ 未处置。
    仅未处置发 `[未处置]`；登记但缺非空原因发 `[跳过无原因]`。引用优先于登记；
    登记路径不在 _md/ 中的静默忽略。返回 (警告, 统计)。"""
    stats = {"total": 0, "cited": 0, "skipped": 0, "unresolved": 0}
    warnings: list[str] = []
    md_dir = root / "_md"
    if not md_dir.is_dir():
        return warnings, stats
    cited_norm = {_posix(c) for c in cited}
    skips = _load_skips(root)
    files = sorted(md_dir.rglob("*.md"))
    stats["total"] = len(files)
    for f in files:
        rel = f.relative_to(root).as_posix()
        if rel in cited_norm:
            stats["cited"] += 1
        elif rel in skips:
            stats["skipped"] += 1
            if not skips[rel]:
                warnings.append(f"[跳过无原因] {rel}")
        else:
            stats["unresolved"] += 1
            warnings.append(f"[未处置] {rel}")
    return warnings, stats


def scan_case(root: Path) -> tuple[int, list[str], list[str], dict[str, int]]:
    """返回 (锚点总数, 违规列表, 警告列表, 覆盖率统计)。纯函数，便于测试。"""
    wiki = root / "wiki"
    if not wiki.is_dir():
        raise FileNotFoundError(f"找不到 {wiki}")
    pages = _load_pages(wiki, root)
    names = _page_names(pages)
    violations, cited, total = _check_anchors(root, pages)
    violations += _check_deadlinks(pages, names)
    violations += _check_timeline_order(pages)
    violations += _check_closures(pages)
    warnings, coverage = _check_coverage(root, cited)
    return total, violations, warnings, coverage


# 闭世界锚点必含的核心约束句（与 tools/init_case.py 的模板同源）。存在性 +
# sentinel 都可零误报机判——故做硬违规；「内容被遵守」不可判，不在此列。
CASE_ANCHOR_SENTINEL = "答前必先检索"


def _check_case_files(root: Path) -> list[str]:
    """案件根必须有闭世界锚点 `AGENTS.md` 与 `CLAUDE.md`（harness 自动加载、
    即便 skill 未触发也在场，见 SKILL.md 第一步）。缺失 / 空 / 无 sentinel →
    硬违规。生成用 `tools/init_case.py`。不进 scan_case（那只管 wiki 内容），
    由 main 的 check 分支追加。"""
    violations: list[str] = []
    for name in ("AGENTS.md", "CLAUDE.md"):
        p = root / name
        if not p.is_file():
            violations.append(
                f"[缺锚点] 案件根缺 {name}（闭世界自描述锚点）。"
                f"跑 python <SKILL_DIR>/tools/init_case.py <案件根> 生成。")
        elif CASE_ANCHOR_SENTINEL not in p.read_text(encoding="utf-8", errors="replace"):
            violations.append(
                f"[锚点无效] {name} 缺闭世界约束句「{CASE_ANCHOR_SENTINEL}」——"
                f"可能是空文件或被掏空。重跑 init_case.py --force 复原。")
    return violations


# ───────────────────────── answer：问答交付闸门 ─────────────────────────

NOT_FOUND_PHRASE = "未在本案材料中找到"


def check_answer_anchors(root: Path, text: str, where: str) -> tuple[int, list[str]]:
    """① 锚点全验（复用 _check_anchors）② 闭世界（锚点须指向本案 _md/）。
    供 answer 闸门与 Stop hook 共用——hook 只跑这两项（零误报，无锚点不拦），
    「整篇兜底」归 scan_answer。返回 (锚点总数, 违规)。"""
    violations, cited, total = _check_anchors(root, [(root / where, where, text)])
    for rel in sorted(cited):
        # 闭世界要的是"归一化后仍落在 _md/ 之内"，纯前缀串检查会被 _md/../ 穿越绕过
        # （_check_anchors 走文件系统解析、能穿到 _md/ 外，前缀串却只看开头骗得过）。
        # normpath 收敛 . / .. 后，残留的 .. 只会出现在开头，故判首段是否 _md 即已
        # 封死穿越；顺带给 ./_md/ 这类无害写法摘掉旧前缀检查误报的帽子。
        if _posix(os.path.normpath(rel)).split("/")[0] != "_md":
            violations.append(
                f"[闭世界] {where}\n          锚点指向本案 _md/ 之外: {rel}")
    return total, violations


def _has_substantive_prose(text: str) -> bool:
    """存在 callout(`>`)/标题(`#`)/空行之外的实质内容行？（前导 frontmatter 跳过）"""
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                lines = lines[i + 1:]
                break
    for line in lines:
        s = line.strip()
        if s and not s.startswith(">") and not s.startswith("#"):
            return True
    return False


def scan_answer(root: Path, draft: Path) -> tuple[int, list[str]]:
    """交付闸门三检：锚点全验 + 闭世界 + 整篇兜底。兜底只在零锚点时触发：
    有实质内容却零锚点、又未明示「未在本案材料中找到」→ 裸答打回。
    不猜哪句是事实陈述（那是蕴含判官的活），误报率设计为 ~0。"""
    text = draft.read_text(encoding="utf-8")
    total, violations = check_answer_anchors(root, text, draft.name)
    if total == 0 and NOT_FOUND_PHRASE not in text and _has_substantive_prose(text):
        violations.append(
            f"[裸答] {draft.name}\n          零锚点、未明示「{NOT_FOUND_PHRASE}」，"
            f"且含分析标注之外的实质内容——事实必须挂锚点")
    return total, violations


# ───────────────────────── extract：抽 claim↔引文清单 ─────────────────────────

def _context(root: Path, src: str, quote: str, cache: dict, window: int = 120) -> str:
    """在源文件里定位引文，返回前后各约 window 字的上下文窗口（折叠空白）。"""
    sp = root / src
    if not sp.is_file():
        return ""
    if sp not in cache:
        raw = sp.read_text(encoding="utf-8")
        cache[sp] = (raw, *norm_with_map(raw))
    raw, nsrc, idxmap = cache[sp]
    frags = [f for f in SPLIT_RE.split(quote) if f.strip()]
    if not frags:
        return ""
    nq = norm(max(frags, key=len))  # 用最长片段定位最稳
    p = nsrc.find(nq)
    if p < 0 or not nq:
        return ""
    raw_start = idxmap[p]
    raw_end = idxmap[min(p + len(nq) - 1, len(idxmap) - 1)] + 1
    ctx = raw[max(0, raw_start - window): raw_end + window]
    return re.sub(r"\s+", " ", ctx).strip()


def get_pairs(root: Path) -> list[dict]:
    """拆出每条 (page, claim, source, quote, context)；每锚点配它紧前的子断言。
    跳过标题与 `>` 开头的分析 callout（已显式标注的 INFERRED）。"""
    wiki = root / "wiki"
    cache: dict = {}
    pairs: list[dict] = []
    for md in sorted(wiki.rglob("*.md")):
        page = md.relative_to(root).as_posix()
        for line in md.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or s.startswith(">"):
                continue
            matches = list(ANCHOR_RE.finditer(line))
            if not matches:
                continue
            last = 0
            for m in matches:
                claim = _LEAD.sub("", line[last:m.start()]).strip(" ；;，,、")
                last = m.end()
                src, quote = m.group(1).strip(), m.group(2).strip()
                pairs.append({"page": page, "claim": claim, "source": src,
                              "quote": quote, "context": _context(root, src, quote, cache)})
    return pairs


# ───────────────────────── CLI ─────────────────────────

def main(argv: list[str]) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 重定向默认 GBK
    except Exception:
        pass
    usage = ("用法：python lint.py check|extract <案件根目录>\n"
             "      python lint.py answer <案件根目录> <回答草稿.md>")
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd in ("check", "extract") and len(argv) == 3:
        root = Path(argv[2])
        if cmd == "extract":
            print(json.dumps(get_pairs(root), ensure_ascii=False, indent=2))
            return 0
        try:
            total, violations, warnings, cov = scan_case(root)
        except FileNotFoundError as e:
            print(e, file=sys.stderr)
            return 2
        violations = violations + _check_case_files(root)  # 结构性前置：闭世界锚点须在
        print(f"扫描锚点 {total} 个；违规 {len(violations)} 处；警告 {len(warnings)} 处。")
        print(f"覆盖率：{cov['total']} 源文件 | 已引用 {cov['cited']} | "
              f"登记跳过 {cov['skipped']} | 未处置 {cov['unresolved']}")
        for v in violations:
            print("  ✗ " + v)
        for w in warnings:
            print("  ! " + w)
        return 1 if violations else 0
    if cmd == "answer" and len(argv) == 4:
        root, draft = Path(argv[2]), Path(argv[3])
        if not draft.is_file():
            print(f"找不到回答草稿：{draft}", file=sys.stderr)
            return 2
        try:
            total, violations = scan_answer(root, draft)
        except (OSError, UnicodeDecodeError) as e:
            # 读不动草稿是环境/编码问题，不是"内容违规"——退出码要能区分开，
            # 否则协议会把"崩溃"误读成"违规打回"。
            print(f"无法读取回答草稿：{e}", file=sys.stderr)
            return 2
        print(f"回答锚点 {total} 个；违规 {len(violations)} 处。")
        for v in violations:
            print("  ✗ " + v)
        return 1 if violations else 0
    print(usage, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
