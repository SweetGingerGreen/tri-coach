#!/usr/bin/env python3
"""
End-to-end demo for tri-coach: chunk a tiny sample corpus, embed it, search.

This script is intentionally a thin orchestration over the project's existing
CLIs. It exists so a first-time reader can clone the repo and run a complete
ingest -> embed -> retrieve cycle without needing the private knowledge package.

Prerequisites:
- Python >= 3.10
- `pip install -r requirements.txt`
- A local Ollama instance running at $TRI_RAG_EMBEDDING_OLLAMA_URL
  (default http://127.0.0.1:11434) with the `bge-m3` model pulled:
      ollama pull bge-m3

Run from the project root:
    python3 examples/seed_demo.py
    python3 examples/seed_demo.py --query "how do I know if my aerobic base is enough?"

The demo writes its build artifacts under examples/build/ and never touches
the real triathlon-knowledge/ tree.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
SAMPLE_APPROVED = EXAMPLES / "sample_knowledge" / "01_approved"
BUILD = EXAMPLES / "build"
CHUNKS = BUILD / "demo_chunks.jsonl"
DB = BUILD / "demo_vectors.sqlite"

DEFAULT_QUERY = "What is aerobic decoupling and when should I worry about it?"
DEFAULT_MODEL = "bge-m3:latest"


def run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", default=DEFAULT_QUERY, help="Query to search after the index is built.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama embedding model name.")
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("TRI_RAG_EMBEDDING_OLLAMA_URL", "http://127.0.0.1:11434"),
        help="Ollama base URL.",
    )
    parser.add_argument("--skip-build", action="store_true", help="Reuse existing demo index if present.")
    args = parser.parse_args()

    BUILD.mkdir(parents=True, exist_ok=True)

    if not args.skip_build or not DB.exists():
        run(
            [
                sys.executable,
                "chunk_approved.py",
                "--approved-dir",
                str(SAMPLE_APPROVED),
                "--output",
                str(CHUNKS),
                "--collection",
                "tri_coach_demo",
            ]
        )
        run(
            [
                sys.executable,
                "vector_store.py",
                "build",
                "--chunks",
                str(CHUNKS),
                "--db",
                str(DB),
                "--model",
                args.model,
                "--ollama-url",
                args.ollama_url,
            ]
        )

    run(
        [
            sys.executable,
            "vector_store.py",
            "search",
            args.query,
            "--db",
            str(DB),
            "--ollama-url",
            args.ollama_url,
            "--top-k",
            "3",
        ]
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
