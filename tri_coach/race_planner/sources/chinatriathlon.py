"""
chinatriathlon.com: a triathlon-only front end on the zuicool catalogue.

The home page lists events newest first (``/?page=N``, 24 per page). A detail
page adds the date range, event type and the matching zuicool event id, which is
how its races are joined to zuicool's. The sitemap is not used: most of its
entries carry no title or date, so reading it would mean fetching every page.
robots.txt allows everything.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Callable

from tri_coach.race_planner import categories as cat
from tri_coach.race_planner import ingest
from tri_coach.race_planner.fetch import Fetcher, FetchError
from tri_coach.race_planner.textutil import clean, parse_date, parse_dates, soup_of
from tri_coach.race_planner.prizes import block_text

SOURCE = "chinatriathlon"
BASE = "https://chinatriathlon.com"
MAX_PAGES = 20
LABELS = ("比赛日期", "比赛地点", "赛事类型", "国家/地区", "赛事简介", "详情 / 报名", "联系方式", "相关赛事")


def list_url(page: int) -> str:
    return f"{BASE}/?page={page}"


def parse_list(html: str) -> list[dict[str, Any]]:
    soup = soup_of(html)
    out = []
    for card in soup.find_all("a", href=re.compile(r"chinatriathlon\.com/events/\d+$")):
        if not card.find("img"):
            continue  # related-event links, not grid cards
        lines = [clean(text) for text in card.stripped_strings]
        lines = [line for line in lines if line]
        date_line = next((line for line in lines if parse_date(line)), None)
        location = next((line for line in lines if "・" in line), "")
        name = lines[0] if lines else ""
        out.append({
            "id": re.search(r"/events/(\d+)", card["href"]).group(1), "url": card["href"],
            "name": name, "start_date": parse_date(date_line), "location": location,
        })
    return out


def parse_event(html: str) -> dict[str, Any]:
    text = block_text(html)
    lines = text.split("\n")
    values: dict[str, str] = {}
    for index, line in enumerate(lines):
        if line in LABELS:
            collected = []
            for following in lines[index + 1:]:
                if following in LABELS:
                    break
                collected.append(following)
            values.setdefault(line, " ".join(collected))
    title_match = re.search(r"<title>([^<|]+)", html)
    dates = parse_dates(values.get("比赛日期"))
    zuicool = re.search(r"zuicool\.com/event/(\d+)", html)
    location = values.get("比赛地点", "")
    parts = [part for part in re.split(r"[・·]", location) if part]
    province = clean(parts[0]) if parts else None
    return {
        "name": clean(title_match.group(1)) if title_match else "",
        "start_date": dates[0] if dates else None,
        "end_date": dates[-1] if len(dates) > 1 else None,
        "province": province,
        "city": clean(parts[1]) if len(parts) > 1 else None,
        "venue": clean("・".join(parts[2:])) or None,
        "event_type": values.get("赛事类型"),
        "region": values.get("国家/地区"),
        "description": values.get("赛事简介"),
        "zuicool_id": zuicool.group(1) if zuicool else None,
    }


def crawl(conn: sqlite3.Connection, fetcher: Fetcher, *, since: str, until: str, limit: int | None = None,
          log: Callable[[str], None] = print) -> dict[str, int]:
    stats = {"listed": 0, "in_window": 0, "new": 0, "matched": 0, "linked_to_zuicool": 0, "errors": 0}
    entries: list[dict[str, Any]] = []
    for page in range(1, MAX_PAGES + 1):
        batch = parse_list(fetcher.get(list_url(page)).text)
        if not batch:
            break
        entries.extend(batch)
        dated = [entry["start_date"] for entry in batch if entry["start_date"]]
        if dated and max(dated) < since:
            break  # newest first
    stats["listed"] = len(entries)
    window = [entry for entry in entries if entry["start_date"] and since <= entry["start_date"] <= until]
    for entry in window[: limit if limit is not None else None]:
        try:
            page = fetcher.get(entry["url"])
        except FetchError as exc:
            stats["errors"] += 1
            log(f"  ! chinatriathlon {entry['id']}: {exc}")
            continue
        detail = parse_event(page.text)
        stats["in_window"] += 1
        name = detail["name"] or entry["name"]
        fields = {
            "name": name, "sport": cat.classify_sport(name, detail["event_type"]),
            "audience": cat.classify_audience(name), "start_date": detail["start_date"] or entry["start_date"],
            "end_date": detail["end_date"], "province": detail["province"], "city": detail["city"],
            "venue": detail["venue"], "description": detail["description"],
            "country": "CN" if (detail["region"] or "").startswith("中国大陆") else ingest.country_for(detail["province"]),
        }
        linked = [("zuicool", detail["zuicool_id"])] if detail["zuicool_id"] else []
        race_id, how = ingest.attach_race(
            conn, fields, source=SOURCE, source_id=entry["id"], url=entry["url"], snapshot_id=page.snapshot_id,
            linked=linked, raw={"event_type": detail["event_type"], "zuicool_id": detail["zuicool_id"]},
        )
        stats["new" if how == "new" else "matched"] += 1
        if how.startswith("linked:zuicool"):
            stats["linked_to_zuicool"] += 1
        conn.commit()
        log(f"  chinatriathlon {entry['id']} {fields['start_date']} {name} -> race {race_id} ({how})")
    return stats
