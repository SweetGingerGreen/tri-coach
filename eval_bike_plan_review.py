#!/usr/bin/env python3
"""
Regression checks for bike_plan_review.py.

The review layer must turn human CSV review edits into a separate override
file without mutating the generated plan or creating daily workout details.
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bike_plan_generator
import bike_plan_intake
import bike_plan_review
from eval_bike_plan_intake import COMPLETE_PAYLOAD


ROOT = Path(__file__).parent.resolve()
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_eval_latest.json"
DEFAULT_REVIEW_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_override_eval_input.csv"
DEFAULT_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_override_eval_plan.json"
DEFAULT_OVERRIDE = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_override_eval_latest.json"


@dataclass
class ReviewEvalCase:
    case_id: str
    name: str
    row_updates: dict[str, str]
    expected_status: str
    expected_overrides: int
    expected_structured: bool = False


CASES = [
    ReviewEvalCase(
        case_id="no_manual_override",
        name="默认复核 CSV 不产生 override",
        row_updates={},
        expected_status="no_overrides",
        expected_overrides=0,
    ),
    ReviewEvalCase(
        case_id="manual_override_request",
        name="人工修改意见生成 override",
        row_updates={
            "human_review_status": "override_requested",
            "review_comment": "周二已经固定跑步 hard，需要人工调整本周 bike 排程。",
            "override_request": "第1周避开周二 bike hard，保留周日 long。",
        },
        expected_status="overrides_ready",
        expected_overrides=1,
    ),
    ReviewEvalCase(
        case_id="structured_override_fields",
        name="结构化字段生成机器可读 override",
        row_updates={
            "move_slot": "hard",
            "blocked_day": "Tuesday",
            "protect_day": "Sunday",
        },
        expected_status="overrides_ready",
        expected_overrides=1,
        expected_structured=True,
    ),
]


def ready_payload() -> dict[str, Any]:
    intake = bike_plan_intake.evaluate(COMPLETE_PAYLOAD, requested_weeks=8)
    return bike_plan_intake.result_to_dict(intake)


def base_result() -> dict[str, Any]:
    return bike_plan_generator.build_generator_output(ready_payload())


def write_review_input(path: Path, result: dict[str, Any], row_updates: dict[str, str]) -> None:
    rows = bike_plan_generator.build_review_export_rows(result)
    if row_updates and rows:
        rows[0].update(row_updates)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=bike_plan_generator.REVIEW_EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


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


def run_case(case: ReviewEvalCase) -> dict[str, Any]:
    result = base_result()
    DEFAULT_PLAN_REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    review_csv = DEFAULT_REVIEW_CSV.with_name(f"{DEFAULT_REVIEW_CSV.stem}_{case.case_id}.csv")
    write_review_input(review_csv, result, case.row_updates)
    override = bike_plan_review.build_review_override(review_csv, DEFAULT_PLAN_REPORT)

    failures = []
    if override.get("status") != case.expected_status:
        failures.append(f"status expected {case.expected_status}, got {override.get('status')}")
    summary = override.get("review_summary") or {}
    if summary.get("overrides_total") != case.expected_overrides:
        failures.append(f"overrides expected {case.expected_overrides}, got {summary.get('overrides_total')}")
    if override.get("source_plan_schema_version") != bike_plan_generator.SCHEMA_VERSION:
        failures.append("source plan schema version mismatch")
    if override.get("source_intake_schema_version") != bike_plan_intake.SCHEMA_VERSION:
        failures.append("source intake schema version mismatch")
    if len(override.get("review_items") or []) != len(result.get("weekly_plan") or []):
        failures.append("review item count does not match weekly plan")

    forbidden_keys = bike_plan_generator.forbidden_detail_keys()
    leaked_keys = forbidden_keys.intersection(collect_keys(override))
    if leaked_keys:
        failures.append(f"override leaks forbidden detail keys: {sorted(leaked_keys)}")

    for item in override.get("overrides") or []:
        if item.get("boundary") != "override_request_only_no_plan_mutation_no_watts_no_minutes_no_intervals":
            failures.append(f"override week {item.get('week')} boundary missing")
        if item.get("week") != 1:
            failures.append(f"override should target week 1, got {item.get('week')}")
        structured = item.get("structured_override") or {}
        if case.expected_structured:
            if structured.get("move_slot") != "hard":
                failures.append(f"structured move_slot expected hard, got {structured.get('move_slot')}")
            blocked_days = structured.get("blocked_days") or []
            if not any(day.get("day") == "Tuesday" for day in blocked_days):
                failures.append("structured blocked_day missing Tuesday")
            protect_days = structured.get("protect_days") or []
            if not any(day.get("day") == "Sunday" for day in protect_days):
                failures.append("structured protect_day missing Sunday")

    return {
        "case_id": case.case_id,
        "name": case.name,
        "passed": not failures,
        "failures": failures,
        "result": override,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--override", type=Path, default=DEFAULT_OVERRIDE)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def print_summary(report: dict[str, Any]) -> None:
    passed = sum(1 for item in report["results"] if item["passed"])
    total = len(report["results"])
    print(f"Bike plan review eval: {passed}/{total} passed")
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
    override_result = next(
        (item["result"] for item in results if item["result"]["status"] == "overrides_ready"),
        None,
    )
    if override_result:
        args.override.write_text(json.dumps(override_result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
        print(f"report={args.report}")
        if override_result:
            print(f"override={args.override}")
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
