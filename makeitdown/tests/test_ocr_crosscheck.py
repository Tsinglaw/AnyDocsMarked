from makeitdown.ocr_crosscheck import normalize, compare, CrossCheck


def test_normalize_ignores_whitespace_and_width():
    a = normalize("金额 ５００，０００ 元")     # full-width digits + thousands comma + spaces
    b = normalize("金额500000元")
    assert a == b


def test_normalize_unifies_punctuation():
    assert normalize("甲、乙，丙。") == normalize("甲,乙,丙.")


def test_normalize_empty():
    assert normalize("") == ""


def test_identical_texts_have_no_reasons():
    cc = compare("合同金额500000元，签于2024年6月", "合同金额500000元，签于2024年6月")
    assert isinstance(cc, CrossCheck)
    assert cc.reasons == []
    assert cc.digit_mismatches == 0


def test_amount_digit_mismatch_is_flagged_as_high_risk():
    a = "本案货款金额为500000元整"
    b = "本案货款金额为800000元整"   # one engine read 5, the other 8
    cc = compare(a, b)
    assert cc.digit_mismatches >= 1
    assert cc.reasons, "a digit mismatch must produce a reason"
    assert "金额" in cc.reasons[0] or "日期" in cc.reasons[0] or "数字" in cc.reasons[0]


def test_minor_text_difference_below_threshold_is_clean():
    a = "甲公司与乙公司签订买卖合同共计十条款项内容如下所述详见正文"
    b = "甲公司与乙公司签订买卖合同共计十条款项内容如下所诉详见正文"  # 1 char off
    cc = compare(a, b, ratio_threshold=0.1)
    assert cc.digit_mismatches == 0
    assert cc.reasons == []  # 1/N chars is below 10%
