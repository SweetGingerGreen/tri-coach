"""
Prize tables from competition rules (竞赛规程).

Three table layouts are read:

- ranks across the header row, one row per group:
  ``组别 | 1 | 2 | 3 …`` then ``全程分龄组(共12组) | 1600 | 1200 | …``
- alternating rank and amount rows under a caption paragraph:
  ``第1名 … 第5名`` / ``7000 … 1000`` / ``第6名 …`` / ``500 …``
- ranks down the first column, one column per group:
  ``名次 | 男子组 | 女子组`` then ``冠军 | 3000 | 3000``

Points tables (积分) and schedules are skipped. When no table yields a ladder,
sentences such as ``冠军奖金5000元`` are read at lower confidence. Everything
extracted lands as status ``extracted`` and needs a human ``verify``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from bs4 import Comment, NavigableString, Tag

from tri_coach.race_planner import categories as cat
from tri_coach.race_planner.textutil import amount_of, clean, rank_of, rank_span_of, soup_of, table_rows

PRIZE_WORDS = re.compile(r"奖金|奖励|现金|奖额|人民币|元")
POINTS_WORDS = re.compile(r"积分")
SECTION_HEAD = re.compile(
    r"^(?:[一二三四五六七八九十]+\s*[、.．]|第[一二三四五六七八九十]+[章条部分]|\d{1,2}\s*[、．]|[(（][一二三四五六七八九十]+[)）])"
)
PRIZE_HEAD = re.compile(r"奖励办法|奖励|奖金设置|奖金|奖项设置|奖项|表彰")
BLOCK_TAGS = ("p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article", "blockquote")


@dataclass
class Ladder:
    label: str
    ranks: dict[int, float]
    method: str = "table"
    group_count: int | None = None
    gender: str | None = None
    age_min: int | None = None
    age_max: int | None = None
    format: str | None = None
    pre_tax: int | None = None
    confidence: float = 0.9
    issues: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label, "ranks": dict(sorted(self.ranks.items())), "method": self.method,
            "group_count": self.group_count, "gender": self.gender, "age_min": self.age_min,
            "age_max": self.age_max, "format": self.format, "pre_tax": self.pre_tax,
            "confidence": round(self.confidence, 2),
            "issues": list(self.issues),
        }


@dataclass
class Extraction:
    ladders: list[Ladder]
    pre_tax: int | None
    rules_text: str
    rules_format: str
    image_count: int
    text_length: int
    categories: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ladders": [ladder.as_dict() for ladder in self.ladders], "pre_tax": self.pre_tax,
            "rules_format": self.rules_format, "image_count": self.image_count, "text_length": self.text_length,
            "categories": self.categories, "rules_text": self.rules_text[:400],
        }


# --- text ------------------------------------------------------------------

def block_text(html: str) -> str:
    """Page text with one line per block element; inline spans are joined without breaks."""
    soup = soup_of(html)
    for junk in soup.find_all(["script", "style", "noscript"]):
        junk.decompose()
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for cell in soup.find_all(["td", "th"]):
        cell.append(" ")
    for tag in soup.find_all(BLOCK_TAGS):
        tag.append("\n")
    lines = [clean(line) for line in soup.get_text("").split("\n")]
    return "\n".join(line for line in lines if line)


def prize_section(text: str, limit: int = 4000) -> str:
    """The 奖励 / 奖金 section of the rules, from its heading to the next top-level heading."""
    lines = text.split("\n")
    start = None
    for index, line in enumerate(lines):
        stripped = SECTION_HEAD.sub("", line).strip()
        is_heading = SECTION_HEAD.match(line) is not None and len(stripped) <= 16
        if (is_heading and PRIZE_HEAD.search(stripped)) or stripped in ("奖励办法", "奖励", "奖金设置", "奖项设置"):
            start = index
            break
    if start is None:
        return ""
    top_level = re.match(r"^[一二三四五六七八九十]+\s*[、.．]", lines[start]) is not None
    out = [lines[start]]
    for line in lines[start + 1:]:
        if top_level and re.match(r"^[一二三四五六七八九十]+\s*[、.．]", line):
            break
        out.append(line)
        if sum(len(item) for item in out) > limit:
            break
    return "\n".join(out)[:limit]


def detect_pre_tax(text: str) -> int | None:
    if "税前" in text:
        return 1
    if re.search(r"税后奖金|奖金为税后|已扣税", text):
        return 0
    return None


# --- label parsing ---------------------------------------------------------

def describe_label(raw: str) -> dict[str, Any]:
    label = clean(raw)
    group_count = None
    match = re.search(r"[(（]?\s*共\s*(\d+)\s*组\s*[)）]?", label)
    if match:
        group_count = int(match.group(1))
        label = (label[: match.start()] + label[match.end():]).strip()
    label = re.sub(r"[(（]\s*单位[^)）]*[)）]", "", label)
    label = re.sub(r"^(?:组别|组别/名次|名次)\s*", "", label).strip(" :：,，、") or clean(raw)
    gender = None
    if "混合" in label:
        gender = "X"
    elif "女" in label and "男" not in label:
        gender = "F"
    elif "男" in label and "女" not in label:
        gender = "M"
    age_min = age_max = None
    match = re.search(r"(\d{2})\s*(?:岁)?\s*[-–—~～至到]\s*(\d{2})\s*岁?", label)
    if match and 5 <= int(match.group(1)) < int(match.group(2)) <= 99:
        age_min, age_max = int(match.group(1)), int(match.group(2))
    return {
        "label": label[:60], "group_count": group_count, "gender": gender,
        "age_min": age_min, "age_max": age_max, "format": cat.classify_format(label),
    }


def validate(ladder: Ladder) -> Ladder:
    issues = list(ladder.issues)
    ranks = sorted(ladder.ranks)
    amounts = [ladder.ranks[rank] for rank in ranks]
    if ranks and ranks[0] != 1:
        issues.append("does_not_start_at_1")
    if ranks and ranks != list(range(ranks[0], ranks[0] + len(ranks))):
        issues.append("gap_in_ranks")
    if any(later > earlier for earlier, later in zip(amounts, amounts[1:])):
        issues.append("amount_rises_with_rank")
    if any(amount < 10 or amount > 1_000_000 for amount in amounts):
        issues.append("implausible_amount")
    if ladder.label == "未标明组别":
        issues.append("unlabelled")
    ladder.issues = sorted(set(issues))
    base = 0.9 if ladder.method == "table" else 0.5
    ladder.confidence = max(0.1, base - 0.2 * len(ladder.issues))
    return ladder


# --- tables ----------------------------------------------------------------

def _is_rank_header(row: list[str]) -> tuple[bool, list[tuple[int, int, int]]]:
    """A header row of consecutive ranks or rank ranges; returns (index, low, high) per rank cell."""
    spans = [(index, rank_span_of(cell)) for index, cell in enumerate(row)]
    found = [(index, span[0], span[1]) for index, span in spans if span is not None]
    if len(found) < 2:
        return False, []
    for (_, _, high), (_, low, _) in zip(found, found[1:]):
        if low != high + 1:
            return False, []
    others = [cell for (index, span), cell in zip(spans, row) if span is None and cell]
    return len(others) <= 1, found


def _caption_before(table: Tag, max_lines: int = 3, max_elements: int = 4000) -> list[str]:
    """Up to three text lines just before the table, nearest first.

    Text inside other tables is skipped, and so is the table's own wrapper:
    Word exports sometimes put a table inside a <p>, and captions are often a
    bare <span> between two tables rather than a paragraph.
    """
    lines: list[str] = []
    parts: list[str] = []

    def flush() -> None:
        text = clean("".join(reversed(parts)))
        parts.clear()
        if text:
            lines.append(text)

    for steps, element in enumerate(table.previous_elements):
        if steps > max_elements or len(lines) >= max_lines:
            break
        if isinstance(element, Tag):
            if element.name == "br" or element.name in BLOCK_TAGS:
                flush()
            continue
        if not isinstance(element, NavigableString) or isinstance(element, Comment):
            continue
        if element.find_parent("table") is not None:
            continue
        parts.append(str(element))
    flush()
    return lines[:max_lines]


def _label_from_caption(captions: list[str]) -> str | None:
    """A caption that names a group ('公开组男子、女子单人三项赛(单位:…)'), not a sentence about prizes."""
    for text in captions:
        head = re.split(r"[(（]\s*单位", text)[0]
        if len(head) > 40 or "如下" in text or re.search(r"[。;；]$", text):
            continue
        if re.search(r"组|赛|项", head):
            return text
    return None


def _cash_context(table: Tag, rows: list[list[str]], captions: list[str]) -> bool:
    nearest = captions[0] if captions else ""
    if POINTS_WORDS.search(nearest) and "奖金" not in nearest:
        return False
    if any(POINTS_WORDS.search(cell) for row in rows[:2] for cell in row):
        return False
    window = " ".join(captions) + " " + " ".join(" ".join(row) for row in rows[:2])
    return bool(PRIZE_WORDS.search(window))


def ladders_from_rows(rows: list[list[str]], caption_label: str | None) -> list[Ladder]:
    ladders: dict[str, dict[int, float]] = {}
    order: list[str] = []

    def add(label: str, rank: int, amount: float) -> None:
        if label not in ladders:
            ladders[label] = {}
            order.append(label)
        ladders[label].setdefault(rank, amount)

    header: list[tuple[int, int, int]] = []
    header_len = 0
    header_has_label = False
    caption = caption_label
    last_label: str | None = None
    for row in rows:
        is_header, found = _is_rank_header(row)
        if is_header:
            header, header_len = found, len(row)
            header_has_label = len(found) < len([cell for cell in row if cell])
            continue
        amounts = [amount_of(cell) for cell in row]
        n_amounts = sum(1 for value in amounts if value is not None)
        if not header:
            if n_amounts == 0 and len([cell for cell in row if cell]) == 1:
                caption = row[0] if row[0] else caption
            continue
        if n_amounts == 0:
            if any(row):
                caption = " ".join(cell for cell in row if cell)
            continue
        n_ranks = len(header)
        if len(row) == header_len:
            cells = row
        elif len(row) == n_ranks:
            cells = [""] * (header_len - n_ranks) + row
        else:
            cells = ([row[0]] + [""] * (header_len - len(row)) + row[1:]) if len(row) < header_len else row[:header_len]
        label_cell = cells[0] if header_has_label and amount_of(cells[0]) is None else ""
        if re.match(r"^税后", label_cell):
            continue  # net-of-tax copy of the row above; keep the gross ladder
        if re.match(r"^税前", label_cell):
            label_cell = last_label or ""
        label = label_cell or (last_label if header_has_label and len(row) == n_ranks else None) or caption or "未标明组别"
        for index, low, high in header:
            if index < len(cells):
                value = amount_of(cells[index])
                if value is not None:
                    for rank in range(low, high + 1):
                        add(label, rank, value)
        if label_cell:
            last_label = label_cell
    pre_tax_labels: set[str] = set()
    if not ladders:
        ladders, order, pre_tax_labels = _column_ladders(rows, caption_label)
    out = []
    for label in order:
        ranks = ladders[label]
        if len(ranks) < 2:
            continue
        info = describe_label(label)
        out.append(validate(Ladder(ranks=ranks, method="table", pre_tax=1 if label in pre_tax_labels else None, **info)))
    return out


def _column_ladders(rows: list[list[str]], caption: str | None) -> tuple[dict[str, dict[int, float]], list[str], set[str]]:
    """Ranks down a column: 名次 | 男子组 | 女子组 | 税后(元) / 第一名 | 3000 | 3000 | 2400.

    The rank column is column 0 or 1 (a 序号 column may come first). Columns
    headed 税后 are net-of-tax copies and are skipped; their presence marks the
    other columns as pre-tax. A one-cell row above the header ('桨板耐力赛(元)')
    prefixes the group names.
    """
    best: tuple[int, list[tuple[int, int, int]]] | None = None
    for column in (0, 1):
        spans = [(index, rank_span_of(row[column])) for index, row in enumerate(rows) if len(row) > column]
        found = [(index, span[0], span[1]) for index, span in spans if span is not None]
        if len(found) < 2 or any(low != high_prev + 1 for (_, _, high_prev), (_, low, _) in zip(found, found[1:])):
            continue
        header_row = rows[found[0][0] - 1] if found[0][0] > 0 else []
        named = len(header_row) > column and "名次" in header_row[column]
        if best is None or named:
            best = (column, found)
        if named:
            break
    if best is None:
        return {}, [], set()
    rank_column, found = best
    first_index = found[0][0]
    header = rows[first_index - 1] if first_index > 0 else []
    prefix = ""
    if first_index >= 2 and len([cell for cell in rows[first_index - 2] if cell]) == 1:
        prefix = re.sub(r"[(（]\s*元\s*[)）]", "", rows[first_index - 2][0]).strip()
    ladders: dict[str, dict[int, float]] = {}
    order: list[str] = []
    net_columns = {index for index, name in enumerate(header) if re.match(r"^税后", name)}
    width = max(len(rows[index]) for index, _, _ in found)
    for column in range(width):
        if column == rank_column or column in net_columns:
            continue
        name = header[column] if column < len(header) else ""
        if re.fullmatch(r"序号|编号|其[它他]奖励.*|奖品.*|证书.*", name):
            continue
        if not name or re.fullmatch(r"(奖金|金额|奖励|税前).*", name):
            name = prefix or caption or "未标明组别"
        elif prefix and re.sub(r"组|赛", "", prefix) not in name:
            name = f"{prefix} {name}"
        for index, low, high in found:
            row = rows[index]
            if column < len(row):
                value = amount_of(row[column])
                if value is not None:
                    if name not in ladders:
                        ladders[name] = {}
                        order.append(name)
                    for rank in range(low, high + 1):
                        ladders[name].setdefault(rank, value)
    pre_tax_labels = set(order) if net_columns else set()
    return ladders, order, pre_tax_labels


# --- prose -----------------------------------------------------------------

PROSE_PRIZE = re.compile(
    r"(冠军|亚军|季军|第\s*[一二三四五六七八九十\d]{1,3}\s*名)[^\d。；;\n]{0,14}?(\d[\d,]*(?:\.\d+)?)\s*(万)?\s*元"
)


def ladders_from_prose(text: str) -> list[Ladder]:
    ranks: dict[int, float] = {}
    for match in PROSE_PRIZE.finditer(text):
        rank = rank_of(match.group(1).replace(" ", ""))
        amount = amount_of(match.group(2) + (match.group(3) or "") + "元")
        if rank and amount and rank not in ranks:
            ranks[rank] = amount
    if not ranks:
        return []
    return [validate(Ladder(label="未标明组别", ranks=ranks, method="prose", issues=["from_prose"]))]


# --- entry point -----------------------------------------------------------

def extract(html: str) -> Extraction:
    soup = soup_of(html)
    text = block_text(html)
    section = prize_section(text)
    image_count = len(soup.find_all("img"))
    all_tables: list[list[list[str]]] = []
    ladders: list[Ladder] = []
    for table in soup.find_all("table"):
        if table.find_parent("table") is not None:
            continue
        rows = table_rows(table)
        all_tables.append(rows)
        captions = _caption_before(table)
        if not _cash_context(table, rows, captions):
            continue
        ladders.extend(ladders_from_rows(rows, _label_from_caption(captions)))
    if not ladders:
        ladders = ladders_from_prose(section or text)
    seen: dict[str, int] = {}
    for ladder in ladders:
        count = seen.get(ladder.label, 0)
        seen[ladder.label] = count + 1
        if count:
            ladder.label = f"{ladder.label} #{count + 1}"
    if image_count and (len(text) < 200 or (not section and not ladders)):
        rules_format = "images"  # the rules (and any prize table) are pictures
    elif text:
        rules_format = "html"
    else:
        rules_format = "none"
    return Extraction(
        ladders=ladders, pre_tax=detect_pre_tax(section or text), rules_text=section,
        rules_format=rules_format, image_count=image_count, text_length=len(text),
        categories=cat.categories_from_tables(all_tables),
    )
