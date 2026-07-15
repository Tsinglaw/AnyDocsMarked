"""Chunking invariants — every emitted chunk must fit the model window.

The embedder silently truncates any input past its max sequence length, so a
chunk larger than `chunk_tokens` loses its tail at embed time. The packer must
therefore never emit an oversized chunk, even when a single indivisible unit
(a long line with no sentence punctuation) is itself larger than the budget.
"""

from __future__ import annotations

import re

from rag_retriever.chunk import (
    chunk_text,
    count_tokens,
    Chunk,
    chunk_document,
    parse_sections,
    chunk_document_hierarchical,
)


def _nows(s: str) -> str:
    return re.sub(r"\s+", "", s)


def test_short_text_is_one_chunk():
    assert chunk_text("hello world", chunk_tokens=800) == ["hello world"]


def test_no_chunk_exceeds_budget_with_normal_prose():
    # overlap=0 gives the strict guarantee: with every unit <= budget, each packed
    # chunk is <= budget. (With overlap>0 a chunk can reach budget+overlap by design,
    # since the next chunk re-includes a tail of the previous one.)
    text = "\n\n".join(f"这是第{i}段内容，用于测试分块逻辑。" * 5 for i in range(50))
    chunks = chunk_text(text, chunk_tokens=100, overlap=0)
    assert len(chunks) > 1
    assert all(count_tokens(c) <= 100 for c in chunks)


def test_oversized_indivisible_unit_is_hard_split():
    # One "sentence" with no breakable punctuation, far larger than the budget.
    giant = "A" * 4000  # ~1 token per 4 chars in o200k → ~1000 tokens
    assert count_tokens(giant) > 200
    chunks = chunk_text(giant, chunk_tokens=200, overlap=0)
    assert len(chunks) > 1
    assert all(count_tokens(c) <= 200 for c in chunks)
    # Hard-split is lossless: re-joining the pieces reproduces the input.
    assert "".join(chunks) == giant


def test_oversized_unit_among_normal_units():
    giant = "甲" * 3000
    text = f"短句一。\n\n{giant}\n\n短句二。"
    chunks = chunk_text(text, chunk_tokens=150, overlap=0)
    assert all(count_tokens(c) <= 150 for c in chunks)


def test_chunk_document_token_strategy_wraps_chunk_text():
    # token strategy must reproduce chunk_text exactly, wrapped as Chunk with empty path.
    text = "\n\n".join(f"这是第{i}段内容。" * 5 for i in range(30))
    plain = chunk_text(text, chunk_tokens=100, overlap=0)
    docs = chunk_document(text, chunk_tokens=100, overlap=0, strategy="token")
    assert [c.text for c in docs] == plain
    assert all(isinstance(c, Chunk) for c in docs)
    assert all(c.heading_path == "" for c in docs)


def test_chunk_is_frozen():
    c = Chunk(text="x", heading_path="a > b")
    import dataclasses
    assert dataclasses.is_dataclass(c)
    try:
        c.text = "y"  # frozen → should raise
        raised = False
    except dataclasses.FrozenInstanceError:
        raised = True
    assert raised


def test_parse_sections_builds_breadcrumb():
    text = (
        "前言段落。\n\n"
        "# 民事判决书\n\n"
        "开头。\n\n"
        "## 本院认为\n\n"
        "认定段。\n\n"
        "## 判决结果\n\n"
        "如下。\n"
    )
    secs = parse_sections(text)
    paths = [s.heading_path for s in secs]
    assert paths == ["", "民事判决书", "民事判决书 > 本院认为", "民事判决书 > 判决结果"]
    assert secs[0].body.strip() == "前言段落。"
    assert "认定段" in secs[2].body


def test_parse_sections_deeper_then_shallower_resets_stack():
    text = "# A\n\n## B\n\n### C\n\ncc\n\n## D\n\ndd\n"
    paths = [s.heading_path for s in parse_sections(text)]
    # going from ### C back to ## D must drop C from the trail
    assert paths == ["A", "A > B", "A > B > C", "A > D"]


def test_parse_sections_no_headings_is_single_empty_path():
    secs = parse_sections("just flat text\n\nmore")
    assert len(secs) == 1
    assert secs[0].heading_path == ""


def test_structure_chunk_carries_heading_path():
    text = "# 合同\n\n## 第一条 标的\n\n货物为钢材。\n\n## 第二条 价款\n\n总价十万元。\n"
    docs = chunk_document(text, chunk_tokens=800, overlap=0, strategy="structure")
    paths = {c.heading_path for c in docs}
    assert "合同 > 第一条 标的" in paths
    assert "合同 > 第二条 价款" in paths


def test_structure_keeps_small_table_intact():
    table = "| 项目 | 金额 |\n|---|---|\n| 货款 | 50万 |\n| 利息 | 2万 |"
    text = f"# 表\n\n{table}\n"
    docs = chunk_document(text, chunk_tokens=800, overlap=0, strategy="structure")
    # the whole table lands in a single chunk, header included
    table_chunks = [c for c in docs if "项目" in c.text]
    assert len(table_chunks) == 1
    assert "货款" in table_chunks[0].text and "利息" in table_chunks[0].text


def test_structure_oversize_table_row_split_repeats_header():
    header = "| 列A | 列B |\n|---|---|"
    rows = "\n".join(f"| 行{i}内容很长很长很长 | 值{i} |" for i in range(60))
    text = f"# 大表\n\n{header}\n{rows}\n"
    docs = chunk_document(text, chunk_tokens=120, overlap=0, strategy="structure")
    table_chunks = [c for c in docs if "列A" in c.text]
    assert len(table_chunks) > 1
    # every table chunk repeats the header row
    assert all("列A" in c.text and "---" in c.text for c in table_chunks)


def test_structure_legal_marker_is_soft_boundary():
    body = "第一条 当事人应诚信。第二条 标的为钢材。第三条 价款十万元。"
    text = f"# 合同\n\n{body}\n"
    # tiny budget so each 第X条 lands separately if they are split as units
    docs = chunk_document(text, chunk_tokens=12, overlap=0, strategy="structure")
    joined = [c.text for c in docs]
    # no chunk should glue two different 第X条 markers together at this budget
    assert any("第一条" in t and "第二条" not in t for t in joined)


def test_structure_enumerated_marker_midbody_is_soft_boundary():
    body = "总则部分内容如下所述。\n一、甲方义务说明。\n二、乙方义务说明。"
    text = f"# 合同\n\n{body}\n"
    docs = chunk_document(text, chunk_tokens=10, overlap=0, strategy="structure")
    joined = [c.text for c in docs]
    assert any("一、" in t and "二、" not in t for t in joined)


def test_chunk_field_parent_ord_defaults_none():
    assert Chunk("t", "h").parent_ord is None


def test_single_level_chunks_have_no_parent_ord():
    chunks = chunk_document("# H\n\nsome body text here", 800, 100, "structure")
    assert all(c.parent_ord is None for c in chunks)


def test_hierarchical_children_have_valid_parent_ord():
    text = "\n\n".join(f"第{i}段：这是用于测试父子分块的中文内容，需要足够长以触发切分。" for i in range(20))
    children, parents = chunk_document_hierarchical(
        text, child_tokens=30, overlap=0, parent_tokens=90, strategy="structure"
    )
    assert len(parents) >= 2
    assert len(children) > len(parents)
    assert all(c.parent_ord is not None and 0 <= c.parent_ord < len(parents) for c in children)


def test_hierarchical_children_cover_their_parent():
    text = "\n\n".join(f"第{i}段：这是用于测试父子分块的中文内容，需要足够长以触发切分。" for i in range(20))
    children, parents = chunk_document_hierarchical(
        text, child_tokens=30, overlap=0, parent_tokens=90, strategy="structure"
    )
    for ord_ in range(len(parents)):
        group = [c.text for c in children if c.parent_ord == ord_]
        assert group, f"parent {ord_} has no children"
        # overlap=0 → children re-joined reproduce the parent's non-whitespace content.
        assert _nows("".join(group)) == _nows(parents[ord_])


def test_hierarchical_parent_does_not_cross_section():
    text = "# 甲节\n\n" + ("甲内容需要足够长。" * 20) + "\n\n# 乙节\n\n" + ("乙内容需要足够长。" * 20)
    children, parents = chunk_document_hierarchical(
        text, child_tokens=30, overlap=0, parent_tokens=200, strategy="structure"
    )
    for p in parents:
        assert not ("甲内容" in p and "乙内容" in p), "a parent block spanned two sections"
    paths = {c.heading_path for c in children}
    assert "甲节" in paths and "乙节" in paths


def test_hierarchical_keeps_table_atomic():
    table = "| 项目 | 金额 |\n| --- | --- |\n| 货款 | 500000 |\n| 利息 | 12000 |"
    text = "## 表\n\n" + table
    children, parents = chunk_document_hierarchical(
        text, child_tokens=200, overlap=0, parent_tokens=400, strategy="structure"
    )
    assert any("项目" in c.text and "货款" in c.text and "利息" in c.text for c in children)
