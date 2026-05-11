#!/usr/bin/env python3
"""
Regression checks for bike_plan_daily_preflight.py.

The daily preflight layer must block daily workout drafting until daily
availability, fixed cross-sport sessions, fatigue, pain, and immovable
constraints are known.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import bike_plan_daily_preflight
import bike_plan_generator
import bike_plan_intake
from eval_bike_plan_intake import COMPLETE_PAYLOAD


ROOT = Path(__file__).parent.resolve()
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_eval_latest.json"
DEFAULT_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_eval_plan.json"
DEFAULT_INPUT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_eval_input.json"
DEFAULT_PREFLIGHT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_eval_result.json"
DEFAULT_SLOTS_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_eval_slots.csv"
DEFAULT_FIXED_SESSIONS_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_eval_fixed_sessions.csv"


@dataclass
class DailyPreflightCase:
    case_id: str
    name: str
    input_data: dict[str, Any]
    expected_status: str
    required_missing_groups: list[str] = field(default_factory=list)
    required_risk_types: list[str] = field(default_factory=list)


def ready_payload() -> dict[str, Any]:
    intake = bike_plan_intake.evaluate(
        {
            **COMPLETE_PAYLOAD,
            "goal": {
                **COMPLETE_PAYLOAD["goal"],
                "race_date": "2026-07-12",
            },
            "availability": {
                **COMPLETE_PAYLOAD["availability"],
                "weekly_bike_days": 4,
                "weekly_bike_hours": 8,
            },
            "other_sports": {
                **COMPLETE_PAYLOAD["other_sports"],
                "fixed_run_hard_days": ["Tuesday"],
                "fixed_swim_hard_days": ["Tuesday"],
                "fixed_strength_lower_body_days": ["Sunday"],
            },
        },
        requested_weeks=6,
    )
    return bike_plan_intake.result_to_dict(intake)


def source_plan() -> dict[str, Any]:
    return bike_plan_generator.build_generator_output(ready_payload())


def base_preflight_input(plan: dict[str, Any]) -> dict[str, Any]:
    daily_availability = []
    daily_status = []
    for week in plan.get("weekly_plan") or []:
        for item in week.get("weekday_schedule") or []:
            daily_availability.append(
                {
                    "week": week.get("week"),
                    "date": item.get("date"),
                    "day": item.get("day"),
                    "available_minutes": 240 if item.get("slot_type") == "long" else 90,
                    "can_bike": True,
                }
            )
            daily_status.append(
                {
                    "week": week.get("week"),
                    "date": item.get("date"),
                    "day": item.get("day"),
                    "fatigue": "normal",
                    "pain_status": "none",
                    "sleep_quality": "ok",
                }
            )
    return {
        "schema_version": bike_plan_daily_preflight.INPUT_SCHEMA_VERSION,
        "daily_availability": daily_availability,
        "daily_status": daily_status,
        "fixed_sessions": [],
    }


def first_scheduled_item(plan: dict[str, Any]) -> dict[str, Any]:
    first_week = (plan.get("weekly_plan") or [])[0]
    return (first_week.get("weekday_schedule") or [])[0]


def hard_item(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    for week in plan.get("weekly_plan") or []:
        for item in week.get("weekday_schedule") or []:
            if item.get("slot_type") == "hard":
                return week, item
    raise AssertionError("fixture has no hard bike slot")


def case_inputs(plan: dict[str, Any]) -> list[DailyPreflightCase]:
    complete = base_preflight_input(plan)
    pain_input = json.loads(json.dumps(complete, ensure_ascii=False))
    first = first_scheduled_item(plan)
    for row in pain_input["daily_status"]:
        if row.get("date") == first.get("date"):
            row["pain_status"] = "膝外侧痛"
            break

    conflict_input = json.loads(json.dumps(complete, ensure_ascii=False))
    hard_week, hard = hard_item(plan)
    conflict_input["fixed_sessions"] = [
        {
            "week": hard_week.get("week"),
            "date": hard.get("date"),
            "day": hard.get("day"),
            "sport": "run",
            "session_type": "hard",
            "movable": False,
        }
    ]

    movable_missing_input = json.loads(json.dumps(complete, ensure_ascii=False))
    movable_missing_input["fixed_sessions"] = [
        {
            "week": hard_week.get("week"),
            "date": hard.get("date"),
            "day": hard.get("day"),
            "sport": "run",
            "session_type": "hard",
        }
    ]

    return [
        DailyPreflightCase(
            case_id="missing_daily_inputs",
            name="缺少 daily 输入时不能进入每日训练课",
            input_data={},
            expected_status="needs_more_daily_data",
            required_missing_groups=["daily_availability", "daily_status", "fixed_sessions"],
        ),
        DailyPreflightCase(
            case_id="complete_safe_inputs",
            name="完整且安全的 daily 输入允许进入草案层",
            input_data=complete,
            expected_status="ready_for_daily_draft",
        ),
        DailyPreflightCase(
            case_id="pain_blocks_daily_draft",
            name="bike 日疼痛必须拦截",
            input_data=pain_input,
            expected_status="blocked_by_daily_risk",
            required_risk_types=["pain_on_bike_day"],
        ),
        DailyPreflightCase(
            case_id="immovable_cross_sport_conflict",
            name="不可移动跨项 hard 冲突必须拦截",
            input_data=conflict_input,
            expected_status="blocked_by_daily_risk",
            required_risk_types=["immovable_cross_sport_conflict"],
        ),
        DailyPreflightCase(
            case_id="fixed_session_missing_movable",
            name="固定跨项训练缺少 movable 不能通过",
            input_data=movable_missing_input,
            expected_status="needs_more_daily_data",
            required_missing_groups=["fixed_sessions"],
        ),
    ]


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


def flattened_missing_groups(result: dict[str, Any]) -> set[str]:
    return {
        row.get("group")
        for week in result.get("weeks") or []
        for row in week.get("missing_data") or []
    }


def flattened_risk_types(result: dict[str, Any]) -> set[str]:
    return {
        row.get("type")
        for week in result.get("weeks") or []
        for row in week.get("risk_flags") or []
    }


def run_case(case: DailyPreflightCase, plan: dict[str, Any]) -> dict[str, Any]:
    DEFAULT_PLAN_REPORT.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    input_path = DEFAULT_INPUT.with_name(f"{DEFAULT_INPUT.stem}_{case.case_id}.json")
    input_path.write_text(json.dumps(case.input_data, ensure_ascii=False, indent=2), encoding="utf-8")
    result = bike_plan_daily_preflight.build_preflight(DEFAULT_PLAN_REPORT, input_path)
    failures = []
    if result.get("status") != case.expected_status:
        failures.append(f"status expected {case.expected_status}, got {result.get('status')}")

    missing_groups = flattened_missing_groups(result)
    for group in case.required_missing_groups:
        if group not in missing_groups:
            failures.append(f"missing group not found: {group}")

    risk_types = flattened_risk_types(result)
    for risk_type in case.required_risk_types:
        if risk_type not in risk_types:
            failures.append(f"risk type not found: {risk_type}")

    forbidden = bike_plan_generator.forbidden_detail_keys().intersection(collect_keys(result))
    if forbidden:
        failures.append(f"preflight leaks forbidden detail keys: {sorted(forbidden)}")

    if result.get("status") == "ready_for_daily_draft":
        if any((week.get("missing_data") or week.get("risk_flags")) for week in result.get("weeks") or []):
            failures.append("ready result still contains missing data or risk flags")

    return {
        "case_id": case.case_id,
        "name": case.name,
        "passed": not failures,
        "failures": failures,
        "result": result,
    }


def write_dicts(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def run_csv_roundtrip_case(plan: dict[str, Any]) -> dict[str, Any]:
    DEFAULT_PLAN_REPORT.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = bike_plan_daily_preflight.slot_csv_rows(DEFAULT_PLAN_REPORT)
    for row in rows:
        row["available_minutes"] = 240 if row.get("slot_type") == "long" else 90
        row["can_bike"] = "yes"
        row["fatigue"] = "normal"
        row["pain_status"] = "none"
        row["sleep_quality"] = "ok"
    write_dicts(
        DEFAULT_SLOTS_CSV,
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
        rows,
    )
    write_dicts(
        DEFAULT_FIXED_SESSIONS_CSV,
        ["week", "date", "day", "day_label", "sport", "session_type", "movable", "notes"],
        [],
    )
    input_data = bike_plan_daily_preflight.input_from_csv(
        DEFAULT_PLAN_REPORT,
        DEFAULT_SLOTS_CSV,
        DEFAULT_FIXED_SESSIONS_CSV,
    )
    input_path = DEFAULT_INPUT.with_name(f"{DEFAULT_INPUT.stem}_csv_roundtrip.json")
    input_path.write_text(json.dumps(input_data, ensure_ascii=False, indent=2), encoding="utf-8")
    result = bike_plan_daily_preflight.build_preflight(DEFAULT_PLAN_REPORT, input_path)
    failures = []
    if result.get("status") != "ready_for_daily_draft":
        failures.append(f"status expected ready_for_daily_draft, got {result.get('status')}")
    if len(input_data.get("daily_availability") or []) != len(rows):
        failures.append("csv roundtrip availability row count mismatch")
    if len(input_data.get("daily_status") or []) != len(rows):
        failures.append("csv roundtrip status row count mismatch")
    return {
        "case_id": "csv_roundtrip_ready",
        "name": "CSV 填写结果可转回 preflight JSON",
        "passed": not failures,
        "failures": failures,
        "result": result,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--preflight", type=Path, default=DEFAULT_PREFLIGHT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def print_summary(report: dict[str, Any]) -> None:
    passed = sum(1 for item in report["results"] if item["passed"])
    total = len(report["results"])
    print(f"Bike plan daily preflight eval: {passed}/{total} passed")
    for item in report["results"]:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"- {status} {item['case_id']}: {item['name']}")
        for failure in item["failures"]:
            print(f"  - {failure}")


def main() -> int:
    args = parse_args()
    plan = source_plan()
    results = [run_case(case, plan) for case in case_inputs(plan)]
    results.append(run_csv_roundtrip_case(plan))
    report = {"results": results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ready_result = next((item["result"] for item in results if item["result"]["status"] == "ready_for_daily_draft"), None)
    if ready_result:
        args.preflight.write_text(json.dumps(ready_result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
        print(f"report={args.report}")
        if ready_result:
            print(f"preflight={args.preflight}")
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
