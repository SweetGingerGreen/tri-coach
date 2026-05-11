#!/usr/bin/env python3
"""
Regression checks for bike_plan_intake.py.

These checks keep the bike plan intake layer strict:
- incomplete athlete context must not proceed to plan framing.
- complete context must produce a planning frame, not daily workouts.
- risky dates must block planning.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bike_plan_intake


ROOT = Path(__file__).parent.resolve()
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_intake_eval_latest.json"


@dataclass
class IntakeEvalCase:
    case_id: str
    name: str
    payload: dict[str, Any]
    expected_status: str
    required_missing_groups: list[str]
    required_plan_keys: list[str]
    forbidden_plan_keys: list[str]
    expected_high_budget: int | None = None
    expected_fixed_days: dict[str, list[str]] | None = None


COMPLETE_PAYLOAD = {
    "athlete_id": "default",
    "goal": {
        "race_name": "示例大铁",
        "race_date": "2026-09-20",
        "race_distance": "ironman",
        "priority": "A",
    },
    "current": {
        "date": "2026-05-03",
        "ftp_w": 193,
        "ftp_test_date": "2026-04-20",
        "ctl": 65,
        "atl": 72,
        "tsb": -7,
        "fatigue": "normal",
        "injury_status": "none",
    },
    "recent_load": {
        "weeks": [],
        "notes": "CTL/ATL/TSB 已提供。",
    },
    "availability": {
        "weekly_bike_days": 3,
        "weekly_bike_hours": 6,
        "long_ride_day": "Sunday",
        "max_long_ride_hours": 4,
        "constraints": ["周一不能训练"],
    },
    "other_sports": {
        "run_hours": 4,
        "swim_hours": 2,
        "strength_sessions": 1,
        "notes": "",
    },
}


CASES = [
    IntakeEvalCase(
        case_id="missing_everything",
        name="空模板必须追问关键数据",
        payload=bike_plan_intake.template_input(),
        expected_status="needs_more_data",
        required_missing_groups=["target_race", "ftp", "recent_load", "availability"],
        required_plan_keys=[],
        forbidden_plan_keys=["week_skeleton"],
    ),
    IntakeEvalCase(
        case_id="complete_context",
        name="完整上下文生成计划框架",
        payload=COMPLETE_PAYLOAD,
        expected_status="ready_for_plan_frame",
        required_missing_groups=[],
        required_plan_keys=["week_skeleton", "intensity_budget", "load_fields", "phase_fields"],
        forbidden_plan_keys=["daily_workouts", "sessions", "workouts"],
        expected_high_budget=1,
    ),
    IntakeEvalCase(
        case_id="cross_sport_load_reduces_budget",
        name="跨项负荷高时下调骑行高强度预算",
        payload={
            **COMPLETE_PAYLOAD,
            "availability": {
                **COMPLETE_PAYLOAD["availability"],
                "weekly_bike_days": 4,
                "weekly_bike_hours": 8,
            },
            "other_sports": {
                **COMPLETE_PAYLOAD["other_sports"],
                "run_hours": 7,
                "swim_hours": 4,
                "strength_sessions": 2,
            },
        },
        expected_status="ready_for_plan_frame",
        required_missing_groups=[],
        required_plan_keys=["week_skeleton", "intensity_budget", "load_fields", "phase_fields"],
        forbidden_plan_keys=["daily_workouts", "sessions", "workouts"],
        expected_high_budget=1,
    ),
    IntakeEvalCase(
        case_id="manual_cross_sport_fixed_days",
        name="固定跑游力量日期进入标准化输入",
        payload={
            **COMPLETE_PAYLOAD,
            "other_sports": {
                **COMPLETE_PAYLOAD["other_sports"],
                "fixed_run_hard_days": ["Tuesday", "周日"],
                "fixed_swim_hard_days": "周二, Friday",
                "fixed_strength_lower_body_days": ["周三"],
            },
        },
        expected_status="ready_for_plan_frame",
        required_missing_groups=[],
        required_plan_keys=["week_skeleton", "intensity_budget", "load_fields", "phase_fields"],
        forbidden_plan_keys=["daily_workouts", "sessions", "workouts"],
        expected_high_budget=1,
        expected_fixed_days={
            "fixed_run_hard_days": ["Tuesday", "Sunday"],
            "fixed_swim_hard_days": ["Tuesday", "Friday"],
            "fixed_strength_lower_body_days": ["Wednesday"],
        },
    ),
    IntakeEvalCase(
        case_id="past_race_date",
        name="过去赛事日期必须拦截",
        payload={
            **COMPLETE_PAYLOAD,
            "goal": {
                **COMPLETE_PAYLOAD["goal"],
                "race_date": "2026-01-01",
            },
        },
        expected_status="blocked_by_risk",
        required_missing_groups=[],
        required_plan_keys=[],
        forbidden_plan_keys=["daily_workouts", "sessions", "workouts"],
    ),
]


def run_case(case: IntakeEvalCase) -> dict[str, Any]:
    result = bike_plan_intake.evaluate(case.payload, requested_weeks=8)
    result_dict = bike_plan_intake.result_to_dict(result)
    failures = []
    if result.status != case.expected_status:
        failures.append(f"status expected {case.expected_status}, got {result.status}")

    missing_groups = {item.group for item in result.missing_data}
    for group in case.required_missing_groups:
        if group not in missing_groups:
            failures.append(f"missing group not found: {group}")

    plan_frame = result.plan_frame or {}
    for key in case.required_plan_keys:
        if key not in plan_frame:
            failures.append(f"plan key not found: {key}")
    plan_keys = collect_keys(plan_frame)
    for key in case.forbidden_plan_keys:
        if key in plan_keys:
            failures.append(f"forbidden plan key found: {key}")

    if case.expected_high_budget is not None:
        budget = plan_frame.get("intensity_budget") or {}
        actual_budget = budget.get("max_bike_high_intensity_sessions_per_week")
        if actual_budget != case.expected_high_budget:
            failures.append(
                f"high budget expected {case.expected_high_budget}, got {actual_budget}"
            )

    if case.expected_fixed_days:
        other_sports = result.normalized.get("other_sports") or {}
        for key, expected_days in case.expected_fixed_days.items():
            actual_days = [item.get("day") for item in other_sports.get(key) or []]
            if actual_days != expected_days:
                failures.append(f"{key} expected {expected_days}, got {actual_days}")

    if result.plan_frame and result.plan_frame.get("week_skeleton"):
        bad_rows = [
            row for row in result.plan_frame["week_skeleton"]
            if row.get("allowed_output") != "planning_frame_only_not_daily_workouts"
        ]
        if bad_rows:
            failures.append("week skeleton contains rows not marked as planning-frame only")

    return {
        "case_id": case.case_id,
        "name": case.name,
        "passed": not failures,
        "failures": failures,
        "result": result_dict,
    }


def collect_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            keys.add(str(key))
            keys.update(collect_keys(item))
    elif isinstance(value, list):
        for item in value:
            keys.update(collect_keys(item))
    return keys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def print_summary(report: dict[str, Any]) -> None:
    passed = sum(1 for item in report["results"] if item["passed"])
    total = len(report["results"])
    print(f"Bike plan intake eval: {passed}/{total} passed")
    for item in report["results"]:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"- {status} {item['case_id']}: {item['name']}")
        for failure in item["failures"]:
            print(f"  - {failure}")


def main() -> int:
    args = parse_args()
    results = [run_case(case) for case in CASES]
    report = {"results": results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
        print(f"report={args.report}")
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
