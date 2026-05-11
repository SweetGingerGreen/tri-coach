#!/usr/bin/env python3
"""
Coordinate bike, run, and swim weekday placement from the existing bike plan.

This is a week-level coordinator, not a detailed workout generator. It keeps
the current bike slots intact, adds run/swim placeholders, and checks the
shared recovery and high-intensity budget before any daily prescriptions are
allowed.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from pathlib import Path
from typing import Any

import bike_plan_generator


ROOT = Path(__file__).parent.resolve()
DEFAULT_INTAKE_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_intake_latest.json"
DEFAULT_BIKE_PLAN = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_latest.json"
DEFAULT_OUTPUT = ROOT / "triathlon-knowledge" / "metadata" / "triathlon_plan_orchestrator_latest.json"
DEFAULT_REVIEW_MD = ROOT / "triathlon-knowledge" / "metadata" / "triathlon_plan_orchestrator_review_latest.md"
DEFAULT_REVIEW_CSV = ROOT / "triathlon-knowledge" / "metadata" / "triathlon_plan_orchestrator_review_latest.csv"
DEFAULT_SOURCE_DB = ROOT / "triathlon-knowledge" / "metadata" / "vectors" / "triathlon_core_v2_bge_m3.sqlite"
SCHEMA_VERSION = "triathlon_plan_orchestrator_v0.1"
ALLOWED_BIKE_STATUSES = {"generated_weekly_type_allocation", "generated_weekly_type_candidate"}
DETAIL_BOUNDARY = "triathlon_weekday_slot_only_no_pace_distance_minutes_sets_reps_intervals"
HIGH_INTENSITY_CLASSES = {"high"}

ROLE_SOURCE_TERMS = {
    "Easy Run": ["easy run", "easy running", "recovery run", "E pace", "有氧", "轻松跑"],
    "Run Endurance": ["long run", "endurance run", "aerobic", "长期计划", "耐力"],
    "Run Quality Placeholder": ["threshold", "tempo", "interval", "quality", "阈值", "节奏跑"],
    "Brick Run Placeholder": ["brick", "bike ride followed immediately by a run", "transition", "骑跑", "下车"],
    "Swim Technique": ["drill", "technique", "Swimming Drill", "游泳技巧", "技术"],
    "Swim Endurance": ["endurance", "aerobic", "swim training", "有氧", "耐力"],
    "Swim Hard Placeholder": ["threshold", "race pace", "hard", "强度", "专项"],
}

REVIEW_EXPORT_COLUMNS = [
    "week",
    "week_start",
    "status",
    "total_high_intensity_budget",
    "assigned_high_intensity_count",
    "bike_days",
    "run_days",
    "swim_days",
    "brick_days",
    "high_days",
    "risk_flags",
    "scheduled_items",
    "source_chunk_ids",
    "review_boundary",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--intake", type=Path, default=DEFAULT_INTAKE_REPORT)
    parser.add_argument("--bike-plan", type=Path, default=DEFAULT_BIKE_PLAN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--review-md", type=Path, default=DEFAULT_REVIEW_MD)
    parser.add_argument("--review-csv", type=Path, default=DEFAULT_REVIEW_CSV)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--write-output", action="store_true")
    parser.add_argument("--write-review-files", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def as_number(value: Any) -> float:
    parsed = bike_plan_generator.as_number(value)
    return float(parsed or 0.0)


def day_set(rows: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("day")) for item in rows if item.get("day") in bike_plan_generator.DAYS}


def sorted_days(days: set[str] | list[str]) -> list[str]:
    valid_days = {day for day in days if day in bike_plan_generator.DAYS}
    return sorted(valid_days, key=bike_plan_generator.day_index)


def day_entries(days: set[str] | list[str]) -> list[dict[str, str]]:
    return [
        {"day": day, "day_label": bike_plan_generator.label_day(day)}
        for day in sorted_days(list(days))
    ]


def date_for_day(week_start: str, day: str) -> str:
    start = bike_plan_generator.parse_date(week_start)
    if not start or day not in bike_plan_generator.DAYS:
        return ""
    return (
        start
        + bike_plan_generator.dt.timedelta(days=bike_plan_generator.day_index(day))
    ).isoformat()


def compact_source_refs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = []
    seen = set()
    for item in items:
        for ref in item.get("source_refs") or []:
            chunk_id = ref.get("chunk_id")
            if not chunk_id or chunk_id in seen:
                continue
            seen.add(chunk_id)
            refs.append(
                {
                    "chunk_id": chunk_id,
                    "title": ref.get("title", ""),
                    "domain": ref.get("domain", ""),
                    "trust_level": ref.get("trust_level", ""),
                    "heading": ref.get("heading", ""),
                    "source_path": ref.get("source_path", ""),
                }
            )
    return refs


class SourceResolver:
    def __init__(self, db_path: Path, top_k: int = 1) -> None:
        self.db_path = db_path
        self.top_k = top_k
        self.records: list[dict[str, Any]] | None = None
        self.cache: dict[str, list[dict[str, Any]]] = {}
        self.warning = ""

    def load_records(self) -> list[dict[str, Any]]:
        if self.records is not None:
            return self.records
        if not self.db_path.exists():
            self.warning = f"source db not found: {self.db_path}"
            self.records = []
            return self.records
        try:
            conn = sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True)
            rows = conn.execute("SELECT chunk_id, text, metadata_json FROM chunks").fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.warning = f"source db unavailable: {exc}"
            self.records = []
            return self.records

        records = []
        for chunk_id, text, metadata_json in rows:
            meta = json.loads(metadata_json)
            if bike_plan_generator.is_backmatter(meta, text):
                continue
            records.append({"chunk_id": chunk_id, "text": text, "metadata": meta})
        self.records = records
        return self.records

    def refs_for_role(self, role: str, domain: str) -> list[dict[str, Any]]:
        key = f"{domain}:{role}"
        if key in self.cache:
            return self.cache[key]
        terms = ROLE_SOURCE_TERMS.get(role, [role])
        scored = []
        for record in self.load_records():
            meta = record["metadata"]
            haystack = "\n".join(
                [
                    str(meta.get("title") or ""),
                    str(meta.get("heading") or ""),
                    record["text"],
                ]
            ).lower()
            matched = [term for term in terms if term.lower() in haystack]
            if not matched:
                continue
            score = float(len(matched))
            if meta.get("domain") == domain:
                score += 4.0
            if meta.get("knowledge_tier") == "approved":
                score += 1.0
            heading = str(meta.get("heading") or "").lower()
            if any(term.lower() in heading for term in matched):
                score += 2.0
            scored.append((score, record, matched))

        scored.sort(key=lambda item: item[0], reverse=True)
        refs = []
        for score, record, matched in scored[: self.top_k]:
            meta = record["metadata"]
            refs.append(
                {
                    "chunk_id": record["chunk_id"],
                    "title": meta.get("title", ""),
                    "domain": meta.get("domain", ""),
                    "trust_level": meta.get("trust_level", ""),
                    "page": bike_plan_generator.page_label(meta),
                    "heading": meta.get("heading", ""),
                    "source_path": meta.get("source_path", ""),
                    "matched_terms": matched,
                    "score": round(score, 3),
                }
            )
        self.cache[key] = refs
        return refs

    def summary(self) -> dict[str, Any]:
        status = "available"
        if self.warning:
            status = "unavailable"
        elif self.records is not None and not self.records:
            status = "empty"
        return {
            "status": status,
            "method": "sqlite_lexical_chunk_refs_metadata_only",
            "source_db": rel_path(self.db_path),
            "warning": self.warning,
        }


def total_high_intensity_budget(week: dict[str, Any], intake: dict[str, Any]) -> int:
    workload_state = str(week.get("workload_state") or "")
    bike_high_budget = int(week.get("high_intensity_budget") or 0)
    bike_high_assigned = int(week.get("assigned_high_intensity_count") or 0)
    sports = bike_plan_generator.other_sports(intake)
    run_hours = as_number(sports.get("run_hours"))
    swim_hours = as_number(sports.get("swim_hours"))
    flags = (
        ((intake.get("plan_frame") or {}).get("intensity_budget") or {})
        .get("other_sports_load", {})
        .get("flags", [])
    )

    if workload_state == "deload":
        return max(bike_high_assigned, 0)
    if workload_state == "peaking":
        return max(bike_high_assigned, min(1, bike_high_budget))

    extra_cross_sport_high = 1 if run_hours >= 4 or swim_hours >= 4 else 0
    budget = min(2, max(1, bike_high_budget) + extra_cross_sport_high)
    if flags:
        budget = min(budget, max(1, bike_high_assigned))
    return max(budget, bike_high_assigned)


def bike_items(week: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in week.get("weekday_schedule") or []:
        day = item.get("day")
        if day not in bike_plan_generator.DAYS:
            continue
        rows.append(
            {
                "sport": "bike",
                "day": day,
                "day_label": bike_plan_generator.label_day(day),
                "date": item.get("date") or date_for_day(str(week.get("week_start") or ""), day),
                "slot_type": item.get("slot_type", ""),
                "role": item.get("workout_type", ""),
                "intensity_class": item.get("intensity_class", ""),
                "origin": "bike_plan",
                "source_refs": item.get("source_refs", []),
                "schedule_rule": item.get("schedule_rule", ""),
                "detail_boundary": DETAIL_BOUNDARY,
            }
        )
    return rows


def planned_run_roles(run_hours: float, can_add_high: bool) -> list[dict[str, str]]:
    if run_hours <= 0:
        return []
    roles = [{"role": "Easy Run", "slot_type": "easy", "intensity_class": "low"}]
    if run_hours >= 2.5:
        roles.append({"role": "Run Endurance", "slot_type": "endurance", "intensity_class": "low"})
    if run_hours >= 4.0:
        if can_add_high:
            roles.insert(0, {"role": "Run Quality Placeholder", "slot_type": "quality", "intensity_class": "high"})
        else:
            roles.append({"role": "Easy Run", "slot_type": "easy", "intensity_class": "low"})
    if run_hours >= 6.5:
        roles.append({"role": "Easy Run", "slot_type": "recovery", "intensity_class": "low"})
    return roles[:4]


def preferred_brick_day(bike_long_days: set[str], blocked_days: set[str]) -> str | None:
    for day in sorted_days(bike_long_days):
        if day not in blocked_days:
            return day
    return None


def convert_low_run_to_brick(
    items: list[dict[str, Any]],
    week: dict[str, Any],
    source_resolver: SourceResolver,
    *,
    bike_long_days: set[str],
    blocked_days: set[str],
) -> None:
    day = preferred_brick_day(bike_long_days, blocked_days)
    if not day:
        return
    candidates = [
        item for item in items
        if item.get("sport") == "run"
        and item.get("origin") == "generated_orchestrator"
        and item.get("intensity_class") != "high"
        and item.get("slot_type") in {"easy", "recovery", "endurance"}
    ]
    if not candidates:
        return

    def priority(item: dict[str, Any]) -> tuple[int, int]:
        slot_rank = {"easy": 0, "recovery": 1, "endurance": 2}
        return (slot_rank.get(str(item.get("slot_type")), 9), day_index_distance(item.get("day"), day))

    target = sorted(candidates, key=priority)[0]
    target["converted_from"] = {
        "sport": "run",
        "day": target.get("day"),
        "day_label": target.get("day_label"),
        "slot_type": target.get("slot_type"),
        "role": target.get("role"),
    }
    target.update(
        {
            "sport": "brick",
            "day": day,
            "day_label": bike_plan_generator.label_day(day),
            "date": date_for_day(str(week.get("week_start") or ""), day),
            "slot_type": "brick",
            "role": "Brick Run Placeholder",
            "intensity_class": "low",
            "source_refs": source_resolver.refs_for_role("Brick Run Placeholder", "race"),
            "schedule_rule": "把一节低强度跑步挂到长骑后形成 brick 占位；不新增总训练次数，不生成配速、距离或间歇。",
        }
    )


def day_index_distance(left: Any, right: Any) -> int:
    if left not in bike_plan_generator.DAYS or right not in bike_plan_generator.DAYS:
        return 99
    return abs(bike_plan_generator.day_index(str(left)) - bike_plan_generator.day_index(str(right)))


def planned_swim_roles(swim_hours: float, can_add_high: bool) -> list[dict[str, str]]:
    if swim_hours <= 0:
        return []
    roles = [{"role": "Swim Technique", "slot_type": "technique", "intensity_class": "low"}]
    if swim_hours >= 2.0:
        roles.append({"role": "Swim Endurance", "slot_type": "endurance", "intensity_class": "low"})
    if swim_hours >= 4.0 and can_add_high:
        roles.append({"role": "Swim Hard Placeholder", "slot_type": "quality", "intensity_class": "high"})
    elif swim_hours >= 4.0:
        roles.append({"role": "Swim Technique", "slot_type": "technique", "intensity_class": "low"})
    return roles[:3]


def day_loads(items: list[dict[str, Any]]) -> dict[str, int]:
    loads = {day: 0 for day in bike_plan_generator.DAYS}
    for item in items:
        day = item.get("day")
        if day in loads:
            loads[day] += 1
    return loads


def high_days(items: list[dict[str, Any]]) -> set[str]:
    return {
        item.get("day")
        for item in items
        if item.get("day") in bike_plan_generator.DAYS
        and item.get("intensity_class") in HIGH_INTENSITY_CLASSES
    }


def choose_day_for_role(
    role: dict[str, str],
    sport: str,
    items: list[dict[str, Any]],
    *,
    blocked_days: set[str],
    run_hard_avoid: set[str],
    swim_hard_caution: set[str],
    bike_long_days: set[str],
) -> str | None:
    loads = day_loads(items)
    current_high_days = high_days(items)
    high = role.get("intensity_class") == "high"
    if sport == "run" and high:
        preferred = ["Wednesday", "Tuesday", "Thursday", "Friday", "Saturday"]
    elif sport == "run":
        preferred = ["Tuesday", "Thursday", "Saturday", "Wednesday", "Friday", "Sunday"]
    elif sport == "swim" and high:
        preferred = ["Friday", "Wednesday", "Thursday", "Tuesday", "Saturday"]
    else:
        preferred = ["Monday", "Wednesday", "Friday", "Tuesday", "Thursday", "Saturday", "Sunday"]

    def score(day: str) -> int:
        value = loads.get(day, 0) * 10
        if day in blocked_days:
            value += 1000
        if high and day in current_high_days:
            value += 500
        if sport == "run" and high and day in run_hard_avoid:
            value += 400
        if sport == "swim" and high and day in swim_hard_caution:
            value += 200
        if sport == "run" and not high and day in bike_long_days:
            value += 40
        for offset, preferred_day in enumerate(preferred):
            if day == preferred_day:
                value += offset
                break
        else:
            value += 30
        return value

    candidates = [day for day in bike_plan_generator.DAYS if day not in blocked_days]
    if not candidates:
        return None
    candidates.sort(key=score)
    return candidates[0]


def make_cross_item(
    *,
    sport: str,
    role: str,
    slot_type: str,
    intensity_class: str,
    day: str,
    week: dict[str, Any],
    origin: str,
    source_resolver: SourceResolver,
    schedule_rule: str,
) -> dict[str, Any]:
    return {
        "sport": sport,
        "day": day,
        "day_label": bike_plan_generator.label_day(day),
        "date": date_for_day(str(week.get("week_start") or ""), day),
        "slot_type": slot_type,
        "role": role,
        "intensity_class": intensity_class,
        "origin": origin,
        "source_refs": source_resolver.refs_for_role(role, sport),
        "schedule_rule": schedule_rule,
        "detail_boundary": DETAIL_BOUNDARY,
    }


def add_fixed_cross_sport_items(
    items: list[dict[str, Any]],
    week: dict[str, Any],
    intake: dict[str, Any],
    source_resolver: SourceResolver,
) -> None:
    sports = bike_plan_generator.other_sports(intake)
    fixed_run = bike_plan_generator.fixed_day_entries(sports, "fixed_run_hard_days")
    fixed_swim = bike_plan_generator.fixed_day_entries(sports, "fixed_swim_hard_days")
    for entry in fixed_run:
        items.append(
            make_cross_item(
                sport="run",
                role="Run Quality Placeholder",
                slot_type="quality",
                intensity_class="high",
                day=entry["day"],
                week=week,
                origin="fixed_user_input",
                source_resolver=source_resolver,
                schedule_rule="用户已固定跑步 hard 日；协调器只做冲突复核，不生成配速或内容。",
            )
        )

    for entry in fixed_swim:
        items.append(
            make_cross_item(
                sport="swim",
                role="Swim Hard Placeholder",
                slot_type="quality",
                intensity_class="high",
                day=entry["day"],
                week=week,
                origin="fixed_user_input",
                source_resolver=source_resolver,
                schedule_rule="用户已固定游泳 hard 日；协调器只做冲突复核，不生成组数或内容。",
            )
        )


def add_generated_cross_sport_items(
    items: list[dict[str, Any]],
    week: dict[str, Any],
    intake: dict[str, Any],
    source_resolver: SourceResolver,
    risk_flags: list[dict[str, Any]],
) -> None:
    sports = bike_plan_generator.other_sports(intake)
    run_hours = as_number(sports.get("run_hours"))
    swim_hours = as_number(sports.get("swim_hours"))
    placeholders = week.get("cross_sport_placeholders") or {}
    run_hard_avoid = day_set(placeholders.get("run_hard_avoid_days") or [])
    swim_hard_caution = day_set(placeholders.get("swim_hard_caution_days") or [])
    bike_long_days = day_set(placeholders.get("bike_long_days") or [])
    blocked_days = bike_plan_generator.unavailable_days(intake)
    budget = total_high_intensity_budget(week, intake)
    fixed_high = len(high_days(items))
    can_add_run_high = fixed_high < budget
    run_roles = planned_run_roles(run_hours, can_add_run_high)

    for role in run_roles:
        if role["intensity_class"] == "high" and len(high_days(items)) >= budget:
            continue
        day = choose_day_for_role(
            role,
            "run",
            items,
            blocked_days=blocked_days,
            run_hard_avoid=run_hard_avoid,
            swim_hard_caution=swim_hard_caution,
            bike_long_days=bike_long_days,
        )
        if not day:
            risk_flags.append(
                {
                    "type": "unable_to_place_run_slot",
                    "severity": "high",
                    "message": "没有可用日期放置跑步占位。",
                }
            )
            continue
        items.append(
            make_cross_item(
                sport="run",
                role=role["role"],
                slot_type=role["slot_type"],
                intensity_class=role["intensity_class"],
                day=day,
                week=week,
                origin="generated_orchestrator",
                source_resolver=source_resolver,
                schedule_rule="按三项共享恢复预算放置；只生成课型占位，不生成配速、距离或间歇。",
            )
        )

    convert_low_run_to_brick(
        items,
        week,
        source_resolver,
        bike_long_days=bike_long_days,
        blocked_days=blocked_days,
    )

    can_add_swim_high = len(high_days(items)) < budget
    swim_roles = planned_swim_roles(swim_hours, can_add_swim_high)
    for role in swim_roles:
        if role["intensity_class"] == "high" and len(high_days(items)) >= budget:
            continue
        day = choose_day_for_role(
            role,
            "swim",
            items,
            blocked_days=blocked_days,
            run_hard_avoid=run_hard_avoid,
            swim_hard_caution=swim_hard_caution,
            bike_long_days=bike_long_days,
        )
        if not day:
            risk_flags.append(
                {
                    "type": "unable_to_place_swim_slot",
                    "severity": "high",
                    "message": "没有可用日期放置游泳占位。",
                }
            )
            continue
        items.append(
            make_cross_item(
                sport="swim",
                role=role["role"],
                slot_type=role["slot_type"],
                intensity_class=role["intensity_class"],
                day=day,
                week=week,
                origin="generated_orchestrator",
                source_resolver=source_resolver,
                schedule_rule="按三项共享恢复预算放置；只生成课型占位，不生成组数、距离或间歇。",
            )
        )


def sorted_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sport_order = {"swim": 0, "bike": 1, "brick": 2, "run": 3, "strength": 4}
    return sorted(
        items,
        key=lambda item: (
            bike_plan_generator.day_index(item.get("day")),
            sport_order.get(item.get("sport"), 9),
            item.get("role", ""),
        ),
    )


def risk(
    risk_type: str,
    severity: str,
    message: str,
    item: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = {"type": risk_type, "severity": severity, "message": message}
    if item:
        row.update(
            {
                "sport": item.get("sport"),
                "day": item.get("day"),
                "day_label": item.get("day_label"),
                "date": item.get("date"),
                "role": item.get("role"),
            }
        )
    if details:
        row["details"] = details
    return row


def detect_risks(
    week: dict[str, Any],
    intake: dict[str, Any],
    items: list[dict[str, Any]],
    seeded_risks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    risks = list(seeded_risks)
    placeholders = week.get("cross_sport_placeholders") or {}
    run_hard_avoid = day_set(placeholders.get("run_hard_avoid_days") or [])
    swim_hard_caution = day_set(placeholders.get("swim_hard_caution_days") or [])
    blocked_days = bike_plan_generator.unavailable_days(intake)
    high_by_day: dict[str, list[dict[str, Any]]] = {}

    for item in items:
        day = item.get("day")
        if day in blocked_days:
            risks.append(risk("scheduled_on_unavailable_day", "high", "安排落在不可训练日。", item))
        if item.get("intensity_class") == "high":
            high_by_day.setdefault(day, []).append(item)
            if item.get("sport") == "run" and day in run_hard_avoid:
                risks.append(risk("run_hard_on_avoid_day", "high", "跑步 hard 落在 bike hard/long 保护窗口。", item))
            if item.get("sport") == "swim" and day in swim_hard_caution:
                risks.append(risk("swim_hard_on_caution_day", "medium", "游泳 hard 落在 bike hard 相关谨慎日。", item))

    for day, rows in high_by_day.items():
        if len(rows) > 1:
            risks.append(
                risk(
                    "multiple_high_intensity_same_day",
                    "high",
                    "同一天出现多个高强度占位，需要人工复核。",
                    rows[0],
                    {"sports": [row.get("sport") for row in rows]},
                )
            )

    budget = total_high_intensity_budget(week, intake)
    assigned_high = sum(1 for item in items if item.get("intensity_class") == "high")
    if assigned_high > budget:
        risks.append(
            risk(
                "total_high_intensity_budget_exceeded",
                "high",
                "三项总高强度占位超过本周共享预算。",
                details={"budget": budget, "assigned": assigned_high},
            )
        )

    for conflict in placeholders.get("cross_sport_conflicts") or []:
        risks.append(
            {
                "type": "bike_plan_cross_sport_conflict",
                "severity": conflict.get("severity", "medium"),
                "message": "继承自 bike plan 的固定跨项冲突。",
                "details": conflict,
            }
        )
    return risks


def week_summary(week: dict[str, Any], items: list[dict[str, Any]], risks: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "week": week.get("week"),
        "week_start": week.get("week_start"),
        "status": "needs_attention" if risks else "ok",
        "total_high_intensity_budget": week.get("total_high_intensity_budget"),
        "assigned_high_intensity_count": sum(1 for item in items if item.get("intensity_class") == "high"),
        "sports_days": {
            "bike": day_entries([item.get("day") for item in items if item.get("sport") == "bike"]),
            "run": day_entries([item.get("day") for item in items if item.get("sport") == "run"]),
            "swim": day_entries([item.get("day") for item in items if item.get("sport") == "swim"]),
            "brick": day_entries([item.get("day") for item in items if item.get("sport") == "brick"]),
        },
        "high_days": day_entries([item.get("day") for item in items if item.get("intensity_class") == "high"]),
        "risk_flags": risks,
        "source_refs": compact_source_refs(items),
        "review_boundary": "human_review_only_no_pace_distance_minutes_sets_reps_intervals",
    }


def build_week(
    week: dict[str, Any],
    intake: dict[str, Any],
    source_resolver: SourceResolver,
) -> dict[str, Any]:
    items = bike_items(week)
    risk_flags: list[dict[str, Any]] = []
    add_fixed_cross_sport_items(items, week, intake, source_resolver)
    add_generated_cross_sport_items(items, week, intake, source_resolver, risk_flags)
    items = sorted_items(items)
    week = {**week, "total_high_intensity_budget": total_high_intensity_budget(week, intake)}
    risks = detect_risks(week, intake, items, risk_flags)
    summary = week_summary(week, items, risks)
    return {
        "week": week.get("week"),
        "week_start": week.get("week_start"),
        "phase_hint": week.get("phase_hint", ""),
        "workload_state": week.get("workload_state", ""),
        "total_high_intensity_budget": week.get("total_high_intensity_budget"),
        "assigned_high_intensity_count": summary["assigned_high_intensity_count"],
        "scheduled_items": items,
        "week_review": summary,
        "guardrails": [
            "这是三项周内排布协调，不是每日训练处方。",
            "跑步不输出配速、距离或间歇；游泳不输出组数、距离或间歇。",
            "进入每日课表前还必须读取 daily availability、疲劳、疼痛和固定训练变更。",
        ],
    }


def build_review_view(weeks: list[dict[str, Any]], intake: dict[str, Any]) -> dict[str, Any]:
    sports = bike_plan_generator.other_sports(intake)
    rows = [week.get("week_review") or {} for week in weeks]
    return {
        "status": "needs_attention" if any(row.get("risk_flags") for row in rows) else "ready_for_human_review",
        "schema_version": SCHEMA_VERSION,
        "review_boundary": "summary_only_no_pace_distance_minutes_sets_reps_intervals",
        "other_sports_input": {
            "run_hours": as_number(sports.get("run_hours")),
            "swim_hours": as_number(sports.get("swim_hours")),
            "strength_sessions": int(bike_plan_generator.as_int(sports.get("strength_sessions")) or 0),
            "fixed_run_hard_days": bike_plan_generator.fixed_day_entries(sports, "fixed_run_hard_days"),
            "fixed_swim_hard_days": bike_plan_generator.fixed_day_entries(sports, "fixed_swim_hard_days"),
            "fixed_strength_lower_body_days": bike_plan_generator.fixed_day_entries(
                sports,
                "fixed_strength_lower_body_days",
            ),
        },
        "blocked_days": day_entries(bike_plan_generator.unavailable_days(intake)),
        "rows": rows,
    }


def build_blocked_result(reason: str, intake_path: Path, bike_plan_path: Path) -> dict[str, Any]:
    return {
        "status": "blocked_by_bike_plan",
        "schema_version": SCHEMA_VERSION,
        "source_intake_report": rel_path(intake_path),
        "source_bike_plan_report": rel_path(bike_plan_path),
        "failures": [reason],
        "weekly_plan": [],
        "review_view": {
            "status": "not_attempted",
            "schema_version": SCHEMA_VERSION,
            "review_boundary": "bike_plan_blocked",
            "rows": [],
        },
    }


def build_orchestrator(
    intake_path: Path = DEFAULT_INTAKE_REPORT,
    bike_plan_path: Path = DEFAULT_BIKE_PLAN,
    source_db: Path = DEFAULT_SOURCE_DB,
) -> dict[str, Any]:
    intake = load_json(intake_path)
    bike_plan = load_json(bike_plan_path)
    if not intake:
        return build_blocked_result("intake report is missing or empty", intake_path, bike_plan_path)
    if bike_plan.get("status") not in ALLOWED_BIKE_STATUSES:
        return build_blocked_result(
            f"bike plan status must be one of {sorted(ALLOWED_BIKE_STATUSES)}, got {bike_plan.get('status')}",
            intake_path,
            bike_plan_path,
        )
    source_resolver = SourceResolver(source_db)
    weeks = [
        build_week(week, intake, source_resolver)
        for week in bike_plan.get("weekly_plan") or []
    ]
    review_view = build_review_view(weeks, intake)
    risk_count = sum(len((week.get("week_review") or {}).get("risk_flags") or []) for week in weeks)
    return {
        "status": "triathlon_schedule_needs_attention" if risk_count else "triathlon_schedule_generated",
        "schema_version": SCHEMA_VERSION,
        "source_intake_report": rel_path(intake_path),
        "source_bike_plan_report": rel_path(bike_plan_path),
        "source_bike_plan_schema_version": bike_plan.get("schema_version", ""),
        "source_grounding": source_resolver.summary(),
        "athlete_id": (intake.get("normalized") or {}).get("athlete_id", "default"),
        "goal": (intake.get("normalized") or {}).get("goal", {}),
        "weekly_plan": weeks,
        "review_view": review_view,
        "summary": {
            "weeks_total": len(weeks),
            "weeks_needing_attention": sum(
                1 for week in weeks if (week.get("week_review") or {}).get("risk_flags")
            ),
            "risk_count": risk_count,
        },
        "global_guardrails": [
            "三项总课表协调器只排周内课型占位，不输出每日训练细节。",
            "所有 bike/run/swim 高强度共用同一个周预算。",
            "固定跑步或游泳 hard 会保留，但一旦冲突就标记人工复核。",
            "下一层应接三项 daily preflight，再考虑生成每日课表草案。",
        ],
    }


def format_days(rows: list[dict[str, Any]]) -> str:
    return "; ".join(row.get("day_label", "") for row in rows) if rows else "-"


def format_scheduled_items(items: list[dict[str, Any]]) -> str:
    parts = []
    for item in items:
        parts.append(
            " ".join(
                str(part)
                for part in (
                    item.get("day_label"),
                    item.get("sport"),
                    item.get("slot_type"),
                    item.get("role"),
                )
                if part
            )
        )
    return "; ".join(parts) if parts else "-"


def format_risks(risks: list[dict[str, Any]]) -> str:
    if not risks:
        return "-"
    return "; ".join(
        " ".join(str(part) for part in (risk.get("severity"), risk.get("type"), risk.get("day_label")) if part)
        for risk in risks
    )


def format_source_chunk_ids(refs: list[dict[str, Any]]) -> str:
    chunk_ids = [str(ref.get("chunk_id")) for ref in refs if ref.get("chunk_id")]
    return "; ".join(chunk_ids) if chunk_ids else "-"


def build_review_export_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    weeks_by_number = {week.get("week"): week for week in result.get("weekly_plan") or []}
    for row in (result.get("review_view") or {}).get("rows") or []:
        week = weeks_by_number.get(row.get("week")) or {}
        sports_days = row.get("sports_days") or {}
        rows.append(
            {
                "week": row.get("week", ""),
                "week_start": row.get("week_start", ""),
                "status": row.get("status", ""),
                "total_high_intensity_budget": row.get("total_high_intensity_budget", ""),
                "assigned_high_intensity_count": row.get("assigned_high_intensity_count", ""),
                "bike_days": format_days(sports_days.get("bike") or []),
                "run_days": format_days(sports_days.get("run") or []),
                "swim_days": format_days(sports_days.get("swim") or []),
                "brick_days": format_days(sports_days.get("brick") or []),
                "high_days": format_days(row.get("high_days") or []),
                "risk_flags": format_risks(row.get("risk_flags") or []),
                "scheduled_items": format_scheduled_items(week.get("scheduled_items") or []),
                "source_chunk_ids": format_source_chunk_ids(row.get("source_refs") or []),
                "review_boundary": row.get("review_boundary", ""),
            }
        )
    return rows


def build_review_markdown(result: dict[str, Any]) -> str:
    lines = [
        "# Triathlon Plan Orchestrator Review",
        "",
        f"- status: {result.get('status', '')}",
        f"- schema_version: {result.get('schema_version', '')}",
        f"- source_intake_report: {result.get('source_intake_report', '')}",
        f"- source_bike_plan_report: {result.get('source_bike_plan_report', '')}",
        "- Human review export only. No pace, distance, minutes, sets, reps, or intervals.",
        "",
        "| Week | Start | Status | High Budget | Assigned High | Bike | Run | Swim | Brick | Risks |",
        "| --- | --- | --- | ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in build_review_export_rows(result):
        lines.append(
            "| {week} | {week_start} | {status} | {total_high_intensity_budget} | "
            "{assigned_high_intensity_count} | {bike_days} | {run_days} | {swim_days} | {brick_days} | {risk_flags} |".format(**row)
        )
    return "\n".join(lines) + "\n"


def write_review_files(result: dict[str, Any], review_md: Path, review_csv: Path) -> None:
    review_md.parent.mkdir(parents=True, exist_ok=True)
    review_md.write_text(build_review_markdown(result), encoding="utf-8")
    write_csv(review_csv, REVIEW_EXPORT_COLUMNS, build_review_export_rows(result))


def print_markdown(result: dict[str, Any]) -> None:
    summary = result.get("summary") or {}
    print(f"status: {result.get('status')}")
    print(f"schema_version: {result.get('schema_version')}")
    print(
        f"weeks: total={summary.get('weeks_total', 0)} "
        f"attention={summary.get('weeks_needing_attention', 0)} risks={summary.get('risk_count', 0)}"
    )
    for week in result.get("weekly_plan") or []:
        review = week.get("week_review") or {}
        print(
            f"- week {week.get('week')} {week.get('week_start')}: "
            f"{review.get('status')} high={week.get('assigned_high_intensity_count')}/"
            f"{week.get('total_high_intensity_budget')} items={len(week.get('scheduled_items') or [])}"
        )


def main() -> int:
    args = parse_args()
    result = build_orchestrator(args.intake, args.bike_plan, args.source_db)
    if args.write_output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_review_files:
        write_review_files(result, args.review_md, args.review_csv)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_markdown(result)
        if args.write_output:
            print(f"output={args.output}")
        if args.write_review_files:
            print(f"review_md={args.review_md}")
            print(f"review_csv={args.review_csv}")
    return 0 if result.get("status") in {"triathlon_schedule_generated", "triathlon_schedule_needs_attention"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
