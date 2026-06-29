"""Token-based chunking with overlap.

Splits on paragraph/sentence boundaries where possible, then packs spans up to
`chunk_tokens` with `overlap` tokens shared between neighbours so a passage that
straddles a boundary is still retrievable. Token counts use tiktoken o200k_base
(an estimate; the embedder tokenizes differently, hence conservative defaults).
"""

from __future__ import annotations

import math
import re
from functools import cache

import tiktoken


@cache
def _encoder():
    # Lazily built: `get_encoding` may fetch the BPE file on first use, so doing
    # it at import would force a network round-trip just to import this module
    # (and break the "local-first / offline" promise). Build it on first count.
    return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_encoder().encode(text))


def _hard_split(unit: str, max_tokens: int) -> list[str]:
    """Last-resort split of a single unit that itself exceeds ``max_tokens``.

    Used for a unit with no sentence punctuation to break on (a wall of text, a
    giant table row). Split by character count, proportional to the token
    overshoot, so each piece lands at or under the budget. Slices the original
    string (lossless, never corrupts a character mid-codepoint, unlike decoding
    arbitrary token-id ranges); the token bound is approximate but the embedder
    truncation it guards against is the real backstop.
    """
    n = count_tokens(unit)
    if n <= max_tokens or not unit:
        return [unit]
    pieces = math.ceil(n / max_tokens)
    size = math.ceil(len(unit) / pieces)
    return [unit[i:i + size] for i in range(0, len(unit), size)]


def _split_units(text: str, max_tokens: int) -> list[str]:
    """Break text into small units (paragraphs, then sentences) to pack into chunks.

    No unit may exceed ``max_tokens``: an over-budget sentence is hard-split so a
    single indivisible unit can never become an oversized (truncated-at-embed) chunk.
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    units: list[str] = []
    for para in paras:
        if count_tokens(para) <= 400:
            candidates = [para]
        else:
            # Long paragraph: fall back to sentence-ish splitting (CJK + latin punctuation).
            sentences = re.split(r"(?<=[。！？.!?])\s*", para)
            candidates = [s.strip() for s in sentences if s.strip()]
        for unit in candidates:
            units.extend(_hard_split(unit, max_tokens))
    return units


def chunk_text(text: str, chunk_tokens: int = 800, overlap: int = 100) -> list[str]:
    """Pack text into overlapping chunks of ~chunk_tokens tokens."""
    text = text.strip()
    if not text:
        return []
    if count_tokens(text) <= chunk_tokens:
        return [text]

    units = _split_units(text, chunk_tokens)
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for unit in units:
        unit_tokens = count_tokens(unit)
        if current and current_tokens + unit_tokens > chunk_tokens:
            chunks.append("\n\n".join(current))
            # Start next chunk with a tail of the previous one for overlap.
            if overlap > 0:
                tail: list[str] = []
                tail_tokens = 0
                for prev in reversed(current):
                    t = count_tokens(prev)
                    if tail_tokens + t > overlap:
                        break
                    tail.insert(0, prev)
                    tail_tokens += t
                current = tail
                current_tokens = tail_tokens
            else:
                current = []
                current_tokens = 0
        current.append(unit)
        current_tokens += unit_tokens

    if current:
        chunks.append("\n\n".join(current))
    return chunks
