#!/usr/bin/env python3
"""
Check whether the generated triathlon schedule follows a historical Ironman 226
coach-plan logic profile.

This is a shape check, not a gold-standard comparison. User availability and
daily preflight remain stronger than this historical coach-plan sample. The
referenced extract is treated as a private case study and is excluded from
version control.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent.resolve()
DEFAULT_PROFILE = ROOT / "triathlon-knowledge" / "metadata" / "ironman_plan_logic_profile.json"
DEFAULT_EXTRACT = ROOT / "triathlon-knowledge" / "metadata" / "ironman_plan_extract_latest.json"
DEFAULT_SCHEDULE = ROOT / "triathlon-knowledge" / "metadata" / "triathlon_plan_orchestrator_latest.json"
DEFAULT_OUTPUT = ROOT / "triathlon-knowledge" / "metadata" / "ironman_plan_logic_check_latest.json"
DEFAULT_REVIEW_MD = ROOT / "triathlon-knowledge" / "metadata" / "ironman_plan_logic_check_latest.md"
SCHEMA_VERSION = "ironman_plan_logic_check_v0.1"
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
WEEKEND_DAYS = {"Saturday", "Sunday"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--coach-extract", type=Path, default=DEFAULT_EXTRACT)
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--review-md", type=Path, default=DEFAULT_REVIEW_MD)
    parser.add_argument("--write-output", action="store_true")
    parser.add_argument("--write-review", action="store_true")
    parser.add_argument("--json", action="store_true")
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


def day_index(day: str) -> int:
    return DAYS.index(day) if day in DAYS else 99


def label_day(day: str) -> str:
    return DAY_LABELS.get(day, day)


def scheduled_items(week: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in week.get("scheduled_items") or []
        if item.get("day") in DAYS and item.get("sport")
    ]


def sports_for_week(week: dict[str, Any]) -> set[str]:
    sports = set()
    for item in scheduled_items(week):
        sport = item.get("sport")
        if sport == "brick":
            sports.update({"bike", "run", "brick"})
        elif sport:
            sports.add(sport)
    return sports


def is_high(item: dict[str, Any]) -> bool:
    return item.get("intensity_class") == "high" or item.get("slot_type") in {"hard", "quality"}


def is_long_or_brick_anchor(item: dict[str, Any]) -> bool:
    text = " ".join(
        str(item.get(key) or "").lower()
        for key in ("sport", "slot_type", "role")
    )
    return "brick" in text or "long" in text


def week_days(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({str(row.get("day")) for row in rows if row.get("day") in DAYS}, key=day_index)


def check_schedule_shape(weeks: list[dict[str, Any]]) -> dict[str, Any]:
    missing = []
    for week in weeks:
        sports = sports_for_week(week)
        absent = sorted({"bike", "run", "swim"} - sports)
        if absent:
            missing.append({"week": week.get("week"), "week_start": week.get("week_start"), "missing": absent})
    return {
        "name": "schedule_shape_consistency",
        "status": "pass" if not missing else "needs_attention",
        "summary": "每周均覆盖 bike/run/swim。" if not missing else "部分周缺少三项覆盖。",
        "details": {"missing_weeks": missing},
    }


def check_weekend_long_anchor(weeks: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    anchors = ((profile.get("global_logic") or {}).get("stable_weekly_anchors") or {})
    preferred = anchors.get("long_bike_or_brick_preferred_day", "Saturday")
    no_weekend_anchor = []
    shifted = []
    matched = []
    for week in weeks:
        anchors_this_week = [
            item for item in scheduled_items(week)
            if item.get("day") in WEEKEND_DAYS and is_long_or_brick_anchor(item)
        ]
        if not anchors_this_week:
            no_weekend_anchor.append({"week": week.get("week"), "week_start": week.get("week_start")})
            continue
        days = week_days(anchors_this_week)
        row = {"week": week.get("week"), "week_start": week.get("week_start"), "days": days}
        if preferred in days:
            matched.append(row)
        else:
            shifted.append(row)

    if no_weekend_anchor:
        status = "needs_attention"
        summary = "部分周没有周末长课或 brick 锚点。"
    elif shifted:
        status = "pass_with_note"
        summary = "都有周末长课锚点，但不完全等于历史教练表的周六偏好。"
    else:
        status = "pass"
        summary = "周末长课锚点与历史教练表偏好一致。"
    return {
        "name": "weekend_long_anchor_consistency",
        "status": status,
        "summary": summary,
        "details": {
            "preferred_day": preferred,
            "matched_weeks": matched,
            "shifted_weeks": shifted,
            "missing_weeks": no_weekend_anchor,
            "interpretation": "用户可用时间优先；周六偏好不应作为硬失败条件。",
        },
    }


def check_brick_specificity(weeks: list[dict[str, Any]], schedule: dict[str, Any]) -> dict[str, Any]:
    brick_weeks = []
    for week in weeks:
        brick_items = [
            item for item in scheduled_items(week)
            if "brick" in " ".join(str(item.get(key) or "").lower() for key in ("sport", "slot_type", "role"))
        ]
        if brick_items:
            brick_weeks.append(
                {
                    "week": week.get("week"),
                    "week_start": week.get("week_start"),
                    "days": week_days(brick_items),
                }
            )

    goal = schedule.get("goal") or {}
    phase_names = {str(week.get("phase_hint") or "") for week in weeks}
    early_window = phase_names.issubset({"", "base"}) or "base" in phase_names
    if brick_weeks:
        status = "pass"
        summary = "当前窗口已包含 brick 专项化。"
    elif early_window:
        status = "watch"
        summary = "当前窗口没有 brick；如果这是早期 base 阶段可以接受，但后续 build/specific 阶段需要增加。"
    else:
        status = "needs_attention"
        summary = "非早期阶段缺少 brick 专项化。"
    return {
        "name": "brick_specificity_consistency",
        "status": status,
        "summary": summary,
        "details": {
            "brick_weeks": brick_weeks,
            "phase_hints": sorted(phase_names),
            "race_date": goal.get("race_date", ""),
        },
    }


def check_recovery_day(weeks: list[dict[str, Any]], profile: dict[str, Any]) -> dict[str, Any]:
    anchors = ((profile.get("global_logic") or {}).get("stable_weekly_anchors") or {})
    preferred = anchors.get("recovery_or_optional_day", "Friday")
    not_protected = []
    protected = []
    for week in weeks:
        day_items = [item for item in scheduled_items(week) if item.get("day") == preferred]
        high_items = [item for item in day_items if is_high(item)]
        if not day_items:
            protected.append({"week": week.get("week"), "week_start": week.get("week_start"), "mode": "empty"})
        elif not high_items and len(day_items) <= 1:
            protected.append({"week": week.get("week"), "week_start": week.get("week_start"), "mode": "light"})
        else:
            not_protected.append(
                {
                    "week": week.get("week"),
                    "week_start": week.get("week_start"),
                    "items": [
                        {
                            "sport": item.get("sport"),
                            "slot_type": item.get("slot_type"),
                            "role": item.get("role"),
                            "intensity_class": item.get("intensity_class"),
                        }
                        for item in day_items
                    ],
                }
            )

    status = "pass" if not not_protected else "pass_with_note"
    summary = (
        "恢复/可选轻量日得到保护。"
        if not not_protected
        else "恢复日是高度定制项；当前不同于历史教练表偏好，只作为说明项，不作为错误。"
    )
    return {
        "name": "recovery_day_consistency",
        "status": status,
        "summary": summary,
        "details": {
            "preferred_day": preferred,
            "preferred_day_label": label_day(preferred),
            "protected_weeks": protected,
            "not_protected_weeks": not_protected,
        },
    }


def check_taper_shape(weeks: list[dict[str, Any]]) -> dict[str, Any]:
    taper_weeks = [
        week for week in weeks
        if str(week.get("workload_state") or "") in {"peaking", "taper", "race_week"}
        or "race" in str(week.get("phase_hint") or "").lower()
    ]
    if not taper_weeks:
        return {
            "name": "taper_shape_consistency",
            "status": "not_applicable",
            "summary": "当前计划窗口未覆盖比赛周或 taper 周。",
            "details": {"taper_weeks": []},
        }

    flagged = []
    for week in taper_weeks:
        long_or_brick = [item for item in scheduled_items(week) if is_long_or_brick_anchor(item)]
        high_count = sum(1 for item in scheduled_items(week) if is_high(item))
        if len(long_or_brick) > 1 or high_count > 1:
            flagged.append(
                {
                    "week": week.get("week"),
                    "week_start": week.get("week_start"),
                    "long_or_brick_count": len(long_or_brick),
                    "high_count": high_count,
                }
            )
    return {
        "name": "taper_shape_consistency",
        "status": "pass" if not flagged else "needs_attention",
        "summary": "taper 周未发现明显堆量。" if not flagged else "taper 周仍有过多长课或高强度。",
        "details": {"flagged_weeks": flagged},
    }


def check_high_intensity_budget(weeks: list[dict[str, Any]]) -> dict[str, Any]:
    over_budget = []
    for week in weeks:
        assigned = int(week.get("assigned_high_intensity_count") or 0)
        budget = int(week.get("total_high_intensity_budget") or 0)
        actual = sum(1 for item in scheduled_items(week) if is_high(item))
        if assigned > budget or actual > budget:
            over_budget.append(
                {
                    "week": week.get("week"),
                    "week_start": week.get("week_start"),
                    "budget": budget,
                    "assigned": assigned,
                    "actual_high_like_items": actual,
                }
            )
    return {
        "name": "shared_high_intensity_budget_consistency",
        "status": "pass" if not over_budget else "needs_attention",
        "summary": "三项共享高强度预算未超标。" if not over_budget else "部分周高强度超过共享预算。",
        "details": {"over_budget_weeks": over_budget},
    }


def infer_profile_fit(weeks: list[dict[str, Any]]) -> dict[str, Any]:
    if not weeks:
        return {"profile_fit": "unknown", "confidence": "low", "reasons": ["no weeks"]}

    total_items = sum(len(scheduled_items(week)) for week in weeks)
    high_items = sum(1 for week in weeks for item in scheduled_items(week) if is_high(item))
    brick_items = sum(
        1
        for week in weeks
        for item in scheduled_items(week)
        if "brick" in " ".join(str(item.get(key) or "").lower() for key in ("sport", "slot_type", "role"))
    )
    avg_high = high_items / len(weeks)
    reasons = [
        f"avg_high_like_items_per_week={avg_high:.2f}",
        f"brick_items={brick_items}",
        f"scheduled_items={total_items}",
    ]
    if brick_items == 0 and avg_high <= 1.25:
        fit = "finish"
        confidence = "medium"
        reasons.append("conservative shape with no current brick block")
    elif brick_items > 0 and avg_high <= 1.25:
        fit = "finish"
        confidence = "medium"
        reasons.append("conservative finish shape with low-intensity brick specificity")
    elif brick_items > 0 and avg_high >= 1.0:
        fit = "advanced"
        confidence = "medium"
        reasons.append("brick and higher specificity are present")
    else:
        fit = "mixed"
        confidence = "low"
        reasons.append("signals are not cleanly finish or advanced")
    return {"profile_fit": fit, "confidence": confidence, "reasons": reasons}


def build_logic_check(
    profile_path: Path = DEFAULT_PROFILE,
    extract_path: Path = DEFAULT_EXTRACT,
    schedule_path: Path = DEFAULT_SCHEDULE,
) -> dict[str, Any]:
    profile = load_json(profile_path)
    coach_extract = load_json(extract_path)
    schedule = load_json(schedule_path)
    weeks = schedule.get("weekly_plan") or []
    checks = [
        check_schedule_shape(weeks),
        check_weekend_long_anchor(weeks, profile),
        check_brick_specificity(weeks, schedule),
        check_recovery_day(weeks, profile),
        check_taper_shape(weeks),
        check_high_intensity_budget(weeks),
    ]
    hard_failures = [check for check in checks if check.get("status") == "needs_attention"]
    notes = [check for check in checks if check.get("status") in {"pass_with_note", "watch", "not_applicable"}]
    return {
        "status": "logic_needs_attention" if hard_failures else "logic_consistent_with_notes" if notes else "logic_consistent",
        "schema_version": SCHEMA_VERSION,
        "source_profile": rel_path(profile_path),
        "source_coach_extract": rel_path(extract_path),
        "source_schedule": rel_path(schedule_path),
        "source_schedule_status": schedule.get("status", ""),
        "source_extract_schema_version": coach_extract.get("schema_version", ""),
        "use_boundary": profile.get("use_boundary", "logic_case_not_gold_standard"),
        "profile_fit": infer_profile_fit(weeks),
        "checks": checks,
        "summary": {
            "weeks_checked": len(weeks),
            "needs_attention": len(hard_failures),
            "notes": len(notes),
            "pass": sum(1 for check in checks if check.get("status") == "pass"),
        },
        "guardrails": [
            "This check validates schedule shape, not exact day-by-day equality.",
            "User availability and daily preflight override the historical coach-plan sample.",
            "Finish and advanced profiles change design bias; they do not bypass fatigue, pain, or high-intensity budget checks.",
        ],
    }


def build_markdown(report: dict[str, Any]) -> str:
    profile = report.get("profile_fit") or {}
    lines = [
        "# Ironman 226 Logic Check",
        "",
        f"- status: `{report.get('status')}`",
        f"- profile_fit: `{profile.get('profile_fit')}` ({profile.get('confidence')})",
        f"- weeks_checked: {((report.get('summary') or {}).get('weeks_checked', 0))}",
        f"- use_boundary: `{report.get('use_boundary')}`",
        "",
        "| Check | Status | Summary |",
        "| --- | --- | --- |",
    ]
    for check in report.get("checks") or []:
        lines.append(f"| {check.get('name')} | {check.get('status')} | {check.get('summary')} |")
    lines.extend(
        [
            "",
            "## Profile Fit Reasons",
            "",
        ]
    )
    for reason in profile.get("reasons") or []:
        lines.append(f"- {reason}")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "这不是逐日判卷。`needs_attention` 表示需要解释设计选择，尤其是用户时间安排导致的偏移；不是自动判定课表错误。",
            "",
        ]
    )
    return "\n".join(lines)


def print_summary(report: dict[str, Any]) -> None:
    profile = report.get("profile_fit") or {}
    print(f"status: {report.get('status')}")
    print(f"profile_fit: {profile.get('profile_fit')} ({profile.get('confidence')})")
    summary = report.get("summary") or {}
    print(
        f"checks: pass={summary.get('pass', 0)} "
        f"notes={summary.get('notes', 0)} needs_attention={summary.get('needs_attention', 0)}"
    )
    for check in report.get("checks") or []:
        print(f"- {check.get('status')} {check.get('name')}: {check.get('summary')}")


def main() -> int:
    args = parse_args()
    report = build_logic_check(args.profile, args.coach_extract, args.schedule)
    if args.write_output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_review:
        args.review_md.parent.mkdir(parents=True, exist_ok=True)
        args.review_md.write_text(build_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
        if args.write_output:
            print(f"output={args.output}")
        if args.write_review:
            print(f"review_md={args.review_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
