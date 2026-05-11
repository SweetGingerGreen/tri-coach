#!/usr/bin/env python3
"""
Build a review override file from the human-edited bike plan review CSV.

This script does not modify the generated bike plan. It turns human review
status, comments, and override requests into a separate input for the next
planning layer.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import bike_plan_generator


ROOT = Path(__file__).parent.resolve()
DEFAULT_REVIEW_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_latest.csv"
DEFAULT_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_generator_latest.json"
DEFAULT_OVERRIDE = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_override_latest.json"
SCHEMA_VERSION = "bike_plan_review_override_v0.2"
NO_VALUE_MARKERS = {"", "-", "none", "null", "无", "n/a"}
SLOT_ALIASES = {
    "hard": "hard",
    "高强度": "hard",
    "强度": "hard",
    "long": "long",
    "长骑": "long",
    "easy": "easy",
    "耐力": "easy",
    "technical": "technical",
    "tech": "technical",
    "技术": "technical",
    "踏频": "technical",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-csv", type=Path, default=DEFAULT_REVIEW_CSV)
    parser.add_argument("--plan-report", type=Path, default=DEFAULT_PLAN_REPORT)
    parser.add_argument("--override", type=Path, default=DEFAULT_OVERRIDE)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write-override", action="store_true")
    return parser.parse_args()


def rel_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def cell(value: Any) -> str:
    return str(value or "").strip()


def meaningful(value: Any) -> bool:
    return cell(value).lower() not in NO_VALUE_MARKERS


def parse_week(value: Any) -> int | None:
    text = cell(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def normalize_status(value: Any) -> str:
    text = cell(value).lower()
    approved = {"ok", "pass", "passed", "approved", "approve", "通过", "已通过"}
    rejected = {"reject", "rejected", "block", "blocked", "不通过", "拒绝"}
    attention = {"needs_attention", "attention", "review", "needs_review", "待复核", "需复核"}
    override = {
        "override",
        "override_requested",
        "change",
        "change_requested",
        "revise",
        "revision",
        "修改",
        "需修改",
        "调整",
    }
    if text in approved:
        return "approved"
    if text in rejected:
        return "rejected"
    if text in attention:
        return "needs_attention"
    if text in override:
        return "override_requested"
    if text in NO_VALUE_MARKERS:
        return "pending"
    return "needs_attention"


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def read_review_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"review csv not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def field(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if meaningful(value):
            return cell(value)
    return ""


def normalize_slot(value: Any) -> str:
    text = cell(value).lower()
    if not text:
        return ""
    for alias, slot in SLOT_ALIASES.items():
        if alias in text:
            return slot
    return ""


def normalize_days(value: Any) -> list[dict[str, str]]:
    text = cell(value)
    if not text:
        return []
    lowered = text.lower()
    days = []
    seen = set()
    for alias, day in bike_plan_generator.DAY_ALIASES.items():
        if alias in text or alias in lowered:
            if day in seen:
                continue
            seen.add(day)
            days.append(
                {
                    "day": day,
                    "day_label": bike_plan_generator.label_day(day),
                    "raw": alias,
                }
            )
    return sorted(days, key=lambda item: bike_plan_generator.day_index(item["day"]))


def build_structured_override(row: dict[str, Any]) -> dict[str, Any]:
    move_slot = normalize_slot(field(row, "move_slot", "override_move_slot", "slot_to_move"))
    blocked_days = normalize_days(field(row, "blocked_day", "blocked_days", "avoid_day", "avoid_days"))
    protect_days = normalize_days(field(row, "protect_day", "protect_days", "keep_day", "keep_days"))
    if not move_slot and not blocked_days and not protect_days:
        return {}
    return {
        "move_slot": move_slot,
        "blocked_days": blocked_days,
        "protect_day": protect_days[0] if protect_days else {},
        "protect_days": protect_days,
        "source_fields": ["move_slot", "blocked_day", "protect_day"],
        "boundary": "structured_weekly_override_only_no_watts_no_minutes_no_intervals",
    }


def plan_weeks(report: dict[str, Any]) -> set[int]:
    return {
        int(week.get("week"))
        for week in report.get("weekly_plan") or []
        if week.get("week") is not None
    }


def build_review_item(row: dict[str, Any], known_weeks: set[int]) -> dict[str, Any]:
    week = parse_week(row.get("week"))
    human_status = field(row, "human_review_status", "human_status", "review_decision")
    generated_status = field(row, "review_status")
    normalized_status = normalize_status(human_status or generated_status)
    review_comment = field(row, "review_comment", "comment", "notes", "修改意见", "备注")
    override_request = field(row, "override_request", "override", "change_request", "修改请求", "调整请求")
    structured_override = build_structured_override(row)
    needs_override = normalized_status in {"override_requested", "rejected"} or bool(override_request) or bool(structured_override)
    if normalized_status == "needs_attention" and review_comment:
        needs_override = True

    return {
        "week": week,
        "week_start": cell(row.get("week_start")),
        "known_week": week in known_weeks if week is not None else False,
        "generated_review_status": generated_status,
        "human_review_status": human_status,
        "normalized_review_status": normalized_status,
        "review_comment": review_comment,
        "override_request": override_request,
        "structured_override": structured_override,
        "needs_override": needs_override,
        "scheduled_days": cell(row.get("scheduled_days")),
        "attention_flags": cell(row.get("attention_flags")),
        "cross_sport_conflicts": cell(row.get("cross_sport_conflicts")),
        "source_chunk_ids": cell(row.get("source_chunk_ids")),
        "boundary": "human_review_override_request_only_no_watts_no_minutes_no_intervals",
    }


def build_override(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    overrides = []
    for item in items:
        if not item.get("needs_override"):
            continue
        overrides.append(
            {
                "week": item.get("week"),
                "week_start": item.get("week_start"),
                "action": "manual_override_requested",
                "normalized_review_status": item.get("normalized_review_status"),
                "review_comment": item.get("review_comment"),
                "override_request": item.get("override_request"),
                "structured_override": item.get("structured_override") or {},
                "current_scheduled_days": item.get("scheduled_days"),
                "current_attention_flags": item.get("attention_flags"),
                "current_cross_sport_conflicts": item.get("cross_sport_conflicts"),
                "source_chunk_ids": item.get("source_chunk_ids"),
                "boundary": "override_request_only_no_plan_mutation_no_watts_no_minutes_no_intervals",
            }
        )
    return overrides


def build_review_override(review_csv: Path, plan_report: Path = DEFAULT_PLAN_REPORT) -> dict[str, Any]:
    rows = read_review_csv(review_csv)
    report = load_json(plan_report)
    known_weeks = plan_weeks(report)
    items = [build_review_item(row, known_weeks) for row in rows]
    overrides = build_override(items)
    missing_weeks = [
        item.get("week")
        for item in items
        if item.get("week") is not None and not item.get("known_week")
    ]
    status_counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("normalized_review_status") or "pending")
        status_counts[status] = status_counts.get(status, 0) + 1

    return {
        "status": "overrides_ready" if overrides else "no_overrides",
        "schema_version": SCHEMA_VERSION,
        "source_review_csv": rel_path(review_csv),
        "source_plan_report": rel_path(plan_report),
        "source_plan_schema_version": report.get("schema_version", ""),
        "source_intake_schema_version": report.get("source_schema_version", ""),
        "review_summary": {
            "rows_total": len(items),
            "overrides_total": len(overrides),
            "status_counts": status_counts,
            "missing_weeks": missing_weeks,
        },
        "review_items": items,
        "overrides": overrides,
        "guardrails": [
            "这个文件只表达人工复核意见和覆盖请求，不直接修改 bike_plan_generator_latest.json。",
            "override_request 是下一层排课输入，不是每日训练课。",
            "move_slot、blocked_day、protect_day 是机器可读的周级 override 字段。",
            "不要在 override 中写 watts、minutes、intervals、sets、reps 等具体处方字段。",
        ],
    }


def print_markdown(result: dict[str, Any]) -> None:
    summary = result.get("review_summary") or {}
    print(f"status: {result['status']}")
    print(f"schema_version: {result['schema_version']}")
    print(f"rows_total: {summary.get('rows_total', 0)}")
    print(f"overrides_total: {summary.get('overrides_total', 0)}")
    if result.get("overrides"):
        print("\noverrides:")
        for item in result["overrides"]:
            print(
                f"- week {item.get('week')} {item.get('week_start')}: "
                f"{item.get('normalized_review_status')} / {item.get('override_request') or item.get('review_comment')}"
            )


def main() -> int:
    args = parse_args()
    result = build_review_override(args.review_csv, args.plan_report)
    if args.write_override:
        args.override.parent.mkdir(parents=True, exist_ok=True)
        args.override.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_markdown(result)
        if args.write_override:
            print(f"override={args.override}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
