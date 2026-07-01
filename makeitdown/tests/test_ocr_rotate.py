from makeitdown.ocr_rotate import best_rotation_angle


def test_picks_highest_confidence_angle():
    assert best_rotation_angle({0: 0.40, 90: 0.95, 180: 0.30, 270: 0.20}) == 90


def test_tie_prefers_no_rotation():
    assert best_rotation_angle({0: 0.9, 90: 0.9}) == 0


def test_empty_defaults_to_zero():
    assert best_rotation_angle({}) == 0
