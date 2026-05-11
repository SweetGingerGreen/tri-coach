#!/usr/bin/env python3
"""
Validate structured inputs before generating a bike power training plan.

This is the application-layer intake step. It does not create daily workouts.
It decides whether enough athlete-specific data exists to safely move into a
future plan generator, and it returns a normalized planning frame.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent.resolve()
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_intake_latest.json"
SCHEMA_VERSION = "bike_plan_intake_v0.3"
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_LABELS = {
    "Monday": "周一",
    "Tuesday": "周二",
    "Wednesday": "周三",
    "Thursday": "周四",
    "Friday": "周五",
    "Saturday": "周六",
    "Sunday": "周日",
}
DAY_ALIASES = {
    "monday": "Monday",
    "mon": "Monday",
    "周一": "Monday",
    "星期一": "Monday",
    "tuesday": "Tuesday",
    "tue": "Tuesday",
    "周二": "Tuesday",
    "星期二": "Tuesday",
    "wednesday": "Wednesday",
    "wed": "Wednesday",
    "周三": "Wednesday",
    "星期三": "Wednesday",
    "thursday": "Thursday",
    "thu": "Thursday",
    "周四": "Thursday",
    "星期四": "Thursday",
    "friday": "Friday",
    "fri": "Friday",
    "周五": "Friday",
    "星期五": "Friday",
    "saturday": "Saturday",
    "sat": "Saturday",
    "周六": "Saturday",
    "星期六": "Saturday",
    "sunday": "Sunday",
    "sun": "Sunday",
    "周日": "Sunday",
    "周天": "Sunday",
    "星期日": "Sunday",
    "星期天": "Sunday",
}


@dataclass
class MissingData:
    group: str
    label: str
    fields: list[str]
    prompt: str


@dataclass
class IntakeResult:
    status: str
    missing_data: list[MissingData] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    normalized: dict[str, Any] = field(default_factory=dict)
    plan_frame: dict[str, Any] | None = None
    next_questions: list[str] = field(default_factory=list)
    rag_queries: list[str] = field(default_factory=list)


def template_input() -> dict[str, Any]:
    return {
        "athlete_id": "default",
        "goal": {
            "race_name": "",
            "race_date": "YYYY-MM-DD",
            "race_distance": "ironman",
            "priority": "A",
        },
        "current": {
            "date": "YYYY-MM-DD",
            "ftp_w": None,
            "ftp_test_date": "YYYY-MM-DD",
            "ctl": None,
            "atl": None,
            "tsb": None,
            "fatigue": "normal",
            "injury_status": "none",
        },
        "recent_load": {
            "weeks": [
                {
                    "week_start": "YYYY-MM-DD",
                    "bike_hours": None,
                    "bike_tss": None,
                    "high_intensity_sessions": None,
                }
            ],
            "notes": "",
        },
        "availability": {
            "weekly_bike_days": None,
            "weekly_bike_hours": None,
            "long_ride_day": "Sunday",
            "max_long_ride_hours": None,
            "constraints": [],
        },
        "other_sports": {
            "run_hours": None,
            "swim_hours": None,
            "strength_sessions": None,
            "fixed_run_hard_days": [],
            "fixed_swim_hard_days": [],
            "fixed_strength_lower_body_days": [],
            "notes": "",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="JSON intake file. Reads stdin when omitted.")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--template", action="store_true", help="Print a blank JSON intake template.")
    parser.add_argument("--write-report", action="store_true", help="Write the result JSON report.")
    parser.add_argument("--weeks", type=int, default=8, help="Planning-frame weeks to outline, not daily workouts.")
    return parser.parse_args()


def parse_date(value: Any, field_name: str) -> dt.date | None:
    if value in (None, "", "YYYY-MM-DD"):
        return None
    if isinstance(value, dt.date):
        return value
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be YYYY-MM-DD")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be YYYY-MM-DD: {value}") from exc


def as_number(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def as_int(value: Any) -> int | None:
    number = as_number(value)
    if number is None:
        return None
    return int(number)


def compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def normalize_day(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in DAY_ALIASES:
        return DAY_ALIASES[lowered]
    for alias, day in DAY_ALIASES.items():
        if alias in text or alias in lowered:
            return day
    return None


def day_entry(day: str, raw: Any) -> dict[str, str]:
    return {
        "day": day,
        "day_label": DAY_LABELS.get(day, day),
        "raw": str(raw),
    }


def normalize_day_entries(values: Any) -> list[dict[str, str]]:
    if values in (None, "", []):
        return []
    if isinstance(values, str):
        values = [item.strip() for item in values.replace("，", ",").split(",")]
    if not isinstance(values, list):
        values = [values]

    entries = []
    seen = set()
    for value in values:
        day = normalize_day(value)
        if not day or day in seen:
            continue
        seen.add(day)
        entries.append(day_entry(day, value))
    return sorted(entries, key=lambda item: DAYS.index(item["day"]))


def load_input(args: argparse.Namespace) -> dict[str, Any]:
    if args.input:
        return json.loads(args.input.read_text(encoding="utf-8"))
    text = sys.stdin.read().strip()
    if not text:
        raise ValueError("input JSON is required unless --template is used")
    return json.loads(text)


def has_recent_load(data: dict[str, Any]) -> bool:
    current = data.get("current") or {}
    recent_load = data.get("recent_load") or {}
    ctl = as_number(current.get("ctl"))
    atl = as_number(current.get("atl"))
    tsb = as_number(current.get("tsb"))
    if ctl is not None and atl is not None and tsb is not None:
        return True

    weeks = recent_load.get("weeks") or []
    usable_weeks = 0
    for week in weeks:
        bike_hours = as_number((week or {}).get("bike_hours"))
        bike_tss = as_number((week or {}).get("bike_tss"))
        if bike_hours is not None or bike_tss is not None:
            usable_weeks += 1
    return usable_weeks >= 4


def missing_data_for(data: dict[str, Any]) -> list[MissingData]:
    goal = data.get("goal") or {}
    current = data.get("current") or {}
    availability = data.get("availability") or {}
    missing: list[MissingData] = []

    if not parse_date(goal.get("race_date"), "goal.race_date") or not (
        goal.get("race_distance") or goal.get("race_name")
    ):
        missing.append(
            MissingData(
                group="target_race",
                label="目标赛事",
                fields=["goal.race_date", "goal.race_distance or goal.race_name"],
                prompt="目标赛事日期是哪天？比赛距离是大铁、半铁、奥运标铁，还是自定义距离？",
            )
        )

    ftp = as_number(current.get("ftp_w"))
    if ftp is None or ftp <= 0:
        missing.append(
            MissingData(
                group="ftp",
                label="当前 FTP",
                fields=["current.ftp_w", "current.ftp_test_date"],
                prompt="当前 FTP 是多少瓦？最近一次测试或估算日期是哪天？",
            )
        )

    if not has_recent_load(data):
        missing.append(
            MissingData(
                group="recent_load",
                label="近期训练负荷",
                fields=["current.ctl/atl/tsb or recent_load.weeks[4-6]"],
                prompt="最近四到六周骑行训练量是多少？有 CTL/ATL/TSB 或每周骑行小时/TSS 也可以。",
            )
        )

    weekly_days = as_int(availability.get("weekly_bike_days"))
    weekly_hours = as_number(availability.get("weekly_bike_hours"))
    if weekly_days is None or weekly_days <= 0 or weekly_hours is None or weekly_hours <= 0:
        missing.append(
            MissingData(
                group="availability",
                label="每周可训练时间",
                fields=["availability.weekly_bike_days", "availability.weekly_bike_hours"],
                prompt="每周可以骑几天？总共大约几小时？最长骑行能放在哪天、最多多久？",
            )
        )

    if not current.get("fatigue") or not current.get("injury_status"):
        missing.append(
            MissingData(
                group="recovery",
                label="疲劳和伤病状态",
                fields=["current.fatigue", "current.injury_status"],
                prompt="当前疲劳状态是 low/normal/high？有没有疼痛、伤病或近期恢复问题？",
            )
        )

    return missing


def normalize_input(data: dict[str, Any]) -> dict[str, Any]:
    goal = data.get("goal") or {}
    current = data.get("current") or {}
    availability = data.get("availability") or {}
    other_sports = data.get("other_sports") or {}
    recent_load = data.get("recent_load") or {}
    today = parse_date(current.get("date"), "current.date") or dt.date.today()
    race_date = parse_date(goal.get("race_date"), "goal.race_date")

    normalized = {
        "schema_version": SCHEMA_VERSION,
        "athlete_id": data.get("athlete_id") or "default",
        "today": today.isoformat(),
        "goal": compact_dict(
            {
                "race_name": goal.get("race_name"),
                "race_date": race_date.isoformat() if race_date else None,
                "race_distance": goal.get("race_distance"),
                "priority": goal.get("priority"),
            }
        ),
        "current": compact_dict(
            {
                "ftp_w": as_number(current.get("ftp_w")),
                "ftp_test_date": current.get("ftp_test_date"),
                "ctl": as_number(current.get("ctl")),
                "atl": as_number(current.get("atl")),
                "tsb": as_number(current.get("tsb")),
                "fatigue": current.get("fatigue"),
                "injury_status": current.get("injury_status"),
            }
        ),
        "availability": compact_dict(
            {
                "weekly_bike_days": as_int(availability.get("weekly_bike_days")),
                "weekly_bike_hours": as_number(availability.get("weekly_bike_hours")),
                "long_ride_day": availability.get("long_ride_day"),
                "max_long_ride_hours": as_number(availability.get("max_long_ride_hours")),
                "constraints": availability.get("constraints") or [],
            }
        ),
        "other_sports": compact_dict(
            {
                "run_hours": as_number(other_sports.get("run_hours")),
                "swim_hours": as_number(other_sports.get("swim_hours")),
                "strength_sessions": as_int(other_sports.get("strength_sessions")),
                "fixed_run_hard_days": normalize_day_entries(other_sports.get("fixed_run_hard_days")),
                "fixed_swim_hard_days": normalize_day_entries(other_sports.get("fixed_swim_hard_days")),
                "fixed_strength_lower_body_days": normalize_day_entries(
                    other_sports.get("fixed_strength_lower_body_days")
                ),
                "notes": other_sports.get("notes"),
            }
        ),
        "recent_load": {
            "weeks": recent_load.get("weeks") or [],
            "notes": recent_load.get("notes") or "",
        },
    }
    if race_date:
        normalized["weeks_to_race"] = max((race_date - today).days // 7, 0)
    return normalized


def collect_warnings(normalized: dict[str, Any]) -> list[str]:
    warnings = []
    today = parse_date(normalized.get("today"), "today")
    race_date = parse_date((normalized.get("goal") or {}).get("race_date"), "goal.race_date")
    if today and race_date and race_date < today:
        warnings.append("目标赛事日期已经早于当前日期，不能进入课表生成。")

    current = normalized.get("current") or {}
    availability = normalized.get("availability") or {}
    fatigue = str(current.get("fatigue") or "").lower()
    injury = str(current.get("injury_status") or "").lower()
    if fatigue in {"high", "very_high", "高", "很高"}:
        warnings.append("当前疲劳偏高，后续课表默认需要先保守处理或安排减载。")
    if injury not in {"none", "无", "no", ""}:
        warnings.append("存在伤病或疼痛信息，生成训练课前需要先做风险拦截。")

    days = as_int(availability.get("weekly_bike_days"))
    hours = as_number(availability.get("weekly_bike_hours"))
    if days is not None and days <= 2:
        warnings.append("每周骑行天数不超过 2 天，不适合安排复杂的高强度组合。")
    if hours is not None and hours < 3:
        warnings.append("每周骑行时间少于 3 小时，课表应优先保留耐力和一致性。")
    return warnings


def next_monday(today: dt.date) -> dt.date:
    days_until_monday = (7 - today.weekday()) % 7
    if days_until_monday == 0:
        days_until_monday = 7
    return today + dt.timedelta(days=days_until_monday)


def phase_hint(weeks_until_race: int) -> str:
    if weeks_until_race <= 2:
        return "peaking_or_taper"
    if weeks_until_race <= 5:
        return "specific_build"
    if weeks_until_race <= 10:
        return "build"
    return "base"


def workload_hint(index: int, weeks_until_race: int) -> str:
    if weeks_until_race <= 2:
        return "peaking"
    if index % 4 == 0:
        return "deload"
    if index % 4 == 3:
        return "load_plus"
    return "load"


def other_sports_load(normalized: dict[str, Any]) -> dict[str, Any]:
    other_sports = normalized.get("other_sports") or {}
    run_hours = as_number(other_sports.get("run_hours")) or 0.0
    swim_hours = as_number(other_sports.get("swim_hours")) or 0.0
    strength_sessions = as_int(other_sports.get("strength_sessions")) or 0
    flags = []

    if run_hours >= 6:
        flags.append("run_load_high")
    if swim_hours >= 4:
        flags.append("swim_load_high")
    if strength_sessions >= 2:
        flags.append("strength_load_high")
    if run_hours + swim_hours >= 9:
        flags.append("endurance_cross_load_high")

    return {
        "run_hours": run_hours,
        "swim_hours": swim_hours,
        "strength_sessions": strength_sessions,
        "flags": flags,
    }


def intensity_budget(normalized: dict[str, Any]) -> dict[str, Any]:
    current = normalized.get("current") or {}
    availability = normalized.get("availability") or {}
    weekly_days = as_int(availability.get("weekly_bike_days")) or 0
    fatigue = str(current.get("fatigue") or "").lower()
    injury = str(current.get("injury_status") or "").lower()
    cross_load = other_sports_load(normalized)

    if fatigue in {"high", "very_high", "高", "很高"} or injury not in {"none", "无", "no", ""}:
        base_high_intensity = 0
    elif weekly_days <= 3:
        base_high_intensity = 1
    else:
        base_high_intensity = 2

    cross_sport_adjustment = -1 if cross_load["flags"] and base_high_intensity > 0 else 0
    max_high_intensity = max(base_high_intensity + cross_sport_adjustment, 0)

    return {
        "base_bike_high_intensity_budget": base_high_intensity,
        "cross_sport_adjustment": cross_sport_adjustment,
        "other_sports_load": cross_load,
        "max_bike_high_intensity_sessions_per_week": max_high_intensity,
        "brick_counting_rule": "如果 brick 中骑和跑都含高强度，把这次 brick 计入一周总高强度，不要额外叠加。",
        "reason": "预算先由每周骑行天数、疲劳和伤病状态决定，再用跑步、游泳和力量训练负荷保守下调。",
    }


def build_week_skeleton(normalized: dict[str, Any], requested_weeks: int) -> list[dict[str, Any]]:
    today = parse_date(normalized["today"], "today")
    weeks_to_race = int(normalized.get("weeks_to_race", 0))
    if today is None or weeks_to_race <= 0:
        return []

    count = max(min(requested_weeks, weeks_to_race), 0)
    start = next_monday(today)
    rows = []
    for index in range(1, count + 1):
        week_start = start + dt.timedelta(days=(index - 1) * 7)
        remaining = max(weeks_to_race - index + 1, 0)
        rows.append(
            {
                "week": index,
                "week_start": week_start.isoformat(),
                "weeks_until_race": remaining,
                "phase_hint": phase_hint(remaining),
                "workload_state": workload_hint(index, remaining),
                "allowed_output": "planning_frame_only_not_daily_workouts",
            }
        )
    return rows


def build_plan_frame(normalized: dict[str, Any], requested_weeks: int) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "macrocycle": "season_to_target_race",
        "mesocycle": "block",
        "microcycle": "week",
        "week_fields": ["week_start", "competition", "primary_goal", "secondary_goal"],
        "load_fields": ["volume_1_10", "intensity_1_10", "workload_state"],
        "phase_fields": ["hypertrophy", "strength", "strength_power", "power", "peaking"],
        "intensity_budget": intensity_budget(normalized),
        "week_skeleton": build_week_skeleton(normalized, requested_weeks),
    }


def build_next_questions(missing: list[MissingData], warnings: list[str]) -> list[str]:
    questions = [item.prompt for item in missing]
    if warnings and not missing:
        questions.append("这些风险信息是否属实？如果属实，是否先做减载/恢复周，而不是进入正常课表生成？")
    return questions


def build_rag_queries(normalized: dict[str, Any], missing: list[MissingData]) -> list[str]:
    if missing:
        return [
            "骑行功率周期训练计划模板 目标赛事 FTP 最近四到六周训练量 每周可训练时间 疲劳 伤病",
        ]
    goal = normalized.get("goal") or {}
    current = normalized.get("current") or {}
    availability = normalized.get("availability") or {}
    return [
        (
            "骑行功率周期训练计划模板 "
            f"目标赛事 {goal.get('race_distance', '')} {goal.get('race_date', '')} "
            f"FTP {current.get('ftp_w', '')} "
            f"每周可训练 {availability.get('weekly_bike_days', '')} 天 {availability.get('weekly_bike_hours', '')} 小时 "
            "base build peak workload brick 高强度计数"
        ).strip(),
        "基础期骑行训练 Endurance Ride Long Ride Cadence Workout Power Intervals Threshold Ride",
        "Ironman brick training high-intensity workouts bike ride followed immediately by a run",
    ]


def evaluate(data: dict[str, Any], requested_weeks: int = 8) -> IntakeResult:
    missing = missing_data_for(data)
    normalized = normalize_input(data)
    warnings = collect_warnings(normalized)
    status = "ready_for_plan_frame"

    if missing:
        status = "needs_more_data"
    if any("已经早于当前日期" in warning for warning in warnings):
        status = "blocked_by_risk"

    plan_frame = None
    if status == "ready_for_plan_frame":
        plan_frame = build_plan_frame(normalized, requested_weeks)

    return IntakeResult(
        status=status,
        missing_data=missing,
        warnings=warnings,
        normalized=normalized,
        plan_frame=plan_frame,
        next_questions=build_next_questions(missing, warnings),
        rag_queries=build_rag_queries(normalized, missing),
    )


def result_to_dict(result: IntakeResult) -> dict[str, Any]:
    return {
        "status": result.status,
        "missing_data": [item.__dict__ for item in result.missing_data],
        "warnings": result.warnings,
        "normalized": result.normalized,
        "plan_frame": result.plan_frame,
        "next_questions": result.next_questions,
        "rag_queries": result.rag_queries,
    }


def print_markdown(result: IntakeResult) -> None:
    print(f"status: {result.status}")
    if result.missing_data:
        print("\nmissing_data:")
        for item in result.missing_data:
            print(f"- {item.label}: {', '.join(item.fields)}")
    if result.warnings:
        print("\nwarnings:")
        for warning in result.warnings:
            print(f"- {warning}")
    if result.next_questions:
        print("\nnext_questions:")
        for question in result.next_questions:
            print(f"- {question}")
    if result.plan_frame:
        print("\nplan_frame:")
        print(json.dumps(result.plan_frame, ensure_ascii=False, indent=2))
    print("\nrag_queries:")
    for query in result.rag_queries:
        print(f"- {query}")


def main() -> int:
    args = parse_args()
    if args.template:
        print(json.dumps(template_input(), ensure_ascii=False, indent=2))
        return 0

    data = load_input(args)
    result = evaluate(data, requested_weeks=args.weeks)
    payload = result_to_dict(result)
    if args.write_report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_markdown(result)
    return 0 if result.status == "ready_for_plan_frame" else 1


if __name__ == "__main__":
    raise SystemExit(main())
