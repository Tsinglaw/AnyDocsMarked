"""Minimal YAML frontmatter reader — top-level scalar fields only.

Deliberately dependency-free (no pyyaml): we only need flat `key: value` pairs
from a leading `---` fenced block (e.g. `quality: suspect`). Nested/list values
are skipped rather than mis-parsed. Reads the raw file, not any converted text,
so the original frontmatter survives extraction.
"""

from __future__ import annotations

from pathlib import Path


def read_frontmatter(path: str | Path) -> dict[str, str]:
    """Parse top-level scalar fields from a file's leading `---` YAML block."""
    try:
        text = Path(path).read_text("utf-8", errors="replace")
    except OSError:
        return {}
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}

    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        # only top-level (non-indented) `key: value` scalar lines
        if not line or line[0] in " \t#-" or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            out[key] = value
    return out


def select_fields(frontmatter: dict[str, str], fields: tuple[str, ...]) -> dict[str, str]:
    """Keep only the configured fields that are present."""
    return {k: frontmatter[k] for k in fields if k in frontmatter}
