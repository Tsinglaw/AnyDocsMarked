"""Chunking invariants — every emitted chunk must fit the model window.

The embedder silently truncates any input past its max sequence length, so a
chunk larger than `chunk_tokens` loses its tail at embed time. The packer must
therefore never emit an oversized chunk, even when a single indivisible unit
(a long line with no sentence punctuation) is itself larger than the budget.
"""

from __future__ import annotations

from rag_retriever.chunk import chunk_text, count_tokens, Chunk, chunk_document, Section, parse_sections


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
