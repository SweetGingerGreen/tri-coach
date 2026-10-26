"""Text helpers shared by the parsers: HTML to text/tables, numbers, dates, names."""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Iterable

from bs4 import BeautifulSoup, Tag

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
PODIUM_WORDS = {"冠军": 1, "亚军": 2, "季军": 3, "殿军": 4}


def soup_of(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def clean(text: str | None) -> str:
    """NFKC-normalise (full-width digits and brackets become ASCII) and collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text).replace(" ", " ").replace("　", " ")
    return re.sub(r"\s+", " ", text).strip()


def cell_text(cell: Tag) -> str:
    # Word-exported tables split one value over several <span>s ("1" + "2" = "12"),
    # so join without a separator, then collapse whitespace.
    return clean(cell.get_text("", strip=True))


def table_rows(table: Tag) -> list[list[str]]:
    rows = []
    for tr in table.find_all("tr"):
        if tr.find_parent("table") is not table:
            continue  # nested table: handled on its own
        rows.append([cell_text(cell) for cell in tr.find_all(["td", "th"], recursive=False)])
    return [row for row in rows if any(row)]


def page_text(node: Tag | BeautifulSoup) -> str:
    for junk in node.find_all(["script", "style", "noscript"]):
        junk.decompose()
    lines = [clean(line) for line in node.get_text("\n").splitlines()]
    return "\n".join(line for line in lines if line)


def chinese_number(text: str) -> int | None:
    """Parse 1–99 written as 一 / 十 / 十二 / 二十 / 二十三."""
    text = text.strip()
    if not text or any(ch not in CN_DIGITS and ch != "十" for ch in text):
        return None
    if "十" not in text:
        return CN_DIGITS[text] if len(text) == 1 else None
    tens, _, ones = text.partition("十")
    value = (CN_DIGITS.get(tens, 0) if tens else 1) * 10
    if ones:
        if ones not in CN_DIGITS:
            return None
        value += CN_DIGITS[ones]
    return value


def rank_of(token: str) -> int | None:
    """'1' / '第1名' / '第一名' / '冠军' / 'No.1' -> 1; anything else -> None."""
    token = clean(token).replace(" ", "")
    if not token:
        return None
    if token in PODIUM_WORDS:
        return PODIUM_WORDS[token]
    match = re.fullmatch(r"(?:第|No\.?|NO\.?)?(\d{1,2})(?:名|位)?", token)
    if match:
        value = int(match.group(1))
        return value if 1 <= value <= 60 else None
    match = re.fullmatch(r"第?([一二三四五六七八九十两]{1,3})名?", token)
    if match and (token.startswith("第") or token.endswith("名")):
        return chinese_number(match.group(1))
    return None


def rank_span_of(token: str) -> tuple[int, int] | None:
    """Like rank_of, plus ranges: '第6-10名' / '6～10' / '第六至十名' -> (6, 10). '冠军(1组)' -> (1, 1)."""
    token = re.sub(r"[(（][^)）]*[)）]", "", clean(token)).replace(" ", "")
    single = rank_of(token)
    if single is not None:
        return single, single
    match = re.fullmatch(r"第?(\d{1,2})\s*[-–—~～至到]\s*第?(\d{1,2})\s*名?", token)
    if match:
        low, high = int(match.group(1)), int(match.group(2))
        return (low, high) if 1 <= low < high <= 60 else None
    match = re.fullmatch(r"第?([一二三四五六七八九十]{1,3})\s*[-–—~～至到]\s*第?([一二三四五六七八九十]{1,3})\s*名", token)
    if match:
        low, high = chinese_number(match.group(1)), chinese_number(match.group(2))
        if low and high and low < high:
            return low, high
    return None


def amount_of(token: str) -> float | None:
    """'1600' / '1,600' / '1600元' / '¥1600' / '1.2万' / '5000元+奖杯' -> number; '奖杯' / '' -> None."""
    token = clean(token).replace(" ", "").replace(",", "").replace("，", "")
    if not token:
        return None
    match = re.match(r"^[¥￥]?(\d+(?:\.\d+)?)(万)?(?:元|RMB|rmb)?", token)
    if not match:
        return None
    rest = token[match.end():]
    if rest and not re.match(r"^(?:\+|＋|及|和|/|（|\(|奖杯|奖牌|证书)", rest):
        return None  # e.g. '3名', '20%': not a cash amount
    value = float(match.group(1)) * (10000 if match.group(2) else 1)
    return value


def parse_date(text: str | None) -> str | None:
    """'2026.10.31' / '2026年10月31日' / '2026-10-31T09:00:00+08:00' -> '2026-10-31'."""
    if not text:
        return None
    match = re.search(r"(20\d{2})\s*[.\-/年]\s*(\d{1,2})\s*[.\-/月]\s*(\d{1,2})", text)
    if not match:
        return None
    try:
        return dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat()
    except ValueError:
        return None


def parse_dates(text: str | None) -> list[str]:
    if not text:
        return []
    out = []
    for match in re.finditer(r"(20\d{2})\s*[.\-/年]\s*(\d{1,2})\s*[.\-/月]\s*(\d{1,2})", text):
        try:
            out.append(dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3))).isoformat())
        except ValueError:
            continue
    return out


def hms_to_s(text: str | None) -> int | None:
    if not text:
        return None
    parts = text.strip().split(":")
    if not all(part.isdigit() for part in parts) or not 2 <= len(parts) <= 3:
        raise ValueError(f"bad duration {text!r}; use H:MM:SS or MM:SS")
    seconds = 0
    for part in parts:
        seconds = seconds * 60 + int(part)
    return seconds


def s_to_hms(seconds: int | None) -> str:
    if seconds is None:
        return ""
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


NAME_NOISE = re.compile(
    r"20\d{2}年?|第[一二三四五六七八九十\d]+届|[“”\"'‘’「」『』《》()（）·•・,，.。:：!！\s-]|暨.*$"
)


def normalize_name(name: str) -> str:
    """Strip year, edition, quotes and the '暨…' co-title so the same race matches across sites."""
    text = clean(name).lower()
    text = NAME_NOISE.sub("", text)
    return text


def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def pad(text: str | None, width: int) -> str:
    """Cut and pad to a terminal width where CJK characters take two columns."""
    text = text or ""
    out, used = "", 0
    for ch in text:
        step = 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        if used + step > width:
            break
        out += ch
        used += step
    return out + " " * (width - used)


def first(items: Iterable[str]) -> str | None:
    for item in items:
        if item:
            return item
    return None
