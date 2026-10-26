"""
wildto.com (野途): event listing used by many CTSA-sanctioned races.

The 铁三 list page loads its events from a form POST to ``/events/screen``
(``event_type=2`` is triathlon) with a date window. Each event's competition
rules live as an HTML fragment at ``static.wildto.com/<wContentUrl>``; when the
organiser typed them in (not pasted images) they include the prize tables.
There is no robots.txt.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any, Callable

from tri_coach.race_planner import categories as cat
from tri_coach.race_planner import ingest
from tri_coach.race_planner.fetch import Fetcher, FetchError
from tri_coach.race_planner.textutil import clean, parse_date

SOURCE = "wildto"
SCREEN_URL = "https://www.wildto.com/events/screen"
STATIC_BASE = "https://static.wildto.com/"
PAGE_SIZE = 12
MAX_PAGES = 60
HEADERS = {"X-Requested-With": "XMLHttpRequest", "Referer": "https://www.wildto.com/event/trievents"}


def screen_form(page: int, since: str, until: str) -> dict[str, str]:
    return {
        "page_index": str(page), "page_size": str(PAGE_SIZE), "event_type": "2", "level": "0",
        "begin_time": since, "end_time": until, "province": "0", "status": "0", "young_flag": "0",
        "list_order": "1",
    }


def event_url(event_id: str) -> str:
    return f"https://www.wildto.com/event/{event_id}.html"


def parse_screen(text: str) -> list[dict[str, Any]]:
    payload = json.loads(text)
    out = []
    for item in payload.get("data") or []:
        address = " ".join(part for part in (item.get("regionName"), item.get("wAddress")) if part)
        out.append({
            "id": str(item["wEventId"]),
            "name": clean(item.get("wName")),
            "start_date": parse_date(item.get("wEventBeginTime")),
            "end_date": parse_date(item.get("wEventEndTime")),
            "reg_open": parse_date(item.get("wApplyBeginTime")),
            "reg_close": parse_date(item.get("wApplyEndTime")),
            "province": clean(item.get("provinceName")) or None,
            "city": clean(item.get("cityName")) or None,
            "venue": clean(address) or None,
            "lat": item.get("wLat"), "lng": item.get("wLng"),
            "level": clean(item.get("sortName")) or None,
            "status": clean(item.get("status")) or None,
            "content_url": item.get("wContentUrl"),
            "country": item.get("wCountry"),
        })
    return out


def race_fields(item: dict[str, Any]) -> dict[str, Any]:
    end_date = item["end_date"] if item["end_date"] and item["end_date"] != item["start_date"] else None
    return {
        "name": item["name"], "sport": cat.classify_sport(item["name"]), "audience": cat.classify_audience(item["name"]),
        "start_date": item["start_date"], "end_date": end_date, "reg_open": item["reg_open"],
        "reg_close": item["reg_close"], "province": item["province"], "city": item["city"], "venue": item["venue"],
        "lat": item["lat"], "lng": item["lng"], "level": item["level"], "status": item["status"],
        "country": item["country"] or ingest.country_for(item["province"]),
    }


def crawl(conn: sqlite3.Connection, fetcher: Fetcher, *, since: str, until: str, limit: int | None = None,
          log: Callable[[str], None] = print) -> dict[str, int]:
    stats = {"listed": 0, "in_window": 0, "new": 0, "matched": 0, "ladders": 0, "rules_images": 0, "errors": 0}
    items: dict[str, dict[str, Any]] = {}
    for page in range(1, MAX_PAGES + 1):
        batch = parse_screen(fetcher.post(SCREEN_URL, screen_form(page, since, until), headers=HEADERS).text)
        if not batch:
            break
        for item in batch:
            items.setdefault(item["id"], item)
        if len(batch) < PAGE_SIZE:
            break
    stats["listed"] = len(items)
    ordered = sorted(items.values(), key=lambda item: item["start_date"] or "")
    for item in ordered[: limit if limit is not None else None]:
        if not item["start_date"] or not since <= item["start_date"] <= until:
            continue
        stats["in_window"] += 1
        race_id, how = ingest.attach_race(
            conn, race_fields(item), source=SOURCE, source_id=item["id"], url=event_url(item["id"]),
            raw={k: item[k] for k in ("level", "status", "content_url")},
        )
        stats["new" if how == "new" else "matched"] += 1
        note = ""
        if item["content_url"]:
            rules_url = STATIC_BASE + item["content_url"].lstrip("/")
            try:
                rules = fetcher.get(rules_url)
                outcome = ingest.apply_rules(conn, race_id, source=SOURCE, source_id=item["id"], html=rules.text,
                                             rules_url=rules_url, snapshot_id=rules.snapshot_id)
                stats["ladders"] += outcome["ladders"]
                stats["rules_images"] += outcome["rules_format"] == "images"
                note = f", rules {outcome['rules_format']}, {outcome['ladders']} prize ladders"
            except FetchError as exc:
                stats["errors"] += 1
                note = f", rules fetch failed: {exc}"
        conn.commit()
        log(f"  wildto {item['id']} {item['start_date']} {item['name']} -> race {race_id} ({how}){note}")
    return stats
