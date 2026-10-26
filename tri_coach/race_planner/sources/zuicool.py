"""
zuicool.com (最酷): China's largest endurance-event registration site.

- ``/events?type=triathlon&per-page=100&page=N``: newest first, server-rendered.
- ``/event/<id>``: categories (组别) with entry fee and registration state, plus
  links to the registration news post, which sometimes carries the rules.

robots.txt only disallows /news/wp-content/. chinatriathlon.com is the same
operator and catalogue.
"""
from __future__ import annotations

import re
import sqlite3
from typing import Any, Callable

from tri_coach.race_planner import categories as cat
from tri_coach.race_planner import ingest
from tri_coach.race_planner.fetch import Fetcher, FetchError
from tri_coach.race_planner.textutil import amount_of, clean, parse_date, soup_of

SOURCE = "zuicool"
BASE = "https://zuicool.com"
MAX_PAGES = 20


def list_url(page: int) -> str:
    return f"{BASE}/events?type=triathlon&per-page=100&page={page}"


def event_url(event_id: str) -> str:
    return f"{BASE}/event/{event_id}"


def split_location(location: str) -> dict[str, str | None]:
    parts = clean(location).split(" ")
    province = parts[0] if parts else None
    city = parts[1] if len(parts) > 1 else None
    venue = " ".join(parts[2:]) or None
    return {"province": province, "city": city, "venue": venue, "country": ingest.country_for(province)}


def parse_list(html: str) -> list[dict[str, Any]]:
    soup = soup_of(html)
    out = []
    for heading in soup.select("h4.name"):
        link = heading.find("a", href=re.compile(r"/event/\d+"))
        if not link:
            continue
        event_id = re.search(r"/event/(\d+)", link["href"]).group(1)
        body = heading.find_parent(class_="event-body") or heading.parent
        info = body.find(class_="info") if body else None
        info_text = clean(info.get_text(" ")) if info else ""
        date_part, _, location = info_text.partition("·")
        out.append({
            "id": event_id,
            "name": clean(link.get_text(" ")),
            "start_date": parse_date(date_part),
            "location": clean(location),
            "url": event_url(event_id),
        })
    return out


def parse_event(html: str) -> dict[str, Any]:
    soup = soup_of(html)
    title = clean(soup.title.get_text()) if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"})
    description = clean(meta["content"]) if meta and meta.get("content") else None
    categories = []
    for package in soup.select(".event-pkgs .pkg2"):
        name_tag = package.select_one("h4.name")
        if not name_tag:
            continue
        detail_tag = package.select_one("small")
        price_tag = package.select_one(".price")
        button = package.select_one(".reg-btn")
        categories.append(cat.category_from_name(
            name_tag.get_text(" "),
            detail_tag.get_text(" ") if detail_tag else None,
            amount_of(price_tag.get_text()) if price_tag else None,
            button.get_text(" ") if button else None,
        ))
    status_tag = soup.select_one("nav.event-tab2 span.pkg") or soup.select_one("nav.event-tab2 a.pkg")
    news = []
    for link in soup.find_all("a", href=re.compile(r"zuicool\.com/news/archives/\d+")):
        text = clean(link.get_text(" "))
        if text.startswith("报名") or "规程" in text:
            news.append({"url": link["href"].split("?")[0], "title": text})
    info_text = ""
    header = soup.find(string=re.compile(r"20\d{2}\.\d{2}\.\d{2}"))
    if header:
        info_text = clean(header)
    return {
        "name": title, "description": description, "categories": categories,
        "status": clean(status_tag.get_text()) if status_tag else None,
        "news": news, "start_date": parse_date(info_text),
    }


def parse_news(html: str) -> str:
    """The article body of a news post, as HTML for the prize extractor."""
    soup = soup_of(html)
    article = soup.find("article") or soup.find(class_=re.compile(r"entry-content|post-content"))
    return str(article) if article else ""


def crawl(conn: sqlite3.Connection, fetcher: Fetcher, *, since: str, until: str, limit: int | None = None,
          log: Callable[[str], None] = print) -> dict[str, int]:
    stats = {"listed": 0, "in_window": 0, "new": 0, "matched": 0, "ladders": 0, "errors": 0}
    seen = 0
    for page in range(1, MAX_PAGES + 1):
        items = parse_list(fetcher.get(list_url(page)).text)
        if not items:
            break
        stats["listed"] += len(items)
        dated = [item for item in items if item["start_date"]]
        for item in items:
            if not item["start_date"] or not since <= item["start_date"] <= until:
                continue
            if limit is not None and seen >= limit:
                break
            seen += 1
            stats["in_window"] += 1
            try:
                crawl_event(conn, fetcher, item, stats, log)
            except FetchError as exc:
                stats["errors"] += 1
                log(f"  ! zuicool {item['id']}: {exc}")
            conn.commit()
        if dated and max(item["start_date"] for item in dated) < since:
            break  # newest first: everything further back is older
        if limit is not None and seen >= limit:
            break
    return stats


def crawl_event(conn: sqlite3.Connection, fetcher: Fetcher, item: dict[str, Any], stats: dict[str, int],
                log: Callable[[str], None]) -> int:
    page = fetcher.get(item["url"])
    detail = parse_event(page.text)
    name = item["name"] or detail["name"]
    fields = {
        "name": name,
        "sport": cat.classify_sport(name, " ".join(c["name"] for c in detail["categories"])),
        "audience": cat.classify_audience(name),
        "start_date": item["start_date"] or detail["start_date"],
        "description": detail["description"],
        "status": detail["status"],
        **split_location(item["location"]),
    }
    race_id, how = ingest.attach_race(
        conn, fields, source=SOURCE, source_id=item["id"], url=item["url"], snapshot_id=page.snapshot_id,
        raw={"list": item, "status": detail["status"], "news": detail["news"]},
    )
    stats["new" if how == "new" else "matched"] += 1
    for category in detail["categories"]:
        ingest.store.upsert_category(conn, race_id, SOURCE, category)
    log(f"  zuicool {item['id']} {fields['start_date']} {name} -> race {race_id} ({how}), {len(detail['categories'])} categories")
    for post in detail["news"][:1]:
        news = fetcher.get(post["url"])
        body = parse_news(news.text)
        if body:
            outcome = ingest.apply_rules(conn, race_id, source=SOURCE, source_id=item["id"], html=body,
                                         rules_url=post["url"], snapshot_id=news.snapshot_id)
            stats["ladders"] += outcome["ladders"]
    return race_id
