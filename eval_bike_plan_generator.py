#!/usr/bin/env python3
"""
Regression checks for bike_plan_generator.py.

The generator must stay at week-level workout type allocation:
- never generate daily workouts, watts, minutes, sets, reps, or intervals.
- block intake payloads that are not ready.
- respect high-intensity budget.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bike_plan_generator
import bike_plan_intake
from eval_bike_plan_intake import COMPLETE_PAYLOAD


ROOT = Path(__file__).parent.resolve()
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_generator_eval_latest.json"
DEFAULT_REVIEW_MD = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_eval_latest.md"
DEFAULT_REVIEW_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_eval_latest.csv"


@dataclass
class GeneratorEvalCase:
    case_id: str
    name: str
    intake_payload: dict[str, Any]
    expected_status: str
    required_role_types: list[str]
    required_slot_types: list[str]
    max_high_intensity: int | None = None
    requires_source_refs: bool = False
    requires_weekday_schedule: bool = False
    requires_review_view: bool = False
    requires_cross_sport_placeholders: bool = False
    requires_review_exports: bool = False
    requires_cross_sport_conflicts: bool = False


def ready_payload(input_payload: dict[str, Any], weeks: int = 8) -> dict[str, Any]:
    intake = bike_plan_intake.evaluate(input_payload, requested_weeks=weeks)
    return bike_plan_intake.result_to_dict(intake)


CASES = [
    GeneratorEvalCase(
        case_id="block_missing_data",
        name="缺数据不能生成周级课型",
        intake_payload=bike_plan_intake.result_to_dict(
            bike_plan_intake.evaluate(bike_plan_intake.template_input(), requested_weeks=8)
        ),
        expected_status="blocked",
        required_role_types=[],
        required_slot_types=[],
    ),
    GeneratorEvalCase(
        case_id="three_day_complete",
        name="三天骑行生成周级课型",
        intake_payload=ready_payload(COMPLETE_PAYLOAD, weeks=8),
        expected_status="generated_weekly_type_allocation",
        required_role_types=["Endurance Ride", "Long Ride"],
        required_slot_types=["easy", "long", "technical"],
        max_high_intensity=1,
        requires_source_refs=True,
        requires_weekday_schedule=True,
        requires_review_view=True,
        requires_cross_sport_placeholders=True,
        requires_review_exports=True,
    ),
    GeneratorEvalCase(
        case_id="four_day_build",
        name="四天骑行可保留最多两次高强度预算",
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
            },
            weeks=6,
        ),
        expected_status="generated_weekly_type_allocation",
        required_role_types=["Endurance Ride", "Long Ride"],
        required_slot_types=["easy", "long", "technical", "hard"],
        max_high_intensity=2,
        requires_source_refs=True,
        requires_weekday_schedule=True,
        requires_review_view=True,
        requires_cross_sport_placeholders=True,
        requires_review_exports=True,
    ),
    GeneratorEvalCase(
        case_id="cross_sport_load_build",
        name="跨项负荷高时生成器遵守下调后的高强度预算",
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
        expected_status="generated_weekly_type_allocation",
        required_role_types=["Endurance Ride", "Long Ride"],
        required_slot_types=["easy", "long", "technical", "hard"],
        max_high_intensity=1,
        requires_source_refs=True,
        requires_weekday_schedule=True,
        requires_review_view=True,
        requires_cross_sport_placeholders=True,
        requires_review_exports=True,
    ),
    GeneratorEvalCase(
        case_id="fixed_cross_sport_day_conflicts",
        name="固定跑游力量日期触发真实冲突检测",
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
                    "fixed_run_hard_days": ["Tuesday", "Sunday"],
                    "fixed_swim_hard_days": ["Tuesday"],
                    "fixed_strength_lower_body_days": ["Sunday"],
                },
            },
            weeks=6,
        ),
        expected_status="generated_weekly_type_allocation",
        required_role_types=["Endurance Ride", "Long Ride"],
        required_slot_types=["easy", "long", "technical", "hard"],
        max_high_intensity=2,
        requires_source_refs=True,
        requires_weekday_schedule=True,
        requires_review_view=True,
        requires_cross_sport_placeholders=True,
        requires_review_exports=True,
        requires_cross_sport_conflicts=True,
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


def role_types(result: dict[str, Any]) -> set[str]:
    roles = set()
    for week in result.get("weekly_plan") or []:
        for item in week.get("workout_type_allocation") or []:
            roles.add(item.get("type", ""))
    return roles


def slot_types(result: dict[str, Any]) -> set[str]:
    slots = set()
    for week in result.get("weekly_plan") or []:
        for item in week.get("week_slots") or []:
            slots.add(item.get("slot_type", ""))
    return slots


def check_source_refs(result: dict[str, Any]) -> list[str]:
    failures = []
    grounding = result.get("source_grounding") or {}
    if grounding.get("status") != "available":
        failures.append(f"source grounding unavailable: {grounding.get('warning', '')}")

    forbidden_ref_keys = {"text", "excerpt", "snippet", "content"}
    for week in result.get("weekly_plan") or []:
        for role in week.get("workout_type_allocation") or []:
            refs = role.get("source_refs") or []
            if not refs:
                failures.append(f"week {week.get('week')} role {role.get('type')} has no source_refs")
            for ref in refs:
                if not ref.get("chunk_id") or not ref.get("source_path"):
                    failures.append(f"week {week.get('week')} role {role.get('type')} has incomplete source_ref")
                leaked_keys = forbidden_ref_keys.intersection(ref.keys())
                if leaked_keys:
                    failures.append(f"source_ref leaks text-bearing keys: {sorted(leaked_keys)}")
        for slot in week.get("week_slots") or []:
            if not slot.get("source_refs"):
                failures.append(f"week {week.get('week')} slot {slot.get('slot')} has no source_refs")
        for item in week.get("weekday_schedule") or []:
            if not item.get("source_refs"):
                failures.append(f"week {week.get('week')} schedule item {item.get('slot')} has no source_refs")
    for row in (result.get("review_view") or {}).get("rows") or []:
        if not row.get("source_refs"):
            failures.append(f"review row {row.get('week')} has no source_refs")
    return failures


def check_review_view(result: dict[str, Any], intake_payload: dict[str, Any]) -> list[str]:
    failures = []
    review_view = result.get("review_view") or {}
    weekly_plan = result.get("weekly_plan") or []
    if review_view.get("status") != "ready_for_human_review":
        failures.append(f"review_view status unexpected: {review_view.get('status')}")
    if review_view.get("review_boundary") != "summary_only_no_watts_no_minutes_no_intervals":
        failures.append("review_view boundary missing")

    rows = review_view.get("rows") or []
    if len(rows) != len(weekly_plan):
        failures.append("review row count does not match weekly_plan")

    blocked_days = bike_plan_generator.unavailable_days(intake_payload)
    expected_blocked = [
        {"day": day, "day_label": bike_plan_generator.label_day(day)}
        for day in sorted(blocked_days, key=bike_plan_generator.day_index)
    ]
    if review_view.get("blocked_days") != expected_blocked:
        failures.append("review_view blocked_days do not match intake")

    forbidden_ref_keys = {"text", "excerpt", "snippet", "content"}
    for row, week in zip(rows, weekly_plan):
        if row.get("week") != week.get("week"):
            failures.append("review row week does not match weekly_plan")
        if row.get("review_boundary") != "human_review_only_no_watts_no_minutes_no_intervals":
            failures.append(f"review row {row.get('week')} boundary missing")
        if len(row.get("scheduled_days") or []) != len(week.get("weekday_schedule") or []):
            failures.append(f"review row {row.get('week')} scheduled_days count mismatch")
        placeholder_summary = row.get("cross_sport_placeholder_summary") or {}
        placeholders = week.get("cross_sport_placeholders") or {}
        if placeholder_summary.get("boundary") != placeholders.get("boundary"):
            failures.append(f"review row {row.get('week')} placeholder boundary mismatch")
        if row.get("high_intensity_budget") != week.get("high_intensity_budget"):
            failures.append(f"review row {row.get('week')} high budget mismatch")
        if row.get("assigned_high_intensity_count") != week.get("assigned_high_intensity_count"):
            failures.append(f"review row {row.get('week')} high count mismatch")
        if not row.get("source_refs"):
            failures.append(f"review row {row.get('week')} source_refs missing")
        for ref in row.get("source_refs") or []:
            leaked_keys = forbidden_ref_keys.intersection(ref.keys())
            if leaked_keys:
                failures.append(f"review row source_ref leaks text-bearing keys: {sorted(leaked_keys)}")

    plan_frame = intake_payload.get("plan_frame") or {}
    budget = plan_frame.get("intensity_budget") or {}
    cross_flags = ((budget.get("other_sports_load") or {}).get("flags") or [])
    if cross_flags and not any("cross_sport_load_with_hard_slot" in (row.get("attention_flags") or []) for row in rows):
        failures.append("cross-sport load review flag not found")
    return failures


def check_review_exports(result: dict[str, Any]) -> list[str]:
    failures = []
    markdown = bike_plan_generator.build_review_markdown(result)
    export_rows = bike_plan_generator.build_review_export_rows(result)
    weekly_plan = result.get("weekly_plan") or []
    if len(export_rows) != len(weekly_plan):
        failures.append("review export row count does not match weekly_plan")

    for expected_text in [
        "# Bike Plan Review",
        bike_plan_generator.SCHEMA_VERSION,
        "| Week |",
        "source_chunk_ids",
        "Human review export only",
    ]:
        if expected_text not in markdown:
            failures.append(f"review markdown missing: {expected_text}")

    expected_columns = set(bike_plan_generator.REVIEW_EXPORT_COLUMNS)
    for row in export_rows:
        missing = expected_columns.difference(row.keys())
        if missing:
            failures.append(f"review export row missing columns: {sorted(missing)}")
        extra_forbidden = bike_plan_generator.forbidden_detail_keys().intersection(row.keys())
        if extra_forbidden:
            failures.append(f"review export row has forbidden detail columns: {sorted(extra_forbidden)}")
        if not row.get("scheduled_days") or row.get("scheduled_days") == "-":
            failures.append(f"review export week {row.get('week')} has empty scheduled_days")
        if not row.get("source_chunk_ids") or row.get("source_chunk_ids") == "-":
            failures.append(f"review export week {row.get('week')} has empty source_chunk_ids")
        if row.get("review_boundary") != "human_review_only_no_watts_no_minutes_no_intervals":
            failures.append(f"review export week {row.get('week')} boundary missing")

    return failures


def day_set(rows: list[dict[str, Any]]) -> set[str]:
    return {item.get("day") for item in rows if item.get("day")}


def expected_window(day: str) -> set[str]:
    return set(bike_plan_generator.adjacent_window(day))


def check_cross_sport_placeholders(result: dict[str, Any], intake_payload: dict[str, Any]) -> list[str]:
    failures = []
    normalized = intake_payload.get("normalized") or {}
    other = normalized.get("other_sports") or {}
    expected_run_hours = other.get("run_hours", 0.0)
    expected_swim_hours = other.get("swim_hours", 0.0)
    expected_strength = other.get("strength_sessions", 0)
    blocked_days = bike_plan_generator.unavailable_days(intake_payload)

    for week in result.get("weekly_plan") or []:
        placeholders = week.get("cross_sport_placeholders") or {}
        if placeholders.get("status") != "placeholder_only":
            failures.append(f"week {week.get('week')} placeholder status unexpected")
        if placeholders.get("boundary") != "cross_sport_placeholder_only_no_run_swim_strength_detail_no_watts_no_minutes_no_intervals":
            failures.append(f"week {week.get('week')} placeholder boundary missing")

        inputs = placeholders.get("other_sports_input") or {}
        if inputs.get("run_hours") != expected_run_hours:
            failures.append(f"week {week.get('week')} run_hours placeholder mismatch")
        if inputs.get("swim_hours") != expected_swim_hours:
            failures.append(f"week {week.get('week')} swim_hours placeholder mismatch")
        if inputs.get("strength_sessions") != expected_strength:
            failures.append(f"week {week.get('week')} strength placeholder mismatch")

        hard_days = [item.get("day") for item in week.get("weekday_schedule") or [] if item.get("slot_type") == "hard"]
        long_days = [item.get("day") for item in week.get("weekday_schedule") or [] if item.get("slot_type") == "long"]
        run_avoid = day_set(placeholders.get("run_hard_avoid_days") or [])
        swim_caution = day_set(placeholders.get("swim_hard_caution_days") or [])
        strength_avoid = day_set(placeholders.get("strength_lower_body_avoid_days") or [])

        for day in hard_days:
            if not expected_window(day).issubset(run_avoid):
                failures.append(f"week {week.get('week')} run hard avoid missing bike hard window")
            if day not in swim_caution:
                failures.append(f"week {week.get('week')} swim hard caution missing bike hard day")
            if not expected_window(day).issubset(strength_avoid):
                failures.append(f"week {week.get('week')} strength avoid missing bike hard window")

        for day in long_days:
            if day not in run_avoid:
                failures.append(f"week {week.get('week')} run hard avoid missing bike long day")
            if day not in strength_avoid:
                failures.append(f"week {week.get('week')} strength avoid missing bike long day")

        for day in blocked_days:
            if day not in run_avoid or day not in swim_caution or day not in strength_avoid:
                failures.append(f"week {week.get('week')} placeholders missing blocked day")

        plan_frame = intake_payload.get("plan_frame") or {}
        flags = ((plan_frame.get("intensity_budget") or {}).get("other_sports_load") or {}).get("flags") or []
        if flags and placeholders.get("active_caution_flags") != flags:
            failures.append(f"week {week.get('week')} active caution flags mismatch")
    return failures


def check_cross_sport_conflicts(result: dict[str, Any]) -> list[str]:
    failures = []
    for week in result.get("weekly_plan") or []:
        placeholders = week.get("cross_sport_placeholders") or {}
        conflicts = placeholders.get("cross_sport_conflicts") or []
        if placeholders.get("conflict_status") != "conflicts_detected":
            failures.append(f"week {week.get('week')} expected conflicts_detected")
        if not conflicts:
            failures.append(f"week {week.get('week')} has no cross_sport_conflicts")
            continue

        fixed_days = placeholders.get("fixed_cross_sport_days") or {}
        if [item.get("day") for item in fixed_days.get("fixed_run_hard_days") or []] != ["Tuesday", "Sunday"]:
            failures.append(f"week {week.get('week')} fixed run days not preserved")
        if [item.get("day") for item in fixed_days.get("fixed_swim_hard_days") or []] != ["Tuesday"]:
            failures.append(f"week {week.get('week')} fixed swim days not preserved")
        if [item.get("day") for item in fixed_days.get("fixed_strength_lower_body_days") or []] != ["Sunday"]:
            failures.append(f"week {week.get('week')} fixed strength days not preserved")

        hard_days = {item.get("day") for item in week.get("weekday_schedule") or [] if item.get("slot_type") == "hard"}
        if hard_days:
            if not any(
                item.get("sport") == "run" and item.get("day") in hard_days and "bike_hard_recovery_window" in item.get("reasons", [])
                for item in conflicts
            ):
                failures.append(f"week {week.get('week')} missing run hard bike hard conflict")
            if not any(
                item.get("sport") == "swim" and item.get("day") in hard_days and "same_day_as_bike_hard" in item.get("reasons", [])
                for item in conflicts
            ):
                failures.append(f"week {week.get('week')} missing swim hard bike hard conflict")
        if not any(
            item.get("sport") == "run" and item.get("day") == "Sunday" and "bike_long_day" in item.get("reasons", [])
            for item in conflicts
        ):
            failures.append(f"week {week.get('week')} missing run hard bike long conflict")
        if not any(
            item.get("sport") == "strength" and item.get("day") == "Sunday" and "bike_long_day" in item.get("reasons", [])
            for item in conflicts
        ):
            failures.append(f"week {week.get('week')} missing strength bike long conflict")

    rows = (result.get("review_view") or {}).get("rows") or []
    if not rows:
        failures.append("review rows missing for conflict case")
    if not all("fixed_cross_sport_day_conflict" in (row.get("attention_flags") or []) for row in rows):
        failures.append("review rows missing fixed_cross_sport_day_conflict flag")
    if not all((row.get("cross_sport_placeholder_summary") or {}).get("cross_sport_conflicts") for row in rows):
        failures.append("review rows missing conflict summaries")

    export_rows = bike_plan_generator.build_review_export_rows(result)
    if not all(row.get("cross_sport_conflicts") and row.get("cross_sport_conflicts") != "-" for row in export_rows):
        failures.append("review export rows missing cross_sport_conflicts")
    return failures


def check_weekday_schedule(result: dict[str, Any], intake_payload: dict[str, Any]) -> list[str]:
    failures = []
    long_day = bike_plan_generator.preferred_long_day(intake_payload)
    blocked_days = bike_plan_generator.unavailable_days(intake_payload)

    for week in result.get("weekly_plan") or []:
        schedule = week.get("weekday_schedule") or []
        slots = week.get("week_slots") or []
        if not schedule:
            failures.append(f"week {week.get('week')} has no weekday_schedule")
            continue
        if len(schedule) != len(slots):
            failures.append(f"week {week.get('week')} schedule count does not match slot count")
        if len(schedule) > week.get("bike_days_budget", 0):
            failures.append(f"week {week.get('week')} has more scheduled days than bike day budget")

        days = [item.get("day") for item in schedule]
        if len(days) != len(set(days)):
            failures.append(f"week {week.get('week')} has duplicate scheduled days")
        for day in days:
            if day in blocked_days:
                failures.append(f"week {week.get('week')} scheduled on blocked day {day}")

        week_start = bike_plan_generator.parse_date(week.get("week_start"))
        if week_start:
            for item in schedule:
                day = item.get("day")
                actual_date = item.get("date")
                if day not in bike_plan_generator.DAYS:
                    failures.append(f"week {week.get('week')} has invalid schedule day {day}")
                    continue
                expected_date = (
                    week_start + bike_plan_generator.dt.timedelta(days=bike_plan_generator.day_index(day))
                ).isoformat()
                if actual_date != expected_date:
                    failures.append(
                        f"week {week.get('week')} {day} date expected {expected_date}, got {actual_date}"
                    )

        long_days = [item.get("day") for item in schedule if item.get("slot_type") == "long"]
        hard_days = [item.get("day") for item in schedule if item.get("slot_type") == "hard"]
        if long_days and long_day not in blocked_days and long_days[0] != long_day:
            failures.append(f"week {week.get('week')} long slot expected {long_day}, got {long_days[0]}")
        for hard_day in hard_days:
            for scheduled_long_day in long_days:
                if bike_plan_generator.day_distance(hard_day, scheduled_long_day) <= 1:
                    failures.append(f"week {week.get('week')} hard day is adjacent to long day")

        for item in schedule:
            if item.get("detail_boundary") != "weekday_slot_only_no_watts_no_minutes_no_intervals":
                failures.append(f"week {week.get('week')} schedule item missing detail boundary")
    return failures


def run_case(case: GeneratorEvalCase) -> dict[str, Any]:
    result = bike_plan_generator.build_generator_output(case.intake_payload)
    failures = []
    if result["status"] != case.expected_status:
        failures.append(f"status expected {case.expected_status}, got {result['status']}")

    keys = collect_keys(result)
    for key in bike_plan_generator.forbidden_detail_keys():
        if key in keys:
            failures.append(f"forbidden detail key found: {key}")

    roles = role_types(result)
    for expected_role in case.required_role_types:
        if expected_role not in roles:
            failures.append(f"required role not found: {expected_role}")

    slots = slot_types(result)
    for expected_slot in case.required_slot_types:
        if expected_slot not in slots:
            failures.append(f"required slot not found: {expected_slot}")

    if result["status"] == "generated_weekly_type_allocation":
        weekly_plan = result.get("weekly_plan") or []
        if not weekly_plan:
            failures.append("weekly_plan is empty")
        for week in weekly_plan:
            week_slots = week.get("week_slots") or []
            if not week_slots:
                failures.append(f"week {week.get('week')} has no week_slots")
            if len(week_slots) > week.get("bike_days_budget", 0):
                failures.append(f"week {week.get('week')} has more slots than bike day budget")
            hard_slots = sum(1 for item in week_slots if item.get("slot_type") == "hard")
            if week.get("assigned_high_intensity_count", 0) > week.get("high_intensity_budget", 0):
                failures.append(f"week {week.get('week')} exceeds high-intensity budget")
            if hard_slots != week.get("assigned_high_intensity_count", 0):
                failures.append(f"week {week.get('week')} hard slots do not match assigned high-intensity count")
            if case.max_high_intensity is not None and week.get("high_intensity_budget") != case.max_high_intensity:
                failures.append(
                    f"week {week.get('week')} high budget expected {case.max_high_intensity}, got {week.get('high_intensity_budget')}"
                )
        if case.requires_source_refs:
            failures.extend(check_source_refs(result))
        if case.requires_weekday_schedule:
            failures.extend(check_weekday_schedule(result, case.intake_payload))
        if case.requires_review_view:
            failures.extend(check_review_view(result, case.intake_payload))
        if case.requires_cross_sport_placeholders:
            failures.extend(check_cross_sport_placeholders(result, case.intake_payload))
        if case.requires_review_exports:
            failures.extend(check_review_exports(result))
        if case.requires_cross_sport_conflicts:
            failures.extend(check_cross_sport_conflicts(result))

    return {
        "case_id": case.case_id,
        "name": case.name,
        "passed": not failures,
        "failures": failures,
        "result": result,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--review-md", type=Path, default=DEFAULT_REVIEW_MD)
    parser.add_argument("--review-csv", type=Path, default=DEFAULT_REVIEW_CSV)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def print_summary(report: dict[str, Any]) -> None:
    passed = sum(1 for item in report["results"] if item["passed"])
    total = len(report["results"])
    print(f"Bike plan generator eval: {passed}/{total} passed")
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
    representative = next(
        (item["result"] for item in results if item["result"]["status"] == "generated_weekly_type_allocation"),
        None,
    )
    if representative:
        bike_plan_generator.write_review_markdown(representative, args.review_md)
        bike_plan_generator.write_review_csv(representative, args.review_csv)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
        print(f"report={args.report}")
        if representative:
            print(f"review_markdown={args.review_md}")
            print(f"review_csv={args.review_csv}")
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
