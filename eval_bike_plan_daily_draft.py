#!/usr/bin/env python3
"""
Regression checks for bike_plan_daily_draft.py.

The daily draft layer may produce concrete workout drafts only after daily
preflight has passed. Simulated preflight must be explicitly allowed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import bike_plan_daily_draft
import bike_plan_daily_preflight
import eval_bike_plan_daily_preflight


ROOT = Path(__file__).parent.resolve()
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_eval_latest.json"
DEFAULT_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_eval_plan.json"
DEFAULT_INTAKE_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_intake_latest.json"
DEFAULT_PREFLIGHT_INPUT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_eval_input.json"
DEFAULT_PREFLIGHT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_eval_preflight.json"
DEFAULT_SIM_PREFLIGHT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_eval_preflight_simulated.json"
DEFAULT_DRAFT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_eval_result.json"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def source_plan() -> dict[str, Any]:
    return eval_bike_plan_daily_preflight.source_plan()


def ready_preflight(plan: dict[str, Any]) -> dict[str, Any]:
    input_data = eval_bike_plan_daily_preflight.base_preflight_input(plan)
    write_json(DEFAULT_PREFLIGHT_INPUT, input_data)
    write_json(DEFAULT_PLAN_REPORT, plan)
    return bike_plan_daily_preflight.build_preflight(DEFAULT_PLAN_REPORT, DEFAULT_PREFLIGHT_INPUT)


def missing_preflight(plan: dict[str, Any]) -> dict[str, Any]:
    write_json(DEFAULT_PREFLIGHT_INPUT, {})
    write_json(DEFAULT_PLAN_REPORT, plan)
    return bike_plan_daily_preflight.build_preflight(DEFAULT_PLAN_REPORT, DEFAULT_PREFLIGHT_INPUT)


def scheduled_bike_count(plan: dict[str, Any]) -> int:
    return sum(
        len(week.get("weekday_schedule") or [])
        for week in plan.get("weekly_plan") or []
    )


def run_blocked_case(plan: dict[str, Any]) -> dict[str, Any]:
    preflight = missing_preflight(plan)
    write_json(DEFAULT_PREFLIGHT_REPORT, preflight)
    result = bike_plan_daily_draft.build_daily_draft(
        DEFAULT_PLAN_REPORT,
        DEFAULT_PREFLIGHT_REPORT,
        DEFAULT_INTAKE_REPORT,
    )
    failures = []
    if result.get("status") != "blocked_by_preflight":
        failures.append(f"expected blocked_by_preflight, got {result.get('status')}")
    if result.get("daily_workouts"):
        failures.append("blocked result still generated daily workouts")
    return case_result("blocked_by_preflight", "preflight 未通过时必须拦截", failures, result)


def run_ready_case(plan: dict[str, Any]) -> dict[str, Any]:
    preflight = ready_preflight(plan)
    write_json(DEFAULT_PREFLIGHT_REPORT, preflight)
    result = bike_plan_daily_draft.build_daily_draft(
        DEFAULT_PLAN_REPORT,
        DEFAULT_PREFLIGHT_REPORT,
        DEFAULT_INTAKE_REPORT,
    )
    write_json(DEFAULT_DRAFT_REPORT, result)
    workouts = result.get("daily_workouts") or []
    failures = []
    if result.get("status") != "daily_draft_generated":
        failures.append(f"expected daily_draft_generated, got {result.get('status')}")
    if len(workouts) != scheduled_bike_count(plan):
        failures.append(f"workout count mismatch: {len(workouts)}")
    if not all(item.get("structure") for item in workouts):
        failures.append("some workouts have no structure")
    if not all(item.get("source_refs") for item in workouts):
        failures.append("some workouts have no source refs")
    if (result.get("athlete_context") or {}).get("ftp_w") is None:
        failures.append("ftp context missing")
    if any(item.get("planned_duration_minutes", 0) <= 0 for item in workouts):
        failures.append("some workouts have non-positive duration")
    return case_result("ready_generates_daily_drafts", "preflight 通过后生成每日草案", failures, result)


def run_simulated_requires_confirmation(plan: dict[str, Any]) -> dict[str, Any]:
    preflight = ready_preflight(plan)
    preflight["source_preflight_input"] = "triathlon-knowledge/metadata/simulated_input.json"
    write_json(DEFAULT_SIM_PREFLIGHT_REPORT, preflight)
    result = bike_plan_daily_draft.build_daily_draft(
        DEFAULT_PLAN_REPORT,
        DEFAULT_SIM_PREFLIGHT_REPORT,
        DEFAULT_INTAKE_REPORT,
    )
    failures = []
    if result.get("status") != "blocked_simulated_input_requires_confirmation":
        failures.append(f"expected simulated block, got {result.get('status')}")
    if not result.get("simulation_mode"):
        failures.append("simulation mode not detected")
    return case_result("simulated_requires_confirmation", "模拟输入未显式允许时必须拦截", failures, result)


def run_simulated_allowed(plan: dict[str, Any]) -> dict[str, Any]:
    preflight = ready_preflight(plan)
    preflight["source_preflight_input"] = "triathlon-knowledge/metadata/simulated_input.json"
    write_json(DEFAULT_SIM_PREFLIGHT_REPORT, preflight)
    result = bike_plan_daily_draft.build_daily_draft(
        DEFAULT_PLAN_REPORT,
        DEFAULT_SIM_PREFLIGHT_REPORT,
        DEFAULT_INTAKE_REPORT,
        allow_simulated=True,
    )
    failures = []
    if result.get("status") != "daily_draft_generated":
        failures.append(f"expected generated simulated draft, got {result.get('status')}")
    if not result.get("simulation_mode"):
        failures.append("simulation mode not retained")
    return case_result("simulated_allowed", "显式允许后可用模拟输入验证流程", failures, result)


def run_long_ride_guardrail(plan: dict[str, Any]) -> dict[str, Any]:
    preflight = ready_preflight(plan)
    write_json(DEFAULT_PREFLIGHT_REPORT, preflight)
    result = bike_plan_daily_draft.build_daily_draft(
        DEFAULT_PLAN_REPORT,
        DEFAULT_PREFLIGHT_REPORT,
        DEFAULT_INTAKE_REPORT,
    )
    long_rows = [
        item for item in result.get("daily_workouts") or []
        if item.get("slot_type") == "long"
    ]
    failures = []
    if not long_rows:
        failures.append("no long ride drafts found")
    if long_rows and not any("补给" in " ".join(item.get("nutrition_notes") or []) for item in long_rows):
        failures.append("long ride draft missing nutrition review note")
    if long_rows and any("比赛补给处方" not in " ".join(item.get("nutrition_notes") or []) for item in long_rows):
        failures.append("long ride note should avoid race nutrition prescription")
    return case_result("long_ride_guardrail", "长骑草案保留补给边界", failures, result)


def case_result(case_id: str, name: str, failures: list[str], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "name": name,
        "passed": not failures,
        "failures": failures,
        "result": result,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def print_summary(report: dict[str, Any]) -> None:
    passed = sum(1 for item in report["results"] if item["passed"])
    total = len(report["results"])
    print(f"Bike plan daily draft eval: {passed}/{total} passed")
    for item in report["results"]:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"- {status} {item['case_id']}: {item['name']}")
        for failure in item["failures"]:
            print(f"  - {failure}")


def main() -> int:
    args = parse_args()
    plan = source_plan()
    write_json(DEFAULT_PLAN_REPORT, plan)
    results = [
        run_blocked_case(plan),
        run_ready_case(plan),
        run_simulated_requires_confirmation(plan),
        run_simulated_allowed(plan),
        run_long_ride_guardrail(plan),
    ]
    report = {"results": results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
        print(f"report={args.report}")
        print(f"draft={DEFAULT_DRAFT_REPORT}")
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
