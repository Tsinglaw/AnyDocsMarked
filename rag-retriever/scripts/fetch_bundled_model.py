#!/usr/bin/env python3
"""Vendor the offline assets (embedding ONNX + tiktoken BPE) for release.

Run this ONCE on a machine with internet BEFORE cutting a release bundle. It:
  1. lets fastembed download the local model, copies it into
     ``rag_retriever/_models/<model>/`` (loaded offline via ``specific_model_path``);
  2. lets tiktoken download the o200k_base BPE into ``rag_retriever/_tiktoken/``
     (loaded offline via ``TIKTOKEN_CACHE_DIR``).

    python scripts/fetch_bundled_model.py                     # BAAI/bge-small-zh-v1.5
    python scripts/fetch_bundled_model.py --model <hf-id>

Why fetch instead of hand-placing files: both tools expect specific file
layouts/hash-names. Letting them produce the files guarantees the names match
what the loaders later resolve.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"
PKG = Path(__file__).resolve().parent.parent / "rag_retriever"
DEST_ROOT = PKG / "_models"
TIKTOKEN_DEST = PKG / "_tiktoken"


def _find_model_dir(cache_dir: Path) -> Path:
    """Return the directory fastembed populated (the one holding the .onnx)."""
    onnx = next((p for p in cache_dir.rglob("*.onnx")), None)
    if onnx is None:
        raise SystemExit(f"no .onnx found under {cache_dir} — did the download run?")
    return onnx.parent


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Vendor the local-embedding ONNX for release.")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"HF model id (default: {DEFAULT_MODEL})")
    args = ap.parse_args(argv[1:])

    from fastembed import TextEmbedding  # imported here so --help works without it

    # 1) embedding ONNX
    dest = DEST_ROOT / args.model.replace("/", "--")
    with tempfile.TemporaryDirectory() as tmp:
        print(f"downloading {args.model} via fastembed …", flush=True)
        TextEmbedding(model_name=args.model, cache_dir=tmp)  # triggers the download
        src = _find_model_dir(Path(tmp))
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest)
    model_mb = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file()) / 1e6
    print(f"✓ vendored model into {dest} ({model_mb:.0f} MB)")

    # 2) tiktoken o200k_base BPE — get_encoding writes it into TIKTOKEN_CACHE_DIR
    import tiktoken

    print("downloading tiktoken o200k_base …", flush=True)
    if TIKTOKEN_DEST.exists():
        shutil.rmtree(TIKTOKEN_DEST)
    TIKTOKEN_DEST.mkdir(parents=True, exist_ok=True)
    os.environ["TIKTOKEN_CACHE_DIR"] = str(TIKTOKEN_DEST)
    tiktoken.get_encoding("o200k_base").encode("warmup")  # force download + cache
    tk_mb = sum(f.stat().st_size for f in TIKTOKEN_DEST.rglob("*") if f.is_file()) / 1e6
    print(f"✓ vendored tiktoken into {TIKTOKEN_DEST} ({tk_mb:.0f} MB)")
    print("  both are git-ignored; the release wheel force-includes them via pyproject artifacts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
