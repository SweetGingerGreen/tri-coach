#!/usr/bin/env python3
"""
Build and query the local triathlon RAG vector store.

Storage is deliberately simple for v1:
- SQLite keeps chunk text, metadata, normalized float32 embeddings, and links.
- NumPy performs exact cosine search over the local vectors.
- Ollama provides local embeddings.

This avoids running a separate vector database service while keeping the data
portable enough to migrate to Chroma, Qdrant, or LanceDB later.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).parent.resolve()
DEFAULT_CHUNKS = (
    ROOT
    / "triathlon-knowledge"
    / "metadata"
    / "chunks"
    / "triathlon_core_v2_chunks.jsonl"
)
DEFAULT_DB = ROOT / "triathlon-knowledge" / "metadata" / "vectors" / "triathlon_core_v2_bge_m3.sqlite"
DEFAULT_MODEL = "bge-m3:latest"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

QUERY_EXPANSIONS = (
    (("有氧解耦", "心率漂移", "解耦"), "aerobic decoupling cardiac drift endurance decoupling aerobic endurance fatigue base period"),
    (("甜点", "甜点骑行"), "sweet spot cycling bike workout threshold fatigue residual fatigue"),
    (("ctl", "tsb", "atl"), "training load fitness fatigue form performance management chart"),
    (("髂胫束", "骼胫束", "膝盖外侧", "膝外侧", "itbs"), "ITBS iliotibial band syndrome lateral knee pain tensor fasciae latae gluteus medius"),
    (("大铁", "ironman"), "ironman long course triathlon taper carbohydrate loading hydration sweat rate race nutrition"),
    (("同期训练", "同步训练"), "concurrent training strength endurance interference combining strength and endurance training"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Embed chunks and write the SQLite vector store.")
    build.add_argument("--chunks", type=Path, default=DEFAULT_CHUNKS)
    build.add_argument("--db", type=Path, default=DEFAULT_DB)
    build.add_argument("--model", default=DEFAULT_MODEL)
    build.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    build.add_argument("--batch-size", type=int, default=32)
    build.add_argument("--limit", type=int, default=0, help="Optional first N chunks for smoke tests.")
    build.add_argument("--force", action="store_true", help="Re-embed chunks even if already current.")

    search = subparsers.add_parser("search", help="Embed a query and search the vector store.")
    search.add_argument("query")
    search.add_argument("--db", type=Path, default=DEFAULT_DB)
    search.add_argument("--model", default=None, help="Defaults to the model stored in the DB manifest.")
    search.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    search.add_argument("--top-k", type=int, default=8)
    search.add_argument("--domain", default=None)
    search.add_argument("--trust", default=None, help="Optional trust_level filter, e.g. A or B.")
    search.add_argument("--expand-neighbors", type=int, default=0)
    search.add_argument("--hybrid", action="store_true", help="Blend vector search with light lexical matching.")
    search.add_argument("--lexical-weight", type=float, default=0.35)
    search.add_argument("--no-query-expansion", action="store_true", help="Disable built-in Chinese/English triathlon term expansion.")
    search.add_argument("--include-backmatter", action="store_true", help="Include glossary, index, and bibliography chunks in results.")

    info = subparsers.add_parser("info", help="Print database summary.")
    info.add_argument("--db", type=Path, default=DEFAULT_DB)

    return parser.parse_args()


def post_json(url: str, payload: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Ollama HTTP {exc.code}: {body}") from exc


def embed_batch(
    texts: list[str],
    *,
    model: str,
    ollama_url: str,
    prefer_batch_endpoint: bool = True,
) -> list[list[float]]:
    if not texts:
        return []

    base = ollama_url.rstrip("/")
    if prefer_batch_endpoint:
        try:
            result = post_json(
                f"{base}/api/embed",
                {"model": model, "input": texts, "keep_alive": "10m"},
                timeout=240,
            )
            embeddings = result.get("embeddings")
            if isinstance(embeddings, list) and len(embeddings) == len(texts):
                return embeddings
        except Exception:
            # Older Ollama builds may only support /api/embeddings.
            pass

    embeddings: list[list[float]] = []
    for text in texts:
        result = post_json(
            f"{base}/api/embeddings",
            {"model": model, "prompt": text, "keep_alive": "10m"},
            timeout=240,
        )
        embedding = result.get("embedding")
        if not isinstance(embedding, list):
            raise RuntimeError(f"Ollama did not return an embedding for model {model}")
        embeddings.append(embedding)
    return embeddings


def normalize_vector(values: list[float]) -> np.ndarray:
    vector = np.asarray(values, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Embedding vector has zero or invalid norm")
    return vector / norm


def vector_to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def blob_to_vector(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def load_chunks(path: Path, limit: int = 0) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            chunks.append(json.loads(line))
            if limit and len(chunks) >= limit:
                break
    return chunks


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chunks (
          chunk_id TEXT PRIMARY KEY,
          text TEXT NOT NULL,
          metadata_json TEXT NOT NULL,
          collection TEXT NOT NULL,
          domain TEXT NOT NULL,
          trust_level TEXT NOT NULL,
          title TEXT NOT NULL,
          source_path TEXT NOT NULL,
          page_start INTEGER,
          page_end INTEGER,
          chunk_index INTEGER NOT NULL,
          content_sha1 TEXT NOT NULL,
          embedding_model TEXT NOT NULL,
          embedding_dim INTEGER NOT NULL,
          vector BLOB NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_chunks_domain ON chunks(domain);
        CREATE INDEX IF NOT EXISTS idx_chunks_trust_level ON chunks(trust_level);
        CREATE INDEX IF NOT EXISTS idx_chunks_source_path ON chunks(source_path);
        CREATE INDEX IF NOT EXISTS idx_chunks_content_sha1 ON chunks(content_sha1);

        CREATE TABLE IF NOT EXISTS chunk_links (
          source_chunk_id TEXT NOT NULL,
          target_chunk_id TEXT NOT NULL,
          relation TEXT NOT NULL,
          weight REAL NOT NULL,
          PRIMARY KEY (source_chunk_id, target_chunk_id, relation)
        );

        CREATE INDEX IF NOT EXISTS idx_chunk_links_source ON chunk_links(source_chunk_id);
        CREATE INDEX IF NOT EXISTS idx_chunk_links_target ON chunk_links(target_chunk_id);

        CREATE TABLE IF NOT EXISTS manifest (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        """
    )
    conn.commit()


def existing_current(conn: sqlite3.Connection, chunk_id: str, content_sha1: str, model: str) -> bool:
    row = conn.execute(
        "SELECT content_sha1, embedding_model FROM chunks WHERE chunk_id = ?",
        (chunk_id,),
    ).fetchone()
    return bool(row and row[0] == content_sha1 and row[1] == model)


def upsert_chunk(
    conn: sqlite3.Connection,
    chunk: dict[str, Any],
    vector: np.ndarray,
    model: str,
    now: str,
) -> None:
    meta = chunk["metadata"]
    conn.execute(
        """
        INSERT INTO chunks (
          chunk_id, text, metadata_json, collection, domain, trust_level, title,
          source_path, page_start, page_end, chunk_index, content_sha1,
          embedding_model, embedding_dim, vector, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(chunk_id) DO UPDATE SET
          text = excluded.text,
          metadata_json = excluded.metadata_json,
          collection = excluded.collection,
          domain = excluded.domain,
          trust_level = excluded.trust_level,
          title = excluded.title,
          source_path = excluded.source_path,
          page_start = excluded.page_start,
          page_end = excluded.page_end,
          chunk_index = excluded.chunk_index,
          content_sha1 = excluded.content_sha1,
          embedding_model = excluded.embedding_model,
          embedding_dim = excluded.embedding_dim,
          vector = excluded.vector,
          updated_at = excluded.updated_at
        """,
        (
            chunk["chunk_id"],
            chunk["text"],
            json.dumps(meta, ensure_ascii=False, sort_keys=True),
            meta.get("collection", ""),
            meta.get("domain", ""),
            meta.get("trust_level", ""),
            meta.get("title", ""),
            meta.get("source_path", ""),
            meta.get("page_start"),
            meta.get("page_end"),
            int(meta.get("chunk_index", 0)),
            meta.get("content_sha1", ""),
            model,
            int(vector.shape[0]),
            vector_to_blob(vector),
            now,
            now,
        ),
    )


def rebuild_links(conn: sqlite3.Connection, chunks: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM chunk_links")
    by_source: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        source = chunk["metadata"].get("source_path", "")
        by_source.setdefault(source, []).append(chunk)

    for source_chunks in by_source.values():
        source_chunks.sort(key=lambda item: int(item["metadata"].get("chunk_index", 0)))
        for previous, current in zip(source_chunks, source_chunks[1:]):
            previous_id = previous["chunk_id"]
            current_id = current["chunk_id"]
            conn.execute(
                "INSERT OR REPLACE INTO chunk_links VALUES (?, ?, ?, ?)",
                (previous_id, current_id, "next", 1.0),
            )
            conn.execute(
                "INSERT OR REPLACE INTO chunk_links VALUES (?, ?, ?, ?)",
                (current_id, previous_id, "prev", 1.0),
            )


def set_manifest(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    for key, value in values.items():
        conn.execute(
            "INSERT OR REPLACE INTO manifest (key, value) VALUES (?, ?)",
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True)),
        )


def get_manifest(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute("SELECT key, value FROM manifest").fetchall()
    result = {}
    for key, value in rows:
        try:
            result[key] = json.loads(value)
        except Exception:
            result[key] = value
    return result


def command_build(args: argparse.Namespace) -> int:
    chunks_path = args.chunks.resolve()
    db_path = args.db.resolve()
    chunks = load_chunks(chunks_path, limit=args.limit)
    if not chunks:
        raise ValueError(f"No chunks loaded from {chunks_path}")

    conn = connect(db_path)
    init_db(conn)

    to_embed = [
        chunk
        for chunk in chunks
        if args.force
        or not existing_current(
            conn,
            chunk["chunk_id"],
            chunk["metadata"].get("content_sha1", ""),
            args.model,
        )
    ]

    print(f"chunks_total={len(chunks)}")
    print(f"chunks_to_embed={len(to_embed)}")
    print(f"model={args.model}")
    print(f"db={display_path(db_path)}")

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    start = time.time()
    embedded = 0

    for batch_start in range(0, len(to_embed), args.batch_size):
        batch = to_embed[batch_start : batch_start + args.batch_size]
        texts = [item["text"] for item in batch]
        embeddings = embed_batch(texts, model=args.model, ollama_url=args.ollama_url)
        if len(embeddings) != len(batch):
            raise RuntimeError("Embedding count did not match batch size")
        for chunk, embedding in zip(batch, embeddings):
            vector = normalize_vector(embedding)
            upsert_chunk(conn, chunk, vector, args.model, now)
        conn.commit()
        embedded += len(batch)
        if embedded % max(args.batch_size * 5, 1) == 0 or embedded == len(to_embed):
            elapsed = max(time.time() - start, 0.001)
            rate = embedded / elapsed
            print(f"embedded={embedded}/{len(to_embed)} rate={rate:.1f}/s")

    rebuild_links(conn, chunks)
    set_manifest(
        conn,
        {
            "chunk_source": str(chunks_path.relative_to(ROOT)),
            "embedding_model": args.model,
            "chunk_count": len(chunks),
            "embedded_count": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
            "link_count": conn.execute("SELECT COUNT(*) FROM chunk_links").fetchone()[0],
            "built_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
    )
    conn.commit()
    info = get_info(conn)
    print(json.dumps(info, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def get_info(conn: sqlite3.Connection) -> dict[str, Any]:
    domain_counts = dict(
        conn.execute("SELECT domain, COUNT(*) FROM chunks GROUP BY domain ORDER BY domain").fetchall()
    )
    row = conn.execute("SELECT embedding_model, embedding_dim, COUNT(*) FROM chunks GROUP BY embedding_model, embedding_dim").fetchall()
    return {
        "chunks": conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "links": conn.execute("SELECT COUNT(*) FROM chunk_links").fetchone()[0],
        "domain_counts": domain_counts,
        "embedding_shapes": [
            {"model": model, "dim": dim, "count": count} for model, dim, count in row
        ],
        "manifest": get_manifest(conn),
    }


def load_search_matrix(
    conn: sqlite3.Connection,
    *,
    domain: str | None,
    trust: str | None,
) -> tuple[list[dict[str, Any]], np.ndarray]:
    where = []
    params: list[Any] = []
    if domain:
        where.append("domain = ?")
        params.append(domain)
    if trust:
        where.append("trust_level = ?")
        params.append(trust)
    sql = "SELECT chunk_id, text, metadata_json, vector FROM chunks"
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        raise ValueError("No chunks matched the search filters")
    records = []
    vectors = []
    for chunk_id, text, metadata_json, vector_blob in rows:
        records.append(
            {
                "chunk_id": chunk_id,
                "text": text,
                "metadata": json.loads(metadata_json),
            }
        )
        vectors.append(blob_to_vector(vector_blob))
    return records, np.vstack(vectors).astype(np.float32)


def snippet(text: str, max_chars: int = 360) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "..."


def fetch_neighbors(conn: sqlite3.Connection, chunk_id: str, depth: int) -> list[dict[str, Any]]:
    if depth <= 0:
        return []
    seen = {chunk_id}
    frontier = [chunk_id]
    neighbors: list[dict[str, Any]] = []
    for _ in range(depth):
        next_frontier = []
        for current in frontier:
            rows = conn.execute(
                """
                SELECT l.target_chunk_id, l.relation, c.text, c.metadata_json
                FROM chunk_links l
                JOIN chunks c ON c.chunk_id = l.target_chunk_id
                WHERE l.source_chunk_id = ?
                ORDER BY l.relation, c.chunk_index
                """,
                (current,),
            ).fetchall()
            for target_id, relation, text, metadata_json in rows:
                if target_id in seen:
                    continue
                seen.add(target_id)
                next_frontier.append(target_id)
                neighbors.append(
                    {
                        "chunk_id": target_id,
                        "relation": relation,
                        "text": text,
                        "metadata": json.loads(metadata_json),
                    }
                )
        frontier = next_frontier
    return neighbors


ASCII_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_+.-]*", re.IGNORECASE)
HAN_RE = re.compile(r"[\u4e00-\u9fff]")


def lexical_terms(text: str) -> set[str]:
    terms = {match.group(0).lower() for match in ASCII_TOKEN_RE.finditer(text)}
    han = HAN_RE.findall(text)
    terms.update("".join(han[idx : idx + 2]) for idx in range(max(len(han) - 1, 0)))
    terms.update("".join(han[idx : idx + 3]) for idx in range(max(len(han) - 2, 0)))
    return {term for term in terms if len(term) > 1}


def lexical_scores(query: str, records: list[dict[str, Any]]) -> np.ndarray:
    query_terms = lexical_terms(query)
    if not query_terms:
        return np.zeros(len(records), dtype=np.float32)

    record_terms = [lexical_terms(record["text"]) for record in records]
    doc_freq = {
        term: sum(1 for terms in record_terms if term in terms)
        for term in query_terms
    }
    total = max(len(records), 1)
    weights = {
        term: math.log((total + 1) / (doc_freq[term] + 1)) + 1.0
        for term in query_terms
    }
    max_possible = sum(weights.values()) or 1.0
    scores = []
    for terms in record_terms:
        matched = sum(weight for term, weight in weights.items() if term in terms)
        scores.append(matched / max_possible)
    return np.asarray(scores, dtype=np.float32)


def normalize_scores(scores: np.ndarray) -> np.ndarray:
    low = float(np.min(scores))
    high = float(np.max(scores))
    if high <= low:
        return np.zeros_like(scores, dtype=np.float32)
    return ((scores - low) / (high - low)).astype(np.float32)


def tier_weight(record: dict[str, Any]) -> float:
    meta = record.get("metadata", {})
    tier = meta.get("knowledge_tier") or meta.get("approved_status") or ""
    if tier == "approved":
        return 1.0
    if tier == "reference":
        return 0.88
    if tier == "conflicting":
        return 0.55
    return 0.95


def expand_query(query: str) -> str:
    query_lower = query.lower()
    extras = []
    for triggers, expansion in QUERY_EXPANSIONS:
        if any(trigger.lower() in query_lower for trigger in triggers):
            extras.append(expansion)
    if not extras:
        return query
    return f"{query}\n\n检索扩写: {' '.join(extras)}"


def is_backmatter(record: dict[str, Any]) -> bool:
    meta = record["metadata"]
    heading = str(meta.get("heading") or "").strip()
    heading_upper = heading.upper()
    text_start = record["text"][:500]
    title = str(meta.get("title") or "")
    page_start = meta.get("page_start")
    if title.startswith("Triathlon science") and isinstance(page_start, int) and page_start >= 590:
        return True
    if heading_upper in {"GLOSSARY", "INDEX", "REFERENCES", "REFERENCE", "BIBLIOGRAPHY"}:
        return True
    if len(heading) == 1 and heading.isalpha():
        return "#ch" in text_start or "#appendix" in text_start or bool(re.search(r"\[[0-9][0-9,– -]*\]", text_start))
    if re.search(r"\b(References|Bibliography|Index|See also)\b", text_start):
        return True
    return False


def filter_backmatter(
    records: list[dict[str, Any]],
    matrix: np.ndarray,
) -> tuple[list[dict[str, Any]], np.ndarray]:
    keep = [idx for idx, record in enumerate(records) if not is_backmatter(record)]
    if len(keep) == len(records):
        return records, matrix
    if not keep:
        raise ValueError("Only backmatter chunks matched the search filters")
    return [records[idx] for idx in keep], matrix[np.asarray(keep, dtype=np.int64)]


def resolve_embedding_model(conn: sqlite3.Connection, model: str | None) -> str:
    info = get_info(conn)
    if model is not None:
        return model
    shapes = info.get("embedding_shapes") or []
    if not shapes:
        raise ValueError("Vector store is empty")
    return shapes[0]["model"]


def search_chunks(
    conn: sqlite3.Connection,
    query: str,
    *,
    model: str | None = None,
    ollama_url: str = DEFAULT_OLLAMA_URL,
    top_k: int = 8,
    domain: str | None = None,
    trust: str | None = None,
    expand_neighbors: int = 0,
    hybrid: bool = True,
    lexical_weight: float = 0.35,
    use_query_expansion: bool = True,
    include_backmatter: bool = False,
) -> list[dict[str, Any]]:
    model = resolve_embedding_model(conn, model)
    search_query = query if not use_query_expansion else expand_query(query)
    query_embedding = embed_batch([search_query], model=model, ollama_url=ollama_url)[0]
    query_vector = normalize_vector(query_embedding)
    records, matrix = load_search_matrix(conn, domain=domain, trust=trust)
    if matrix.shape[1] != query_vector.shape[0]:
        raise ValueError(
            f"Query vector dim {query_vector.shape[0]} does not match DB dim {matrix.shape[1]}"
        )
    if not include_backmatter:
        records, matrix = filter_backmatter(records, matrix)

    scores = matrix @ query_vector
    lexical = np.zeros_like(scores, dtype=np.float32)
    rank_scores = scores
    if hybrid:
        weight = min(max(float(lexical_weight), 0.0), 1.0)
        lexical = lexical_scores(search_query, records)
        rank_scores = (1.0 - weight) * normalize_scores(scores) + weight * lexical
    tier_weights = np.asarray([tier_weight(record) for record in records], dtype=np.float32)
    rank_scores = rank_scores * tier_weights

    top_k = max(1, min(top_k, len(records)))
    top_indices = np.argpartition(-rank_scores, top_k - 1)[:top_k]
    top_indices = top_indices[np.argsort(-rank_scores[top_indices])]
    results = []
    for rank, idx in enumerate(top_indices, start=1):
        record = records[int(idx)]
        results.append(
            {
                "rank": rank,
                "score": float(rank_scores[idx]),
                "vector_score": float(scores[idx]),
                "lexical_score": float(lexical[idx]),
                "chunk": record,
                "neighbors": fetch_neighbors(conn, record["chunk_id"], expand_neighbors),
                "embedding_model": model,
                "search_query": search_query,
            }
        )
    return results


def command_search(args: argparse.Namespace) -> int:
    conn = connect(args.db.resolve())
    results = search_chunks(
        conn,
        args.query,
        model=args.model,
        ollama_url=args.ollama_url,
        top_k=args.top_k,
        domain=args.domain,
        trust=args.trust,
        expand_neighbors=args.expand_neighbors,
        hybrid=args.hybrid,
        lexical_weight=args.lexical_weight,
        use_query_expansion=not args.no_query_expansion,
        include_backmatter=args.include_backmatter,
    )

    for result in results:
        rank = result["rank"]
        record = result["chunk"]
        meta = record["metadata"]
        page = meta.get("page_start")
        page_text = f" p.{page}" if page else ""
        score_text = f"score={result['score']:.4f}"
        if args.hybrid:
            score_text += (
                f" vector={result['vector_score']:.4f}"
                f" lexical={result['lexical_score']:.4f}"
            )
        print(
            f"\n[{rank}] {score_text} "
            f"{meta.get('domain')}/{meta.get('trust_level')} {meta.get('title')}{page_text}"
        )
        print(f"chunk_id={record['chunk_id']}")
        if meta.get("heading"):
            print(f"heading={meta.get('heading')}")
        print(snippet(record["text"]))

        for neighbor in result["neighbors"]:
            nmeta = neighbor["metadata"]
            print(
                f"  - neighbor({neighbor['relation']}) "
                f"{neighbor['chunk_id']} {nmeta.get('title')} "
                f"{'p.' + str(nmeta.get('page_start')) if nmeta.get('page_start') else ''}"
            )
    return 0


def command_info(args: argparse.Namespace) -> int:
    conn = connect(args.db.resolve())
    print(json.dumps(get_info(conn), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main() -> int:
    args = parse_args()
    if args.command == "build":
        return command_build(args)
    if args.command == "search":
        return command_search(args)
    if args.command == "info":
        return command_info(args)
    raise AssertionError(args.command)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
