"""SQLite storage for the race planner: schema, connection and upserts."""
from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
DATA_DIR_ENV = "TRI_RACE_PLANNER_DIR"

# Prize rows a re-extraction may replace. Rows a person has checked are kept.
REPLACEABLE_PRIZE_STATUSES = ("extracted",)
PRIZE_STATUSES = ("extracted", "verified", "rejected", "manual")

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshot (
  id INTEGER PRIMARY KEY,
  url TEXT NOT NULL,
  method TEXT NOT NULL DEFAULT 'GET',
  request_body TEXT NOT NULL DEFAULT '',
  fetched_at TEXT NOT NULL,
  status INTEGER NOT NULL,
  content_type TEXT,
  sha256 TEXT NOT NULL,
  path TEXT NOT NULL,
  UNIQUE (url, method, request_body, sha256)
);

CREATE TABLE IF NOT EXISTS race (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  sport TEXT NOT NULL DEFAULT 'triathlon',
  audience TEXT NOT NULL DEFAULT 'adult',
  series TEXT,
  start_date TEXT,
  end_date TEXT,
  country TEXT,
  province TEXT,
  city TEXT,
  venue TEXT,
  lat REAL,
  lng REAL,
  elevation_m REAL,
  level TEXT,
  reg_open TEXT,
  reg_close TEXT,
  status TEXT,
  description TEXT,
  prize_rules_text TEXT,
  rules_format TEXT,
  pre_tax INTEGER,
  note TEXT,
  manual INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS race_start_date ON race (start_date);

CREATE TABLE IF NOT EXISTS race_source (
  source TEXT NOT NULL,
  source_id TEXT NOT NULL,
  race_id INTEGER NOT NULL REFERENCES race (id) ON DELETE CASCADE,
  url TEXT,
  snapshot_id INTEGER REFERENCES snapshot (id),
  rules_url TEXT,
  rules_snapshot_id INTEGER REFERENCES snapshot (id),
  raw_json TEXT,
  seen_at TEXT NOT NULL,
  PRIMARY KEY (source, source_id)
);

CREATE TABLE IF NOT EXISTS race_category (
  id INTEGER PRIMARY KEY,
  race_id INTEGER NOT NULL REFERENCES race (id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  detail TEXT,
  format TEXT,
  swim_km REAL,
  bike_km REAL,
  run_km REAL,
  fee_cny REAL,
  reg_status TEXT,
  source TEXT NOT NULL,
  UNIQUE (race_id, name, source)
);

CREATE TABLE IF NOT EXISTS prize_table (
  id INTEGER PRIMARY KEY,
  race_id INTEGER NOT NULL REFERENCES race (id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  group_count INTEGER,
  gender TEXT,
  age_min INTEGER,
  age_max INTEGER,
  format TEXT,
  currency TEXT NOT NULL DEFAULT 'CNY',
  pre_tax INTEGER,
  method TEXT NOT NULL,
  status TEXT NOT NULL,
  confidence REAL,
  issues TEXT,
  source TEXT NOT NULL,
  snapshot_id INTEGER REFERENCES snapshot (id),
  reviewed_at TEXT,
  note TEXT,
  UNIQUE (race_id, label, source)
);

CREATE TABLE IF NOT EXISTS prize_rank (
  prize_table_id INTEGER NOT NULL REFERENCES prize_table (id) ON DELETE CASCADE,
  rank INTEGER NOT NULL,
  amount REAL NOT NULL,
  PRIMARY KEY (prize_table_id, rank)
);

CREATE TABLE IF NOT EXISTS result (
  id INTEGER PRIMARY KEY,
  race_id INTEGER NOT NULL REFERENCES race (id) ON DELETE CASCADE,
  athlete_key TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT '',
  division TEXT,
  finish_s INTEGER,
  swim_s INTEGER,
  t1_s INTEGER,
  bike_s INTEGER,
  t2_s INTEGER,
  run_s INTEGER,
  time_source TEXT,
  rank_division INTEGER,
  division_field INTEGER,
  rank_gender INTEGER,
  gender_field INTEGER,
  rank_overall INTEGER,
  overall_field INTEGER,
  prize_cny REAL,
  source TEXT NOT NULL,
  note TEXT,
  UNIQUE (race_id, athlete_key, category)
);

CREATE TABLE IF NOT EXISTS athlete_profile (
  athlete_key TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_entry (
  id INTEGER PRIMARY KEY,
  athlete_key TEXT NOT NULL,
  race_id INTEGER NOT NULL REFERENCES race (id) ON DELETE CASCADE,
  role TEXT,
  status TEXT,
  note TEXT,
  UNIQUE (athlete_key, race_id)
);
"""

RACE_FIELDS = (
    "name", "sport", "audience", "series", "start_date", "end_date", "country", "province", "city",
    "venue", "lat", "lng", "elevation_m", "level", "reg_open", "reg_close", "status", "description",
    "prize_rules_text", "rules_format", "pre_tax", "note",
)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def default_data_dir() -> Path:
    override = os.environ.get(DATA_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".local" / "share" / "tri-coach" / "race_planner"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


# --- snapshots -------------------------------------------------------------

def find_snapshot(conn: sqlite3.Connection, url: str, method: str = "GET", body: str = "") -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM snapshot WHERE url = ? AND method = ? AND request_body = ? ORDER BY fetched_at DESC, id DESC LIMIT 1",
        (url, method, body),
    ).fetchone()


def add_snapshot(
    conn: sqlite3.Connection, *, url: str, method: str, body: str, status: int,
    content_type: str | None, sha256: str, path: str,
) -> int:
    row = conn.execute(
        "SELECT id FROM snapshot WHERE url = ? AND method = ? AND request_body = ? AND sha256 = ?",
        (url, method, body, sha256),
    ).fetchone()
    if row:
        conn.execute("UPDATE snapshot SET fetched_at = ?, status = ? WHERE id = ?", (now_iso(), status, row["id"]))
        return int(row["id"])
    cur = conn.execute(
        "INSERT INTO snapshot (url, method, request_body, fetched_at, status, content_type, sha256, path)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (url, method, body, now_iso(), status, content_type, sha256, path),
    )
    return int(cur.lastrowid)


def get_snapshot(conn: sqlite3.Connection, snapshot_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM snapshot WHERE id = ?", (snapshot_id,)).fetchone()


# --- races -----------------------------------------------------------------

def race_by_source(conn: sqlite3.Connection, source: str, source_id: str) -> int | None:
    row = conn.execute(
        "SELECT race_id FROM race_source WHERE source = ? AND source_id = ?", (source, str(source_id))
    ).fetchone()
    return int(row["race_id"]) if row else None


def insert_race(conn: sqlite3.Connection, fields: dict[str, Any], *, manual: bool = False) -> int:
    data = {key: fields.get(key) for key in RACE_FIELDS if fields.get(key) is not None}
    if not data.get("name"):
        raise ValueError("a race needs a name")
    stamp = now_iso()
    columns = list(data) + ["manual", "created_at", "updated_at"]
    values = list(data.values()) + [1 if manual else 0, stamp, stamp]
    cur = conn.execute(
        f"INSERT INTO race ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", values
    )
    return int(cur.lastrowid)


def merge_race(conn: sqlite3.Connection, race_id: int, fields: dict[str, Any], *, overwrite: bool = False) -> None:
    """Fill empty columns from a new source; ``overwrite`` (manual seed) replaces them."""
    data = {key: fields.get(key) for key in RACE_FIELDS if fields.get(key) is not None and key != "name"}
    if not data:
        return
    if overwrite:
        assignments = ", ".join(f"{key} = ?" for key in data)
    else:
        assignments = ", ".join(f"{key} = COALESCE({key}, ?)" for key in data)
    conn.execute(
        f"UPDATE race SET {assignments}, updated_at = ? WHERE id = ?",
        list(data.values()) + [now_iso(), race_id],
    )
    if overwrite:
        conn.execute("UPDATE race SET manual = 1 WHERE id = ?", (race_id,))


def link_source(
    conn: sqlite3.Connection, *, race_id: int, source: str, source_id: str, url: str | None = None,
    snapshot_id: int | None = None, rules_url: str | None = None, rules_snapshot_id: int | None = None,
    raw: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO race_source (source, source_id, race_id, url, snapshot_id, rules_url, rules_snapshot_id, raw_json, seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (source, source_id) DO UPDATE SET
          race_id = excluded.race_id,
          url = COALESCE(excluded.url, race_source.url),
          snapshot_id = COALESCE(excluded.snapshot_id, race_source.snapshot_id),
          rules_url = COALESCE(excluded.rules_url, race_source.rules_url),
          rules_snapshot_id = COALESCE(excluded.rules_snapshot_id, race_source.rules_snapshot_id),
          raw_json = COALESCE(excluded.raw_json, race_source.raw_json),
          seen_at = excluded.seen_at
        """,
        (
            source, str(source_id), race_id, url, snapshot_id, rules_url, rules_snapshot_id,
            json.dumps(raw, ensure_ascii=False) if raw is not None else None, now_iso(),
        ),
    )


def upsert_category(conn: sqlite3.Connection, race_id: int, source: str, category: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO race_category (race_id, name, detail, format, swim_km, bike_km, run_km, fee_cny, reg_status, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (race_id, name, source) DO UPDATE SET
          detail = excluded.detail, format = excluded.format, swim_km = excluded.swim_km,
          bike_km = excluded.bike_km, run_km = excluded.run_km, fee_cny = excluded.fee_cny,
          reg_status = excluded.reg_status
        """,
        (
            race_id, category["name"], category.get("detail"), category.get("format"), category.get("swim_km"),
            category.get("bike_km"), category.get("run_km"), category.get("fee_cny"), category.get("reg_status"),
            source,
        ),
    )


# --- prizes ----------------------------------------------------------------

def replace_extracted_prizes(
    conn: sqlite3.Connection, race_id: int, source: str, ladders: Iterable[dict[str, Any]],
    *, snapshot_id: int | None, pre_tax: int | None,
) -> dict[str, int]:
    """Swap this source's machine-extracted ladders; never touch reviewed or manual rows."""
    kept = {
        row["label"]
        for row in conn.execute(
            "SELECT label FROM prize_table WHERE race_id = ? AND source = ? AND status NOT IN (%s)"
            % ", ".join("?" for _ in REPLACEABLE_PRIZE_STATUSES),
            (race_id, source, *REPLACEABLE_PRIZE_STATUSES),
        )
    }
    conn.execute(
        "DELETE FROM prize_table WHERE race_id = ? AND source = ? AND status IN (%s)"
        % ", ".join("?" for _ in REPLACEABLE_PRIZE_STATUSES),
        (race_id, source, *REPLACEABLE_PRIZE_STATUSES),
    )
    counts = {"written": 0, "kept_reviewed": 0}
    for ladder in ladders:
        if ladder["label"] in kept:
            counts["kept_reviewed"] += 1
            continue
        write_prize_table(conn, race_id, source, ladder, status="extracted", snapshot_id=snapshot_id, pre_tax=pre_tax)
        counts["written"] += 1
    return counts


def write_prize_table(
    conn: sqlite3.Connection, race_id: int, source: str, ladder: dict[str, Any], *, status: str,
    snapshot_id: int | None = None, pre_tax: int | None = None,
) -> int:
    if status not in PRIZE_STATUSES:
        raise ValueError(f"unknown prize status {status!r}")
    ranks = {int(rank): float(amount) for rank, amount in ladder["ranks"].items()}
    conn.execute(
        """
        INSERT INTO prize_table (race_id, label, group_count, gender, age_min, age_max, format, currency, pre_tax,
                                 method, status, confidence, issues, source, snapshot_id, reviewed_at, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (race_id, label, source) DO UPDATE SET
          group_count = excluded.group_count, gender = excluded.gender, age_min = excluded.age_min,
          age_max = excluded.age_max, format = excluded.format, currency = excluded.currency,
          pre_tax = excluded.pre_tax, method = excluded.method, status = excluded.status,
          confidence = excluded.confidence, issues = excluded.issues, snapshot_id = excluded.snapshot_id,
          reviewed_at = excluded.reviewed_at, note = excluded.note
        """,
        (
            race_id, ladder["label"], ladder.get("group_count"), ladder.get("gender"), ladder.get("age_min"),
            ladder.get("age_max"), ladder.get("format"), ladder.get("currency", "CNY"),
            ladder["pre_tax"] if ladder.get("pre_tax") is not None else pre_tax, ladder.get("method", "table"),
            status, ladder.get("confidence"),
            json.dumps(ladder.get("issues") or [], ensure_ascii=False), source, snapshot_id,
            now_iso() if status in ("verified", "rejected", "manual") else None, ladder.get("note"),
        ),
    )
    # lastrowid is not reliable on the UPDATE branch of an upsert, so look the row up.
    prize_id = int(
        conn.execute(
            "SELECT id FROM prize_table WHERE race_id = ? AND label = ? AND source = ?", (race_id, ladder["label"], source)
        ).fetchone()["id"]
    )
    conn.execute("DELETE FROM prize_rank WHERE prize_table_id = ?", (prize_id,))
    conn.executemany(
        "INSERT INTO prize_rank (prize_table_id, rank, amount) VALUES (?, ?, ?)",
        [(prize_id, rank, amount) for rank, amount in sorted(ranks.items())],
    )
    return prize_id


def set_prize_status(conn: sqlite3.Connection, prize_id: int, status: str, note: str | None = None) -> bool:
    if status not in ("verified", "rejected", "extracted"):
        raise ValueError(f"cannot set status {status!r} by hand")
    cur = conn.execute(
        "UPDATE prize_table SET status = ?, reviewed_at = ?, note = COALESCE(?, note) WHERE id = ?",
        (status, now_iso() if status != "extracted" else None, note, prize_id),
    )
    return cur.rowcount == 1


def prize_ladder(conn: sqlite3.Connection, prize_id: int) -> dict[int, float]:
    return {
        int(row["rank"]): float(row["amount"])
        for row in conn.execute("SELECT rank, amount FROM prize_rank WHERE prize_table_id = ? ORDER BY rank", (prize_id,))
    }


# --- athlete ---------------------------------------------------------------

def upsert_result(conn: sqlite3.Connection, race_id: int, athlete_key: str, result: dict[str, Any], source: str) -> None:
    columns = (
        "division", "finish_s", "swim_s", "t1_s", "bike_s", "t2_s", "run_s", "time_source", "rank_division",
        "division_field", "rank_gender", "gender_field", "rank_overall", "overall_field", "prize_cny", "note",
    )
    values = [result.get(column) for column in columns]
    conn.execute(
        f"""
        INSERT INTO result (race_id, athlete_key, category, {', '.join(columns)}, source)
        VALUES (?, ?, ?, {', '.join('?' for _ in columns)}, ?)
        ON CONFLICT (race_id, athlete_key, category) DO UPDATE SET
          {', '.join(f'{column} = excluded.{column}' for column in columns)}, source = excluded.source
        """,
        [race_id, athlete_key, result.get("category") or ""] + values + [source],
    )


def upsert_profile(conn: sqlite3.Connection, athlete_key: str, data: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO athlete_profile (athlete_key, data, updated_at) VALUES (?, ?, ?)
        ON CONFLICT (athlete_key) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
        """,
        (athlete_key, json.dumps(data, ensure_ascii=False), now_iso()),
    )


def upsert_calendar(conn: sqlite3.Connection, athlete_key: str, race_id: int, entry: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO calendar_entry (athlete_key, race_id, role, status, note) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (athlete_key, race_id) DO UPDATE SET role = excluded.role, status = excluded.status, note = excluded.note
        """,
        (athlete_key, race_id, entry.get("role"), entry.get("status"), entry.get("note")),
    )


def merge_races(conn: sqlite3.Connection, keep: int, drop: int) -> None:
    """Fold race ``drop`` into ``keep`` (same event found twice) and delete it."""
    if keep == drop:
        return
    row = conn.execute("SELECT * FROM race WHERE id = ?", (drop,)).fetchone()
    if row is None:
        return
    merge_race(conn, keep, {key: row[key] for key in RACE_FIELDS})
    conn.execute("UPDATE race_source SET race_id = ? WHERE race_id = ?", (keep, drop))
    conn.execute("UPDATE OR IGNORE race_category SET race_id = ? WHERE race_id = ?", (keep, drop))
    conn.execute("UPDATE OR IGNORE prize_table SET race_id = ? WHERE race_id = ?", (keep, drop))
    conn.execute("UPDATE OR IGNORE result SET race_id = ? WHERE race_id = ?", (keep, drop))
    conn.execute("UPDATE OR IGNORE calendar_entry SET race_id = ? WHERE race_id = ?", (keep, drop))
    conn.execute("DELETE FROM race WHERE id = ?", (drop,))
