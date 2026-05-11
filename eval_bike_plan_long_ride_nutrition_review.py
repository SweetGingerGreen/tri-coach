#!/usr/bin/env python3
"""
Regression checks for bike_plan_long_ride_nutrition_review.py.

The long-ride nutrition review must connect daily drafts to the approved
energy/carbohydrate calculator while refusing to create a fueling prescription
without individual tolerance data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import bike_plan_daily_draft
import bike_plan_daily_preflight
import bike_plan_generator
import bike_plan_intake
import bike_plan_long_ride_nutrition_review
from eval_bike_plan_intake import COMPLETE_PAYLOAD


ROOT = Path(__file__).parent.resolve()
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_long_ride_nutrition_review_eval_latest.json"
DEFAULT_INTAKE_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_long_ride_nutrition_review_eval_intake.json"
DEFAULT_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_long_ride_nutrition_review_eval_plan.json"
DEFAULT_PREFLIGHT_INPUT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_long_ride_nutrition_review_eval_preflight_input.json"
DEFAULT_PREFLIGHT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_long_ride_nutrition_review_eval_preflight.json"
DEFAULT_DRAFT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_long_ride_nutrition_review_eval_draft.json"
DEFAULT_SIM_DRAFT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_long_ride_nutrition_review_eval_draft_simulated.json"
DEFAULT_REVIEW_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_long_ride_nutrition_review_eval_result.json"


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def source_fixture(simulated: bool = False) -> tuple[Path, Path]:
    intake = bike_plan_intake.result_to_dict(
        bike_plan_intake.evaluate(COMPLETE_PAYLOAD, requested_weeks=8)
    )
    plan = bike_plan_generator.build_generator_output(intake)
    daily_availability = []
    daily_status = []
    for week in plan.get("weekly_plan") or []:
        for item in week.get("weekday_schedule") or []:
            daily_availability.append(
                {
                    "week": week.get("week"),
                    "date": item.get("date"),
                    "day": item.get("day"),
                    "available_minutes": 120 if item.get("slot_type") == "long" else 60,
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
                    "sleep_quality": "good",
                }
            )
    preflight_input = {
        "schema_version": bike_plan_daily_preflight.INPUT_SCHEMA_VERSION,
        "source_slots_csv": "simulated_slots.csv" if simulated else "real_slots.csv",
        "daily_availability": daily_availability,
        "daily_status": daily_status,
        "fixed_sessions": [],
    }
    write_json(DEFAULT_INTAKE_REPORT, intake)
    write_json(DEFAULT_PLAN_REPORT, plan)
    write_json(DEFAULT_PREFLIGHT_INPUT, preflight_input)
    preflight = bike_plan_daily_preflight.build_preflight(DEFAULT_PLAN_REPORT, DEFAULT_PREFLIGHT_INPUT)
    if simulated:
        preflight["source_preflight_input"] = "triathlon-knowledge/metadata/simulated_preflight_input.json"
    write_json(DEFAULT_PREFLIGHT_REPORT, preflight)
    draft = bike_plan_daily_draft.build_daily_draft(
        DEFAULT_PLAN_REPORT,
        DEFAULT_PREFLIGHT_REPORT,
        DEFAULT_INTAKE_REPORT,
        allow_simulated=simulated,
    )
    draft_path = DEFAULT_SIM_DRAFT_REPORT if simulated else DEFAULT_DRAFT_REPORT
    write_json(draft_path, draft)
    return draft_path, DEFAULT_INTAKE_REPORT


def case_result(case_id: str, name: str, failures: list[str], result: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "name": name,
        "passed": not failures,
        "failures": failures,
        "result": result,
    }


def run_blocked_case() -> dict[str, Any]:
    result = bike_plan_long_ride_nutrition_review.build_review(
        ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_latest.json",
        bike_plan_long_ride_nutrition_review.DEFAULT_DB,
    )
    failures = []
    if result.get("status") != "blocked_by_daily_draft":
        failures.append(f"expected blocked_by_daily_draft, got {result.get('status')}")
    return case_result("blocked_by_daily_draft", "daily draft 未生成时必须拦截", failures, result)


def run_simulated_requires_confirmation() -> dict[str, Any]:
    draft_path, _ = source_fixture(simulated=True)
    result = bike_plan_long_ride_nutrition_review.build_review(
        draft_path,
        bike_plan_long_ride_nutrition_review.DEFAULT_DB,
    )
    failures = []
    if result.get("status") != "blocked_simulated_input_requires_confirmation":
        failures.append(f"expected simulated block, got {result.get('status')}")
    if not result.get("simulation_mode"):
        failures.append("simulation mode not detected")
    return case_result("simulated_requires_confirmation", "模拟 daily draft 必须显式允许", failures, result)


def run_generated_case(simulated: bool) -> dict[str, Any]:
    draft_path, _ = source_fixture(simulated=simulated)
    result = bike_plan_long_ride_nutrition_review.build_review(
        draft_path,
        bike_plan_long_ride_nutrition_review.DEFAULT_DB,
        allow_simulated=simulated,
    )
    if simulated:
        write_json(DEFAULT_REVIEW_REPORT, result)
    reviews = result.get("long_ride_reviews") or []
    first = reviews[0] if reviews else {}
    calc = first.get("estimated_energy") or {}
    failures = []
    if result.get("status") != "long_ride_nutrition_review_generated":
        failures.append(f"expected generated review, got {result.get('status')}")
    if len(reviews) != 8:
        failures.append(f"expected 8 long ride reviews, got {len(reviews)}")
    if not (result.get("calculator_template") or {}).get("source_chunks"):
        failures.append("calculator template source chunks missing")
    if first.get("nutrition_prescription_status") != "blocked_missing_individual_tolerance_data":
        failures.append("nutrition prescription should be blocked by missing tolerance data")
    if "carb_tolerance_g_per_hour" not in (first.get("missing_for_prescription") or []):
        failures.append("missing carb tolerance not listed")
    if calc and not (1020 <= calc.get("total_kcal", 0) <= 1040):
        failures.append(f"unexpected kcal estimate: {calc.get('total_kcal')}")
    if calc and not (120 <= calc.get("carb_g", 0) <= 130):
        failures.append(f"unexpected carb estimate: {calc.get('carb_g')}")
    return case_result(
        "simulated_review_generated" if simulated else "real_ready_review_generated",
        "长骑复核生成热量/碳水估算并拒绝处方",
        failures,
        result,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def print_summary(report: dict[str, Any]) -> None:
    passed = sum(1 for item in report["results"] if item["passed"])
    total = len(report["results"])
    print(f"Bike plan long ride nutrition review eval: {passed}/{total} passed")
    for item in report["results"]:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"- {status} {item['case_id']}: {item['name']}")
        for failure in item["failures"]:
            print(f"  - {failure}")


def main() -> int:
    args = parse_args()
    results = [
        run_blocked_case(),
        run_simulated_requires_confirmation(),
        run_generated_case(simulated=True),
        run_generated_case(simulated=False),
    ]
    report = {"results": results}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
        print(f"report={args.report}")
        print(f"review={DEFAULT_REVIEW_REPORT}")
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
