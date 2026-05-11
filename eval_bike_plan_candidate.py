#!/usr/bin/env python3
"""
Regression checks for bike_plan_candidate.py.

The candidate layer may apply narrow manual overrides to a weekly plan, but it
must keep the original report immutable and stay at weekly slot level.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import bike_plan_candidate
import bike_plan_generator
import bike_plan_intake
import bike_plan_review
from eval_bike_plan_intake import COMPLETE_PAYLOAD


ROOT = Path(__file__).parent.resolve()
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_eval_latest.json"
DEFAULT_SOURCE_PLAN = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_eval_source_plan.json"
DEFAULT_REVIEW_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_eval_review.csv"
DEFAULT_OVERRIDE = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_eval_override.json"
DEFAULT_CANDIDATE = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_eval_latest_candidate.json"


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
                "fixed_run_hard_days": ["Tuesday", "Sunday"],
                "fixed_swim_hard_days": ["Tuesday"],
                "fixed_strength_lower_body_days": ["Sunday"],
            },
        },
        requested_weeks=6,
    )
    return bike_plan_intake.result_to_dict(intake)


def write_override_fixture(source_plan: dict[str, Any]) -> dict[str, Any]:
    rows = bike_plan_generator.build_review_export_rows(source_plan)
    rows[0].update(
        {
            "human_review_status": "override_requested",
            "review_comment": "周二已经固定跑步 hard，需要人工调整本周 bike 排程。",
            "move_slot": "hard",
            "blocked_day": "Tuesday",
            "protect_day": "Sunday",
        }
    )
    DEFAULT_REVIEW_CSV.parent.mkdir(parents=True, exist_ok=True)
    with DEFAULT_REVIEW_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=bike_plan_generator.REVIEW_EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    override = bike_plan_review.build_review_override(DEFAULT_REVIEW_CSV, DEFAULT_SOURCE_PLAN)
    DEFAULT_OVERRIDE.write_text(json.dumps(override, ensure_ascii=False, indent=2), encoding="utf-8")
    return override


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


def week_by_number(result: dict[str, Any], number: int) -> dict[str, Any]:
    return next(week for week in result.get("weekly_plan") or [] if week.get("week") == number)


def day_for_slot(week: dict[str, Any], slot_type: str) -> str | None:
    for item in week.get("weekday_schedule") or []:
        if item.get("slot_type") == slot_type:
            return item.get("day")
    return None


def conflict_types(week: dict[str, Any]) -> set[str]:
    placeholders = week.get("cross_sport_placeholders") or {}
    return {item.get("conflict_type") for item in placeholders.get("cross_sport_conflicts") or []}


def run_eval() -> dict[str, Any]:
    source_plan = bike_plan_generator.build_generator_output(ready_payload())
    DEFAULT_SOURCE_PLAN.write_text(json.dumps(source_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    override = write_override_fixture(source_plan)
    candidate = bike_plan_candidate.build_candidate(DEFAULT_SOURCE_PLAN, DEFAULT_OVERRIDE)
    DEFAULT_CANDIDATE.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")

    failures = []
    source_week = week_by_number(source_plan, 1)
    candidate_week = week_by_number(candidate, 1)
    if day_for_slot(source_week, "hard") != "Tuesday":
        failures.append("fixture source hard slot is not on Tuesday")
    if day_for_slot(candidate_week, "hard") == "Tuesday":
        failures.append("candidate hard slot did not move away from Tuesday")
    if day_for_slot(candidate_week, "long") != "Sunday":
        failures.append("candidate did not protect Sunday long slot")

    metadata = candidate.get("candidate_metadata") or {}
    if metadata.get("status") != "candidate_ready":
        failures.append(f"candidate status expected candidate_ready, got {metadata.get('status')}")
    if not metadata.get("applied_overrides"):
        failures.append("candidate has no applied_overrides")
    else:
        directive = (metadata["applied_overrides"][0].get("directive") or {})
        if directive.get("directive_source") != "structured_override_fields":
            failures.append(f"candidate did not use structured override fields: {directive.get('directive_source')}")

    source_conflicts = conflict_types(source_week)
    candidate_conflicts = conflict_types(candidate_week)
    if "fixed_swim_hard_on_caution_day" in candidate_conflicts:
        failures.append("candidate still has swim hard same-day bike hard conflict")
    if "fixed_run_hard_on_avoid_day" not in source_conflicts:
        failures.append("source fixture missing run conflict")
    if len((candidate_week.get("cross_sport_placeholders") or {}).get("cross_sport_conflicts") or []) >= len(
        (source_week.get("cross_sport_placeholders") or {}).get("cross_sport_conflicts") or []
    ):
        failures.append("candidate did not reduce conflict count")

    forbidden = bike_plan_generator.forbidden_detail_keys().intersection(collect_keys(candidate))
    if forbidden:
        failures.append(f"candidate leaks forbidden detail keys: {sorted(forbidden)}")

    return {
        "passed": not failures,
        "failures": failures,
        "source_plan": source_plan,
        "override": override,
        "candidate": candidate,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_eval()
    report = {
        "results": [
            {
                "case_id": "manual_override_candidate",
                "name": "人工 override 生成第二版候选周级排程",
                "passed": result["passed"],
                "failures": result["failures"],
                "candidate_metadata": result["candidate"].get("candidate_metadata"),
            }
        ]
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        item = report["results"][0]
        status = "PASS" if item["passed"] else "FAIL"
        print(f"Bike plan candidate eval: {1 if item['passed'] else 0}/1 passed")
        print(f"- {status} {item['case_id']}: {item['name']}")
        for failure in item["failures"]:
            print(f"  - {failure}")
        print(f"report={args.report}")
        print(f"candidate={DEFAULT_CANDIDATE}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
