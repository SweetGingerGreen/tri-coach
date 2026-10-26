"""
Load an athlete's own races from a JSON seed file.

The seed is private (results, prize money, plans) and lives in the data
directory, not in the repository; ``examples/race_planner/seed_example.json``
shows the format with a made-up athlete. Seeded fields win over crawled ones.

    {
      "athlete": {"key": "self", "profile": {...}},
      "races": [{
        "key": "dalian-dahei-2026",              # stable id for this race in the seed
        "name": "...", "start_date": "2026-07-12", "sport": "triathlon", ...race fields,
        "links": [{"source": "wildto", "id": "11099"}],    # same race on crawled sites
        "categories": [{"name": "...", "swim_km": 1.5, "bike_km": 40, "run_km": 10}],
        "prizes": [{"label": "...", "ranks": {"1": 1000, "2": 600}, "pre_tax": 1, "note": "..."}],
        "calendar": {"role": "B", "status": "done", "note": "..."},
        "result": {"category": "...", "division": "...", "finish": "2:41:10", "swim": "31:05", ...,
                   "rank_division": 3, "division_field": 14, "prize_cny": 800, "time_source": "official"}
      }]
    }
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from tri_coach.race_planner import categories as cat
from tri_coach.race_planner import ingest, store
from tri_coach.race_planner.textutil import hms_to_s

SOURCE = "manual"
TIME_KEYS = {"finish": "finish_s", "swim": "swim_s", "t1": "t1_s", "bike": "bike_s", "t2": "t2_s", "run": "run_s"}
RESULT_KEYS = (
    "category", "division", "time_source", "rank_division", "division_field", "rank_gender", "gender_field",
    "rank_overall", "overall_field", "prize_cny", "note",
)


def _resolve(conn: sqlite3.Connection, race: dict[str, Any]) -> tuple[int, str]:
    key = race["key"]
    linked_ids = [
        race_id for race_id in (store.race_by_source(conn, link["source"], str(link["id"])) for link in race.get("links", []))
        if race_id is not None
    ]
    own = store.race_by_source(conn, SOURCE, key)
    if own is not None:
        for other in set(linked_ids) - {own}:
            store.merge_races(conn, own, other)
        return own, "seeded-before"
    if linked_ids:
        keep = linked_ids[0]
        for other in set(linked_ids[1:]) - {keep}:
            store.merge_races(conn, keep, other)
        return keep, "linked"
    fields = _race_fields(race)
    if fields.get("start_date"):
        match, score = ingest.find_match(conn, fields)
        if match is not None:
            return match, f"matched:{score:.2f}"
    return store.insert_race(conn, fields, manual=True), "new"


def _race_fields(race: dict[str, Any]) -> dict[str, Any]:
    fields = {key: race.get(key) for key in store.RACE_FIELDS if race.get(key) is not None}
    fields.setdefault("sport", cat.classify_sport(race["name"]))
    fields.setdefault("audience", "adult")
    if fields.get("province") and not fields.get("country"):
        fields["country"] = ingest.country_for(fields["province"])
    return fields


def load(conn: sqlite3.Connection, path: Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    athlete = data.get("athlete") or {}
    athlete_key = athlete.get("key", "self")
    if athlete.get("profile"):
        store.upsert_profile(conn, athlete_key, athlete["profile"])
    report = []
    for race in data.get("races", []):
        if not race.get("key") or not race.get("name"):
            raise ValueError(f"every seeded race needs key and name: {race}")
        race_id, how = _resolve(conn, race)
        store.merge_race(conn, race_id, _race_fields(race), overwrite=True)
        conn.execute("UPDATE race SET name = ? WHERE id = ?", (race["name"], race_id))
        store.link_source(conn, race_id=race_id, source=SOURCE, source_id=race["key"])
        for link in race.get("links", []):
            store.link_source(conn, race_id=race_id, source=link["source"], source_id=str(link["id"]), url=link.get("url"))
        for category in race.get("categories", []):
            entry = cat.category_from_name(category["name"], category.get("detail"), category.get("fee_cny"))
            entry.update({key: category[key] for key in ("swim_km", "bike_km", "run_km", "format") if key in category})
            if "format" not in category:
                entry["format"] = cat.classify_format(entry["name"], entry["swim_km"], entry["bike_km"], entry["run_km"])
            store.upsert_category(conn, race_id, SOURCE, entry)
        for prize in race.get("prizes", []):
            ladder = {
                "label": prize["label"], "ranks": prize["ranks"], "method": "manual", "confidence": 1.0,
                "group_count": prize.get("group_count"), "gender": prize.get("gender"),
                "age_min": prize.get("age_min"), "age_max": prize.get("age_max"),
                "format": prize.get("format"), "pre_tax": prize.get("pre_tax"), "note": prize.get("note"),
            }
            store.write_prize_table(conn, race_id, SOURCE, ladder, status="manual")
        if race.get("calendar"):
            store.upsert_calendar(conn, athlete_key, race_id, race["calendar"])
        if race.get("result"):
            result = {key: race["result"].get(key) for key in RESULT_KEYS}
            for key, column in TIME_KEYS.items():
                result[column] = hms_to_s(race["result"].get(key))
            store.upsert_result(conn, race_id, athlete_key, result, SOURCE)
        report.append({"key": race["key"], "race_id": race_id, "how": how})
    conn.commit()
    return {"athlete": athlete_key, "races": report}
