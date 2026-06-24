# rag-retriever

A lightweight, **local-first document retrieval engine** that mounts to an agent
as an **MCP tool**. Drop files in; the agent searches them and answers with **its
own LLM**. There is no LLM in here — this is only the "front half" of RAG
(extract → chunk → embed → store + similarity search).

```
your agent (owns the LLM)
   │  calls MCP tool: search("question")
   ▼
rag-retriever ──► extract ─► chunk ─► embed ─► LanceDB
   ▲                                              │
   └────────── returns relevant passages ◄────────┘
   │
   agent reads passages → answers with its own LLM
```

Built from the same proven pieces as Open Notebook (file extraction + bge-m3
embeddings + vector search), minus the heavyweight backend, UI, and answer/podcast
generation you don't need.

## Why this shape

- **One LLM, not two.** The retriever never answers; your agent does. You keep
  full control of reasoning, prompts, and cost.
- **Local-first.** Default backend (`fastembed`) runs entirely offline, no server.
- **Pluggable embeddings.** Switch between fully local and a China-friendly cloud
  API (SiliconFlow) with one env var — no code change.

## Install

```bash
cd rag-retriever
uv sync
cp .env.example .env   # then pick your embedding backend
```

## Configure the embedding backend (`.env`)

| `RAG_EMBED_BACKEND` | What it uses | Notes |
|---|---|---|
| `local` (default) | fastembed (ONNX, in-process) | 100% offline, no server, heavier first install |
| `ollama` | local Ollama daemon | `ollama serve` + `ollama pull bge-m3` |
| `openai` | OpenAI-compatible API (e.g. SiliconFlow) | needs `RAG_OPENAI_API_KEY`; text leaves the machine |

> ⚠️ Index-time and query-time must use the **same backend + model**. Changing the
> model means re-indexing everything.

## Use (CLI, for testing)

```bash
uv run rag-retriever index "C:\path\to\docs"     # a file or a whole folder
uv run rag-retriever search "什么是表见代理" -k 5
uv run rag-retriever list
uv run rag-retriever stats
uv run rag-retriever doctor          # check manifest vs table; add --fix to repair
```

## Mount as an MCP server (the real entry point)

Run `uv run rag-retriever-mcp` (stdio). Register it with your MCP client. For
Claude Code, add to your MCP config:

```json
{
  "mcpServers": {
    "rag-retriever": {
      "command": "uv",
      "args": ["run", "--directory", "D:\\Vibe Coding Items\\rag-retriever", "rag-retriever-mcp"]
    }
  }
}
```

Tools exposed: `index_path`, `search`, `list_sources`, `stats`.

## Supported files

pdf, docx, pptx, xlsx, html, md, txt, csv, json, epub (via markitdown).
**Scanned / image-only PDFs** need an OCR engine (tesseract) installed separately;
without it they extract empty and are reported as skipped.

## Layout

```
rag_retriever/
  config.py     # env-driven config; picks the embedding backend
  extract.py    # file -> text (markitdown)
  chunk.py      # token-based chunking with overlap
  embed.py      # local | ollama | openai-compatible backends
  store.py      # LanceDB vector store (embedded, no server)
  pipeline.py   # ingest + search orchestration (no LLM)
  server.py     # MCP server (agent-facing)
  cli.py        # manual CLI
```
