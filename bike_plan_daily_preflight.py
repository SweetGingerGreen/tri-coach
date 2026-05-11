#!/usr/bin/env python3
"""
Check whether a weekly bike slot plan is ready to become daily workout drafts.

This layer is intentionally a gate, not a workout generator. It verifies daily
availability, fixed cross-sport sessions, fatigue, pain, and immovable
constraints before any daily prescription is allowed.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import bike_plan_generator


ROOT = Path(__file__).parent.resolve()
DEFAULT_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_latest.json"
DEFAULT_PREFLIGHT_INPUT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_input_latest.json"
DEFAULT_SLOTS_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_slots_latest.csv"
DEFAULT_FIXED_SESSIONS_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_fixed_sessions_latest.csv"
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_latest.json"
SCHEMA_VERSION = "bike_plan_daily_preflight_v0.1"
INPUT_SCHEMA_VERSION = "bike_plan_daily_preflight_input_v0.1"
NO_VALUE_MARKERS = {"", "-", "none", "null", "无", "n/a"}
SAFE_PAIN_VALUES = {"", "none", "no", "0", "无", "没有", "不痛"}
HIGH_FATIGUE_VALUES = {"high", "very_high", "severe", "高", "很高", "严重", "累爆"}
REQUIRED_INPUT_GROUPS = ("daily_availability", "daily_status", "fixed_sessions")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-report", type=Path, default=DEFAULT_PLAN_REPORT)
    parser.add_argument("--preflight-input", type=Path, default=DEFAULT_PREFLIGHT_INPUT)
    parser.add_argument("--slots-csv", type=Path, default=DEFAULT_SLOTS_CSV)
    parser.add_argument("--fixed-sessions-csv", type=Path, default=DEFAULT_FIXED_SESSIONS_CSV)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--write-template", action="store_true")
    parser.add_argument("--write-csv-template", action="store_true")
    parser.add_argument("--input-from-csv", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


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


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def cell(value: Any) -> str:
    return str(value or "").strip()


def meaningful(value: Any) -> bool:
    return cell(value).lower() not in NO_VALUE_MARKERS


def normalize_day(value: Any) -> str:
    text = cell(value)
    lowered = text.lower()
    if text in bike_plan_generator.DAYS:
        return text
    for alias, day in bike_plan_generator.DAY_ALIASES.items():
        if alias == lowered or alias == text:
            return day
    return ""


def parse_minutes(row: dict[str, Any]) -> int | None:
    for key in ("available_minutes", "bike_minutes", "minutes"):
        if row.get(key) is not None and meaningful(row.get(key)):
            try:
                return int(float(row[key]))
            except (TypeError, ValueError):
                return None
    if row.get("available_hours") is not None and meaningful(row.get("available_hours")):
        try:
            return int(float(row["available_hours"]) * 60)
        except (TypeError, ValueError):
            return None
    return None


def bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = cell(value).lower()
    if text in {"true", "yes", "y", "1", "是", "可以", "可"}:
        return True
    if text in {"false", "no", "n", "0", "否", "不可以", "不可"}:
        return False
    return None


def row_matches(row: dict[str, Any], *, date: str, week: int, day: str) -> bool:
    if cell(row.get("date")) == date:
        return True
    row_week = row.get("week")
    try:
        same_week = int(float(row_week)) == week if meaningful(row_week) else False
    except (TypeError, ValueError):
        same_week = False
    row_day = normalize_day(row.get("day") or row.get("day_label"))
    return same_week and row_day == day


def matching_rows(rows: list[dict[str, Any]], *, date: str, week: int, day: str) -> list[dict[str, Any]]:
    return [row for row in rows if row_matches(row, date=date, week=week, day=day)]


def scheduled_bike_items(week: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in week.get("weekday_schedule") or []
        if item.get("date") and item.get("day") in bike_plan_generator.DAYS
    ]


def missing(group: str, field: str, message: str, week: int | None = None, item: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {
        "group": group,
        "field": field,
        "message": message,
    }
    if week is not None:
        row["week"] = week
    if item:
        row.update(
            {
                "date": item.get("date"),
                "day": item.get("day"),
                "day_label": item.get("day_label"),
                "slot_type": item.get("slot_type"),
            }
        )
    return row


def risk(risk_type: str, message: str, week: int, item: dict[str, Any], details: dict[str, Any] | None = None) -> dict[str, Any]:
    row = {
        "type": risk_type,
        "week": week,
        "date": item.get("date"),
        "day": item.get("day"),
        "day_label": item.get("day_label"),
        "slot_type": item.get("slot_type"),
        "message": message,
    }
    if details:
        row["details"] = details
    return row


def check_availability(
    rows: list[dict[str, Any]],
    week_number: int,
    item: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    matches = matching_rows(rows, date=item["date"], week=week_number, day=item["day"])
    misses = []
    risks = []
    if not matches:
        misses.append(
            missing(
                "daily_availability",
                "daily_availability[].date",
                "缺少该 bike slot 当天的可用时间。",
                week_number,
                item,
            )
        )
        return None, misses, risks
    row = matches[0]
    minutes = parse_minutes(row)
    can_bike = bool_or_none(row.get("can_bike"))
    if minutes is None:
        misses.append(
            missing(
                "daily_availability",
                "available_minutes",
                "缺少当天可骑行分钟数。",
                week_number,
                item,
            )
        )
    elif minutes <= 0:
        risks.append(risk("no_bike_time", "当天可用骑行时间为 0，不能进入每日课表。", week_number, item))
    if can_bike is None:
        misses.append(
            missing("daily_availability", "can_bike", "缺少当天是否可骑行的确认。", week_number, item)
        )
    elif can_bike is False:
        risks.append(risk("bike_not_allowed", "当天被标记为不可骑行。", week_number, item))
    return row, misses, risks


def check_daily_status(
    rows: list[dict[str, Any]],
    week_number: int,
    item: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    matches = matching_rows(rows, date=item["date"], week=week_number, day=item["day"])
    misses = []
    risks = []
    if not matches:
        misses.append(missing("daily_status", "daily_status[].date", "缺少当天疲劳和疼痛状态。", week_number, item))
        return None, misses, risks
    row = matches[0]
    fatigue = cell(row.get("fatigue")).lower()
    pain = cell(row.get("pain_status") or row.get("pain")).lower()
    if not meaningful(fatigue):
        misses.append(missing("daily_status", "fatigue", "缺少当天疲劳状态。", week_number, item))
    elif fatigue in HIGH_FATIGUE_VALUES:
        risks.append(risk("high_fatigue_on_bike_day", "当天疲劳状态偏高，不能直接生成每日训练课。", week_number, item))
    if pain in {"", "-", "null", "n/a"}:
        misses.append(missing("daily_status", "pain_status", "缺少当天疼痛状态。", week_number, item))
    elif pain not in SAFE_PAIN_VALUES:
        risks.append(risk("pain_on_bike_day", "当天存在疼痛记录，不能直接生成每日训练课。", week_number, item, {"pain": pain}))
    return row, misses, risks


def session_type(row: dict[str, Any]) -> str:
    return cell(row.get("session_type") or row.get("type")).lower()


def sport_type(row: dict[str, Any]) -> str:
    return cell(row.get("sport")).lower()


def check_fixed_sessions(
    rows: list[dict[str, Any]],
    week_number: int,
    item: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    matches = matching_rows(rows, date=item["date"], week=week_number, day=item["day"])
    misses = []
    risks = []
    for row in matches:
        movable = bool_or_none(row.get("movable"))
        if movable is None:
            misses.append(
                missing(
                    "fixed_sessions",
                    "movable",
                    "固定跨项训练缺少是否可移动的确认。",
                    week_number,
                    item,
                )
            )
            continue
        stype = session_type(row)
        sport = sport_type(row)
        bike_slot = str(item.get("slot_type") or "")
        conflicts = (
            (sport == "run" and stype == "hard" and bike_slot in {"hard", "long"})
            or (sport == "swim" and stype == "hard" and bike_slot == "hard")
            or (sport in {"strength", "strength_lower_body"} and stype in {"lower_body", "hard"} and bike_slot in {"hard", "long"})
        )
        if conflicts and movable is False:
            risks.append(
                risk(
                    "immovable_cross_sport_conflict",
                    "当天有不可移动跨项训练与 bike slot 冲突。",
                    week_number,
                    item,
                    {"sport": sport, "session_type": stype},
                )
            )
    return matches, misses, risks


def week_status(missing_rows: list[dict[str, Any]], risk_rows: list[dict[str, Any]]) -> str:
    if risk_rows:
        return "blocked_by_daily_risk"
    if missing_rows:
        return "needs_more_daily_data"
    return "ready_for_daily_draft"


def overall_status(weeks: list[dict[str, Any]]) -> str:
    statuses = {week.get("status") for week in weeks}
    if "blocked_by_daily_risk" in statuses:
        return "blocked_by_daily_risk"
    if "needs_more_daily_data" in statuses:
        return "needs_more_daily_data"
    if statuses == {"ready_for_daily_draft"}:
        return "ready_for_daily_draft"
    return "needs_more_daily_data"


def build_preflight(plan_report: Path, preflight_input: Path) -> dict[str, Any]:
    plan = load_json(plan_report)
    inputs = load_json(preflight_input)
    input_missing = [
        missing(group, group, f"preflight input 缺少 {group}。")
        for group in REQUIRED_INPUT_GROUPS
        if group not in inputs
    ]
    availability = inputs.get("daily_availability") if isinstance(inputs.get("daily_availability"), list) else []
    daily_status = inputs.get("daily_status") if isinstance(inputs.get("daily_status"), list) else []
    fixed_sessions = inputs.get("fixed_sessions") if isinstance(inputs.get("fixed_sessions"), list) else []
    weeks = []
    for week in plan.get("weekly_plan") or []:
        week_number = int(week.get("week"))
        week_missing = list(input_missing)
        week_risks = []
        ready_inputs = []
        for item in scheduled_bike_items(week):
            availability_row, misses, risks = check_availability(availability, week_number, item)
            week_missing.extend(misses)
            week_risks.extend(risks)
            status_row, misses, risks = check_daily_status(daily_status, week_number, item)
            week_missing.extend(misses)
            week_risks.extend(risks)
            fixed_rows, misses, risks = check_fixed_sessions(fixed_sessions, week_number, item)
            week_missing.extend(misses)
            week_risks.extend(risks)
            ready_inputs.append(
                {
                    "date": item.get("date"),
                    "day": item.get("day"),
                    "day_label": item.get("day_label"),
                    "slot_type": item.get("slot_type"),
                    "workout_type": item.get("workout_type"),
                    "available_minutes": parse_minutes(availability_row or {}),
                    "daily_status_present": bool(status_row),
                    "fixed_sessions_same_day": len(fixed_rows),
                }
            )
        weeks.append(
            {
                "week": week_number,
                "week_start": week.get("week_start"),
                "status": week_status(week_missing, week_risks),
                "scheduled_bike_days": ready_inputs,
                "missing_data": week_missing,
                "risk_flags": week_risks,
                "boundary": "daily_preflight_only_no_workout_generation_no_watts_no_minutes_no_intervals",
            }
        )

    return {
        "status": overall_status(weeks),
        "schema_version": SCHEMA_VERSION,
        "source_plan_report": rel_path(plan_report),
        "source_preflight_input": rel_path(preflight_input),
        "source_plan_schema_version": plan.get("schema_version", ""),
        "source_candidate_schema_version": (plan.get("candidate_metadata") or {}).get("schema_version", ""),
        "weeks": weeks,
        "summary": {
            "weeks_total": len(weeks),
            "ready_weeks": sum(1 for week in weeks if week.get("status") == "ready_for_daily_draft"),
            "missing_weeks": sum(1 for week in weeks if week.get("status") == "needs_more_daily_data"),
            "blocked_weeks": sum(1 for week in weeks if week.get("status") == "blocked_by_daily_risk"),
        },
        "guardrails": [
            "preflight 只判断能否进入每日训练课草案，不生成每日训练课。",
            "缺少 daily_availability、daily_status 或 fixed_sessions 时必须先补数据。",
            "出现疼痛、高疲劳或不可移动跨项冲突时，不允许直接生成每日训练课。",
            "不输出 watts、minutes、intervals、sets、reps。",
        ],
    }


def template_from_plan(plan_report: Path) -> dict[str, Any]:
    plan = load_json(plan_report)
    daily_availability = []
    daily_status = []
    for week in plan.get("weekly_plan") or []:
        for item in scheduled_bike_items(week):
            base = {
                "week": week.get("week"),
                "date": item.get("date"),
                "day": item.get("day"),
                "day_label": item.get("day_label"),
                "slot_type": item.get("slot_type"),
                "workout_type": item.get("workout_type"),
            }
            daily_availability.append({**base, "available_minutes": "", "can_bike": ""})
            daily_status.append({**base, "fatigue": "", "pain_status": "", "sleep_quality": ""})
    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "source_plan_report": rel_path(plan_report),
        "daily_availability": daily_availability,
        "daily_status": daily_status,
        "fixed_sessions": [
            {
                "week": "",
                "date": "",
                "day": "",
                "sport": "run|swim|strength",
                "session_type": "easy|hard|lower_body",
                "movable": "",
                "notes": "",
            }
        ],
        "boundary": "input_template_only_no_watts_no_minutes_no_intervals",
    }


def slot_csv_rows(plan_report: Path) -> list[dict[str, Any]]:
    plan = load_json(plan_report)
    rows = []
    for week in plan.get("weekly_plan") or []:
        for item in scheduled_bike_items(week):
            rows.append(
                {
                    "week": week.get("week"),
                    "date": item.get("date"),
                    "day": item.get("day"),
                    "day_label": item.get("day_label"),
                    "slot_type": item.get("slot_type"),
                    "workout_type": item.get("workout_type"),
                    "available_minutes": "",
                    "can_bike": "",
                    "fatigue": "",
                    "pain_status": "",
                    "sleep_quality": "",
                    "notes": "",
                }
            )
    return rows


def fixed_session_csv_rows() -> list[dict[str, Any]]:
    return [
        {
            "week": "",
            "date": "",
            "day": "",
            "day_label": "",
            "sport": "run|swim|strength",
            "session_type": "easy|hard|lower_body",
            "movable": "",
            "notes": "",
        }
    ]


def write_csv_templates(plan_report: Path, slots_csv: Path, fixed_sessions_csv: Path) -> None:
    write_csv(
        slots_csv,
        [
            "week",
            "date",
            "day",
            "day_label",
            "slot_type",
            "workout_type",
            "available_minutes",
            "can_bike",
            "fatigue",
            "pain_status",
            "sleep_quality",
            "notes",
        ],
        slot_csv_rows(plan_report),
    )
    write_csv(
        fixed_sessions_csv,
        ["week", "date", "day", "day_label", "sport", "session_type", "movable", "notes"],
        fixed_session_csv_rows(),
    )


def input_from_csv(plan_report: Path, slots_csv: Path, fixed_sessions_csv: Path) -> dict[str, Any]:
    slot_rows = read_csv(slots_csv)
    fixed_rows = read_csv(fixed_sessions_csv)
    daily_availability = []
    daily_status = []
    for row in slot_rows:
        day = normalize_day(row.get("day") or row.get("day_label"))
        base = {
            "week": row.get("week", ""),
            "date": cell(row.get("date")),
            "day": day or cell(row.get("day")),
            "day_label": bike_plan_generator.label_day(day) if day else cell(row.get("day_label")),
            "slot_type": cell(row.get("slot_type")),
            "workout_type": cell(row.get("workout_type")),
        }
        daily_availability.append(
            {
                **base,
                "available_minutes": cell(row.get("available_minutes")),
                "can_bike": cell(row.get("can_bike")),
            }
        )
        daily_status.append(
            {
                **base,
                "fatigue": cell(row.get("fatigue")),
                "pain_status": cell(row.get("pain_status")),
                "sleep_quality": cell(row.get("sleep_quality")),
            }
        )

    fixed_sessions = []
    for row in fixed_rows:
        if not any(meaningful(row.get(key)) for key in ("date", "week", "day", "sport", "session_type", "movable")):
            continue
        sport = cell(row.get("sport"))
        if "|" in sport:
            continue
        stype = cell(row.get("session_type"))
        if "|" in stype:
            continue
        day = normalize_day(row.get("day") or row.get("day_label"))
        fixed_sessions.append(
            {
                "week": cell(row.get("week")),
                "date": cell(row.get("date")),
                "day": day or cell(row.get("day")),
                "day_label": bike_plan_generator.label_day(day) if day else cell(row.get("day_label")),
                "sport": sport,
                "session_type": stype,
                "movable": cell(row.get("movable")),
                "notes": cell(row.get("notes")),
            }
        )

    return {
        "schema_version": INPUT_SCHEMA_VERSION,
        "source_plan_report": rel_path(plan_report),
        "source_slots_csv": rel_path(slots_csv),
        "source_fixed_sessions_csv": rel_path(fixed_sessions_csv),
        "daily_availability": daily_availability,
        "daily_status": daily_status,
        "fixed_sessions": fixed_sessions,
        "boundary": "input_from_csv_only_no_watts_no_minutes_no_intervals",
    }


def print_markdown(result: dict[str, Any]) -> None:
    summary = result.get("summary") or {}
    print(f"status: {result.get('status')}")
    print(f"schema_version: {result.get('schema_version')}")
    print(
        f"weeks: ready={summary.get('ready_weeks', 0)} "
        f"missing={summary.get('missing_weeks', 0)} blocked={summary.get('blocked_weeks', 0)}"
    )
    for week in result.get("weeks") or []:
        print(
            f"- week {week.get('week')} {week.get('week_start')}: {week.get('status')} "
            f"missing={len(week.get('missing_data') or [])} risks={len(week.get('risk_flags') or [])}"
        )


def main() -> int:
    args = parse_args()
    if args.write_template:
        template = template_from_plan(args.plan_report)
        args.preflight_input.parent.mkdir(parents=True, exist_ok=True)
        args.preflight_input.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"template={args.preflight_input}")
    if args.write_csv_template:
        write_csv_templates(args.plan_report, args.slots_csv, args.fixed_sessions_csv)
        print(f"slots_csv={args.slots_csv}")
        print(f"fixed_sessions_csv={args.fixed_sessions_csv}")
    if args.input_from_csv:
        preflight_input = input_from_csv(args.plan_report, args.slots_csv, args.fixed_sessions_csv)
        args.preflight_input.parent.mkdir(parents=True, exist_ok=True)
        args.preflight_input.write_text(json.dumps(preflight_input, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"preflight_input={args.preflight_input}")
    result = build_preflight(args.plan_report, args.preflight_input)
    if args.write_report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_markdown(result)
        if args.write_report:
            print(f"report={args.report}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
