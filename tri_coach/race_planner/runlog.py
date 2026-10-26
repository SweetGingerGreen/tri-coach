"""
Crawl runs and what changed between them.

After each crawl the watched fields of every race are snapshotted under the
run's id. The digest compares the latest run with the one before: new races,
changed dates or registration state, new prize ladders, and deadlines coming up.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any

from tri_coach.race_planner import store

RUN_SCHEMA = """
CREATE TABLE IF NOT EXISTS crawl_run (
  id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  window_start TEXT,
  window_end TEXT,
  stats_json TEXT
);
CREATE TABLE IF NOT EXISTS run_race_state (
  run_id INTEGER NOT NULL REFERENCES crawl_run (id) ON DELETE CASCADE,
  race_id INTEGER NOT NULL,
  state_json TEXT NOT NULL,
  PRIMARY KEY (run_id, race_id)
);
"""
WATCHED = ("name", "start_date", "end_date", "status", "reg_open", "reg_close", "rules_format")
FIELD_NAMES = {"start_date": "比赛日期", "end_date": "结束日期", "status": "状态", "reg_open": "报名开始",
               "reg_close": "报名截止", "rules_format": "规程格式", "name": "名称"}


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(RUN_SCHEMA)


def start_run(conn: sqlite3.Connection, window_start: str, window_end: str) -> int:
    ensure(conn)
    cur = conn.execute("INSERT INTO crawl_run (started_at, window_start, window_end) VALUES (?, ?, ?)",
                       (store.now_iso(), window_start, window_end))
    conn.commit()
    return int(cur.lastrowid)


def race_state(conn: sqlite3.Connection, race_id: int) -> dict[str, Any]:
    race = conn.execute("SELECT * FROM race WHERE id = ?", (race_id,)).fetchone()
    state = {key: race[key] for key in WATCHED}
    state["prize_labels"] = sorted(
        row["label"] for row in conn.execute(
            "SELECT label FROM prize_table WHERE race_id = ? AND status != 'rejected'", (race_id,)))
    state["categories"] = conn.execute("SELECT count(*) FROM race_category WHERE race_id = ?", (race_id,)).fetchone()[0]
    return state


def finish_run(conn: sqlite3.Connection, run_id: int, stats: dict[str, Any]) -> None:
    ensure(conn)
    for row in conn.execute("SELECT id FROM race").fetchall():
        conn.execute(
            "INSERT OR REPLACE INTO run_race_state (run_id, race_id, state_json) VALUES (?, ?, ?)",
            (run_id, row["id"], json.dumps(race_state(conn, row["id"]), ensure_ascii=False)),
        )
    conn.execute("UPDATE crawl_run SET finished_at = ?, stats_json = ? WHERE id = ?",
                 (store.now_iso(), json.dumps(stats, ensure_ascii=False, default=str), run_id))
    conn.commit()


def _states(conn: sqlite3.Connection, run_id: int) -> dict[int, dict[str, Any]]:
    return {row["race_id"]: json.loads(row["state_json"])
            for row in conn.execute("SELECT race_id, state_json FROM run_race_state WHERE run_id = ?", (run_id,))}


def digest(conn: sqlite3.Connection, *, today: dt.date, deadline_days: int = 21) -> dict[str, Any]:
    """Changes between the last two finished runs, plus registration deadlines within ``deadline_days``."""
    ensure(conn)
    runs = conn.execute("SELECT * FROM crawl_run WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 2").fetchall()
    out: dict[str, Any] = {"run": dict(runs[0]) if runs else None, "previous_run": dict(runs[1]) if len(runs) > 1 else None,
                           "new_races": [], "changes": [], "new_prizes": [], "deadlines": [], "opening": []}
    if not runs:
        return out
    latest = _states(conn, runs[0]["id"])
    previous = _states(conn, runs[1]["id"]) if len(runs) > 1 else None
    for race_id, state in sorted(latest.items(), key=lambda item: item[1].get("start_date") or ""):
        item = {"race_id": race_id, "name": state["name"], "start_date": state["start_date"]}
        if previous is not None and race_id not in previous:
            out["new_races"].append(item)
        elif previous is not None:
            before = previous[race_id]
            changed = {key: (before.get(key), state.get(key)) for key in WATCHED
                       if key != "name" and before.get(key) != state.get(key)}
            if changed:
                out["changes"].append({**item, "fields": {FIELD_NAMES[k]: v for k, v in changed.items()}})
            added = sorted(set(state["prize_labels"]) - set(before.get("prize_labels") or []))
            if added:
                out["new_prizes"].append({**item, "labels": added})
        start = dt.date.fromisoformat(state["start_date"]) if state.get("start_date") else None
        if start is None or start < today:
            continue
        if state.get("reg_close"):
            close = dt.date.fromisoformat(state["reg_close"])
            if today <= close <= today + dt.timedelta(days=deadline_days):
                out["deadlines"].append({**item, "reg_close": state["reg_close"], "days_left": (close - today).days})
        if state.get("reg_open"):
            opening = dt.date.fromisoformat(state["reg_open"])
            if today <= opening <= today + dt.timedelta(days=deadline_days):
                out["opening"].append({**item, "reg_open": state["reg_open"]})
    return out
