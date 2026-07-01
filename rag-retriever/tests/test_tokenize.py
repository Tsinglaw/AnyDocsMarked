from rag_retriever.tokenize import tokenize_for_fts


def test_tokenize_splits_chinese_into_space_separated_terms():
    out = tokenize_for_fts("表见代理与无权代理")
    # jieba segments into words; result is space-joined and contains the key terms
    terms = out.split()
    assert "表见" in out or "表见代理" in terms or "代理" in terms
    assert " " in out  # produced multiple whitespace-separated terms


def test_tokenize_preserves_latin_and_digits():
    out = tokenize_for_fts("Contract 2024 amount 500000")
    assert "2024" in out.split()
    assert "Contract" in out.split() or "contract" in out.split()


def test_tokenize_empty_is_empty():
    assert tokenize_for_fts("") == ""
