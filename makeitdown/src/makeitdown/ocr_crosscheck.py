"""Dual-OCR cross-check: normalize and diff two OCR outputs to flag disagreements.

Pure and dependency-free. Normalization erases differences that don't matter
(whitespace, full/half-width, punctuation style, thousands separators) so that
what remains is genuine recognition disagreement — with digits/amounts/dates,
the legally dangerous bits, called out separately.
"""

from __future__ import annotations

import re

# Full-width digits/letters → half-width.
_WIDTH_MAP = {ord(c): ord(c) - 0xFEE0 for c in
              "０１２３４５６７８９"
              "ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
              "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"}

# Common CJK punctuation → ASCII equivalents (so style differences don't diff).
_PUNCT_MAP = {
    "，": ",", "。": ".", "、": ",", "；": ";", "：": ":",
    "（": "(", "）": ")", "％": "%", "～": "~", "－": "-",
}
_PUNCT_TABLE = {ord(k): v for k, v in _PUNCT_MAP.items()}

_WS_RE = re.compile(r"\s+")
_THOUSANDS_RE = re.compile(r"(?<=\d),(?=\d{3}\b)")


def normalize(text: str) -> str:
    """Normalize OCR text for comparison (lossy; for diffing only, never stored)."""
    if not text:
        return ""
    text = text.translate(_WIDTH_MAP).translate(_PUNCT_TABLE)
    text = _THOUSANDS_RE.sub("", text)      # 500,000 -> 500000
    text = _WS_RE.sub("", text)             # ignore all whitespace differences
    return text
