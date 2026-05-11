#!/usr/bin/env python3
"""
Chunk approved/reference triathlon knowledge Markdown into JSONL for RAG.

The script intentionally does not embed or index anything. It only turns
approved long-form Markdown into small, source-aware records that a later
embedding step can consume.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent.resolve()
DEFAULT_APPROVED_DIR = ROOT / "triathlon-knowledge" / "01_approved"
DEFAULT_REFERENCE_DIR = ROOT / "triathlon-knowledge" / "02_reference"
DEFAULT_OUTPUT = (
    ROOT
    / "triathlon-knowledge"
    / "metadata"
    / "chunks"
    / "triathlon_core_v1_chunks.jsonl"
)
DEFAULT_COLLECTION = "triathlon_core_v1"

BODY_MARKER = "# 正文内容 (Content)"
SUMMARY_MARKER = "# 核心摘要 (Summary)"


@dataclass
class Atom:
    text: str
    page: int | None
    heading: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--approved-dir",
        type=Path,
        default=DEFAULT_APPROVED_DIR,
        help="Directory containing approved Markdown files.",
    )
    parser.add_argument(
        "--reference-dir",
        type=Path,
        default=DEFAULT_REFERENCE_DIR,
        help="Directory containing second-tier reference Markdown files.",
    )
    parser.add_argument(
        "--include-reference",
        action="store_true",
        help="Also chunk Markdown files from --reference-dir.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="JSONL chunk output path.",
    )
    parser.add_argument(
        "--collection",
        default=DEFAULT_COLLECTION,
        help="RAG collection name to write into chunk metadata.",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=1800,
        help="Approximate maximum body characters per chunk before context header.",
    )
    parser.add_argument(
        "--overlap-chars",
        type=int,
        default=220,
        help="Approximate character overlap between adjacent chunks.",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=280,
        help="Minimum target chunk size; smaller trailing chunks are merged when possible.",
    )
    return parser.parse_args()


def stable_hash(value: str, length: int = 12) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except Exception:
            return [part.strip().strip("\"'") for part in value[1:-1].split(",") if part.strip()]
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        try:
            return ast.literal_eval(value)
        except Exception:
            return value[1:-1]
    return value


def parse_frontmatter(text: str, path: Path) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise ValueError(f"Missing frontmatter: {path}")
    parts = text.split("---", 2)
    if len(parts) < 3:
        raise ValueError(f"Malformed frontmatter: {path}")

    metadata: dict[str, Any] = {}
    for line in parts[1].splitlines():
        if not line.strip() or line.startswith(" "):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = parse_scalar(value)
    return metadata, parts[2].lstrip("\n")


def extract_body(markdown_without_frontmatter: str) -> str:
    if BODY_MARKER in markdown_without_frontmatter:
        return markdown_without_frontmatter.split(BODY_MARKER, 1)[1].lstrip()
    if SUMMARY_MARKER in markdown_without_frontmatter:
        return markdown_without_frontmatter.split(SUMMARY_MARKER, 1)[-1].lstrip()
    return markdown_without_frontmatter


def clean_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"<span[^>]*>", "", line)
    line = line.replace("</span>", "")
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def normalize_paragraph(lines: list[str]) -> str:
    cleaned = [clean_line(line) for line in lines]
    cleaned = [line for line in cleaned if line]
    return " ".join(cleaned).strip()


def split_text_atoms(text: str, max_atom_chars: int) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    if len(text) <= max_atom_chars:
        return [text]

    sentence_pattern = r".+?(?:[。！？!?](?=\s|$)|\.(?=\s+[A-Z0-9\"'“‘]|$)|；|;|$)"
    sentences = [match.group(0).strip() for match in re.finditer(sentence_pattern, text)]
    if not sentences:
        sentences = [text]

    atoms: list[str] = []
    current = ""
    for sentence in sentences:
        if not sentence:
            continue
        if len(sentence) > max_atom_chars:
            if current:
                atoms.append(current.strip())
                current = ""
            start = 0
            while start < len(sentence):
                atoms.append(sentence[start : start + max_atom_chars].strip())
                start += max_atom_chars
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if len(candidate) > max_atom_chars and current:
            atoms.append(current.strip())
            current = sentence
        else:
            current = candidate
    if current:
        atoms.append(current.strip())
    return atoms


def markdown_to_atoms(body: str, default_heading: str, max_atom_chars: int) -> list[Atom]:
    atoms: list[Atom] = []
    page: int | None = None
    heading_stack: dict[int, str] = {}
    paragraph: list[str] = []

    def current_heading() -> str:
        if not heading_stack:
            return default_heading
        level = max(heading_stack)
        return heading_stack[level] or default_heading

    def flush_paragraph() -> None:
        nonlocal paragraph
        text = normalize_paragraph(paragraph)
        paragraph = []
        if not text:
            return
        for atom_text in split_text_atoms(text, max_atom_chars=max_atom_chars):
            atoms.append(Atom(text=atom_text, page=page, heading=current_heading()))

    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            flush_paragraph()
            continue

        page_match = re.match(r"^##\s+Page\s+(\d+)\b", stripped, flags=re.IGNORECASE)
        if page_match:
            flush_paragraph()
            page = int(page_match.group(1))
            continue

        heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", stripped)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            heading_text = clean_line(heading_match.group(2))
            if heading_text and not heading_text.lower().startswith("page "):
                for existing_level in list(heading_stack):
                    if existing_level >= level:
                        del heading_stack[existing_level]
                heading_stack[level] = heading_text
            continue

        if stripped.startswith("!["):
            continue
        if stripped.startswith("> source:") or stripped.startswith("> pages:"):
            continue

        paragraph.append(stripped)

    flush_paragraph()
    return atoms


def suffix_overlap_atoms(atoms: list[Atom], overlap_chars: int) -> list[Atom]:
    if overlap_chars <= 0:
        return []
    result: list[Atom] = []
    total = 0
    for atom in reversed(atoms):
        atom_len = len(atom.text)
        if result and total + atom_len > overlap_chars:
            break
        result.append(atom)
        total += atom_len
    return list(reversed(result))


def unique_ordered(values: list[Any]) -> list[Any]:
    seen = set()
    result = []
    for value in values:
        if value is None or value == "":
            continue
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True)
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result


def build_chunk_text(title: str, atoms: list[Atom]) -> str:
    body = "\n\n".join(atom.text for atom in atoms).strip()
    headings = unique_ordered([atom.heading for atom in atoms])
    pages = unique_ordered([atom.page for atom in atoms])

    context = [f"资料: {title}"]
    if headings and headings != [title]:
        context.append(f"章节: {' > '.join(str(item) for item in headings[:3])}")
    if pages:
        if len(pages) == 1:
            context.append(f"页码: {pages[0]}")
        else:
            context.append(f"页码: {pages[0]}-{pages[-1]}")
    return "\n".join(context) + "\n\n" + body


def emit_chunk(
    chunks: list[dict[str, Any]],
    atoms: list[Atom],
    metadata: dict[str, Any],
    source_path: Path,
    rel_source_path: str,
    collection: str,
    doc_id: str,
    chunk_index: int,
) -> None:
    if not atoms:
        return

    title = str(metadata.get("title") or source_path.stem)
    pages = unique_ordered([atom.page for atom in atoms])
    headings = unique_ordered([atom.heading for atom in atoms])
    chunk_text = build_chunk_text(title, atoms)

    chunk = {
        "chunk_id": f"{collection}:{metadata.get('domain', 'unknown')}:{doc_id}:{chunk_index:05d}",
        "text": chunk_text,
        "metadata": {
            "collection": collection,
            "doc_id": doc_id,
            "chunk_index": chunk_index,
            "title": title,
            "domain": metadata.get("domain", ""),
            "trust_level": metadata.get("trust_level", ""),
            "language": metadata.get("language", ""),
            "author": metadata.get("author", ""),
            "tags": metadata.get("tags", []),
            "usage_rule": metadata.get("usage_rule", ""),
            "approved_status": metadata.get("approved_status", ""),
            "approved_date": metadata.get("approved_date", ""),
            "knowledge_tier": metadata.get("knowledge_tier", metadata.get("approved_status", "")),
            "knowledge_type": metadata.get("knowledge_type", ""),
            "rag_collection": collection,
            "source_rag_collection": metadata.get("rag_collection", ""),
            "source_path": rel_source_path,
            "approved_source_path": metadata.get("approved_source_path", ""),
            "reference_source_path": metadata.get("reference_source_path", ""),
            "page_start": pages[0] if pages else None,
            "page_end": pages[-1] if pages else None,
            "pages": pages,
            "heading": headings[-1] if headings else "",
            "headings": headings,
            "char_count": len(chunk_text),
            "content_sha1": hashlib.sha1(chunk_text.encode("utf-8")).hexdigest(),
        },
    }
    chunks.append(chunk)


def chunk_atoms(
    atoms: list[Atom],
    metadata: dict[str, Any],
    source_path: Path,
    approved_dir: Path,
    collection: str,
    max_chars: int,
    overlap_chars: int,
    min_chars: int,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    rel_source_path = str(source_path.relative_to(ROOT))
    try:
        identity_path = source_path.relative_to(approved_dir)
    except ValueError:
        identity_path = source_path.relative_to(ROOT)
    doc_id = stable_hash(str(identity_path))
    current: list[Atom] = []
    current_chars = 0
    current_heading = ""

    def flush(with_overlap: bool) -> None:
        nonlocal current, current_chars, current_heading
        if not current:
            return
        emit_chunk(
            chunks=chunks,
            atoms=current,
            metadata=metadata,
            source_path=source_path,
            rel_source_path=rel_source_path,
            collection=collection,
            doc_id=doc_id,
            chunk_index=len(chunks),
        )
        current = suffix_overlap_atoms(current, overlap_chars) if with_overlap else []
        current_chars = sum(len(atom.text) for atom in current)
        current_heading = current[-1].heading if current else ""

    for atom in atoms:
        atom_chars = len(atom.text)
        heading_changed = current and atom.heading != current_heading
        if heading_changed and current_chars >= min_chars:
            flush(with_overlap=False)

        if current and current_chars + atom_chars > max_chars:
            flush(with_overlap=True)

        current.append(atom)
        current_chars += atom_chars
        current_heading = atom.heading

    if current:
        if chunks and current_chars < min_chars:
            # Small trailing chunks are still emitted because merging them can
            # blur headings/pages. The metadata makes them easy to filter later.
            flush(with_overlap=False)
        else:
            flush(with_overlap=False)

    return chunks


def validate_metadata(metadata: dict[str, Any], path: Path, collection: str) -> None:
    required = ["title", "domain", "trust_level", "tags", "usage_rule", "approved_status"]
    missing = [key for key in required if not metadata.get(key)]
    if missing:
        raise ValueError(f"{path} missing metadata fields: {', '.join(missing)}")
    if metadata.get("approved_status") not in {"approved", "reference"}:
        raise ValueError(f"{path} approved_status must be approved or reference")
    if not isinstance(metadata.get("tags"), list):
        metadata["tags"] = [str(metadata["tags"])]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def build_manifest(chunks: list[dict[str, Any]], output: Path, input_dirs: list[Path]) -> dict[str, Any]:
    by_domain: dict[str, int] = {}
    by_doc: dict[str, int] = {}
    by_tier: dict[str, int] = {}
    for chunk in chunks:
        meta = chunk["metadata"]
        by_domain[meta["domain"]] = by_domain.get(meta["domain"], 0) + 1
        by_doc[meta["source_path"]] = by_doc.get(meta["source_path"], 0) + 1
        tier = meta.get("knowledge_tier") or meta.get("approved_status") or "unknown"
        by_tier[tier] = by_tier.get(tier, 0) + 1
    return {
        "input_dirs": [str(path.relative_to(ROOT)) for path in input_dirs],
        "output": str(output.relative_to(ROOT)),
        "chunk_count": len(chunks),
        "domain_counts": dict(sorted(by_domain.items())),
        "tier_counts": dict(sorted(by_tier.items())),
        "document_counts": dict(sorted(by_doc.items())),
    }


def main() -> int:
    args = parse_args()
    approved_dir = args.approved_dir.resolve()
    reference_dir = args.reference_dir.resolve()
    output = args.output.resolve()

    if not approved_dir.exists():
        raise FileNotFoundError(f"Approved directory does not exist: {approved_dir}")
    input_dirs = [approved_dir]
    if args.include_reference:
        if not reference_dir.exists():
            raise FileNotFoundError(f"Reference directory does not exist: {reference_dir}")
        input_dirs.append(reference_dir)
    if args.overlap_chars >= args.max_chars:
        raise ValueError("--overlap-chars must be smaller than --max-chars")

    markdown_files = []
    for input_dir in input_dirs:
        markdown_files.extend(sorted(input_dir.rglob("*.md")))
    if not markdown_files:
        raise ValueError("No Markdown files found under input directories")

    all_chunks: list[dict[str, Any]] = []
    max_atom_chars = max(500, args.max_chars // 2)

    for path in markdown_files:
        raw = path.read_text(encoding="utf-8", errors="ignore")
        metadata, markdown = parse_frontmatter(raw, path)
        validate_metadata(metadata, path, args.collection)
        body = extract_body(markdown)
        title = str(metadata.get("title") or path.stem)
        atoms = markdown_to_atoms(body, default_heading=title, max_atom_chars=max_atom_chars)
        if not atoms:
            raise ValueError(f"No chunkable body text found: {path}")
        chunks = chunk_atoms(
            atoms=atoms,
            metadata=metadata,
            source_path=path,
            approved_dir=approved_dir,
            collection=args.collection,
            max_chars=args.max_chars,
            overlap_chars=args.overlap_chars,
            min_chars=args.min_chars,
        )
        all_chunks.extend(chunks)

    write_jsonl(output, all_chunks)
    manifest = build_manifest(all_chunks, output=output, input_dirs=input_dirs)
    manifest_path = output.with_name(output.stem + "_manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"documents={len(markdown_files)}")
    print(f"chunks={len(all_chunks)}")
    print(f"jsonl={output.relative_to(ROOT)}")
    print(f"manifest={manifest_path.relative_to(ROOT)}")
    print("domain_counts=" + json.dumps(manifest["domain_counts"], ensure_ascii=False, sort_keys=True))
    print("tier_counts=" + json.dumps(manifest["tier_counts"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
