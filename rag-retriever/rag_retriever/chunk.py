"""Token-based chunking with overlap.

Splits on paragraph/sentence boundaries where possible, then packs spans up to
`chunk_tokens` with `overlap` tokens shared between neighbours so a passage that
straddles a boundary is still retrievable. Token counts use tiktoken o200k_base
(an estimate; the embedder tokenizes differently, hence conservative defaults).
"""

from __future__ import annotations

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


def _split_units(text: str) -> list[str]:
    """Break text into small units (paragraphs, then sentences) to pack into chunks."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    units: list[str] = []
    for para in paras:
        if count_tokens(para) <= 400:
            units.append(para)
        else:
            # Long paragraph: fall back to sentence-ish splitting (CJK + latin punctuation).
            sentences = re.split(r"(?<=[。！？.!?])\s*", para)
            units.extend(s.strip() for s in sentences if s.strip())
    return units


def chunk_text(text: str, chunk_tokens: int = 800, overlap: int = 100) -> list[str]:
    """Pack text into overlapping chunks of ~chunk_tokens tokens."""
    text = text.strip()
    if not text:
        return []
    if count_tokens(text) <= chunk_tokens:
        return [text]

    units = _split_units(text)
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
