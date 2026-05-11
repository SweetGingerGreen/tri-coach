#!/usr/bin/env python3
"""
Generate week-level bike workout type allocation from a validated plan frame.

This is the application-layer bike plan generator. It intentionally does
not create daily workouts, exact watts, intervals, or minute-by-minute sessions.
It only assigns weekly workout roles so the next step can be reviewed safely.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent.resolve()
DEFAULT_INPUT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_intake_latest.json"
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_generator_latest.json"
DEFAULT_REVIEW_MD = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_latest.md"
DEFAULT_REVIEW_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_latest.csv"
DEFAULT_SOURCE_DB = ROOT / "triathlon-knowledge" / "metadata" / "vectors" / "triathlon_core_v2_bge_m3.sqlite"
SCHEMA_VERSION = "bike_plan_generator_v0.11"
INTAKE_READY_STATUS = "ready_for_plan_frame"
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
DAY_ALIASES = {
    "monday": "Monday",
    "mon": "Monday",
    "周一": "Monday",
    "星期一": "Monday",
    "tuesday": "Tuesday",
    "tue": "Tuesday",
    "周二": "Tuesday",
    "星期二": "Tuesday",
    "wednesday": "Wednesday",
    "wed": "Wednesday",
    "周三": "Wednesday",
    "星期三": "Wednesday",
    "thursday": "Thursday",
    "thu": "Thursday",
    "周四": "Thursday",
    "星期四": "Thursday",
    "friday": "Friday",
    "fri": "Friday",
    "周五": "Friday",
    "星期五": "Friday",
    "saturday": "Saturday",
    "sat": "Saturday",
    "周六": "Saturday",
    "星期六": "Saturday",
    "sunday": "Sunday",
    "sun": "Sunday",
    "周日": "Sunday",
    "周天": "Sunday",
    "星期日": "Sunday",
    "星期天": "Sunday",
}
ROLE_SOURCE_TERMS = {
    "Endurance Ride": [
        "Endurance Ride",
        "endurance rides",
        "develop the body's ability to consume oxygen",
        "conserve carbohydrate",
    ],
    "Long Ride": ["Long Ride", "weekly or biweekly long ride", "long ride at endurance intensity"],
    "Cadence Workout": ["Cadence Workout", "cadence"],
    "Power Intervals": ["Power Intervals", "Sprints", "sprints"],
    "Threshold Ride": ["Threshold Ride", "above-threshold", "threshold"],
    "Brick": ["brick", "bike ride followed immediately by a run", "high-intensity workouts"],
}
REVIEW_EXPORT_COLUMNS = [
    "week",
    "week_start",
    "phase_hint",
    "workload_state",
    "bike_days_budget",
    "bike_hours_budget",
    "scheduled_days",
    "hard_summary",
    "long_summary",
    "run_hard_avoid_days",
    "swim_hard_caution_days",
    "strength_lower_body_avoid_days",
    "cross_sport_conflicts",
    "attention_flags",
    "review_status",
    "human_review_status",
    "review_comment",
    "override_request",
    "move_slot",
    "blocked_day",
    "protect_day",
    "source_chunk_ids",
    "review_boundary",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--review-md", type=Path, default=DEFAULT_REVIEW_MD)
    parser.add_argument("--review-csv", type=Path, default=DEFAULT_REVIEW_CSV)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE_DB)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--write-review-files", action="store_true")
    return parser.parse_args()


def load_json(path: Path | None) -> dict[str, Any]:
    if path:
        return json.loads(path.read_text(encoding="utf-8"))
    text = sys.stdin.read().strip()
    if not text:
        raise ValueError("input JSON is required")
    return json.loads(text)


def as_number(value: Any) -> float | None:
    if value in (None, "", "null"):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def as_int(value: Any) -> int | None:
    number = as_number(value)
    if number is None:
        return None
    return int(number)


def parse_date(value: Any) -> dt.date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        return None


def forbidden_detail_keys() -> set[str]:
    return {
        "daily_workouts",
        "sessions",
        "workouts",
        "watts",
        "target_watts",
        "minutes",
        "intervals",
        "sets",
        "reps",
    }


def normalize_day(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    lowered = text.lower()
    if lowered in DAY_ALIASES:
        return DAY_ALIASES[lowered]
    for alias, day in DAY_ALIASES.items():
        if alias in text or alias in lowered:
            return day
    return None


def day_index(day: str) -> int:
    return DAYS.index(day)


def day_distance(left: str, right: str) -> int:
    return abs(day_index(left) - day_index(right))


def label_day(day: str) -> str:
    return DAY_LABELS.get(day, day)


def day_entries(days: list[str] | set[str]) -> list[dict[str, str]]:
    return [
        {"day": day, "day_label": label_day(day)}
        for day in sorted(set(days), key=day_index)
    ]


def add_day_reason(day_reasons: dict[str, list[str]], day: str, reason: str) -> None:
    day_reasons.setdefault(day, [])
    if reason not in day_reasons[day]:
        day_reasons[day].append(reason)


def adjacent_window(day: str, before: int = 1, after: int = 1) -> list[str]:
    index = day_index(day)
    low = max(index - before, 0)
    high = min(index + after, len(DAYS) - 1)
    return DAYS[low : high + 1]


def availability(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = payload.get("normalized") or {}
    return normalized.get("availability") or {}


def unavailable_days(payload: dict[str, Any]) -> set[str]:
    blocked = set()
    markers = ("不能", "不可", "不训练", "无法", "休息", "no ", "not ", "unavailable", "off")
    for constraint in availability(payload).get("constraints") or []:
        text = str(constraint)
        lowered = text.lower()
        if not any(marker in lowered or marker in text for marker in markers):
            continue
        day = normalize_day(text)
        if day:
            blocked.add(day)
    return blocked


def preferred_long_day(payload: dict[str, Any]) -> str:
    day = normalize_day(availability(payload).get("long_ride_day"))
    return day or "Sunday"


def choose_day(
    preferred: list[str],
    used_days: set[str],
    blocked_days: set[str],
    *,
    avoid_adjacent_to: set[str] | None = None,
) -> str | None:
    avoid_adjacent_to = avoid_adjacent_to or set()
    available = [day for day in DAYS if day not in used_days and day not in blocked_days]
    if not available:
        return None

    def allowed(day: str) -> bool:
        return all(day_distance(day, other) > 1 for other in avoid_adjacent_to)

    for day in preferred:
        if day in available and allowed(day):
            return day
    for day in available:
        if allowed(day):
            return day
    return available[0]


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def page_label(meta: dict[str, Any]) -> str:
    page_start = meta.get("page_start")
    page_end = meta.get("page_end")
    if not page_start:
        return ""
    if page_end and page_end != page_start:
        return f"p.{page_start}-{page_end}"
    return f"p.{page_start}"


def is_backmatter(meta: dict[str, Any], text: str) -> bool:
    heading = str(meta.get("heading") or "").strip().upper()
    text_start = text[:500]
    if heading in {"INDEX", "REFERENCES", "REFERENCE", "BIBLIOGRAPHY", "GLOSSARY"}:
        return True
    return "Index" in text_start[:80] or "References" in text_start[:80]


class SourceResolver:
    def __init__(self, db_path: Path, top_k: int = 1) -> None:
        self.db_path = db_path
        self.top_k = top_k
        self.cache: dict[str, list[dict[str, Any]]] = {}
        self.records: list[dict[str, Any]] | None = None
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
            conn.execute("PRAGMA busy_timeout = 5000")
            rows = conn.execute(
                "SELECT chunk_id, text, metadata_json FROM chunks"
            ).fetchall()
            conn.close()
        except sqlite3.Error as exc:
            self.warning = f"source db unavailable: {exc}"
            self.records = []
            return self.records

        records = []
        for chunk_id, text, metadata_json in rows:
            meta = json.loads(metadata_json)
            if is_backmatter(meta, text):
                continue
            records.append({"chunk_id": chunk_id, "text": text, "metadata": meta})
        self.records = records
        return self.records

    def refs_for_role(self, role_type: str) -> list[dict[str, Any]]:
        if role_type in self.cache:
            return self.cache[role_type]

        terms = ROLE_SOURCE_TERMS.get(role_type, [role_type])
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
            heading = str(meta.get("heading") or "").lower()
            title = str(meta.get("title") or "").lower()
            if role_type.lower() in heading:
                score += 4.0
            if "complete triathlon book" in title:
                score += 1.0
            if meta.get("domain") == "bike":
                score += 3.0
            if meta.get("knowledge_tier") == "approved":
                score += 1.0
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
                    "page": page_label(meta),
                    "heading": meta.get("heading", ""),
                    "source_path": meta.get("source_path", ""),
                    "matched_terms": matched,
                    "score": round(score, 3),
                }
            )
        self.cache[role_type] = refs
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


def validate_intake(payload: dict[str, Any]) -> list[str]:
    failures = []
    if payload.get("status") != INTAKE_READY_STATUS:
        failures.append("intake status is not ready_for_plan_frame")
    if payload.get("missing_data"):
        failures.append("intake still has missing_data")
    if payload.get("warnings"):
        failures.append("intake has warnings that require review before generation")
    if not isinstance(payload.get("plan_frame"), dict):
        failures.append("plan_frame is missing")
    return failures


def weekly_days(payload: dict[str, Any]) -> int:
    normalized = payload.get("normalized") or {}
    availability = normalized.get("availability") or {}
    days = as_int(availability.get("weekly_bike_days"))
    if days is None or days <= 0:
        return 3
    return min(max(days, 1), 6)


def weekly_hours(payload: dict[str, Any]) -> float:
    normalized = payload.get("normalized") or {}
    availability = normalized.get("availability") or {}
    hours = as_number(availability.get("weekly_bike_hours"))
    return hours if hours is not None and hours > 0 else 0.0


def max_high_intensity(payload: dict[str, Any]) -> int:
    plan_frame = payload.get("plan_frame") or {}
    budget = plan_frame.get("intensity_budget") or {}
    value = as_int(budget.get("max_bike_high_intensity_sessions_per_week"))
    return max(value or 0, 0)


def volume_level(hours: float, workload_state: str) -> int:
    if hours <= 0:
        base = 1
    elif hours < 4:
        base = 3
    elif hours < 7:
        base = 5
    elif hours < 10:
        base = 7
    else:
        base = 8
    if workload_state == "load_plus":
        base += 1
    elif workload_state in {"deload", "peaking"}:
        base -= 2
    return min(max(base, 1), 10)


def intensity_level(workload_state: str, phase: str, high_budget: int) -> int:
    if high_budget <= 0:
        base = 2
    elif phase == "base":
        base = 3
    elif phase == "build":
        base = 5
    elif phase == "specific_build":
        base = 6
    else:
        base = 4
    if workload_state == "load_plus":
        base += 1
    elif workload_state == "deload":
        base -= 2
    elif workload_state == "peaking":
        base -= 1
    return min(max(base, 1), 10)


def role(role_type: str, priority: str, purpose: str, intensity: str = "low") -> dict[str, Any]:
    return {
        "type": role_type,
        "priority": priority,
        "intensity_class": intensity,
        "purpose": purpose,
    }


def base_roles(days: int, workload_state: str) -> list[dict[str, Any]]:
    roles = [
        role(
            "Endurance Ride",
            "primary",
            "耐力骑作为周内基础，维持有氧能力和骑行一致性。",
            "low",
        )
    ]
    if days >= 2:
        roles.append(
            role(
                "Long Ride",
                "primary",
                "长骑放在本周最宽裕的一天，作为长距离骑行耐力底座。",
                "low",
            )
        )
    if days >= 3 and workload_state != "deload":
        roles.append(
            role(
                "Cadence Workout",
                "support",
                "踏频练习用于技术和经济性，不作为主要高强度。",
                "low",
            )
        )
    return roles


def high_intensity_role(phase: str, workload_state: str) -> dict[str, Any] | None:
    if workload_state in {"deload", "peaking"}:
        return None
    if phase == "base":
        return role(
            "Power Intervals",
            "optional",
            "基础期只做少量短促神经激活或力量感刺激，不能替代耐力骑。",
            "high",
        )
    if phase == "build":
        return role(
            "Threshold Ride",
            "primary",
            "build 阶段可逐步引入阈值骑，但仍受一周总强度预算限制。",
            "high",
        )
    if phase == "specific_build":
        return role(
            "Brick",
            "primary",
            "专项阶段可安排骑跑转换；如果骑跑都含强度，计入周总高强度。",
            "high",
        )
    return role(
        "Endurance Ride",
        "primary",
        "临近比赛阶段保留熟悉强度，避免堆叠疲劳。",
        "low",
    )


def deload_adjustments(roles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    adjusted = []
    for item in roles:
        if item["type"] in {"Power Intervals", "Threshold Ride", "Brick"}:
            continue
        updated = dict(item)
        updated["purpose"] = f"减载周保留：{updated['purpose']}"
        adjusted.append(updated)
    if not any(item["type"] == "Cadence Workout" for item in adjusted):
        adjusted.append(
            role(
                "Cadence Workout",
                "support",
                "减载周可保留轻量技术练习，但不做疲劳堆叠。",
                "low",
            )
        )
    return adjusted


def cap_roles_to_days(roles: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
    priority_order = {"primary": 0, "support": 1, "optional": 2}
    ordered = sorted(roles, key=lambda item: priority_order.get(item["priority"], 3))
    return ordered[:days]


def slot_type_for_role(item: dict[str, Any]) -> str:
    role_type = item["type"]
    if role_type == "Long Ride":
        return "long"
    if role_type == "Cadence Workout":
        return "technical"
    if item["intensity_class"] == "high":
        return "hard"
    return "easy"


def placement_rule_for_slot(slot_type: str, role_type: str, workload_state: str) -> str:
    if slot_type == "long":
        if workload_state == "deload":
            return "放在本周最宽裕的位置，但保持减载周属性；不是固定星期。"
        return "放在本周最宽裕的位置，和 hard 槽位分开；不是固定星期。"
    if slot_type == "technical":
        return "放在低疲劳位置，可与 easy 周期相邻；不是固定星期。"
    if slot_type == "hard":
        if role_type == "Brick":
            return "作为本周唯一或主要 hard 槽位；若骑跑都含强度，计入周总强度。"
        return "放在恢复较好的位置，避免和 long 槽位连续堆叠；不是固定星期。"
    return "放在周内恢复或衔接位置，用来维持一致性；不是固定星期。"


def slot_sort_key(item: dict[str, Any]) -> tuple[int, str]:
    order = {"hard": 0, "long": 1, "technical": 2, "easy": 3}
    slot_type = slot_type_for_role(item)
    return (order.get(slot_type, 9), item["type"])


def build_week_slots(
    roles: list[dict[str, Any]],
    workload_state: str,
) -> list[dict[str, Any]]:
    slots = []
    for index, item in enumerate(sorted(roles, key=slot_sort_key), start=1):
        slot_type = slot_type_for_role(item)
        slots.append(
            {
                "slot": index,
                "slot_type": slot_type,
                "workout_type": item["type"],
                "intensity_class": item["intensity_class"],
                "priority": item["priority"],
                "source_refs": item.get("source_refs", []),
                "placement_rule": placement_rule_for_slot(slot_type, item["type"], workload_state),
                "detail_boundary": "slot_only_no_calendar_day_no_watts_no_minutes_no_intervals",
            }
        )
    return slots


def schedule_rule_for_slot(slot: dict[str, Any], assigned_day: str, long_day: str) -> str:
    slot_type = slot["slot_type"]
    if slot_type == "long":
        if assigned_day == long_day:
            return "使用用户提供的 long_ride_day；只绑定星期，不生成具体训练内容。"
        return "用户 long_ride_day 不可用或已冲突，改放周末可用日；只绑定星期。"
    if slot_type == "hard":
        return "hard 槽位避开 long 槽位相邻日，保留恢复间隔；只绑定星期。"
    if slot_type == "technical":
        return "technical 槽位放在剩余低冲突日，避免挤占 long/hard；只绑定星期。"
    return "easy 槽位填补剩余可用日，用于维持一致性；只绑定星期。"


def preferred_days_for_slot(slot: dict[str, Any], long_day: str) -> list[str]:
    slot_type = slot["slot_type"]
    if slot_type == "long":
        return [long_day, "Sunday", "Saturday", "Friday"]
    if slot_type == "hard":
        return ["Tuesday", "Wednesday", "Thursday", "Monday", "Friday"]
    if slot_type == "technical":
        return ["Wednesday", "Thursday", "Friday", "Tuesday"]
    return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def build_weekday_schedule(
    payload: dict[str, Any],
    skeleton_row: dict[str, Any],
    week_slots: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    week_start = parse_date(skeleton_row.get("week_start"))
    long_day = preferred_long_day(payload)
    blocked = unavailable_days(payload)
    used: set[str] = set()
    long_assigned: set[str] = set()
    schedule = []
    schedule_order = {"long": 0, "hard": 1, "technical": 2, "easy": 3}

    for slot in sorted(week_slots, key=lambda item: (schedule_order.get(item["slot_type"], 9), item["slot"])):
        slot_type = slot["slot_type"]
        avoid_adjacent_to = long_assigned if slot_type == "hard" else set()
        assigned_day = choose_day(
            preferred_days_for_slot(slot, long_day),
            used,
            blocked,
            avoid_adjacent_to=avoid_adjacent_to,
        )
        if assigned_day is None:
            continue
        used.add(assigned_day)
        if slot_type == "long":
            long_assigned.add(assigned_day)

        offset = day_index(assigned_day)
        assigned_date = (week_start + dt.timedelta(days=offset)).isoformat() if week_start else ""
        schedule.append(
            {
                "day": assigned_day,
                "day_label": DAY_LABELS[assigned_day],
                "date": assigned_date,
                "slot": slot["slot"],
                "slot_type": slot_type,
                "workout_type": slot["workout_type"],
                "intensity_class": slot["intensity_class"],
                "source_refs": slot.get("source_refs", []),
                "schedule_rule": schedule_rule_for_slot(slot, assigned_day, long_day),
                "detail_boundary": "weekday_slot_only_no_watts_no_minutes_no_intervals",
            }
        )
    return sorted(schedule, key=lambda item: day_index(item["day"]))


def other_sports(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = payload.get("normalized") or {}
    return normalized.get("other_sports") or {}


def fixed_day_entries(sports: dict[str, Any], key: str) -> list[dict[str, str]]:
    values = sports.get(key) or []
    if isinstance(values, str):
        values = [item.strip() for item in values.replace("，", ",").split(",")]
    if not isinstance(values, list):
        values = [values]

    entries = []
    seen = set()
    for value in values:
        if isinstance(value, dict):
            day = normalize_day(value.get("day"))
            raw = value.get("raw") or value.get("day_label") or value.get("day") or day
        else:
            day = normalize_day(value)
            raw = value
        if not day or day in seen:
            continue
        seen.add(day)
        entries.append({"day": day, "day_label": label_day(day), "raw": str(raw)})
    return sorted(entries, key=lambda item: day_index(item["day"]))


def reason_rows(day_reasons: dict[str, list[str]]) -> list[dict[str, Any]]:
    return [
        {
            "day": day,
            "day_label": label_day(day),
            "reasons": day_reasons[day],
        }
        for day in sorted(day_reasons, key=day_index)
    ]


def reasons_by_day(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {
        row.get("day"): row.get("reasons") or []
        for row in rows
        if row.get("day")
    }


def detect_fixed_day_conflicts(
    fixed_days: list[dict[str, str]],
    avoid_rows: list[dict[str, Any]],
    *,
    sport: str,
    fixed_key: str,
    conflict_type: str,
    severity: str,
) -> list[dict[str, Any]]:
    avoid = reasons_by_day(avoid_rows)
    conflicts = []
    for item in fixed_days:
        day = item.get("day")
        if day not in avoid:
            continue
        conflicts.append(
            {
                "sport": sport,
                "fixed_key": fixed_key,
                "day": day,
                "day_label": item.get("day_label", label_day(str(day))),
                "conflict_type": conflict_type,
                "severity": severity,
                "reasons": avoid[day],
                "resolution_hint": "人工复核：移动该跨项 hard/力量日，或调整 bike hard/long 槽位。",
            }
        )
    return conflicts


def build_cross_sport_placeholders(
    payload: dict[str, Any],
    week: dict[str, Any],
    weekday_schedule: list[dict[str, Any]],
) -> dict[str, Any]:
    sports = other_sports(payload)
    run_hours = as_number(sports.get("run_hours")) or 0.0
    swim_hours = as_number(sports.get("swim_hours")) or 0.0
    strength_count = as_int(sports.get("strength_sessions")) or 0
    plan_frame = payload.get("plan_frame") or {}
    intensity_budget = plan_frame.get("intensity_budget") or {}
    load = intensity_budget.get("other_sports_load") or {}
    active_flags = load.get("flags") or []

    hard_days = [item["day"] for item in weekday_schedule if item.get("slot_type") == "hard"]
    long_days = [item["day"] for item in weekday_schedule if item.get("slot_type") == "long"]
    blocked = unavailable_days(payload)

    run_hard_avoid: dict[str, list[str]] = {}
    swim_hard_caution: dict[str, list[str]] = {}
    strength_avoid: dict[str, list[str]] = {}

    for day in hard_days:
        for window_day in adjacent_window(day):
            add_day_reason(run_hard_avoid, window_day, "bike_hard_recovery_window")
            add_day_reason(strength_avoid, window_day, "bike_hard_recovery_window")
        add_day_reason(swim_hard_caution, day, "same_day_as_bike_hard")

    for day in long_days:
        add_day_reason(run_hard_avoid, day, "bike_long_day")
        add_day_reason(strength_avoid, day, "bike_long_day")
        if day_index(day) < len(DAYS) - 1:
            next_day = DAYS[day_index(day) + 1]
            add_day_reason(run_hard_avoid, next_day, "post_bike_long_recovery")
            add_day_reason(strength_avoid, next_day, "post_bike_long_recovery")

    for day in blocked:
        add_day_reason(run_hard_avoid, day, "athlete_unavailable")
        add_day_reason(swim_hard_caution, day, "athlete_unavailable")
        add_day_reason(strength_avoid, day, "athlete_unavailable")

    if swim_hours >= 4:
        for day in hard_days:
            for window_day in adjacent_window(day, before=0, after=1):
                add_day_reason(swim_hard_caution, window_day, "swim_load_high_with_bike_hard")

    run_avoid_rows = reason_rows(run_hard_avoid)
    swim_caution_rows = reason_rows(swim_hard_caution)
    strength_avoid_rows = reason_rows(strength_avoid)
    fixed_run_hard_days = fixed_day_entries(sports, "fixed_run_hard_days")
    fixed_swim_hard_days = fixed_day_entries(sports, "fixed_swim_hard_days")
    fixed_strength_days = fixed_day_entries(sports, "fixed_strength_lower_body_days")
    conflicts = []
    conflicts.extend(
        detect_fixed_day_conflicts(
            fixed_run_hard_days,
            run_avoid_rows,
            sport="run",
            fixed_key="fixed_run_hard_days",
            conflict_type="fixed_run_hard_on_avoid_day",
            severity="high",
        )
    )
    conflicts.extend(
        detect_fixed_day_conflicts(
            fixed_swim_hard_days,
            swim_caution_rows,
            sport="swim",
            fixed_key="fixed_swim_hard_days",
            conflict_type="fixed_swim_hard_on_caution_day",
            severity="medium",
        )
    )
    conflicts.extend(
        detect_fixed_day_conflicts(
            fixed_strength_days,
            strength_avoid_rows,
            sport="strength",
            fixed_key="fixed_strength_lower_body_days",
            conflict_type="fixed_strength_lower_body_on_avoid_day",
            severity="high",
        )
    )

    return {
        "status": "placeholder_only",
        "boundary": "cross_sport_placeholder_only_no_run_swim_strength_detail_no_watts_no_minutes_no_intervals",
        "other_sports_input": {
            "run_hours": run_hours,
            "swim_hours": swim_hours,
            "strength_sessions": strength_count,
        },
        "active_caution_flags": active_flags,
        "fixed_cross_sport_days": {
            "fixed_run_hard_days": fixed_run_hard_days,
            "fixed_swim_hard_days": fixed_swim_hard_days,
            "fixed_strength_lower_body_days": fixed_strength_days,
        },
        "conflict_status": "conflicts_detected" if conflicts else "clear",
        "cross_sport_conflicts": conflicts,
        "bike_hard_days": day_entries(hard_days),
        "bike_long_days": day_entries(long_days),
        "run_hard_avoid_days": run_avoid_rows,
        "swim_hard_caution_days": swim_caution_rows,
        "strength_lower_body_avoid_days": strength_avoid_rows,
        "rules": [
            "跑步 hard 不要放在 bike hard 同日或相邻日。",
            "跑步 hard 不要放在 bike long 当日或之后恢复日。",
            "下肢力量不要放在 bike hard 恢复窗口或 bike long 当日。",
            "游泳 hard 如需安排，应避开 bike hard 同日；游泳负荷高时也避开次日。",
            "如果用户提供了固定跑步 hard、游泳 hard 或下肢力量日期，这里只检测冲突，不生成跑步、游泳或力量训练内容。",
        ],
    }


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
                    "heading": ref.get("heading", ""),
                    "source_path": ref.get("source_path", ""),
                }
            )
    return refs


def review_flags_for_week(
    week: dict[str, Any],
    blocked_days: set[str],
    long_day: str,
    cross_sport_flags: list[str],
) -> list[str]:
    flags = []
    schedule = week.get("weekday_schedule") or []
    slots = week.get("week_slots") or []
    if len(schedule) != len(slots):
        flags.append("schedule_slot_count_mismatch")

    scheduled_days = [item.get("day") for item in schedule]
    if len(scheduled_days) != len(set(scheduled_days)):
        flags.append("duplicate_scheduled_day")
    if any(day in blocked_days for day in scheduled_days):
        flags.append("scheduled_on_blocked_day")

    long_days = [item.get("day") for item in schedule if item.get("slot_type") == "long"]
    hard_days = [item.get("day") for item in schedule if item.get("slot_type") == "hard"]
    if long_days and long_day not in blocked_days and long_days[0] != long_day:
        flags.append("long_not_on_preferred_day")
    if any(day_distance(hard_day, long_day_item) <= 1 for hard_day in hard_days for long_day_item in long_days):
        flags.append("hard_adjacent_to_long")

    high_budget = week.get("high_intensity_budget", 0)
    assigned_high = week.get("assigned_high_intensity_count", 0)
    if assigned_high > high_budget:
        flags.append("exceeds_high_intensity_budget")
    if week.get("workload_state") == "deload" and assigned_high > 0:
        flags.append("hard_slot_in_deload")
    if cross_sport_flags and assigned_high > 0:
        flags.append("cross_sport_load_with_hard_slot")
    if (week.get("cross_sport_placeholders") or {}).get("cross_sport_conflicts"):
        flags.append("fixed_cross_sport_day_conflict")
    if not compact_source_refs(schedule):
        flags.append("missing_source_refs")
    return flags


def build_review_row(
    week: dict[str, Any],
    *,
    blocked_days: set[str],
    long_day: str,
    cross_sport_flags: list[str],
) -> dict[str, Any]:
    schedule = week.get("weekday_schedule") or []
    cross_placeholders = week.get("cross_sport_placeholders") or {}
    hard_items = [item for item in schedule if item.get("slot_type") == "hard"]
    long_items = [item for item in schedule if item.get("slot_type") == "long"]
    flags = review_flags_for_week(week, blocked_days, long_day, cross_sport_flags)
    return {
        "week": week.get("week"),
        "week_start": week.get("week_start"),
        "phase_hint": week.get("phase_hint"),
        "workload_state": week.get("workload_state"),
        "bike_days_budget": week.get("bike_days_budget"),
        "bike_hours_budget": week.get("bike_hours_budget"),
        "scheduled_days": [
            {
                "day": item.get("day"),
                "day_label": item.get("day_label"),
                "slot_type": item.get("slot_type"),
                "workout_type": item.get("workout_type"),
            }
            for item in schedule
        ],
        "hard_summary": [
            {
                "day": item.get("day"),
                "day_label": item.get("day_label"),
                "workout_type": item.get("workout_type"),
            }
            for item in hard_items
        ],
        "long_summary": [
            {
                "day": item.get("day"),
                "day_label": item.get("day_label"),
                "workout_type": item.get("workout_type"),
            }
            for item in long_items
        ],
        "high_intensity_budget": week.get("high_intensity_budget"),
        "assigned_high_intensity_count": week.get("assigned_high_intensity_count"),
        "cross_sport_flags": cross_sport_flags,
        "cross_sport_placeholder_summary": {
            "status": cross_placeholders.get("status", ""),
            "run_hard_avoid_days": cross_placeholders.get("run_hard_avoid_days", []),
            "swim_hard_caution_days": cross_placeholders.get("swim_hard_caution_days", []),
            "strength_lower_body_avoid_days": cross_placeholders.get("strength_lower_body_avoid_days", []),
            "fixed_cross_sport_days": cross_placeholders.get("fixed_cross_sport_days", {}),
            "conflict_status": cross_placeholders.get("conflict_status", ""),
            "cross_sport_conflicts": cross_placeholders.get("cross_sport_conflicts", []),
            "boundary": cross_placeholders.get("boundary", ""),
        },
        "blocked_days": [{"day": day, "day_label": label_day(day)} for day in sorted(blocked_days, key=day_index)],
        "preferred_long_day": {"day": long_day, "day_label": label_day(long_day)},
        "attention_flags": flags,
        "review_status": "needs_attention" if flags else "ok",
        "source_refs": compact_source_refs(schedule),
        "review_boundary": "human_review_only_no_watts_no_minutes_no_intervals",
    }


def build_review_view(payload: dict[str, Any], weekly_plan: list[dict[str, Any]]) -> dict[str, Any]:
    plan_frame = payload.get("plan_frame") or {}
    intensity_budget = plan_frame.get("intensity_budget") or {}
    other_sports = intensity_budget.get("other_sports_load") or {}
    cross_sport_flags = other_sports.get("flags") or []
    blocked_days = unavailable_days(payload)
    long_day = preferred_long_day(payload)
    rows = [
        build_review_row(
            week,
            blocked_days=blocked_days,
            long_day=long_day,
            cross_sport_flags=cross_sport_flags,
        )
        for week in weekly_plan
    ]
    return {
        "status": "ready_for_human_review",
        "schema_version": SCHEMA_VERSION,
        "review_boundary": "summary_only_no_watts_no_minutes_no_intervals",
        "preferred_long_day": {"day": long_day, "day_label": label_day(long_day)},
        "blocked_days": [{"day": day, "day_label": label_day(day)} for day in sorted(blocked_days, key=day_index)],
        "cross_sport_load": other_sports,
        "cross_sport_adjustment": intensity_budget.get("cross_sport_adjustment", 0),
        "rows": rows,
    }


def attach_source_refs(
    roles: list[dict[str, Any]],
    source_resolver: SourceResolver,
) -> list[dict[str, Any]]:
    grounded = []
    for item in roles:
        updated = dict(item)
        updated["source_refs"] = source_resolver.refs_for_role(item["type"])
        updated["source_ref_boundary"] = "metadata_only_no_watts_no_minutes_no_intervals"
        grounded.append(updated)
    return grounded


def build_week_allocation(
    payload: dict[str, Any],
    skeleton_row: dict[str, Any],
    source_resolver: SourceResolver,
) -> dict[str, Any]:
    days = weekly_days(payload)
    hours = weekly_hours(payload)
    high_budget = max_high_intensity(payload)
    phase = skeleton_row.get("phase_hint") or "base"
    workload_state = skeleton_row.get("workload_state") or "load"

    roles = base_roles(days, workload_state)
    high_role = high_intensity_role(phase, workload_state)
    if high_role and high_budget > 0:
        roles.append(high_role)
    if workload_state in {"deload", "peaking"}:
        roles = deload_adjustments(roles)

    roles = cap_roles_to_days(roles, days)
    high_count = sum(1 for item in roles if item["intensity_class"] == "high")
    if high_count > high_budget:
        roles = [item for item in roles if item["intensity_class"] != "high"]
        high_count = 0
    roles = attach_source_refs(roles, source_resolver)
    week_slots = build_week_slots(roles, workload_state)
    weekday_schedule = build_weekday_schedule(payload, skeleton_row, week_slots)
    cross_sport_placeholders = build_cross_sport_placeholders(payload, skeleton_row, weekday_schedule)

    return {
        "week": skeleton_row.get("week"),
        "week_start": skeleton_row.get("week_start"),
        "phase_hint": phase,
        "workload_state": workload_state,
        "volume_1_10": volume_level(hours, workload_state),
        "intensity_1_10": intensity_level(workload_state, phase, high_budget),
        "bike_days_budget": days,
        "bike_hours_budget": hours,
        "high_intensity_budget": high_budget,
        "assigned_high_intensity_count": high_count,
        "workout_type_allocation": roles,
        "week_slots": week_slots,
        "weekday_schedule": weekday_schedule,
        "cross_sport_placeholders": cross_sport_placeholders,
        "guardrails": [
            "这是周级课型、周内槽位、星期排程和跨项冲突占位，不是每日训练课。",
            "不输出瓦数、分钟、组数、重复次数或具体间歇。",
            "brick 中如果骑和跑都含高强度，计入一周总高强度。",
        ],
    }


def build_generator_output(
    payload: dict[str, Any],
    source_db: Path = DEFAULT_SOURCE_DB,
) -> dict[str, Any]:
    failures = validate_intake(payload)
    if failures:
        return {
            "status": "blocked",
            "schema_version": SCHEMA_VERSION,
            "source_grounding": {
                "status": "not_attempted",
                "method": "sqlite_lexical_chunk_refs_metadata_only",
                "source_db": rel_path(source_db),
                "warning": "intake blocked",
            },
            "review_view": {
                "status": "not_attempted",
                "schema_version": SCHEMA_VERSION,
                "review_boundary": "intake_blocked",
                "rows": [],
            },
            "failures": failures,
            "weekly_plan": [],
        }

    plan_frame = payload["plan_frame"]
    week_skeleton = plan_frame.get("week_skeleton") or []
    source_resolver = SourceResolver(source_db)
    weekly_plan = [build_week_allocation(payload, row, source_resolver) for row in week_skeleton]
    review_view = build_review_view(payload, weekly_plan)
    return {
        "status": "generated_weekly_type_allocation",
        "schema_version": SCHEMA_VERSION,
        "source_schema_version": plan_frame.get("schema_version"),
        "source_grounding": source_resolver.summary(),
        "athlete_id": (payload.get("normalized") or {}).get("athlete_id", "default"),
        "goal": (payload.get("normalized") or {}).get("goal", {}),
        "weekly_plan": weekly_plan,
        "review_view": review_view,
        "global_guardrails": [
            "本版本只做周级课型、周内槽位、星期排程、跨项占位和人工复核摘要。",
            "后续要生成每日课表前，必须再次检查跑步、游泳、疲劳和伤病状态。",
            "所有高强度骑行、跑步和 brick 都要合并计算周总强度。",
        ],
    }


def format_scheduled_days(rows: list[dict[str, Any]]) -> str:
    parts = []
    for item in rows:
        day_label = item.get("day_label") or label_day(str(item.get("day") or ""))
        label_parts = [
            day_label,
            str(item.get("slot_type") or ""),
            str(item.get("workout_type") or ""),
        ]
        parts.append(" ".join(part for part in label_parts if part))
    return "; ".join(parts) if parts else "-"


def format_day_summary(rows: list[dict[str, Any]]) -> str:
    parts = []
    for item in rows:
        day_label = item.get("day_label") or label_day(str(item.get("day") or ""))
        workout_type = str(item.get("workout_type") or "")
        parts.append(" ".join(part for part in [day_label, workout_type] if part))
    return "; ".join(parts) if parts else "-"


def format_reason_days(rows: list[dict[str, Any]]) -> str:
    parts = []
    for item in rows:
        day_label = item.get("day_label") or label_day(str(item.get("day") or ""))
        reasons = ",".join(str(reason) for reason in item.get("reasons") or [])
        parts.append(f"{day_label}({reasons})" if reasons else day_label)
    return "; ".join(parts) if parts else "-"


def format_source_chunk_ids(row: dict[str, Any]) -> str:
    chunk_ids = [
        str(ref.get("chunk_id"))
        for ref in row.get("source_refs") or []
        if ref.get("chunk_id")
    ]
    return "; ".join(chunk_ids) if chunk_ids else "-"


def format_string_list(values: list[Any]) -> str:
    return "; ".join(str(value) for value in values) if values else "-"


def format_conflicts(rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in rows:
        reasons = ",".join(str(reason) for reason in row.get("reasons") or [])
        parts.append(
            f"{row.get('sport')} {row.get('day_label')} {row.get('conflict_type')}({reasons})"
        )
    return "; ".join(parts) if parts else "-"


def build_review_export_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    review_rows = (result.get("review_view") or {}).get("rows") or []
    export_rows = []
    for row in review_rows:
        placeholder = row.get("cross_sport_placeholder_summary") or {}
        export_rows.append(
            {
                "week": row.get("week", ""),
                "week_start": row.get("week_start", ""),
                "phase_hint": row.get("phase_hint", ""),
                "workload_state": row.get("workload_state", ""),
                "bike_days_budget": row.get("bike_days_budget", ""),
                "bike_hours_budget": row.get("bike_hours_budget", ""),
                "scheduled_days": format_scheduled_days(row.get("scheduled_days") or []),
                "hard_summary": format_day_summary(row.get("hard_summary") or []),
                "long_summary": format_day_summary(row.get("long_summary") or []),
                "run_hard_avoid_days": format_reason_days(placeholder.get("run_hard_avoid_days") or []),
                "swim_hard_caution_days": format_reason_days(placeholder.get("swim_hard_caution_days") or []),
                "strength_lower_body_avoid_days": format_reason_days(
                    placeholder.get("strength_lower_body_avoid_days") or []
                ),
                "cross_sport_conflicts": format_conflicts(placeholder.get("cross_sport_conflicts") or []),
                "attention_flags": format_string_list(row.get("attention_flags") or []),
                "review_status": row.get("review_status", ""),
                "human_review_status": "",
                "review_comment": "",
                "override_request": "",
                "move_slot": "",
                "blocked_day": "",
                "protect_day": "",
                "source_chunk_ids": format_source_chunk_ids(row),
                "review_boundary": row.get("review_boundary", ""),
            }
        )
    return export_rows


def markdown_cell(value: Any) -> str:
    text = str(value)
    return text.replace("\n", " ").replace("|", "\\|")


def build_review_markdown(result: dict[str, Any]) -> str:
    review_view = result.get("review_view") or {}
    preferred_long_day = review_view.get("preferred_long_day") or {}
    blocked_days = format_scheduled_days(review_view.get("blocked_days") or [])
    source_grounding = result.get("source_grounding") or {}
    export_rows = build_review_export_rows(result)
    lines = [
        "# Bike Plan Review",
        "",
        f"- status: {result.get('status', '')}",
        f"- schema_version: {result.get('schema_version', '')}",
        f"- review_boundary: {review_view.get('review_boundary', '')}",
        f"- preferred_long_day: {preferred_long_day.get('day_label', '-')}",
        f"- blocked_days: {blocked_days}",
        f"- source_grounding: {source_grounding.get('status', '')} / {source_grounding.get('method', '')}",
        "",
        "> Human review export only. No watts, minutes, intervals, sets, reps, or daily prescription.",
        "",
        "## Weekly Rows",
        "",
    ]
    if not export_rows:
        lines.extend(["No review rows available.", ""])
        return "\n".join(lines)

    headers = [
        "Week",
        "Start",
        "Phase",
        "State",
        "Bike Budget",
        "Scheduled Days",
        "Hard",
        "Long",
        "Run Hard Avoid",
        "Swim Hard Caution",
        "Strength Avoid",
        "Conflicts",
        "Flags",
        "Status",
        "Human Status",
        "Review Comment",
        "Override Request",
        "Move Slot",
        "Blocked Day",
        "Protect Day",
        "source_chunk_ids",
    ]
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for row in export_rows:
        cells = [
            row["week"],
            row["week_start"],
            row["phase_hint"],
            row["workload_state"],
            f"{row['bike_days_budget']} days / {row['bike_hours_budget']} h",
            row["scheduled_days"],
            row["hard_summary"],
            row["long_summary"],
            row["run_hard_avoid_days"],
            row["swim_hard_caution_days"],
            row["strength_lower_body_avoid_days"],
            row["cross_sport_conflicts"],
            row["attention_flags"],
            row["review_status"],
            row["human_review_status"],
            row["review_comment"],
            row["override_request"],
            row["move_slot"],
            row["blocked_day"],
            row["protect_day"],
            row["source_chunk_ids"],
        ]
        lines.append("| " + " | ".join(markdown_cell(cell) for cell in cells) + " |")
    lines.append("")
    return "\n".join(lines)


def write_review_csv(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(build_review_export_rows(result))


def write_review_markdown(result: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_review_markdown(result), encoding="utf-8")


def print_markdown(result: dict[str, Any]) -> None:
    print(f"status: {result['status']}")
    if result.get("failures"):
        print("\nfailures:")
        for failure in result["failures"]:
            print(f"- {failure}")
        return
    print("\nweekly_plan:")
    for week in result.get("weekly_plan", []):
        roles = ", ".join(item["type"] for item in week["workout_type_allocation"])
        slots = ", ".join(item["slot_type"] for item in week.get("week_slots", []))
        print(
            f"- week {week['week']} {week['week_start']}: "
            f"{week['phase_hint']} / {week['workload_state']} / {roles} / slots={slots}"
        )


def main() -> int:
    args = parse_args()
    payload = load_json(args.input)
    result = build_generator_output(payload, source_db=args.source_db)
    if args.write_report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_review_files:
        write_review_markdown(result, args.review_md)
        write_review_csv(result, args.review_csv)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_markdown(result)
        if args.write_review_files:
            print(f"review_markdown={args.review_md}")
            print(f"review_csv={args.review_csv}")
    return 0 if result["status"] == "generated_weekly_type_allocation" else 1


if __name__ == "__main__":
    raise SystemExit(main())
