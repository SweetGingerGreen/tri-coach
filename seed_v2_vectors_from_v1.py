#!/usr/bin/env python3
"""
Seed v2 vector DB from v1 when chunk text is unchanged.

The v2 collection keeps old approved documents but adds reference/bike material.
Embeddings for unchanged text can be reused safely because the embedding input is
chunk text, not metadata or chunk_id.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path

import vector_store


ROOT = Path(__file__).parent.resolve()
DEFAULT_CHUNKS = ROOT / "triathlon-knowledge/metadata/chunks/triathlon_core_v2_chunks.jsonl"
DEFAULT_SOURCE_DB = ROOT / "triathlon-knowledge/metadata/vectors/triathlon_core_v1_bge_m3.sqlite"
DEFAULT_TARGET_DB = ROOT / "triathlon-knowledge/metadata/vectors/triathlon_core_v2_bge_m3.sqlite"
DEFAULT_MODEL = "bge-m3:latest"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    chunks = vector_store.load_chunks(args.chunks.resolve())
    source = sqlite3.connect(args.source_db.resolve())
    target = vector_store.connect(args.target_db.resolve())
    vector_store.init_db(target)

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    seeded = 0
    already_current = 0
    missing = 0
    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        content_sha1 = chunk["metadata"].get("content_sha1", "")
        if vector_store.existing_current(target, chunk_id, content_sha1, args.model):
            already_current += 1
            continue
        row = source.execute(
            """
            SELECT vector
            FROM chunks
            WHERE content_sha1 = ?
              AND embedding_model = ?
            LIMIT 1
            """,
            (content_sha1, args.model),
        ).fetchone()
        if not row:
            missing += 1
            continue
        vector = vector_store.blob_to_vector(row[0])
        vector_store.upsert_chunk(target, chunk, vector, args.model, now)
        seeded += 1
        if seeded % 500 == 0:
            target.commit()

    vector_store.rebuild_links(target, chunks)
    vector_store.set_manifest(
        target,
        {
            "chunk_source": str(args.chunks.resolve().relative_to(ROOT)),
            "embedding_model": args.model,
            "chunk_count": len(chunks),
            "seeded_from": str(args.source_db.resolve().relative_to(ROOT)),
            "seeded_count": seeded,
            "already_current_count": already_current,
            "missing_count": missing,
            "seeded_at": now,
        },
    )
    target.commit()
    print(
        json.dumps(
            {
                "chunks": len(chunks),
                "seeded": seeded,
                "already_current": already_current,
                "missing": missing,
                "target_db": str(args.target_db),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
