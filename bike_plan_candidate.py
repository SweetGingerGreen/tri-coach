#!/usr/bin/env python3
"""
Build a second-pass weekly bike plan candidate from manual review overrides.

This script never mutates the original generator report. It reads a generated
week-level plan plus a review override file, applies only narrow scheduling
requests it can understand, and writes a separate candidate report.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
from pathlib import Path
from typing import Any

import bike_plan_generator


ROOT = Path(__file__).parent.resolve()
DEFAULT_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_generator_latest.json"
DEFAULT_OVERRIDE = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_override_latest.json"
DEFAULT_CANDIDATE = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_latest.json"
DEFAULT_CANDIDATE_REVIEW_MD = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_review_latest.md"
DEFAULT_CANDIDATE_REVIEW_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_review_latest.csv"
SCHEMA_VERSION = "bike_plan_candidate_v0.2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-report", type=Path, default=DEFAULT_PLAN_REPORT)
    parser.add_argument("--override", type=Path, default=DEFAULT_OVERRIDE)
    parser.add_argument("--candidate", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--candidate-review-md", type=Path, default=DEFAULT_CANDIDATE_REVIEW_MD)
    parser.add_argument("--candidate-review-csv", type=Path, default=DEFAULT_CANDIDATE_REVIEW_CSV)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-candidate", action="store_true")
    parser.add_argument("--write-review-files", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def text_for_override(item: dict[str, Any]) -> str:
    return "\n".join(
        str(item.get(key) or "")
        for key in ("override_request", "review_comment", "normalized_review_status")
    )


def parse_days_from_text(text: str) -> set[str]:
    days = set()
    for alias, day in bike_plan_generator.DAY_ALIASES.items():
        if alias in text or alias in text.lower():
            days.add(day)
    return days


def day_values(entries: Any) -> list[str]:
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return []
    days = []
    seen = set()
    for item in entries:
        day = item.get("day") if isinstance(item, dict) else str(item or "")
        if day not in bike_plan_generator.DAYS or day in seen:
            continue
        seen.add(day)
        days.append(day)
    return sorted(days, key=bike_plan_generator.day_index)


def parse_structured_override(item: dict[str, Any]) -> dict[str, Any] | None:
    structured = item.get("structured_override") or {}
    if not isinstance(structured, dict) or not structured:
        return None

    move_slot = str(structured.get("move_slot") or "").strip().lower()
    blocked_days = day_values(structured.get("blocked_days") or structured.get("blocked_day"))
    protect_days = day_values(structured.get("protect_days") or structured.get("protect_day"))
    avoid_days_by_slot_type: dict[str, list[str]] = {}
    protect_days_by_slot_type: dict[str, list[str]] = {}
    if move_slot and blocked_days:
        avoid_days_by_slot_type[move_slot] = blocked_days
    if protect_days:
        protect_days_by_slot_type["long"] = protect_days

    return {
        "week": item.get("week"),
        "avoid_days_by_slot_type": avoid_days_by_slot_type,
        "protect_days_by_slot_type": protect_days_by_slot_type,
        "directive_source": "structured_override_fields",
        "raw_override_request": item.get("override_request", ""),
        "raw_review_comment": item.get("review_comment", ""),
    }


def parse_override_directive(item: dict[str, Any]) -> dict[str, Any]:
    structured = parse_structured_override(item)
    if structured is not None:
        return structured

    text = text_for_override(item)
    lowered = text.lower()
    hard_avoid: set[str] = set()
    long_protect: set[str] = set()

    hard_patterns = [
        r"(?:避开|不要|不放|移开|avoid)[^。\n；;,，]*?(?:bike\s*)?hard",
        r"(?:bike\s*)?hard[^。\n；;,，]*?(?:避开|不要|不放|移开|avoid)",
    ]
    long_patterns = [
        r"(?:保留|保护|固定|keep|protect)[^。\n；;,，]*?(?:bike\s*)?long",
        r"(?:bike\s*)?long[^。\n；;,，]*?(?:保留|保护|固定|keep|protect)",
    ]
    for pattern in hard_patterns:
        for match in re.finditer(pattern, lowered, flags=re.IGNORECASE):
            hard_avoid.update(parse_days_from_text(match.group(0)))
    for pattern in long_patterns:
        for match in re.finditer(pattern, lowered, flags=re.IGNORECASE):
            long_protect.update(parse_days_from_text(match.group(0)))

    return {
        "week": item.get("week"),
        "avoid_days_by_slot_type": {"hard": sorted(hard_avoid, key=bike_plan_generator.day_index)},
        "protect_days_by_slot_type": {"long": sorted(long_protect, key=bike_plan_generator.day_index)},
        "directive_source": "natural_language_fallback",
        "raw_override_request": item.get("override_request", ""),
        "raw_review_comment": item.get("review_comment", ""),
    }


def pseudo_payload_from_report(report: dict[str, Any]) -> dict[str, Any]:
    review_view = report.get("review_view") or {}
    preferred_long = review_view.get("preferred_long_day") or {}
    blocked_days = review_view.get("blocked_days") or []
    first_week = (report.get("weekly_plan") or [{}])[0]
    placeholders = first_week.get("cross_sport_placeholders") or {}
    fixed_days = placeholders.get("fixed_cross_sport_days") or {}
    other_input = placeholders.get("other_sports_input") or {}
    return {
        "normalized": {
            "availability": {
                "long_ride_day": preferred_long.get("day") or "Sunday",
                "constraints": [
                    f"{item.get('day_label') or item.get('day')}不能训练"
                    for item in blocked_days
                    if item.get("day") or item.get("day_label")
                ],
            },
            "other_sports": {
                "run_hours": other_input.get("run_hours", 0.0),
                "swim_hours": other_input.get("swim_hours", 0.0),
                "strength_sessions": other_input.get("strength_sessions", 0),
                "fixed_run_hard_days": fixed_days.get("fixed_run_hard_days", []),
                "fixed_swim_hard_days": fixed_days.get("fixed_swim_hard_days", []),
                "fixed_strength_lower_body_days": fixed_days.get("fixed_strength_lower_body_days", []),
            },
        },
        "plan_frame": {
            "intensity_budget": {
                "other_sports_load": {
                    "flags": placeholders.get("active_caution_flags", []),
                }
            }
        },
    }


def update_schedule_dates(week: dict[str, Any], schedule: list[dict[str, Any]]) -> list[dict[str, Any]]:
    week_start = bike_plan_generator.parse_date(week.get("week_start"))
    updated = []
    for item in schedule:
        row = dict(item)
        day = row.get("day")
        row["day_label"] = bike_plan_generator.label_day(day)
        if week_start and day in bike_plan_generator.DAYS:
            row["date"] = (week_start + bike_plan_generator.dt.timedelta(days=bike_plan_generator.day_index(day))).isoformat()
        updated.append(row)
    return sorted(updated, key=lambda item: bike_plan_generator.day_index(item["day"]))


def choose_replacement_day(
    schedule: list[dict[str, Any]],
    item: dict[str, Any],
    blocked_days: set[str],
    avoid_days: set[str],
    protect_long_days: set[str],
) -> str | None:
    used_by_slot = {row.get("slot_type"): row.get("day") for row in schedule}
    used_days = {row.get("day") for row in schedule if row is not item}
    long_days = {
        row.get("day")
        for row in schedule
        if row.get("slot_type") == "long"
    }.union(protect_long_days)
    preferred = bike_plan_generator.preferred_days_for_slot(item, next(iter(long_days), "Sunday"))
    candidates = [
        day
        for day in preferred + bike_plan_generator.DAYS
        if day not in used_days and day not in blocked_days and day not in avoid_days
    ]
    for day in candidates:
        if item.get("slot_type") == "hard" and any(
            bike_plan_generator.day_distance(day, long_day) <= 1 for long_day in long_days if long_day
        ):
            continue
        return day

    current_long_day = used_by_slot.get("long")
    for day in bike_plan_generator.DAYS:
        if day in used_days or day in blocked_days or day in avoid_days:
            continue
        if item.get("slot_type") == "hard" and current_long_day and bike_plan_generator.day_distance(day, current_long_day) <= 1:
            continue
        return day
    return None


def apply_directive_to_week(
    week: dict[str, Any],
    directive: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    updated_week = copy.deepcopy(week)
    schedule = updated_week.get("weekday_schedule") or []
    blocked_days = bike_plan_generator.unavailable_days(payload)
    avoid_by_slot = directive.get("avoid_days_by_slot_type") or {}
    protect_by_slot = directive.get("protect_days_by_slot_type") or {}
    protect_long = set(protect_by_slot.get("long") or [])
    changes = []
    unresolved = []

    for item in schedule:
        slot_type = item.get("slot_type")
        day = item.get("day")
        protect_days = set(protect_by_slot.get(slot_type) or [])
        if protect_days and day in protect_days:
            changes.append(
                {
                    "type": "protected_slot",
                    "slot_type": slot_type,
                    "day": day,
                    "day_label": bike_plan_generator.label_day(day),
                }
            )
        avoid_days = set(avoid_by_slot.get(slot_type) or [])
        if not avoid_days or day not in avoid_days:
            continue
        replacement = choose_replacement_day(schedule, item, blocked_days, avoid_days, protect_long)
        if not replacement:
            unresolved.append(
                {
                    "type": "move_unresolved",
                    "slot_type": slot_type,
                    "from_day": day,
                    "reason": "no_available_weekday_after_override",
                }
            )
            continue
        item["day"] = replacement
        item["schedule_rule"] = (
            "人工 override 候选：槽位避开人工指定日期，并继续遵守 long 相邻恢复窗口；只绑定星期。"
        )
        changes.append(
            {
                "type": "moved_slot",
                "slot_type": slot_type,
                "from_day": day,
                "from_day_label": bike_plan_generator.label_day(day),
                "to_day": replacement,
                "to_day_label": bike_plan_generator.label_day(replacement),
            }
        )

    updated_schedule = update_schedule_dates(updated_week, schedule)
    updated_week["weekday_schedule"] = updated_schedule
    updated_week["cross_sport_placeholders"] = bike_plan_generator.build_cross_sport_placeholders(
        payload,
        updated_week,
        updated_schedule,
    )
    updated_week["candidate_override"] = {
        "status": "applied" if changes and not unresolved else "partial" if changes else "unresolved",
        "directive": directive,
        "changes": changes,
        "unresolved": unresolved,
        "boundary": "candidate_weekly_schedule_only_no_watts_no_minutes_no_intervals",
    }
    return updated_week, updated_week["candidate_override"]


def build_candidate(plan_report: Path, override_report: Path) -> dict[str, Any]:
    plan = load_json(plan_report)
    override = load_json(override_report)
    candidate = copy.deepcopy(plan)
    payload = pseudo_payload_from_report(plan)
    applied = []
    unresolved = []
    weekly_plan = candidate.get("weekly_plan") or []
    by_week = {
        week.get("week"): index
        for index, week in enumerate(weekly_plan)
    }

    for item in override.get("overrides") or []:
        directive = parse_override_directive(item)
        week_number = directive.get("week")
        if week_number not in by_week:
            unresolved.append({"week": week_number, "reason": "week_not_found", "directive": directive})
            continue
        if not any((directive.get("avoid_days_by_slot_type") or {}).values()) and not any(
            (directive.get("protect_days_by_slot_type") or {}).values()
        ):
            unresolved.append({"week": week_number, "reason": "unsupported_override_directive", "directive": directive})
            continue
        updated_week, meta = apply_directive_to_week(weekly_plan[by_week[week_number]], directive, payload)
        weekly_plan[by_week[week_number]] = updated_week
        if meta.get("status") in {"applied", "partial"}:
            applied.append({"week": week_number, **meta})
        if meta.get("unresolved"):
            unresolved.append({"week": week_number, "reason": "partial_unresolved", "details": meta.get("unresolved")})

    candidate["weekly_plan"] = weekly_plan
    candidate["review_view"] = bike_plan_generator.build_review_view(payload, weekly_plan)
    candidate["candidate_metadata"] = {
        "status": "candidate_ready" if applied else "no_candidate_changes",
        "schema_version": SCHEMA_VERSION,
        "source_plan_report": rel_path(plan_report),
        "source_override_report": rel_path(override_report),
        "source_plan_schema_version": plan.get("schema_version", ""),
        "source_override_schema_version": override.get("schema_version", ""),
        "applied_overrides": applied,
        "unresolved_overrides": unresolved,
        "guardrails": [
            "候选计划不覆盖原始 bike_plan_generator_latest.json。",
            "这里只做周级星期槽位候选调整，不生成每日训练课。",
            "不输出 watts、minutes、intervals、sets、reps。",
        ],
    }
    candidate["status"] = "generated_weekly_type_candidate"
    candidate["schema_version"] = plan.get("schema_version", "")
    return candidate


def print_markdown(candidate: dict[str, Any]) -> None:
    metadata = candidate.get("candidate_metadata") or {}
    print(f"status: {candidate.get('status')}")
    print(f"candidate_status: {metadata.get('status')}")
    for item in metadata.get("applied_overrides") or []:
        print(f"- applied week {item.get('week')}: {item.get('status')}")
        for change in item.get("changes") or []:
            print(f"  - {change}")
    for item in metadata.get("unresolved_overrides") or []:
        print(f"- unresolved week {item.get('week')}: {item.get('reason')}")


def main() -> int:
    args = parse_args()
    candidate = build_candidate(args.plan_report, args.override)
    if args.write_candidate:
        args.candidate.parent.mkdir(parents=True, exist_ok=True)
        args.candidate.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_review_files:
        bike_plan_generator.write_review_markdown(candidate, args.candidate_review_md)
        bike_plan_generator.write_review_csv(candidate, args.candidate_review_csv)
    if args.json:
        print(json.dumps(candidate, ensure_ascii=False, indent=2))
    else:
        print_markdown(candidate)
        if args.write_candidate:
            print(f"candidate={args.candidate}")
        if args.write_review_files:
            print(f"candidate_review_markdown={args.candidate_review_md}")
            print(f"candidate_review_csv={args.candidate_review_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
