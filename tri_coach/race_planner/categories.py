"""Category names to distances and a coarse race format."""
from __future__ import annotations

import re
from typing import Any

from tri_coach.race_planner.textutil import amount_of, clean

LEG_WORDS = {
    "swim": r"游泳|游|swim",
    "bike": r"自行车|骑行|公路车|骑|bike|cycling",
    "run": r"跑步|越野跑|跑|run",
}
UNIT = r"(公里|千米|km|KM|Km|米|m)"
# Order matters: an event called 铁人三项 that also runs a 骑跑 side race is a triathlon.
SPORT_NAME_RULES = (
    ("hyrox", r"hyrox"),
    ("triathlon", r"铁人三项|铁三|triathlon|ironman|t100|challenge|大铁|226|113|70\.3"),
    ("duathlon", r"骑跑两项|骑跑|duathlon"),
    ("aquathlon", r"游跑两项|游跑|水陆两项|aquathlon"),
    ("multisport", r"户外三项|多项|挑战赛"),
)
YOUTH_RULES = r"小铁人|小小铁人|青少年|儿童|少儿|亲子|kids|youth"


def _km(value: str, unit: str) -> float:
    number = float(value)
    return round(number / 1000.0, 3) if unit in ("米", "m") else number


def parse_distances(text: str | None) -> dict[str, float | None]:
    """'游泳2.1公里+自行车88公里+跑步20公里' -> swim 2.1 / bike 88 / run 20 (km)."""
    out: dict[str, float | None] = {"swim_km": None, "bike_km": None, "run_km": None}
    if not text:
        return out
    text = clean(text)
    for leg, words in LEG_WORDS.items():
        match = re.search(rf"(?:{words})\s*[:：]?\s*(\d+(?:\.\d+)?)\s*{UNIT}", text)
        if not match:
            match = re.search(rf"(\d+(?:\.\d+)?)\s*{UNIT}\s*(?:{words})", text)
        if match:
            out[f"{leg}_km"] = _km(match.group(1), match.group(2))
    return out


def classify_format(name: str | None, swim_km: float | None = None, bike_km: float | None = None,
                    run_km: float | None = None) -> str | None:
    """Coarse format: full / middle / olympic / sprint / supersprint / relay / duathlon / aquathlon / youth.

    CTSA's 全程 / 半程 are deliberately left unclassified without distances:
    at a CTSA age-group race 全程 is usually standard distance, while at a T100
    or 226 event it means the long course.
    """
    text = clean(name).lower()
    if re.search(YOUTH_RULES, text):
        return "youth"
    if "接力" in text or "relay" in text:
        return "relay"
    has = {leg: value is not None and value > 0 for leg, value in (("swim", swim_km), ("bike", bike_km), ("run", run_km))}
    if has["swim"] and has["run"] and not has["bike"]:
        return "aquathlon"
    if has["bike"] and has["run"] and not has["swim"]:
        return "duathlon"
    if bike_km:
        if bike_km >= 150:
            return "full"
        if bike_km >= 70:
            return "middle"
        if bike_km >= 30:
            return "olympic"
        if bike_km >= 15:
            return "sprint"
        return "supersprint"
    if re.search(r"骑跑", text):
        return "duathlon"
    if re.search(r"游跑|水陆", text):
        return "aquathlon"
    if re.search(r"226|大铁|ironman(?!\s*70\.3)全程|全程大铁", text):
        return "full"
    if re.search(r"113|70\.3|半程大铁|半程铁人|t100", text):
        return "middle"
    if re.search(r"标准距离|奥运距离|51\.5|515|olympic", text):
        return "olympic"
    if re.search(r"冲刺|sprint", text):
        return "sprint"
    if re.search(r"超级短|迷你|体验", text):
        return "supersprint"
    return None


def classify_sport(name: str | None, fallback_text: str | None = None) -> str:
    """Sport from the event name; category names are only consulted when the name says nothing."""
    for text in (clean(name).lower(), clean(fallback_text).lower()):
        for sport, pattern in SPORT_NAME_RULES:
            if text and re.search(pattern, text):
                return sport
    return "other"


def classify_audience(name: str | None) -> str:
    text = clean(name).lower()
    if re.search(YOUTH_RULES, text) and not re.search(r"成人|大众|分龄|公开组", text):
        return "youth"
    return "adult"


def category_from_name(name: str, detail: str | None = None, fee: float | None = None,
                       reg_status: str | None = None) -> dict[str, Any]:
    distances = parse_distances(f"{name} {detail or ''}")
    return {
        "name": clean(name),
        "detail": clean(detail) or None,
        "fee_cny": fee,
        "reg_status": clean(reg_status) or None,
        **distances,
        "format": classify_format(name, distances["swim_km"], distances["bike_km"], distances["run_km"]),
    }


# --- categories from rules tables (wildto) ---------------------------------

def _norm_cat(label: str) -> str:
    return re.sub(r"分龄|组别|组|团体|个人|\s", "", clean(label))


def categories_from_tables(tables: list[list[list[str]]]) -> list[dict[str, Any]]:
    """Read distance tables (header has 游泳 + 自行车/跑步) and fee tables (header 身份/类别 + category columns)."""
    categories: dict[str, dict[str, Any]] = {}
    fees: dict[str, float] = {}
    for rows in tables:
        if not rows:
            continue
        header = rows[0]
        joined = " ".join(header)
        if "游泳" in joined and ("自行车" in joined or "跑步" in joined):
            columns = {}
            for index, cell in enumerate(header):
                for leg, words in LEG_WORDS.items():
                    if re.fullmatch(rf"(?:{words})(?:\(.*\))?", cell):
                        columns[leg] = index
            for row in rows[1:]:
                if not row or not row[0] or len(row) < len(header) - 1:
                    continue
                offset = len(row) - len(header)  # rowspan on the left shortens a row
                category = {"name": row[0], "detail": None, "fee_cny": None, "reg_status": None,
                            "swim_km": None, "bike_km": None, "run_km": None}
                for leg, index in columns.items():
                    position = index + offset
                    if 0 < position < len(row):
                        cell = row[position]
                        other_legs = [other for other in LEG_WORDS if other != leg]
                        if any(re.search(LEG_WORDS[other], cell) for other in other_legs):
                            continue  # '5公里(跑步)' in the swim column: a duathlon's first run
                        match = re.search(rf"(\d+(?:\.\d+)?)\s*{UNIT}", cell)
                        if match:
                            category[f"{leg}_km"] = _km(match.group(1), match.group(2))
                category["format"] = classify_format(row[0], category["swim_km"], category["bike_km"], category["run_km"])
                categories.setdefault(_norm_cat(row[0]), category)
        elif re.search(r"身份|会员|类别|类型", header[0] if header else "") and len(header) >= 2:
            fee_row = next((row for row in rows[1:] if row and "非" in row[0]), None) or (rows[1] if len(rows) > 1 else None)
            if not fee_row:
                continue
            for index, cell in enumerate(header[1:], start=1):
                if index < len(fee_row):
                    value = amount_of(fee_row[index])
                    if value is not None and 10 <= value <= 20000:
                        fees[_norm_cat(cell)] = value
    for key, fee in fees.items():
        match = next((cat for cat_key, cat in categories.items() if key and (key in cat_key or cat_key in key)), None)
        if match is not None and match.get("fee_cny") is None:
            match["fee_cny"] = fee
    return list(categories.values())
