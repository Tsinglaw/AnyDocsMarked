"""Dual-OCR cross-check: normalize and diff two OCR outputs to flag disagreements.

Pure and dependency-free. Normalization erases differences that don't matter
(whitespace, full/half-width, punctuation style, thousands separators) so that
what remains is genuine recognition disagreement — with digits/amounts/dates,
the legally dangerous bits, called out separately.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

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


# Digit runs and date-like tokens — the legally dangerous bits to compare exactly.
_NUM_TOKEN_RE = re.compile(r"\d+(?:年|月|日)?")


@dataclass
class CrossCheck:
    disagreement_ratio: float = 0.0
    digit_mismatches: int = 0
    reasons: list[str] = field(default_factory=list)


def _char_disagreement_ratio(a: str, b: str) -> float:
    """1 - similarity, via difflib ratio on normalized strings."""
    if not a and not b:
        return 0.0
    return 1.0 - difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _digit_mismatch_count(a: str, b: str) -> int:
    """Number of positions where the ordered digit/date tokens of a and b differ."""
    ta, tb = _NUM_TOKEN_RE.findall(a), _NUM_TOKEN_RE.findall(b)
    mism = abs(len(ta) - len(tb))  # extra/missing tokens
    for x, y in zip(ta, tb):       # positional mismatches among the shared length
        if x != y:
            mism += 1
    return mism


def compare(primary: str, secondary: str, ratio_threshold: float = 0.1) -> CrossCheck:
    """Compare two OCR outputs after normalization. Returns disagreement metrics
    and at most one summary reason string (empty list = clean)."""
    a, b = normalize(primary), normalize(secondary)
    ratio = _char_disagreement_ratio(a, b)
    digits = _digit_mismatch_count(a, b)
    reasons: list[str] = []
    if digits > 0 or ratio > ratio_threshold:
        pct = ratio * 100
        suffix = f"，含 {digits} 处数字/日期位不一致" if digits else ""
        # No engine names here — this is a pure diff; the caller records which
        # engines were compared (in ConversionResult.engine).
        reasons.append(f"双OCR分歧 {pct:.1f}%{suffix}")
    return CrossCheck(disagreement_ratio=round(ratio, 4), digit_mismatches=digits, reasons=reasons)


def normalize(text: str) -> str:
    """Normalize OCR text for comparison (lossy; for diffing only, never stored)."""
    if not text:
        return ""
    text = text.translate(_WIDTH_MAP).translate(_PUNCT_TABLE)
    text = _THOUSANDS_RE.sub("", text)      # 500,000 -> 500000
    text = _WS_RE.sub("", text)             # ignore all whitespace differences
    return text
