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
| `ollama` | Ollama daemon | local loopback needs no consent; a remote `RAG_OLLAMA_URL` also requires `RAG_CLOUD_CONSENT=1` |
| `openai` | OpenAI-compatible API (e.g. SiliconFlow) | needs `RAG_OPENAI_API_KEY` + `RAG_CLOUD_CONSENT=1`; text leaves the machine |

`RAG_CLOUD_CONSENT` governs the data boundary, not a vendor: it is required for
OpenAI-compatible APIs and for any Ollama URL that is not loopback. `localhost`,
the whole `127.0.0.0/8` range, and `::1` remain local by default.

> ⚠️ Index-time and query-time must use the **same backend + model**. Changing the
> model means re-indexing everything.

## Configure retrieval & chunking (`.env`)

| Setting | Default | Notes |
|---|---|---|
| `RAG_CHUNK_STRATEGY` | `structure` | heading/table/legal-marker aware, or `token` for plain packing |
| `RAG_HYBRID` | `1` | BM25+vector RRF; `0` for pure vector |
| `RAG_RRF_K` | `60` | RRF constant |
| `RAG_HYBRID_CANDIDATES` | `50` | per-channel candidate pool before fusion |
| `RAG_MIN_SCORE` | `0` | cosine-similarity floor on the vector channel only (`0` = off); must be finite and within `[0,1]`, otherwise it safely falls back to `0` with a warning |
| `RAG_RERANK` | `none` | `none` (zero-model) / `local` (fastembed multilingual cross-encoder `BAAI/bge-reranker-v2-m3`, suitable for Chinese) |
| `RAG_PARENT_CONTEXT` | `false` | enable small-to-big retrieval. Every hit has a stable `parent_text` field (`None` when disabled/legacy); enabling requires a re-index to populate parents |
| `RAG_PARENT_TOKENS` | `1600` | target size of a parent block in tokens (floored to `2 × RAG_CHUNK_TOKENS`). Only used when `RAG_PARENT_CONTEXT` is on |

### Retrieval quality

Search is **hybrid by default**: a BM25 keyword channel (Chinese segmented with
jieba, fully offline) runs alongside vector similarity and the two are merged with
Reciprocal Rank Fusion. This sharpens recall for exact legal terms (e.g. 表见代理
vs 无权代理) that pure vectors blur. Set `RAG_HYBRID=0` for pure vector. An optional
cross-encoder reranker (`RAG_RERANK=local`) further reorders results — it is the
only setting that loads a model and is off by default.

Chunking is **structure-aware by default**: documents are split along markdown
headings (each chunk carries its section breadcrumb), tables are kept intact, and
legal section markers (第X条, 本院认为, …) are preferred split points. Set
`RAG_CHUNK_STRATEGY=token` for the old plain packing.

## Use (CLI, for testing)

```bash
uv run rag-retriever index "C:\path\to\docs"     # a file or a whole folder
uv run rag-retriever search "什么是表见代理" -k 5
uv run rag-retriever search "什么是表见代理" --show-parent   # also print each hit's parent block (needs RAG_PARENT_CONTEXT=1)
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
