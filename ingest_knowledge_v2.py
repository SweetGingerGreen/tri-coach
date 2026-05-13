#!/usr/bin/env python3
"""
铁三大脑 - 知识摄取引擎 v2 (多格式分派版)

支持格式:
    .pdf                           → pypdf 快速文本抽取，可选回退 MinerU/OCR
    .docx / .pptx / .xlsx          → MinerU (pipeline + auto)
    .epub                          → pandoc
    .mobi / .azw3 / .fb2          → calibre (ebook-convert)

用法:
    python ingest_knowledge_v2.py audit              # 审计现有 md 质量
    python ingest_knowledge_v2.py audit --path DIR
    python ingest_knowledge_v2.py ingest             # 转换 inbox 下所有支持的文件
    python ingest_knowledge_v2.py ingest --force     # 已处理的也重跑

前置依赖:
    pip install -U "mineru[core]"    # PDF/Office, 需 Python 3.10+
    brew install pandoc              # EPUB
    brew install --cask calibre      # 可选, 兜底奇葩格式

健康检查规则 (任一不通过即判"需人工复核"):
    - 正文字符数 >= 2000
    - 图片占位符行占比 <= 30%，但图谱类资料正文足够多时允许更高图片占比
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

ROOT = Path(__file__).parent
INBOX = ROOT / "triathlon-knowledge" / "00_inbox"
PROCESSED = INBOX / "processed"
FAILED = INBOX / "failed_needs_review"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434/api/generate")
MINERU_API_URL = os.environ.get("MINERU_API_URL")
MINERU_LANG = os.environ.get("MINERU_LANG")
MINERU_TIMEOUT_SECONDS = int(os.environ.get("MINERU_TIMEOUT_SECONDS", "7200"))


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


MINERU_FORMULA_ENABLE = env_bool("MINERU_FORMULA_ENABLE", True)
MINERU_TABLE_ENABLE = env_bool("MINERU_TABLE_ENABLE", True)

IMG_RATIO_MAX = 0.30
MIN_TEXT_CHARS = 2000
IMG_RATIO_BYPASS_TEXT_CHARS = 20000
PDF_TEXT_MIN_CHARS = 10000
ALLOWED_DOMAINS = {"swim", "bike", "run", "strength", "nutrition", "recovery", "race"}

MINERU_EXTS = {".pdf", ".docx", ".pptx", ".xlsx"}
PANDOC_EXTS = {".epub"}
CALIBRE_EXTS = {".mobi", ".azw3", ".azw", ".fb2", ".lit", ".pdb"}
SUPPORTED_EXTS = MINERU_EXTS | PANDOC_EXTS | CALIBRE_EXTS


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def iter_source_files(path: Path, recursive: bool) -> Iterable[Path]:
    if path.is_file():
        if path.suffix.lower() in SUPPORTED_EXTS:
            yield path
        return

    iterator = path.rglob("*") if recursive else path.iterdir()
    for src in iterator:
        if not src.is_file():
            continue
        if src.suffix.lower() not in SUPPORTED_EXTS:
            continue
        if is_under(src, PROCESSED) or is_under(src, FAILED):
            continue
        yield src


def output_dir_for(src: Path, source_root: Path) -> Path:
    if source_root.is_file():
        return PROCESSED
    try:
        rel_parent = src.parent.relative_to(source_root)
    except ValueError:
        rel_parent = src.parent.relative_to(INBOX) if is_under(src.parent, INBOX) else Path()
    return PROCESSED / rel_parent


def health_check(md_path: Path) -> Tuple[bool, str]:
    """返回 (是否健康, 诊断信息)"""
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return False, f"读取失败: {e}"

    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False, "空文件"

    img_lines = sum(1 for ln in lines if ln.lstrip().startswith("!["))
    img_ratio = img_lines / len(lines)

    text_chars = sum(
        len(ln) for ln in lines
        if not ln.lstrip().startswith(("![", "#"))
    )

    issues = []
    if img_ratio > IMG_RATIO_MAX and text_chars < IMG_RATIO_BYPASS_TEXT_CHARS:
        issues.append(f"图片占位符比例 {img_ratio:.1%} (阈值 {IMG_RATIO_MAX:.0%}) — 疑似扫描件未 OCR")
    if text_chars < MIN_TEXT_CHARS:
        issues.append(f"正文字符数仅 {text_chars} (阈值 {MIN_TEXT_CHARS}) — 疑似提取失败")

    if issues:
        return False, "; ".join(issues)
    return True, f"{len(lines)} 行 / 正文 {text_chars} 字符 / 图片占比 {img_ratio:.1%}"


def cmd_audit(path: Path) -> int:
    if not path.exists():
        print(f"❌ 目录不存在: {path}")
        return 1

    md_files = sorted(path.rglob("*.md"))
    if not md_files:
        print(f"📭 {path} 下没有 md 文件")
        return 0

    print(f"🔍 审计 {len(md_files)} 份 md (目录: {path})\n")
    ok, bad = [], []
    for md in md_files:
        healthy, msg = health_check(md)
        icon = "✅" if healthy else "⚠️ "
        print(f"{icon} {md.name}")
        print(f"    {msg}")
        (ok if healthy else bad).append(md.name)

    print("\n" + "=" * 60)
    print(f"结果: ✅ 健康 {len(ok)} / ⚠️  需复核 {len(bad)}")
    if bad:
        print("\n建议对以下文件用 MinerU (带 OCR) 重新处理:")
        for name in bad:
            print(f"  - {name}")
    return 0


def _mineru_cmd() -> Optional[str]:
    """优先查当前 python 同目录下的 mineru (venv 场景)，其次查 PATH"""
    same_dir = Path(sys.executable).parent / "mineru"
    if same_dir.exists():
        return str(same_dir)
    return shutil.which("mineru")


def check_mineru_available() -> bool:
    return _mineru_cmd() is not None


def mineru_lang(src: Path) -> str:
    if MINERU_LANG:
        return MINERU_LANG
    return "ch" if re.search(r"[\u4e00-\u9fff]", str(src)) else "en"


def convert_with_mineru(src: Path, out_dir: Path) -> Tuple[bool, str]:
    """MinerU CLI (pipeline + 自动判断文本/OCR)"""
    cmd = [
        _mineru_cmd(),
        "-p", str(src),
        "-o", str(out_dir),
        "-b", "pipeline",
        "-m", "auto",
        "-l", mineru_lang(src),
        "--formula", str(MINERU_FORMULA_ENABLE).lower(),
        "--table", str(MINERU_TABLE_ENABLE).lower(),
    ]
    if MINERU_API_URL:
        cmd.extend(["--api-url", MINERU_API_URL])
    try:
        env = os.environ.copy()
        env.setdefault("NO_PROXY", "127.0.0.1,localhost")
        env.setdefault("no_proxy", "127.0.0.1,localhost")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=MINERU_TIMEOUT_SECONDS, env=env)
    except subprocess.TimeoutExpired:
        return False, f"超时 (>{MINERU_TIMEOUT_SECONDS} 秒)"
    except Exception as e:
        return False, f"调用异常: {e}"
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-300:]
        return False, f"MinerU 退出码 {result.returncode}: {tail.strip()}"
    return True, ""


def convert_with_pypdf(src: Path, out_dir: Path) -> Tuple[bool, str]:
    """PDF → Markdown，优先用于文本型电子书。扫描件会因正文太少而回退 MinerU。"""
    try:
        from pypdf import PdfReader
    except Exception as e:
        return False, f"pypdf 不可用: {e}"

    target_md = out_dir / f"{src.stem}.md"
    try:
        reader = PdfReader(str(src))
        sections = [f"# {title_from_filename(src)}", "", f"> source: {src.name}", f"> pages: {len(reader.pages)}", ""]
        text_chars = 0
        for idx, page in enumerate(reader.pages, 1):
            page_text = page.extract_text() or ""
            page_text = re.sub(r"\n{3,}", "\n\n", page_text).strip()
            if not page_text:
                continue
            text_chars += len(page_text)
            sections.extend([f"## Page {idx}", "", page_text, ""])
    except Exception as e:
        return False, f"pypdf 抽取失败: {e}"

    if text_chars < PDF_TEXT_MIN_CHARS:
        return False, f"pypdf 正文字符数仅 {text_chars}，需要 MinerU/OCR"

    target_md.write_text("\n".join(sections).strip() + "\n", encoding="utf-8")
    return True, ""


def convert_with_pandoc(src: Path, out_dir: Path) -> Tuple[bool, str]:
    """EPUB → Markdown (含图片导出)"""
    stem = src.stem
    target_md = out_dir / f"{stem}.md"
    media_dir = out_dir / f"{stem}_images"
    media_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "pandoc", str(src),
        "-o", str(target_md),
        "--wrap=none",
        "--extract-media", str(media_dir),
        "-t", "gfm",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return False, "pandoc 超时 (>10 分钟)"
    except Exception as e:
        return False, f"pandoc 调用异常: {e}"
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-300:]
        return False, f"pandoc 退出码 {result.returncode}: {tail.strip()}"
    return True, ""


def convert_with_calibre(src: Path, out_dir: Path) -> Tuple[bool, str]:
    """mobi/azw3 等 → TXT via calibre ebook-convert，再包成 Markdown"""
    stem = src.stem
    target = out_dir / f"{stem}.md"
    tmp_txt = out_dir / f"{stem}.txt"
    cmd = ["ebook-convert", str(src), str(tmp_txt)]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    except subprocess.TimeoutExpired:
        return False, "calibre 超时 (>20 分钟)"
    except Exception as e:
        return False, f"calibre 调用异常: {e}"
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "")[-300:]
        return False, f"calibre 退出码 {result.returncode}: {tail.strip()}"
    try:
        text = tmp_txt.read_text(encoding="utf-8", errors="ignore").strip()
        target.write_text(
            "\n".join([f"# {title_from_filename(src)}", "", f"> source: {src.name}", "", text, ""]),
            encoding="utf-8",
        )
        tmp_txt.unlink(missing_ok=True)
    except Exception as e:
        return False, f"calibre TXT 包装 Markdown 失败: {e}"
    return True, ""


def dispatch(src: Path, out_dir: Path, ocr_fallback: bool) -> Tuple[bool, str, str]:
    """按扩展名分派转换器。返回 (成功, 错误信息, 工具名)"""
    ext = src.suffix.lower()
    if ext == ".pdf":
        ok, err = convert_with_pypdf(src, out_dir)
        if ok:
            return True, "", "pypdf"
        if not ocr_fallback:
            return False, f"{err}; 未启用 OCR 回退 (--ocr-fallback)", "pypdf"
        if not _mineru_cmd():
            return False, f"{err}; mineru 未安装", "pypdf/mineru"
        ok, mineru_err = convert_with_mineru(src, out_dir)
        if ok:
            return True, "", "mineru"
        return False, f"pypdf: {err}; mineru: {mineru_err}", "pypdf/mineru"
    if ext in MINERU_EXTS:
        if not _mineru_cmd():
            return False, "mineru 未安装", "mineru"
        ok, err = convert_with_mineru(src, out_dir)
        return ok, err, "mineru"
    if ext in PANDOC_EXTS:
        if not shutil.which("pandoc"):
            return False, "pandoc 未安装 (brew install pandoc)", "pandoc"
        ok, err = convert_with_pandoc(src, out_dir)
        return ok, err, "pandoc"
    if ext in CALIBRE_EXTS:
        if not shutil.which("ebook-convert"):
            return False, "calibre 未安装 (brew install --cask calibre)", "calibre"
        ok, err = convert_with_calibre(src, out_dir)
        return ok, err, "calibre"
    return False, f"不支持的扩展名: {ext}", "none"


def find_output_md(out_dir: Path, stem: str) -> Optional[Path]:
    """MinerU: <out>/<stem>/auto|ocr/<stem>.md; pandoc/calibre: <out>/<stem>.md"""
    candidates = list(out_dir.rglob(f"{stem}*.md"))
    if not candidates and len(stem) > 60:
        prefix = stem[:60]
        candidates = [c for c in out_dir.rglob("*.md") if c.stem.startswith(prefix)]
    candidates = [c for c in candidates if "images" not in c.parts and "_images" not in c.parent.name]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


def has_knowledge_template(text: str) -> bool:
    head = text[:1500]
    return head.startswith("---\n") and "# 核心摘要 (Summary)" in head and "# 正文内容 (Content)" in text[:4000]


def infer_domain(src: Path) -> str:
    text = str(src).lower()
    if any(x in text for x in ("游泳", "swim")):
        return "swim"
    if any(x in text for x in ("自行车", "bike", "cycling")):
        return "bike"
    if any(x in text for x in ("跑步", "running", "run", "marathon")):
        return "run"
    if any(x in text for x in ("力量", "strength", "nsca", "cscs", "cpt", "triphasic", "训练学")):
        return "strength"
    if any(x in text for x in ("营养", "nutrition", "diet", "膳食")):
        return "nutrition"
    if any(x in text for x in ("损伤", "按摩", "解剖", "recovery", "injury", "massage", "anatomy")):
        return "recovery"
    return "race"


def infer_language(text: str) -> str:
    sample = text[:6000]
    zh_chars = len(re.findall(r"[\u4e00-\u9fff]", sample))
    return "zh" if zh_chars >= 80 else "en"


# Trailing source-tag suffixes that some ebook tools/sites append to filenames
# (e.g. " (Some-Library)"). Configure via env if you want to strip a specific
# marker; otherwise the generic regex below removes any short
# capital-led parenthesized tag at the end of the stem.
_SOURCE_TAG_RE = re.compile(r"\s*\(\s*[A-Z][A-Za-z0-9-]{2,}\s*\)\s*$")


def title_from_filename(src: Path) -> str:
    title = _SOURCE_TAG_RE.sub("", src.stem)
    title = re.sub(r"\s+", " ", title).strip()
    return title or src.stem


def author_from_filename(src: Path) -> str:
    title = _SOURCE_TAG_RE.sub("", src.stem)
    matches = re.findall(r"\(([^()]+)\)", title)
    candidates = [m.strip() for m in matches if m.strip()]
    return candidates[-1] if candidates else "unknown"


def read_metadata_sample(md_path: Path) -> str:
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    headings = [ln.strip() for ln in text.splitlines() if ln.lstrip().startswith("#")]
    parts = []
    if headings:
        parts.append("目录/标题片段:\n" + "\n".join(headings[:80]))
    parts.append("正文开头片段:\n" + text[:9000])
    return "\n\n".join(parts)


def parse_json_object(raw: str) -> Dict[str, Any]:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}


def ask_local_gemma_for_metadata(src: Path, md_path: Path, model: str) -> Dict[str, Any]:
    sample = read_metadata_sample(md_path)
    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
        "system": (
            "你是一个私有铁人三项知识库整理员。只根据给定文件名、目录和正文片段生成 RAG 元数据。"
            "不要编造没有证据的事实。只输出 JSON。"
        ),
        "prompt": (
            "请输出 JSON，字段必须包含: "
            "title, domain, source, trust_level, language, author, tags, usage_rule, summary。\n"
            f"domain 只能是: {', '.join(sorted(ALLOWED_DOMAINS))}。\n"
            "trust_level 默认 B，除非是官方指南/标准才可用 A。\n"
            "summary 用中文一句话概括，tags 输出 3 到 8 个短标签。\n\n"
            f"文件路径: {src}\n"
            f"文件名: {src.name}\n\n"
            f"{sample}"
        ),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        return {"_error": str(e)}
    return parse_json_object(str(body.get("response", "")))


def fallback_metadata(src: Path, md_path: Path) -> Dict[str, Any]:
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    title = title_from_filename(src)
    domain = infer_domain(src)
    return {
        "title": title,
        "domain": domain,
        "source": str(src.relative_to(ROOT)) if is_under(src, ROOT) else str(src),
        "trust_level": "B",
        "language": infer_language(text),
        "author": author_from_filename(src),
        "tags": [domain, "triathlon", "book"],
        "usage_rule": "作为训练知识参考使用；回答时需要结合个人状态、伤病风险和资料出处，不可当作唯一依据。",
        "summary": f"这是一份关于 {title} 的铁人三项相关参考资料，已转换为可检索的 Markdown。",
    }


def clean_metadata(meta: Dict[str, Any], src: Path, md_path: Path) -> Dict[str, Any]:
    fallback = fallback_metadata(src, md_path)
    cleaned = {**fallback, **{k: v for k, v in meta.items() if v}}
    cleaned["source"] = fallback["source"]

    domain = str(cleaned.get("domain", "")).strip().lower()
    cleaned["domain"] = domain if domain in ALLOWED_DOMAINS else fallback["domain"]

    trust = str(cleaned.get("trust_level", "B")).strip().upper()
    cleaned["trust_level"] = trust if trust in {"A", "B"} else "B"

    language = str(cleaned.get("language", fallback["language"])).strip().lower()
    cleaned["language"] = language if language in {"zh", "en"} else fallback["language"]

    tags = cleaned.get("tags", fallback["tags"])
    if isinstance(tags, str):
        tags = [tag.strip() for tag in re.split(r"[,，/ ]+", tags) if tag.strip()]
    if not isinstance(tags, list):
        tags = fallback["tags"]
    cleaned["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()][:8]

    for key in ("title", "author", "usage_rule", "summary"):
        cleaned[key] = str(cleaned.get(key, fallback[key])).strip() or fallback[key]
    if len(cleaned["usage_rule"]) < 20:
        cleaned["usage_rule"] = fallback["usage_rule"]
    cleaned["date"] = dt.date.today().isoformat()
    return cleaned


def yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def format_tags(tags: Any) -> str:
    if not isinstance(tags, list):
        tags = [str(tags)]
    return "[" + ", ".join(yaml_scalar(str(tag)) for tag in tags) + "]"


def apply_knowledge_template(md_path: Path, src: Path, model: str) -> Tuple[bool, str]:
    text = md_path.read_text(encoding="utf-8", errors="ignore")
    if has_knowledge_template(text):
        return True, "已是知识库模板格式"

    model_meta = ask_local_gemma_for_metadata(src, md_path, model)
    model_error = model_meta.pop("_error", "")
    meta = clean_metadata(model_meta, src, md_path)

    frontmatter = "\n".join([
        "---",
        f"title: {yaml_scalar(meta['title'])}",
        f"domain: {meta['domain']}",
        f"source: {yaml_scalar(meta['source'])}",
        f"trust_level: {meta['trust_level']}",
        f"language: {meta['language']}",
        f"author: {yaml_scalar(meta['author'])}",
        f"date: {meta['date']}",
        f"tags: {format_tags(meta['tags'])}",
        f"usage_rule: {yaml_scalar(meta['usage_rule'])}",
        "---",
        "",
        "# 核心摘要 (Summary)",
        meta["summary"],
        "",
        "# 正文内容 (Content)",
        text.lstrip(),
    ])
    md_path.write_text(frontmatter, encoding="utf-8")
    if model_error:
        return True, f"已套模板；Gemma 元数据失败，使用规则兜底: {model_error}"
    return True, "已套模板并由本地 Gemma 生成元数据"


def cmd_ingest(source_path: Path, recursive: bool, force: bool, metadata: bool, model: str, ocr_fallback: bool) -> int:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    FAILED.mkdir(parents=True, exist_ok=True)

    source_path = source_path if source_path.is_absolute() else ROOT / source_path
    files = sorted(iter_source_files(source_path, recursive))
    if not files:
        print(f"📭 没有支持的文件: {source_path}")
        print(f"   支持扩展名: {sorted(SUPPORTED_EXTS)}")
        return 0

    by_tool = {}
    for f in files:
        ext = f.suffix.lower()
        tool = (
            "pypdf/mineru" if ext == ".pdf" else
            "mineru" if ext in MINERU_EXTS else
            "pandoc" if ext in PANDOC_EXTS else
            "calibre"
        )
        by_tool.setdefault(tool, []).append(f.name)

    print(f"📚 找到 {len(files)} 份文件，分派如下:")
    for tool, names in by_tool.items():
        print(f"   {tool}: {len(names)} 份")
    print()

    summary = {"ok": [], "fail": []}

    for i, src in enumerate(files, 1):
        out_dir = output_dir_for(src, source_path)
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"[{i}/{len(files)}] 📄 {src.relative_to(source_path) if is_under(src, source_path) and source_path.is_dir() else src.name}")

        existing = find_output_md(out_dir, src.stem)
        if existing and not force:
            if metadata:
                ok, note = apply_knowledge_template(existing, src, model)
                print(f"    ⏭  已存在输出 ({existing.name})，{note}")
                healthy, msg = health_check(existing)
                print(f"    {'✅' if healthy else '⚠️ '} {msg}")
            else:
                print(f"    ⏭  已存在输出 ({existing.name})，跳过。用 --force 重跑")
            continue

        print(f"    🔄 转换中...")
        ok, err, tool = dispatch(src, out_dir, ocr_fallback)
        if not ok:
            print(f"    ❌ {tool} 失败: {err}")
            summary["fail"].append((src.name, f"[{tool}] {err}"))
            continue

        md = find_output_md(out_dir, src.stem)
        if not md:
            print(f"    ⚠️  {tool} 转换完成但未找到输出 md")
            summary["fail"].append((src.name, f"[{tool}] 无输出 md"))
            continue

        if metadata:
            _, note = apply_knowledge_template(md, src, model)
            print(f"    🧠 {note}")

        healthy, msg = health_check(md)
        if healthy:
            print(f"    ✅ [{tool}] {msg}")
            summary["ok"].append(src.name)
        else:
            print(f"    ⚠️  [{tool}] 健康检查未通过: {msg}")
            dest = FAILED / md.name
            shutil.move(str(md), str(dest))
            summary["fail"].append((src.name, f"[{tool}] {msg}"))

    print("\n" + "=" * 60)
    print(f"完成: ✅ 成功 {len(summary['ok'])} / ⚠️  异常 {len(summary['fail'])}")
    for name, reason in summary["fail"]:
        print(f"  ⚠️  {name}")
        print(f"      → {reason}")
    print(f"\n📂 健康 md: {PROCESSED}")
    print(f"📂 需复核:  {FAILED}")
    return 0 if not summary["fail"] else 2


def main():
    parser = argparse.ArgumentParser(description="铁三大脑知识摄取引擎 v2")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_audit = sub.add_parser("audit", help="审计现有 md 质量")
    p_audit.add_argument("--path", type=Path, default=PROCESSED)

    p_ing = sub.add_parser("ingest", help="转换 inbox 下的资料为 Markdown")
    p_ing.add_argument("--path", type=Path, default=INBOX, help="要转换的文件或目录")
    p_ing.add_argument("--recursive", action="store_true", help="递归扫描目录")
    p_ing.add_argument("--force", action="store_true", help="已存在输出也重跑")
    p_ing.add_argument("--metadata", action="store_true", help="用本地 Gemma 生成知识库模板元数据")
    p_ing.add_argument("--model", default="gemma2:latest", help="Ollama 本地模型名")
    p_ing.add_argument("--ocr-fallback", action="store_true", help="PDF 快速抽取不足时回退到 MinerU/OCR，速度较慢")

    args = parser.parse_args()

    if args.cmd == "audit":
        sys.exit(cmd_audit(args.path))
    elif args.cmd == "ingest":
        sys.exit(cmd_ingest(args.path, args.recursive, args.force, args.metadata, args.model, args.ocr_fallback))


if __name__ == "__main__":
    main()
