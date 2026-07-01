"""Offline tokenizer for BM25/full-text search.

LanceDB's built-in tokenizers don't segment Chinese well, so we segment ourselves
with jieba (pure-python, offline) into space-separated terms and let the FTS index
use a plain whitespace tokenizer. Latin words and digits pass through unchanged.
"""

from __future__ import annotations

from functools import cache


@cache
def _jieba():
    import jieba  # lazy: importing jieba builds its dictionary

    return jieba


def tokenize_for_fts(text: str) -> str:
    """Return space-joined terms suitable for a whitespace FTS tokenizer."""
    text = text.strip()
    if not text:
        return ""
    terms = [t for t in _jieba().cut(text) if t.strip()]
    return " ".join(terms)
