#!/usr/bin/env python3
"""
Race planner CLI (P0: data collection).

  crawl [--source all|zuicool|chinatriathlon|wildto]   fetch listings, categories, fees and prize tables
  seed PATH                                             load the athlete's own races (private JSON)
  races [--year 2026]                                   one line per race with sources and best prize
  race ID                                               everything stored for one race
  prizes [--status extracted]                           prize ladders waiting for review
  verify PRIZE_ID [--reject] [--note TEXT]              mark a ladder checked against the rules
  reextract                                             rerun parsers on stored pages: prizes, categories, sport (no network)
  stats                                                 coverage per source
  where                                                 data directory and database path

Data lives in ~/.local/share/tri-coach/race_planner (override with
TRI_RACE_PLANNER_DIR or --data-dir). Every fetched page is kept as a snapshot.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from tri_coach.race_planner import categories, ingest, seed, store
from tri_coach.race_planner.fetch import DEFAULT_MIN_INTERVAL_S, Fetcher, FetchError
from tri_coach.race_planner.sources import chinatriathlon, wildto, zuicool
from tri_coach.race_planner.textutil import pad, s_to_hms

SOURCES = {"zuicool": zuicool, "chinatriathlon": chinatriathlon, "wildto": wildto}
# zuicool first so chinatriathlon can join its races by the zuicool id it links to.
CRAWL_ORDER = ("zuicool", "chinatriathlon", "wildto")


def _paths(args: argparse.Namespace) -> tuple[Path, Path]:
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else store.default_data_dir()
    return data_dir, data_dir / "race_planner.sqlite"


def _connect(args: argparse.Namespace) -> tuple[sqlite3.Connection, Path]:
    data_dir, db_path = _paths(args)
    return store.connect(db_path), data_dir


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _ladder_text(ladder: dict[int, float], width: int = 8) -> str:
    items = [f"{rank}:{amount:g}" for rank, amount in sorted(ladder.items())]
    return " ".join(items[:width]) + (" …" if len(items) > width else "")


# --- commands --------------------------------------------------------------

def cmd_where(args: argparse.Namespace) -> int:
    data_dir, db_path = _paths(args)
    _print_json({"data_dir": str(data_dir), "database": str(db_path), "exists": db_path.exists()})
    return 0


def cmd_crawl(args: argparse.Namespace) -> int:
    conn, data_dir = _connect(args)
    fetcher = Fetcher(conn, data_dir, min_interval_s=args.interval, max_age_s=0 if args.refresh else None if args.offline else 20 * 3600,
                      offline=args.offline, log=(lambda message: print(f"    {message}")) if args.verbose else None)
    names = CRAWL_ORDER if args.source == "all" else (args.source,)
    summary = {}
    for name in names:
        print(f"== {name} ({args.since} → {args.until})")
        try:
            summary[name] = SOURCES[name].crawl(conn, fetcher, since=args.since, until=args.until, limit=args.limit)
        except FetchError as exc:
            summary[name] = {"error": str(exc)}
            print(f"  ! {name} stopped: {exc}")
        conn.commit()
    summary["network_requests"] = fetcher.network_calls
    _print_json(summary)
    return 0 if not any(isinstance(v, dict) and "error" in v for v in summary.values()) else 1


def cmd_seed(args: argparse.Namespace) -> int:
    conn, _ = _connect(args)
    _print_json(seed.load(conn, Path(args.path)))
    return 0


def _race_rows(conn: sqlite3.Connection, args: argparse.Namespace) -> list[sqlite3.Row]:
    where, params = ["1 = 1"], []
    if args.year:
        where.append("substr(r.start_date, 1, 4) = ?")
        params.append(str(args.year))
    if args.sport:
        where.append("r.sport = ?")
        params.append(args.sport)
    if not args.all_audiences:
        where.append("r.audience = 'adult'")
    return conn.execute(
        f"""
        SELECT r.*, (SELECT group_concat(source || ':' || source_id, ' ') FROM race_source s WHERE s.race_id = r.id) AS sources,
               (SELECT count(*) FROM race_category c WHERE c.race_id = r.id) AS n_categories,
               (SELECT count(*) FROM prize_table p WHERE p.race_id = r.id AND p.status != 'rejected') AS n_prizes,
               (SELECT max(pr.amount) FROM prize_table p JOIN prize_rank pr ON pr.prize_table_id = p.id
                 WHERE p.race_id = r.id AND pr.rank = 1 AND p.status != 'rejected') AS top_prize,
               (SELECT role || '/' || status FROM calendar_entry e WHERE e.race_id = r.id LIMIT 1) AS calendar
        FROM race r WHERE {' AND '.join(where)} ORDER BY r.start_date IS NULL, r.start_date, r.id
        """,
        params,
    ).fetchall()


def cmd_races(args: argparse.Namespace) -> int:
    conn, _ = _connect(args)
    rows = _race_rows(conn, args)
    if args.json:
        _print_json([dict(row) for row in rows])
        return 0
    for row in rows:
        place = " ".join(part for part in (row["province"], row["city"]) if part)
        prize = f"1st ¥{row['top_prize']:g} ({row['n_prizes']} ladders)" if row["top_prize"] else "no prize table"
        flags = " ".join(flag for flag in (
            f"[{row['calendar']}]" if row["calendar"] else "",
            "rules=images" if row["rules_format"] == "images" else "",
            row["status"] or "",
        ) if flag)
        print(f"{row['id']:>4}  {row['start_date'] or '????-??-??'}  {row['sport']:<10} {pad(row['name'], 44)}  {pad(place, 12)}  "
              f"{prize:<26} cats {row['n_categories']:<2} {row['sources']}  {flags}")
    print(f"{len(rows)} races")
    return 0


def cmd_race(args: argparse.Namespace) -> int:
    conn, _ = _connect(args)
    race = conn.execute("SELECT * FROM race WHERE id = ?", (args.id,)).fetchone()
    if race is None:
        print(f"no race {args.id}", file=sys.stderr)
        return 1
    payload = {
        "race": dict(race),
        "sources": [dict(row) for row in conn.execute(
            "SELECT source, source_id, url, rules_url FROM race_source WHERE race_id = ?", (args.id,))],
        "categories": [dict(row) for row in conn.execute(
            "SELECT name, format, swim_km, bike_km, run_km, fee_cny, reg_status, source FROM race_category WHERE race_id = ?",
            (args.id,))],
        "prizes": [],
        "results": [],
        "calendar": [dict(row) for row in conn.execute(
            "SELECT athlete_key, role, status, note FROM calendar_entry WHERE race_id = ?", (args.id,))],
    }
    for row in conn.execute("SELECT * FROM prize_table WHERE race_id = ? ORDER BY id", (args.id,)):
        item = {key: row[key] for key in ("id", "label", "group_count", "gender", "age_min", "age_max", "pre_tax",
                                           "method", "status", "confidence", "source", "note")}
        item["issues"] = json.loads(row["issues"] or "[]")
        item["ranks"] = store.prize_ladder(conn, row["id"])
        payload["prizes"].append(item)
    for row in conn.execute("SELECT * FROM result WHERE race_id = ?", (args.id,)):
        item = dict(row)
        for column in ("finish_s", "swim_s", "t1_s", "bike_s", "t2_s", "run_s"):
            item[column.replace("_s", "")] = s_to_hms(item.pop(column))
        payload["results"].append(item)
    if not args.full and payload["race"].get("prize_rules_text"):
        payload["race"]["prize_rules_text"] = payload["race"]["prize_rules_text"][:600] + " …"
    _print_json(payload)
    return 0


def cmd_prizes(args: argparse.Namespace) -> int:
    conn, _ = _connect(args)
    where, params = ["1 = 1"], []
    if args.status:
        where.append("p.status = ?")
        params.append(args.status)
    if args.race:
        where.append("p.race_id = ?")
        params.append(args.race)
    rows = conn.execute(
        f"""SELECT p.*, r.name AS race_name, r.start_date FROM prize_table p JOIN race r ON r.id = p.race_id
            WHERE {' AND '.join(where)} ORDER BY r.start_date, p.race_id, p.id""",
        params,
    ).fetchall()
    out = []
    for row in rows:
        ladder = store.prize_ladder(conn, row["id"])
        item = {"id": row["id"], "race_id": row["race_id"], "race": row["race_name"], "date": row["start_date"],
                "label": row["label"], "groups": row["group_count"], "gender": row["gender"],
                "ages": f"{row['age_min']}-{row['age_max']}" if row["age_min"] else None, "pre_tax": row["pre_tax"],
                "status": row["status"], "method": row["method"], "confidence": row["confidence"],
                "issues": json.loads(row["issues"] or "[]"), "ranks": ladder, "source": row["source"]}
        out.append(item)
    if args.json:
        _print_json(out)
        return 0
    for item in out:
        meta = " ".join(part for part in (
            f"×{item['groups']}组" if item["groups"] else "", item["gender"] or "", item["ages"] or "",
            "税前" if item["pre_tax"] == 1 else "", ",".join(item["issues"]),
        ) if part)
        print(f"{item['id']:>4} [{item['status']:<9}] {item['date']} {pad(item['race'], 30)} | {pad(item['label'], 26)} "
              f"| {_ladder_text(item['ranks'])}  {meta}  ({item['source']}, conf {item['confidence']})")
    print(f"{len(out)} prize ladders")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    conn, _ = _connect(args)
    status = "rejected" if args.reject else "extracted" if args.unverify else "verified"
    if not store.set_prize_status(conn, args.id, status, args.note):
        print(f"no prize ladder {args.id}", file=sys.stderr)
        return 1
    conn.commit()
    print(f"prize ladder {args.id} -> {status}")
    return 0


def cmd_reextract(args: argparse.Namespace) -> int:
    conn, data_dir = _connect(args)
    fetcher = Fetcher(conn, data_dir, offline=True)
    where = "rules_snapshot_id IS NOT NULL" + (" AND race_id = ?" if args.race else "")
    rows = conn.execute(f"SELECT * FROM race_source WHERE {where}", ((args.race,) if args.race else ())).fetchall()
    totals = {"pages": 0, "ladders": 0, "kept_reviewed": 0, "reclassified": 0}
    race_ids = sorted({row["race_id"] for row in rows})
    conn.executemany("UPDATE race SET rules_format = NULL WHERE id = ?", [(race_id,) for race_id in race_ids])
    # Sport and audience come from the race name; rerun them too (seeded races keep their own).
    for race in conn.execute("SELECT id, name, sport, audience FROM race WHERE manual = 0").fetchall():
        sport = categories.classify_sport(race["name"])
        audience = categories.classify_audience(race["name"])
        sport = race["sport"] if sport == "other" and race["sport"] not in (None, "other") else sport
        if (sport, audience) != (race["sport"], race["audience"]):
            conn.execute("UPDATE race SET sport = ?, audience = ? WHERE id = ?", (sport, audience, race["id"]))
            totals["reclassified"] += 1
    for row in rows:
        page = fetcher.load(row["rules_snapshot_id"])
        html = page.text if row["source"] != "zuicool" else zuicool.parse_news(page.text)
        outcome = ingest.apply_rules(conn, row["race_id"], source=row["source"], source_id=row["source_id"], html=html,
                                     rules_url=row["rules_url"], snapshot_id=row["rules_snapshot_id"])
        totals["pages"] += 1
        totals["ladders"] += outcome["ladders"]
        totals["kept_reviewed"] += outcome["kept_reviewed"]
    conn.commit()
    _print_json(totals)
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    conn, data_dir = _connect(args)
    year = str(args.year) if args.year else None
    year_clause = "AND substr(r.start_date, 1, 4) = ?" if year else ""
    params = (year,) if year else ()
    payload = {
        "data_dir": str(data_dir),
        "races_by_sport": {row["sport"]: row["n"] for row in conn.execute(
            f"SELECT sport, count(*) n FROM race r WHERE 1=1 {year_clause} GROUP BY sport ORDER BY n DESC", params)},
        "races_by_source": {row["source"]: row["n"] for row in conn.execute(
            f"""SELECT s.source, count(DISTINCT s.race_id) n FROM race_source s JOIN race r ON r.id = s.race_id
                WHERE 1=1 {year_clause} GROUP BY s.source""", params)},
        "races_on_2_plus_sources": conn.execute(
            f"""SELECT count(*) FROM (SELECT s.race_id FROM race_source s JOIN race r ON r.id = s.race_id
                WHERE s.source != 'manual' {year_clause} GROUP BY s.race_id HAVING count(DISTINCT s.source) > 1)""",
            params).fetchone()[0],
        "rules_format": {row["rules_format"] or "not fetched": row["n"] for row in conn.execute(
            f"SELECT rules_format, count(*) n FROM race r WHERE 1=1 {year_clause} GROUP BY rules_format", params)},
        "prize_ladders_by_status": {row["status"]: row["n"] for row in conn.execute(
            f"""SELECT p.status, count(*) n FROM prize_table p JOIN race r ON r.id = p.race_id
                WHERE 1=1 {year_clause} GROUP BY p.status""", params)},
        "races_with_prize_ladder": conn.execute(
            f"""SELECT count(DISTINCT p.race_id) FROM prize_table p JOIN race r ON r.id = p.race_id
                WHERE p.status != 'rejected' {year_clause}""", params).fetchone()[0],
        "snapshots": conn.execute("SELECT count(*) FROM snapshot").fetchone()[0],
    }
    _print_json(payload)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    today = dt.date.today()
    parser = argparse.ArgumentParser(prog="python3 -m tri_coach.race_planner", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", help="override the data directory")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("where").set_defaults(func=cmd_where)

    crawl = sub.add_parser("crawl", help="fetch race listings and rules")
    crawl.add_argument("--source", choices=("all",) + CRAWL_ORDER, default="all")
    crawl.add_argument("--since", default=f"{today.year}-01-01")
    crawl.add_argument("--until", default=f"{today.year + 1}-12-31")
    crawl.add_argument("--limit", type=int, help="at most N events per source (for trying things out)")
    crawl.add_argument("--interval", type=float, default=DEFAULT_MIN_INTERVAL_S, help="seconds between requests to one host")
    crawl.add_argument("--refresh", action="store_true", help="refetch even if a recent snapshot exists")
    crawl.add_argument("--offline", action="store_true", help="use stored snapshots only")
    crawl.add_argument("--verbose", "-v", action="store_true")
    crawl.set_defaults(func=cmd_crawl)

    seed_cmd = sub.add_parser("seed", help="load the athlete's own races from JSON")
    seed_cmd.add_argument("path")
    seed_cmd.set_defaults(func=cmd_seed)

    races = sub.add_parser("races", help="list races")
    races.add_argument("--year", type=int)
    races.add_argument("--sport")
    races.add_argument("--all-audiences", action="store_true", help="include youth-only races")
    races.add_argument("--json", action="store_true")
    races.set_defaults(func=cmd_races)

    race = sub.add_parser("race", help="show one race")
    race.add_argument("id", type=int)
    race.add_argument("--full", action="store_true", help="print the whole prize rules text")
    race.set_defaults(func=cmd_race)

    prizes_cmd = sub.add_parser("prizes", help="list prize ladders")
    prizes_cmd.add_argument("--status", choices=store.PRIZE_STATUSES)
    prizes_cmd.add_argument("--race", type=int)
    prizes_cmd.add_argument("--json", action="store_true")
    prizes_cmd.set_defaults(func=cmd_prizes)

    verify = sub.add_parser("verify", help="mark a prize ladder as checked")
    verify.add_argument("id", type=int)
    group = verify.add_mutually_exclusive_group()
    group.add_argument("--reject", action="store_true")
    group.add_argument("--unverify", action="store_true", help="back to 'extracted'")
    verify.add_argument("--note")
    verify.set_defaults(func=cmd_verify)

    reextract = sub.add_parser("reextract", help="rerun prize extraction on stored rules pages")
    reextract.add_argument("--race", type=int)
    reextract.set_defaults(func=cmd_reextract)

    stats = sub.add_parser("stats", help="coverage summary")
    stats.add_argument("--year", type=int)
    stats.set_defaults(func=cmd_stats)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
