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

  evaluate [--today D]                                  per race: category, fee, travel, prize, break-even rank
  plan [--today D] [--out FILE]                         Markdown report with a proposed prize-money season
  digest                                                what changed between the last two crawls
  run                                                   crawl + digest + plan, report saved under reports/ (for a bot)
  doctor                                                check Python, packages, data dir, profile and site reachability

Data lives in ~/.local/share/tri-coach/race_planner (override with
TRI_RACE_PLANNER_DIR or --data-dir). Every fetched page is kept as a snapshot.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib
import json
import platform
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from tri_coach.race_planner import categories, evaluate, ingest, report, runlog, seed, store
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
    conn = store.connect(db_path)
    runlog.ensure(conn)
    return conn, data_dir


def china_today() -> dt.date:
    """Race dates are Chinese local dates; a cloud machine may run on UTC."""
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("Asia/Shanghai")).date()
    except Exception:  # noqa: BLE001 - no tz database: fixed UTC+8 is exact for China
        return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date()


def _today(args: argparse.Namespace) -> dt.date:
    return dt.date.fromisoformat(args.today) if getattr(args, "today", None) else china_today()


def _until(args: argparse.Namespace, today: dt.date) -> dt.date:
    return dt.date.fromisoformat(args.until) if getattr(args, "until", None) else dt.date(today.year + 1, 12, 31)


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


def do_crawl(conn: sqlite3.Connection, data_dir: Path, *, source: str, since: str, until: str, limit: int | None = None,
             interval: float = DEFAULT_MIN_INTERVAL_S, refresh: bool = False, offline: bool = False,
             verbose: bool = False, quiet: bool = False) -> dict[str, Any]:
    fetcher = Fetcher(conn, data_dir, min_interval_s=interval, max_age_s=0 if refresh else None if offline else 20 * 3600,
                      offline=offline, log=(lambda message: print(f"    {message}")) if verbose else None)
    names = CRAWL_ORDER if source == "all" else (source,)
    log = (lambda message: None) if quiet else print
    run_id = runlog.start_run(conn, since, until)
    summary: dict[str, Any] = {}
    for name in names:
        log(f"== {name} ({since} → {until})")
        try:
            summary[name] = SOURCES[name].crawl(conn, fetcher, since=since, until=until, limit=limit, log=log)
        except FetchError as exc:
            summary[name] = {"error": str(exc)}
            print(f"  ! {name} stopped: {exc}", file=sys.stderr)
        conn.commit()
    summary["network_requests"] = fetcher.network_calls
    summary["run_id"] = run_id
    runlog.finish_run(conn, run_id, summary)
    return summary


def _crawl_failed(summary: dict[str, Any]) -> bool:
    return any(isinstance(v, dict) and "error" in v for v in summary.values())


def cmd_crawl(args: argparse.Namespace) -> int:
    conn, data_dir = _connect(args)
    summary = do_crawl(conn, data_dir, source=args.source, since=args.since, until=args.until, limit=args.limit,
                       interval=args.interval, refresh=args.refresh, offline=args.offline, verbose=args.verbose)
    _print_json(summary)
    return 1 if _crawl_failed(summary) else 0


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


def _evaluations(conn: sqlite3.Connection, args: argparse.Namespace) -> tuple[evaluate.Profile, dt.date, dt.date, list[evaluate.Evaluation]]:
    profile = evaluate.load_profile(conn, args.athlete)
    today = _today(args)
    until = _until(args, today)
    return profile, today, until, evaluate.evaluate_races(conn, profile, today=today, until=until,
                                                          include_past=getattr(args, "include_past", False))


def cmd_evaluate(args: argparse.Namespace) -> int:
    conn, _ = _connect(args)
    profile, today, until, evaluations = _evaluations(conn, args)
    if args.json:
        _print_json([e.as_dict() for e in evaluations])
        return 0
    for e in evaluations:
        head = f"{e.race['id']:>4}  {e.race['start_date'] or '????-??-??'}  {pad(e.race['name'], 40)}"
        if e.excluded:
            print(f"{head}  -- {e.excluded}")
            continue
        ladder = f"{e.ladder['label']}[{e.ladder['status']}]" if e.ladder else "no ladder"
        print(f"{head}  fee {e.fee or '—'}  travel {(e.travel or {}).get('cost') or '—'}  "
              f"1st net {e.net_first or '—'}  break-even {e.break_even_rank or '—'}  {ladder}  "
              f"{'; '.join(e.conflicts + e.flags)}")
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    conn, _ = _connect(args)
    profile, today, until, evaluations = _evaluations(conn, args)
    plan = evaluate.plan_season(evaluations, profile)
    text = report.render(profile=profile, today=today, until=until, evaluations=evaluations, plan=plan,
                         digest=None, stats=None)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(args.out)
    else:
        print(text)
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    conn, _ = _connect(args)
    _print_json(runlog.digest(conn, today=_today(args), deadline_days=args.days))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """One unattended pass: crawl, then write the report. Meant for a scheduled bot."""
    conn, data_dir = _connect(args)
    today = _today(args)
    until = _until(args, today)
    since = args.since or f"{today.year}-01-01"
    summary: dict[str, Any] = {}
    if not args.no_crawl:
        summary = do_crawl(conn, data_dir, source="all", since=since, until=until.isoformat(), offline=args.offline,
                           interval=args.interval, quiet=True)
    profile = evaluate.load_profile(conn, args.athlete)
    evaluations = evaluate.evaluate_races(conn, profile, today=today, until=until)
    plan = evaluate.plan_season(evaluations, profile)
    changes = runlog.digest(conn, today=today, deadline_days=args.days)
    stats_line = None
    if summary:
        parts = [f"{name} {value.get('in_window', 0)} 场" if "error" not in value else f"{name} 失败"
                 for name, value in summary.items() if isinstance(value, dict)]
        stats_line = "，".join(parts) + f"，{summary.get('network_requests', 0)} 个请求"
    text = report.render(profile=profile, today=today, until=until, evaluations=evaluations, plan=plan,
                         digest=changes, stats=stats_line)
    reports = data_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
    path = reports / f"report_{stamp}.md"
    path.write_text(text, encoding="utf-8")
    (reports / "latest.md").write_text(text, encoding="utf-8")
    payload = {
        "report": str(path), "today": today.isoformat(), "until": until.isoformat(), "crawl": summary,
        "digest": {key: changes[key] for key in ("new_races", "changes", "new_prizes", "deadlines", "opening")},
        "picked": [{"race": e.race["name"], "date": e.race["start_date"], "net_first": e.net_first, "cost": e.cost,
                    "break_even_rank": e.break_even_rank} for e in plan.picked],
        "unverified_prize_tables": conn.execute("SELECT count(*) FROM prize_table WHERE status = 'extracted'").fetchone()[0],
    }
    (reports / "latest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    _print_json(payload)
    failed = summary and all(isinstance(v, dict) and "error" in v for k, v in summary.items() if k in SOURCES)
    return 1 if failed else 0


DOCTOR_PROBES = (
    ("zuicool", "GET", zuicool.list_url(1), True),
    ("chinatriathlon", "GET", chinatriathlon.list_url(1), True),
    ("wildto", "GET", "https://www.wildto.com/event/trievents", True),
    ("wildto-static", "GET", "https://static.wildto.com/", False),
    ("ctsa.org.cn (not crawled yet)", "GET", "https://www.ctsa.org.cn/", False),
    ("ctsa members (not crawled yet)", "GET", "https://triathlon.basts.com.cn/", False),
)


def cmd_doctor(args: argparse.Namespace) -> int:
    import requests

    data_dir, db_path = _paths(args)
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str, required: bool = True) -> None:
        checks.append({"check": name, "ok": ok, "required": required, "detail": detail})

    add("python>=3.10", sys.version_info >= (3, 10), platform.python_version())
    for module in ("requests", "bs4"):
        try:
            importlib.import_module(module)
            add(f"import {module}", True, "ok")
        except ImportError as exc:
            add(f"import {module}", False, f"{exc}; run pip install -r requirements.txt")
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".doctor_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        add("data dir writable", True, str(data_dir))
    except OSError as exc:
        add("data dir writable", False, f"{data_dir}: {exc}")
    try:
        conn = store.connect(db_path)
        runlog.ensure(conn)
        races = conn.execute("SELECT count(*) FROM race").fetchone()[0]
        add("database", True, f"{db_path}, {races} races")
        profile = evaluate.load_profile(conn, args.athlete)
        missing = [name for name, value in (("gender", profile.gender), ("home_city", profile.home_city),
                                            ("age_group or birth_year", profile.age_range_ref or profile.birth_year))
                   if not value]
        add("athlete profile", not missing and bool(profile.raw),
            "ok" if not missing and profile.raw else f"missing: {', '.join(missing) or 'whole profile'}; run seed", required=False)
    except sqlite3.Error as exc:
        add("database", False, str(exc))
    add("today (Asia/Shanghai)", True, china_today().isoformat(), required=False)
    if not args.skip_network:
        session = requests.Session()
        session.headers["User-Agent"] = "tri-coach-race-planner/0.1 doctor"
        for name, method, url, required in DOCTOR_PROBES:
            started = time.monotonic()
            try:
                response = session.request(method, url, timeout=args.timeout)
                ok = response.status_code < 500
                detail = f"HTTP {response.status_code} in {time.monotonic() - started:.1f}s"
            except requests.RequestException as exc:
                ok, detail = False, f"{type(exc).__name__}: {str(exc)[:120]}"
            add(f"reach {name}", ok, detail, required=required)
    for item in checks:
        mark = "ok  " if item["ok"] else ("FAIL" if item["required"] else "warn")
        print(f"[{mark}] {item['check']}: {item['detail']}")
    core_ok = all(item["ok"] for item in checks if item["required"] and not item["check"].startswith("reach "))
    any_source = args.skip_network or any(item["ok"] for item in checks if item["check"].startswith("reach ") and item["required"])
    return 0 if core_ok and any_source else 1


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

    def planning_args(command: argparse.ArgumentParser) -> None:
        command.add_argument("--athlete", default="self")
        command.add_argument("--today", help="pretend today is this date (YYYY-MM-DD), e.g. for a backtest")
        command.add_argument("--until", help="last race date to consider (default: end of next year)")

    evaluate_cmd = sub.add_parser("evaluate", help="per-race money view for the athlete")
    planning_args(evaluate_cmd)
    evaluate_cmd.add_argument("--include-past", action="store_true")
    evaluate_cmd.add_argument("--json", action="store_true")
    evaluate_cmd.set_defaults(func=cmd_evaluate)

    plan = sub.add_parser("plan", help="Markdown report with a proposed season")
    planning_args(plan)
    plan.add_argument("--out", help="write the report to this file")
    plan.set_defaults(func=cmd_plan)

    digest = sub.add_parser("digest", help="changes between the last two crawls")
    digest.add_argument("--today")
    digest.add_argument("--days", type=int, default=21, help="registration deadlines within this many days")
    digest.set_defaults(func=cmd_digest)

    run = sub.add_parser("run", help="crawl + digest + plan; saves reports/latest.md (for a scheduled bot)")
    planning_args(run)
    run.add_argument("--since", help="first race date to crawl (default: 1 Jan this year)")
    run.add_argument("--days", type=int, default=21)
    run.add_argument("--interval", type=float, default=DEFAULT_MIN_INTERVAL_S)
    run.add_argument("--offline", action="store_true", help="use stored snapshots only")
    run.add_argument("--no-crawl", action="store_true", help="only rebuild the report from the database")
    run.set_defaults(func=cmd_run)

    doctor = sub.add_parser("doctor", help="check this machine can run the planner")
    doctor.add_argument("--athlete", default="self")
    doctor.add_argument("--skip-network", action="store_true")
    doctor.add_argument("--timeout", type=float, default=10.0)
    doctor.set_defaults(func=cmd_doctor)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
