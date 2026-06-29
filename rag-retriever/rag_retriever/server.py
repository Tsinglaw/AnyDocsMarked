"""MCP server exposing the retriever as agent tools.

Mount this in any MCP client (Claude Code, etc.). The agent calls `search` to
pull relevant passages from indexed documents, then answers with its OWN LLM.
This server has no LLM and never generates answers.
"""

from __future__ import annotations

import json
from dataclasses import replace

from mcp.server.fastmcp import FastMCP

from .config import Config, split_csv
from .pipeline import Retriever

mcp = FastMCP("rag-retriever")
_retriever: Retriever | None = None


def retriever(metadata_fields: str | None = None) -> Retriever:
    """The shared retriever. If `metadata_fields` is given, return one whose
    config carries those frontmatter fields through as per-hit metadata
    (matching the CLI's --metadata-fields); otherwise reuse the default."""
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    if metadata_fields:
        cfg = replace(_retriever.cfg, metadata_fields=split_csv(metadata_fields))
        return Retriever(cfg)
    return _retriever


@mcp.tool()
def index_path(
    path: str,
    recursive: bool = True,
    source_root: str = "",
    exclude: str = "",
    metadata_fields: str = "",
) -> str:
    """Index a file or a whole folder of documents (pdf, docx, pptx, xlsx, html, md, txt...).
    Extracts text, chunks it, embeds it, and stores vectors for later search.

    Optional, for parity with the CLI:
    - source_root: store each file's source as a POSIX path relative to this root
      (e.g. the case dir), so anchors stay stable across machines; default: absolute path.
    - exclude: comma-separated filename globs to skip (e.g. "report.json,*.tmp").
    - metadata_fields: comma-separated frontmatter fields to carry through per hit
      (e.g. "quality,source_type").

    Returns a summary of how many files/chunks were indexed and what was skipped."""
    result = retriever(metadata_fields).index_path(
        path,
        recursive=recursive,
        source_root=source_root or None,
        exclude=split_csv(exclude),
    )
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.tool()
def search(query: str, k: int = 5) -> str:
    """Search the indexed documents for passages relevant to `query` and return the
    top `k` chunks (with source path and similarity score). Use these passages as
    grounding to answer the user's question yourself — this tool does NOT answer."""
    hits = retriever().search(query, k=k)
    if not hits:
        return "No relevant passages found (is anything indexed yet? run index_path first)."
    parts = []
    for i, h in enumerate(hits, 1):
        parts.append(
            f"[{i}] source={h['source']} (chunk {h['ord']}, score {h['score']})\n{h['text']}"
        )
    return "\n\n---\n\n".join(parts)


@mcp.tool()
def list_sources() -> str:
    """List the documents currently indexed and how many chunks each has."""
    return json.dumps(retriever().list_sources(), ensure_ascii=False, indent=2)


@mcp.tool()
def stats() -> str:
    """Show retriever status: embedding backend/model, storage location, and counts."""
    return json.dumps(retriever().stats(), ensure_ascii=False, indent=2)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
