from makeitdown.ocr_crosscheck import normalize


def test_normalize_ignores_whitespace_and_width():
    a = normalize("金额 ５００，０００ 元")     # full-width digits + thousands comma + spaces
    b = normalize("金额500000元")
    assert a == b


def test_normalize_unifies_punctuation():
    assert normalize("甲、乙，丙。") == normalize("甲,乙,丙.")


def test_normalize_empty():
    assert normalize("") == ""
