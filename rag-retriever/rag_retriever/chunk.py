"""Token-based chunking with overlap.

Splits on paragraph/sentence boundaries where possible, then packs spans up to
`chunk_tokens` with `overlap` tokens shared between neighbours so a passage that
straddles a boundary is still retrievable. Token counts use tiktoken o200k_base
(an estimate; the embedder tokenizes differently, hence conservative defaults).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from functools import cache

import tiktoken


@dataclass(frozen=True)
class Chunk:
    """A chunk plus the heading breadcrumb of the section it came from.

    `heading_path` is a " > "-joined trail like "民事判决书 > 本院认为", or "" when
    the chunk has no enclosing markdown heading.
    """

    text: str
    heading_path: str


@dataclass(frozen=True)
class Section:
    heading_path: str
    body: str


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def parse_sections(text: str) -> list[Section]:
    """Split markdown into sections by ATX headings, tracking a heading breadcrumb.

    The text before the first heading becomes a leading section with an empty path.
    A heading at level L resets any deeper levels on the stack, so a `## D` after a
    `### C` drops C from the trail.
    """
    lines = text.splitlines()
    stack: list[tuple[int, str]] = []  # (level, title)
    cur_path = ""
    buf: list[str] = []
    out: list[Section] = []

    def flush():
        body = "\n".join(buf).strip()
        # Keep a heading even with an empty body so its breadcrumb still appears
        # (e.g. a heading immediately followed by a subheading); pure preamble with
        # neither body nor heading is dropped.
        if body or cur_path:
            out.append(Section(heading_path=cur_path, body=body))
        buf.clear()

    for line in lines:
        m = _HEADING_RE.match(line)
        if not m:
            buf.append(line)
            continue
        flush()
        level = len(m.group(1))
        title = m.group(2).strip()
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, title))
        cur_path = " > ".join(t for _, t in stack)
    flush()

    if not out:
        return [Section(heading_path="", body=text.strip())]
    return out


@cache
def _encoder():
    # Lazily built: `get_encoding` may fetch the BPE file on first use, so doing
    # it at import would force a network round-trip just to import this module
    # (and break the "local-first / offline" promise). Build it on first count.
    return tiktoken.get_encoding("o200k_base")


def count_tokens(text: str) -> int:
    return len(_encoder().encode(text))


def _hard_split(unit: str, max_tokens: int, n: int | None = None) -> list[str]:
    """Last-resort split of a single unit that itself exceeds ``max_tokens``.

    Used for a unit with no sentence punctuation to break on (a wall of text, a
    giant table row). Split by character count, proportional to the token
    overshoot, so each piece lands at or under the budget. Slices the original
    string (lossless, never corrupts a character mid-codepoint, unlike decoding
    arbitrary token-id ranges); the token bound is approximate but the embedder
    truncation it guards against is the real backstop. ``n`` is the unit's
    pre-computed token count when the caller already has it (avoids re-encoding).
    """
    if n is None:
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
        para_tokens = count_tokens(para)
        if para_tokens <= 400:
            # (unit, known token count) — reuse the count just computed.
            candidates: list[tuple[str, int | None]] = [(para, para_tokens)]
        else:
            # Long paragraph: fall back to sentence-ish splitting (CJK + latin punctuation).
            sentences = re.split(r"(?<=[。！？.!?])\s*", para)
            candidates = [(s.strip(), None) for s in sentences if s.strip()]
        for unit, n in candidates:
            units.extend(_hard_split(unit, max_tokens, n))
    return units


def _pack_units(units: list[str], chunk_tokens: int, overlap: int) -> list[str]:
    """Greedily pack pre-split units into ~chunk_tokens chunks with token overlap."""
    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0

    for unit in units:
        unit_tokens = count_tokens(unit)
        if current and current_tokens + unit_tokens > chunk_tokens:
            chunks.append("\n\n".join(current))
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


# Legal section markers used as soft (preferred) split points when a section has
# no finer markdown structure. Order-independent; matched at unit-splitting time.
_LEGAL_MARKERS = re.compile(
    r"(第[一二三四五六七八九十百零\d]+条"
    r"|^[（(][一二三四五六七八九十]+[)）]"
    r"|^[一二三四五六七八九十]+、"
    r"|本院认为|审理终结|如不服本判决|事实和理由|本院查明)",
    re.MULTILINE
)


def _is_table_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("|") or ("|" in s and set(s) <= set("|-: "))


def _split_table_block(block: str, max_tokens: int) -> list[str]:
    """Keep a markdown table whole; if it exceeds max_tokens, split by rows and
    repeat the header (first two lines: header + separator) on each piece."""
    if count_tokens(block) <= max_tokens:
        return [block]
    lines = block.splitlines()
    header = lines[:2]  # header row + separator
    body_rows = lines[2:]
    header_tokens = count_tokens("\n".join(header))  # fixed for the whole block
    pieces: list[str] = []
    cur = list(header)
    cur_tokens = header_tokens
    for row in body_rows:
        t = count_tokens(row)
        if len(cur) > 2 and cur_tokens + t > max_tokens:
            pieces.append("\n".join(cur))
            cur = list(header)
            cur_tokens = header_tokens
        cur.append(row)
        cur_tokens += t
    if len(cur) > 2:
        pieces.append("\n".join(cur))
    return pieces


def _split_structured_units(body: str, max_tokens: int) -> list[str]:
    """Split a section body into units, keeping tables atomic and preferring legal
    markers as paragraph boundaries. Non-table prose falls back to _split_units."""
    units: list[str] = []
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        if _is_table_line(lines[i]):
            # A blank line is not a table line, so the scan naturally stops there.
            j = i
            while j < len(lines) and _is_table_line(lines[j]):
                j += 1
            block = "\n".join(lines[i:j]).strip()
            if block:
                units.extend(_split_table_block(block, max_tokens))
            i = j
            continue
        # gather a prose run until the next table line
        j = i
        while j < len(lines) and not _is_table_line(lines[j]):
            j += 1
        prose = "\n".join(lines[i:j]).strip()
        if prose:
            # insert paragraph breaks before legal markers so they split cleanly
            marked = _LEGAL_MARKERS.sub(lambda m: "\n\n" + m.group(0), prose)
            units.extend(_split_units(marked, max_tokens))
        i = j
    return units


def chunk_text(text: str, chunk_tokens: int = 800, overlap: int = 100) -> list[str]:
    """Pack text into overlapping chunks of ~chunk_tokens tokens."""
    text = text.strip()
    if not text:
        return []
    if count_tokens(text) <= chunk_tokens:
        return [text]
    return _pack_units(_split_units(text, chunk_tokens), chunk_tokens, overlap)


def chunk_document(
    text: str, chunk_tokens: int = 800, overlap: int = 100, strategy: str = "structure"
) -> list[Chunk]:
    """Structure-aware chunking. `strategy="token"` reproduces chunk_text exactly
    (every chunk gets an empty heading_path); `strategy="structure"` uses parse_sections
    to preserve section headings. Unknown strategies fall back to token.
    """
    if strategy != "structure":
        return [Chunk(text=t, heading_path="") for t in chunk_text(text, chunk_tokens, overlap)]
    sections = parse_sections(text)
    out: list[Chunk] = []
    for sec in sections:
        units = _split_structured_units(sec.body, chunk_tokens)
        for piece in _pack_units(units, chunk_tokens, overlap):
            out.append(Chunk(text=piece, heading_path=sec.heading_path))
    return out
