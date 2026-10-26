"""Attach a parsed race to the database: match it across sources, then merge fields, categories and prizes."""
from __future__ import annotations

import datetime as dt
import difflib
import sqlite3
from typing import Any

from tri_coach.race_planner import prizes as prize_extract
from tri_coach.race_planner import store
from tri_coach.race_planner.textutil import normalize_name

MATCH_WINDOW_DAYS = 3
RULES_FORMAT_RANK = {"none": 0, "images": 1, "html": 2}
MATCH_MIN_SCORE = 0.6
CN_PROVINCES = (
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江", "上海", "江苏", "浙江", "安徽", "福建",
    "江西", "山东", "河南", "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州", "云南", "西藏",
    "陕西", "甘肃", "青海", "宁夏", "新疆", "台湾", "香港", "澳门",
)


def country_for(province: str | None) -> str | None:
    if not province:
        return None
    return "CN" if any(province.startswith(name) for name in CN_PROVINCES) else None


def name_score(a: str, b: str) -> float:
    left, right = normalize_name(a), normalize_name(b)
    if not left or not right:
        return 0.0
    if left in right or right in left:
        return max(0.85, difflib.SequenceMatcher(None, left, right).ratio())
    return difflib.SequenceMatcher(None, left, right).ratio()


def _days_apart(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    return abs((dt.date.fromisoformat(a) - dt.date.fromisoformat(b)).days)


def find_match(conn: sqlite3.Connection, fields: dict[str, Any]) -> tuple[int | None, float]:
    """Best existing race with a start date within three days and a similar name."""
    start = fields.get("start_date")
    if not start:
        return None, 0.0
    day = dt.date.fromisoformat(start)
    low = (day - dt.timedelta(days=MATCH_WINDOW_DAYS)).isoformat()
    high = (day + dt.timedelta(days=MATCH_WINDOW_DAYS)).isoformat()
    best: tuple[int | None, float] = (None, 0.0)
    for row in conn.execute(
        "SELECT id, name, audience, start_date, end_date FROM race WHERE"
        " (start_date BETWEEN ? AND ?) OR (end_date BETWEEN ? AND ?)",
        (low, high, low, high),
    ):
        if fields.get("audience") and row["audience"] and fields["audience"] != row["audience"]:
            continue
        score = name_score(fields["name"], row["name"])
        if score > best[1]:
            best = (int(row["id"]), score)
    return best if best[1] >= MATCH_MIN_SCORE else (None, best[1])


def attach_race(
    conn: sqlite3.Connection, fields: dict[str, Any], *, source: str, source_id: str, url: str | None = None,
    snapshot_id: int | None = None, linked: list[tuple[str, str]] | None = None, raw: dict[str, Any] | None = None,
) -> tuple[int, str]:
    """Return (race_id, how) where how is source | linked | matched:<score> | new."""
    race_id = store.race_by_source(conn, source, source_id)
    how = "source"
    if race_id is None:
        for other_source, other_id in linked or []:
            race_id = store.race_by_source(conn, other_source, other_id)
            if race_id is not None:
                how = f"linked:{other_source}"
                break
    if race_id is None:
        race_id, score = find_match(conn, fields)
        how = f"matched:{score:.2f}" if race_id is not None else "new"
    if race_id is None:
        race_id = store.insert_race(conn, fields)
    else:
        store.merge_race(conn, race_id, fields)
    store.link_source(conn, race_id=race_id, source=source, source_id=source_id, url=url, snapshot_id=snapshot_id, raw=raw)
    return race_id, how


def apply_rules(
    conn: sqlite3.Connection, race_id: int, *, source: str, source_id: str, html: str, rules_url: str | None,
    snapshot_id: int | None,
) -> dict[str, Any]:
    """Extract prize ladders and categories from a rules page and store them for this race."""
    result = prize_extract.extract(html)
    counts = store.replace_extracted_prizes(
        conn, race_id, source, [ladder.as_dict() for ladder in result.ladders],
        snapshot_id=snapshot_id, pre_tax=result.pre_tax,
    )
    for category in result.categories:
        store.upsert_category(conn, race_id, source, category)
    store.merge_race(conn, race_id, {"pre_tax": result.pre_tax})
    # Several sources can carry rules for one race: text beats pictures beats nothing.
    current = conn.execute("SELECT rules_format FROM race WHERE id = ?", (race_id,)).fetchone()["rules_format"]
    if RULES_FORMAT_RANK.get(result.rules_format, 0) > RULES_FORMAT_RANK.get(current, -1):
        conn.execute("UPDATE race SET rules_format = ? WHERE id = ?", (result.rules_format, race_id))
    if result.rules_text:
        conn.execute(
            "UPDATE race SET prize_rules_text = ? WHERE id = ? AND (prize_rules_text IS NULL OR length(prize_rules_text) < ?)",
            (result.rules_text, race_id, len(result.rules_text)),
        )
    conn.execute(
        "UPDATE race_source SET rules_url = ?, rules_snapshot_id = ? WHERE source = ? AND source_id = ?",
        (rules_url, snapshot_id, source, str(source_id)),
    )
    return {
        "ladders": counts["written"], "kept_reviewed": counts["kept_reviewed"], "categories": len(result.categories),
        "rules_format": result.rules_format, "pre_tax": result.pre_tax,
    }
