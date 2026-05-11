#!/usr/bin/env python3
"""
Prefill daily preflight CSVs from Garmin Connect.

Garmin can provide recent readiness/sleep data and scheduled calendar sessions.
It cannot prove future availability or pain status, so those fields remain
empty unless Garmin has an explicit same-day calendar workout signal.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Any

import bike_plan_daily_preflight


ROOT = Path(__file__).parent.resolve()
DEFAULT_SLOTS_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_slots_latest.csv"
DEFAULT_FIXED_SESSIONS_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_fixed_sessions_latest.csv"
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "garmin_preflight_prefill_latest.json"
DEFAULT_TOKENSTORE = Path.home() / ".garminconnect"
DEFAULT_ENV_FILES = [
    Path.home() / ".openclaw_lark" / ".env",
    Path.home() / ".openclaw_lark" / ".openclaw" / ".env",
    Path.home() / "Desktop" / "OpenClaw" / ".env",
]
GARMIN_ENV_KEYS = ("GARMIN_EMAIL", "GARMIN_PASSWORD", "GARMIN_REGION")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slots-csv", type=Path, default=DEFAULT_SLOTS_CSV)
    parser.add_argument("--fixed-sessions-csv", type=Path, default=DEFAULT_FIXED_SESSIONS_CSV)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--tokenstore", type=Path, default=DEFAULT_TOKENSTORE)
    parser.add_argument("--env-file", type=Path, action="append", default=[])
    parser.add_argument("--auth-mode", choices=("auto", "env", "tokenstore"), default="auto")
    parser.add_argument("--region", choices=("global", "cn"), default=None)
    parser.add_argument("--as-of", default="2026-05-03", help="Latest Garmin status date to use.")
    parser.add_argument("--lookback-days", type=int, default=7)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def cell(value: Any) -> str:
    return str(value or "").strip()


def parse_date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(cell(value)[:10])
    except ValueError:
        return None


def row_date_range(rows: list[dict[str, str]]) -> tuple[dt.date | None, dt.date | None]:
    dates = [date for date in (parse_date(row.get("date")) for row in rows) if date]
    if not dates:
        return None, None
    return min(dates), max(dates)


def month_iter(start: dt.date, end: dt.date) -> list[tuple[int, int]]:
    months = []
    cursor = dt.date(start.year, start.month, 1)
    last = dt.date(end.year, end.month, 1)
    while cursor <= last:
        months.append((cursor.year, cursor.month))
        if cursor.month == 12:
            cursor = dt.date(cursor.year + 1, 1, 1)
        else:
            cursor = dt.date(cursor.year, cursor.month + 1, 1)
    return months


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        if text.startswith("export "):
            text = text.removeprefix("export ").strip()
        key, value = text.split("=", 1)
        key = key.strip()
        if key not in GARMIN_ENV_KEYS:
            continue
        values[key] = value.strip().strip("'\"")
    return values


def load_garmin_env(env_files: list[Path]) -> tuple[dict[str, str], list[str]]:
    values = {key: os.environ.get(key, "") for key in GARMIN_ENV_KEYS}
    files_with_keys = []
    for path in env_files:
        parsed = parse_env_file(path.expanduser())
        if parsed:
            files_with_keys.append(str(path.expanduser()))
        for key, value in parsed.items():
            if value and not values.get(key):
                values[key] = value
    return values, files_with_keys


def login_garmin(args: argparse.Namespace) -> tuple[Any, dict[str, Any]]:
    try:
        from garminconnect import Garmin
    except ImportError as exc:
        raise RuntimeError("missing garminconnect library; run with the Garmin POC venv") from exc

    env_files = [path.expanduser() for path in (args.env_file or DEFAULT_ENV_FILES)]
    env_values, files_with_keys = load_garmin_env(env_files)
    region = args.region or env_values.get("GARMIN_REGION") or "global"
    if region not in {"global", "cn"}:
        region = "global"

    email = env_values.get("GARMIN_EMAIL") or ""
    password = env_values.get("GARMIN_PASSWORD") or ""
    auth_base = {
        "mode_requested": args.auth_mode,
        "env_files_checked": [str(path) for path in env_files],
        "env_files_with_garmin_keys": files_with_keys,
        "garmin_email_present": bool(email),
        "garmin_password_present": bool(password),
        "region": region,
        "tokenstore": str(args.tokenstore),
    }

    if args.auth_mode in {"auto", "env"} and email and password:
        try:
            client = Garmin(email, password, is_cn=(region == "cn"))
            client.login(str(args.tokenstore))
            return client, {**auth_base, "mode_used": "env_credentials"}
        except Exception as exc:
            if args.auth_mode == "env":
                raise
            auth_base["env_login_error"] = exc.__class__.__name__

    if args.auth_mode == "env":
        raise RuntimeError("missing GARMIN_EMAIL/GARMIN_PASSWORD in env or --env-file")

    client = Garmin()
    client.login(str(args.tokenstore))
    return client, {**auth_base, "mode_used": "tokenstore"}


def newest_readiness(items: Any) -> dict[str, Any] | None:
    if isinstance(items, list) and items:
        return sorted(items, key=lambda row: str(row.get("timestampLocal") or row.get("timestamp") or ""))[-1]
    if isinstance(items, dict):
        return items
    return None


def readiness_summary(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    keys = ("calendarDate", "timestampLocal", "level", "score", "sleepScore", "feedbackShort")
    return {key: row.get(key) for key in keys if key in row}


def sleep_summary(data: Any) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    dto = data.get("dailySleepDTO") or {}
    score = (((dto.get("sleepScores") or {}).get("overall") or {}).get("value"))
    seconds = dto.get("sleepTimeSeconds")
    return {
        "score": score,
        "duration_hours": round(seconds / 3600, 2) if isinstance(seconds, (int, float)) else None,
        "resting_heart_rate": data.get("restingHeartRate"),
        "avg_overnight_hrv": data.get("avgOvernightHrv"),
    }


def readiness_to_fatigue(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    score = row.get("score")
    try:
        value = float(score)
    except (TypeError, ValueError):
        return ""
    if value < 35:
        return "high"
    if value < 60:
        return "moderate"
    return "normal"


def sleep_to_quality(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    score = row.get("score")
    try:
        value = float(score)
    except (TypeError, ValueError):
        return ""
    if value < 60:
        return "poor"
    if value < 80:
        return "moderate"
    return "good"


def sport_from_item(item: dict[str, Any]) -> str:
    text = " ".join(
        cell(item.get(key)).lower()
        for key in ("sportTypeKey", "title", "itemType")
    )
    if "run" in text or "running" in text or "跑" in text:
        return "run"
    if "swim" in text or "游" in text:
        return "swim"
    if "strength" in text or "力量" in text:
        return "strength"
    if "cycl" in text or "bike" in text or "骑" in text:
        return "bike"
    return ""


def session_type_from_item(item: dict[str, Any]) -> str:
    title = cell(item.get("title")).lower()
    hard_terms = ("hard", "interval", "threshold", "tempo", "t pace", "强度", "间歇", "阈值", "节奏")
    if any(term in title for term in hard_terms):
        return "hard"
    if "strength" in title or "力量" in title:
        return "lower_body"
    return "easy"


def fetch_calendar_items(client: Any, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
    items = []
    for year, month in month_iter(start, end):
        data = client.get_scheduled_workouts(year, month)
        for item in (data.get("calendarItems") or []):
            date = parse_date(item.get("date"))
            if date and start <= date <= end:
                items.append(item)
    return items


def fetch_recent_status(client: Any, as_of: str, lookback_days: int) -> dict[str, Any]:
    end = dt.date.fromisoformat(as_of)
    for offset in range(lookback_days):
        day = end - dt.timedelta(days=offset)
        date_text = day.isoformat()
        readiness = None
        sleep = None
        try:
            readiness = readiness_summary(newest_readiness(client.get_training_readiness(date_text)))
        except Exception:
            readiness = None
        try:
            sleep = sleep_summary(client.get_sleep_data(date_text))
        except Exception:
            sleep = None
        if readiness or sleep:
            return {
                "date": date_text,
                "training_readiness": readiness,
                "sleep": sleep,
                "fatigue_value": readiness_to_fatigue(readiness),
                "sleep_quality_value": sleep_to_quality(sleep),
            }
    return {}


def existing_fixed_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    result = []
    for row in rows:
        sport = cell(row.get("sport"))
        stype = cell(row.get("session_type"))
        if not sport or "|" in sport or not stype or "|" in stype:
            continue
        result.append(row)
    return result


def calendar_fixed_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        if item.get("itemType") != "workout":
            continue
        sport = sport_from_item(item)
        if sport not in {"run", "swim", "strength"}:
            continue
        rows.append(
            {
                "week": "",
                "date": cell(item.get("date")),
                "day": "",
                "day_label": "",
                "sport": sport,
                "session_type": session_type_from_item(item),
                "movable": "",
                "notes": f"garmin_calendar:{cell(item.get('title')) or item.get('workoutId')}",
            }
        )
    return rows


def prefill_slots(rows: list[dict[str, str]], status: dict[str, Any]) -> tuple[list[dict[str, str]], int]:
    fatigue = status.get("fatigue_value") or ""
    sleep_quality = status.get("sleep_quality_value") or ""
    source_date = status.get("date") or ""
    updated = 0
    for row in rows:
        notes = cell(row.get("notes"))
        note_bits = [notes] if notes else []
        if fatigue and not cell(row.get("fatigue")):
            row["fatigue"] = fatigue
            updated += 1
            note_bits.append(f"fatigue_from_garmin_readiness:{source_date}")
        if sleep_quality and not cell(row.get("sleep_quality")):
            row["sleep_quality"] = sleep_quality
            updated += 1
            note_bits.append(f"sleep_from_garmin:{source_date}")
        if note_bits:
            row["notes"] = "; ".join(note_bits)
    return rows, updated


def build_prefill(args: argparse.Namespace) -> dict[str, Any]:
    slot_rows = read_csv(args.slots_csv)
    fixed_rows = read_csv(args.fixed_sessions_csv)
    start, end = row_date_range(slot_rows)
    if not start or not end:
        raise ValueError("slots csv has no dated rows")
    client, auth = login_garmin(args)
    calendar_items = fetch_calendar_items(client, start, end)
    recent_status = fetch_recent_status(client, args.as_of, args.lookback_days)
    updated_slots, updated_cells = prefill_slots(slot_rows, recent_status)
    generated_fixed = calendar_fixed_rows(calendar_items)
    merged_fixed = existing_fixed_rows(fixed_rows) + generated_fixed

    if args.write:
        write_csv(args.slots_csv, list(updated_slots[0].keys()), updated_slots)
        write_csv(
            args.fixed_sessions_csv,
            ["week", "date", "day", "day_label", "sport", "session_type", "movable", "notes"],
            merged_fixed,
        )

    return {
        "status": "prefilled_from_garmin",
        "source": "garmin_connect",
        "auth": auth,
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "recent_status": recent_status,
        "calendar_items_total": len(calendar_items),
        "calendar_fixed_sessions_added": len(generated_fixed),
        "slot_cells_updated": updated_cells,
        "fields_not_inferred_from_garmin": [
            "available_minutes",
            "can_bike",
            "pain_status",
            "fixed_sessions.movable",
        ],
        "guardrails": [
            "Garmin recent readiness and sleep can seed fatigue/sleep_quality, but cannot prove future pain or availability.",
            "Garmin calendar workouts are copied as fixed sessions only when present in the slot date range.",
            "No Garmin token or credential content is copied into this report.",
        ],
    }


def print_markdown(report: dict[str, Any]) -> None:
    print(f"status: {report['status']}")
    auth = report.get("auth") or {}
    if auth:
        print(f"auth_mode: {auth.get('mode_used')}")
    print(f"date_range: {report['date_range']['start']} -> {report['date_range']['end']}")
    print(f"slot_cells_updated: {report['slot_cells_updated']}")
    print(f"calendar_fixed_sessions_added: {report['calendar_fixed_sessions_added']}")
    print("not_inferred: " + ", ".join(report["fields_not_inferred_from_garmin"]))


def main() -> int:
    args = parse_args()
    report = build_prefill(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_markdown(report)
        print(f"report={args.report}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
