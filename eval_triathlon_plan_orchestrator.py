#!/usr/bin/env python3
"""
Regression checks for triathlon_plan_orchestrator.py.

The orchestrator must coordinate bike/run/swim at week level only:
- keep the existing bike plan intact.
- add run/swim placeholders without pace, distance, intervals, sets, or reps.
- enforce a shared high-intensity budget across all three sports.
- flag fixed cross-sport conflicts instead of silently hiding them.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import bike_plan_generator
import bike_plan_intake
import triathlon_plan_orchestrator
from eval_bike_plan_intake import COMPLETE_PAYLOAD


ROOT = Path(__file__).parent.resolve()
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "triathlon_plan_orchestrator_eval_latest.json"
DEFAULT_INTAKE = ROOT / "triathlon-knowledge" / "metadata" / "triathlon_plan_orchestrator_eval_intake.json"
DEFAULT_BIKE_PLAN = ROOT / "triathlon-knowledge" / "metadata" / "triathlon_plan_orchestrator_eval_bike_plan.json"
FORBIDDEN_KEYS = {
    "daily_workouts",
    "workouts",
    "watts",
    "target_watts",
    "pace",
    "distance",
    "minutes",
    "intervals",
    "sets",
    "reps",
}


@dataclass
class OrchestratorCase:
    case_id: str
    name: str
    intake_payload: dict[str, Any] | None
    expected_status: str
    required_sports: list[str] = field(default_factory=list)
    required_risk_types: list[str] = field(default_factory=list)
    allow_risks: bool = False
    bike_plan_override: dict[str, Any] | None = None


def ready_payload(input_payload: dict[str, Any], weeks: int = 6) -> dict[str, Any]:
    intake = bike_plan_intake.evaluate(input_payload, requested_weeks=weeks)
    return bike_plan_intake.result_to_dict(intake)


def source_bike_plan(intake_payload: dict[str, Any]) -> dict[str, Any]:
    return bike_plan_generator.build_generator_output(intake_payload)


CASES = [
    OrchestratorCase(
        case_id="blocked_bike_plan",
        name="bike plan 未生成时必须阻断三项协调",
        intake_payload=ready_payload(COMPLETE_PAYLOAD, weeks=6),
        bike_plan_override={"status": "blocked", "weekly_plan": []},
        expected_status="blocked_by_bike_plan",
    ),
    OrchestratorCase(
        case_id="balanced_week",
        name="基础输入生成 bike/run/swim 三项周内排布",
        intake_payload=ready_payload(COMPLETE_PAYLOAD, weeks=6),
        expected_status="triathlon_schedule_generated",
        required_sports=["bike", "run", "swim", "brick"],
    ),
    OrchestratorCase(
        case_id="fixed_run_on_long_bike_day",
        name="固定跑步 hard 落在长骑日必须标记风险",
        intake_payload=ready_payload(
            {
                **COMPLETE_PAYLOAD,
                "goal": {
                    **COMPLETE_PAYLOAD["goal"],
                    "race_date": "2026-07-12",
                },
                "other_sports": {
                    **COMPLETE_PAYLOAD["other_sports"],
                    "fixed_run_hard_days": ["Sunday"],
                },
            },
            weeks=6,
        ),
        expected_status="triathlon_schedule_needs_attention",
        required_sports=["bike", "run", "swim", "brick"],
        required_risk_types=["run_hard_on_avoid_day", "bike_plan_cross_sport_conflict"],
        allow_risks=True,
    ),
    OrchestratorCase(
        case_id="high_cross_sport_load",
        name="跨项负荷高时不得超过共享高强度预算",
        intake_payload=ready_payload(
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
                    "run_hours": 7,
                    "swim_hours": 4,
                    "strength_sessions": 2,
                },
            },
            weeks=6,
        ),
        expected_status="triathlon_schedule_generated",
        required_sports=["bike", "run", "swim", "brick"],
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


def sports_in_result(result: dict[str, Any]) -> set[str]:
    return {
        item.get("sport")
        for week in result.get("weekly_plan") or []
        for item in week.get("scheduled_items") or []
    }


def risk_types(result: dict[str, Any]) -> set[str]:
    return {
        risk.get("type")
        for week in result.get("weekly_plan") or []
        for risk in ((week.get("week_review") or {}).get("risk_flags") or [])
    }


def high_count_failures(result: dict[str, Any]) -> list[str]:
    failures = []
    for week in result.get("weekly_plan") or []:
        budget = int(week.get("total_high_intensity_budget") or 0)
        assigned = int(week.get("assigned_high_intensity_count") or 0)
        risks = {
            risk.get("type")
            for risk in ((week.get("week_review") or {}).get("risk_flags") or [])
        }
        if assigned > budget and "total_high_intensity_budget_exceeded" not in risks:
            failures.append(f"week {week.get('week')} exceeds high budget without risk flag")
    return failures


def source_ref_failures(result: dict[str, Any]) -> list[str]:
    failures = []
    forbidden_ref_keys = {"text", "excerpt", "snippet", "content"}
    for week in result.get("weekly_plan") or []:
        for item in week.get("scheduled_items") or []:
            if not item.get("source_refs"):
                failures.append(f"week {week.get('week')} {item.get('sport')} {item.get('role')} has no source_refs")
            for ref in item.get("source_refs") or []:
                leaked = forbidden_ref_keys.intersection(ref.keys())
                if leaked:
                    failures.append(f"source_ref leaks text-bearing keys: {sorted(leaked)}")
        review = week.get("week_review") or {}
        if not review.get("source_refs"):
            failures.append(f"week {week.get('week')} review has no source_refs")
    return failures


def review_export_failures(result: dict[str, Any]) -> list[str]:
    failures = []
    rows = triathlon_plan_orchestrator.build_review_export_rows(result)
    if len(rows) != len(result.get("weekly_plan") or []):
        failures.append("review export row count does not match weekly_plan")
    markdown = triathlon_plan_orchestrator.build_review_markdown(result)
    for text in [
        "# Triathlon Plan Orchestrator Review",
        triathlon_plan_orchestrator.SCHEMA_VERSION,
        "Human review export only",
        "| Week |",
    ]:
        if text not in markdown:
            failures.append(f"review markdown missing: {text}")
    for row in rows:
        missing = set(triathlon_plan_orchestrator.REVIEW_EXPORT_COLUMNS).difference(row.keys())
        if missing:
            failures.append(f"review row missing columns: {sorted(missing)}")
        if not row.get("scheduled_items") or row.get("scheduled_items") == "-":
            failures.append(f"week {row.get('week')} export has empty scheduled_items")
        if row.get("review_boundary") != "human_review_only_no_pace_distance_minutes_sets_reps_intervals":
            failures.append(f"week {row.get('week')} review boundary missing")
    return failures


def run_case(case: OrchestratorCase) -> dict[str, Any]:
    failures = []
    intake_payload = case.intake_payload or {}
    bike_plan = case.bike_plan_override or source_bike_plan(intake_payload)
    intake_path = DEFAULT_INTAKE.with_name(f"{DEFAULT_INTAKE.stem}_{case.case_id}.json")
    bike_path = DEFAULT_BIKE_PLAN.with_name(f"{DEFAULT_BIKE_PLAN.stem}_{case.case_id}.json")
    intake_path.write_text(json.dumps(intake_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    bike_path.write_text(json.dumps(bike_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    result = triathlon_plan_orchestrator.build_orchestrator(intake_path, bike_path)

    if result.get("status") != case.expected_status:
        failures.append(f"status expected {case.expected_status}, got {result.get('status')}")

    keys = collect_keys(result)
    leaked = FORBIDDEN_KEYS.intersection(keys)
    if leaked:
        failures.append(f"forbidden detail keys found: {sorted(leaked)}")

    actual_sports = sports_in_result(result)
    for sport in case.required_sports:
        if sport not in actual_sports:
            failures.append(f"required sport not found: {sport}")

    actual_risks = risk_types(result)
    for risk_type in case.required_risk_types:
        if risk_type not in actual_risks:
            failures.append(f"required risk not found: {risk_type}")

    if not case.allow_risks and actual_risks:
        failures.append(f"unexpected risk flags: {sorted(actual_risks)}")

    failures.extend(high_count_failures(result))
    if result.get("status") in {"triathlon_schedule_generated", "triathlon_schedule_needs_attention"}:
        failures.extend(source_ref_failures(result))
        failures.extend(review_export_failures(result))

    return {
        "case_id": case.case_id,
        "name": case.name,
        "passed": not failures,
        "failures": failures,
        "result": result,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    results = [run_case(case) for case in CASES]
    passed = sum(1 for item in results if item["passed"])
    report = {
        "schema_version": "triathlon_plan_orchestrator_eval_v0.1",
        "passed": passed,
        "total": len(results),
        "cases": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"triathlon_plan_orchestrator eval: {passed}/{len(results)} passed")
        for item in results:
            status = "PASS" if item["passed"] else "FAIL"
            print(f"- {status} {item['case_id']}: {item['name']}")
            for failure in item["failures"]:
                print(f"  - {failure}")
        print(f"report={args.report}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
