#!/usr/bin/env python3
"""
Generate first-pass daily bike workout drafts from a passed daily preflight.

This layer is allowed to turn week-level bike slots into simple daily workout
drafts. It is still not a final prescription: pain, fatigue, fixed cross-sport
sessions, and simulated inputs must be reviewed before use.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

import bike_plan_daily_preflight
import bike_plan_generator


ROOT = Path(__file__).parent.resolve()
DEFAULT_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_latest.json"
DEFAULT_PREFLIGHT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_latest.json"
DEFAULT_INTAKE_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_intake_latest.json"
DEFAULT_OUTPUT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_latest.json"
DEFAULT_REVIEW_MD = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_review_latest.md"
DEFAULT_REVIEW_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_review_latest.csv"
SCHEMA_VERSION = "bike_plan_daily_draft_v0.1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-report", type=Path, default=DEFAULT_PLAN_REPORT)
    parser.add_argument("--preflight-report", type=Path, default=DEFAULT_PREFLIGHT_REPORT)
    parser.add_argument("--intake-report", type=Path, default=DEFAULT_INTAKE_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--review-md", type=Path, default=DEFAULT_REVIEW_MD)
    parser.add_argument("--review-csv", type=Path, default=DEFAULT_REVIEW_CSV)
    parser.add_argument("--allow-simulated", action="store_true")
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


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def ftp_from_intake(intake: dict[str, Any]) -> tuple[float | None, str]:
    current = ((intake.get("normalized") or {}).get("current") or {})
    ftp = current.get("ftp_w")
    try:
        value = float(ftp)
    except (TypeError, ValueError):
        return None, ""
    return value, str(current.get("ftp_test_date") or "")


def power_range(ftp: float | None, low: float, high: float) -> dict[str, Any]:
    pct = f"{int(low * 100)}-{int(high * 100)}% FTP"
    if not ftp:
        return {"percent_ftp": pct, "watts": ""}
    return {
        "percent_ftp": pct,
        "watts": f"{round(ftp * low)}-{round(ftp * high)} W",
    }


def zone_targets(ftp: float | None) -> dict[str, dict[str, Any]]:
    return {
        "Z1_recovery": power_range(ftp, 0.50, 0.60),
        "Z2_endurance": power_range(ftp, 0.60, 0.75),
        "Z3_tempo": power_range(ftp, 0.76, 0.87),
        "sweet_spot": power_range(ftp, 0.88, 0.94),
        "threshold": power_range(ftp, 0.95, 1.00),
        "vo2_power": power_range(ftp, 1.05, 1.15),
    }


def add_segment(
    rows: list[dict[str, Any]],
    name: str,
    duration: int,
    target: str,
    notes: str,
) -> None:
    if duration <= 0:
        return
    rows.append(
        {
            "segment": name,
            "duration_minutes": duration,
            "target": target,
            "notes": notes,
        }
    )


def easy_structure(duration: int) -> list[dict[str, Any]]:
    warmup = 10 if duration >= 45 else 5
    cooldown = 10 if duration >= 45 else 5
    main = max(duration - warmup - cooldown, 0)
    rows: list[dict[str, Any]] = []
    add_segment(rows, "warmup", warmup, "Z1-Z2", "逐步进入稳定踩踏。")
    add_segment(rows, "main", main, "Z2_endurance", "稳定耐力骑，保持可对话强度。")
    add_segment(rows, "cooldown", cooldown, "Z1_recovery", "轻松收操。")
    return rows


def technical_structure(duration: int) -> list[dict[str, Any]]:
    warmup = 10 if duration >= 45 else 8
    cooldown = 10 if duration >= 45 else 5
    available = max(duration - warmup - cooldown, 0)
    reps = max(3, min(6, available // 5)) if available >= 15 else 0
    drill_total = reps * 5
    fill = max(available - drill_total, 0)
    rows: list[dict[str, Any]] = []
    add_segment(rows, "warmup", warmup, "Z1-Z2", "先把踏频和呼吸稳定下来。")
    if reps:
        add_segment(rows, "cadence_drills", drill_total, "Z2_endurance", f"{reps} 轮：3 分钟高踏频控制 + 2 分钟轻松恢复。")
    add_segment(rows, "endurance_fill", fill, "Z2_endurance", "剩余时间做平稳耐力，不追强度。")
    add_segment(rows, "cooldown", cooldown, "Z1_recovery", "轻松收操。")
    return rows


def long_structure(duration: int) -> list[dict[str, Any]]:
    warmup = 15 if duration >= 90 else 10
    cooldown = 10
    main = max(duration - warmup - cooldown, 0)
    rows: list[dict[str, Any]] = []
    add_segment(rows, "warmup", warmup, "Z1-Z2", "前段不急，先稳定输出。")
    add_segment(rows, "main", main, "Z2_endurance", "长时间稳定耐力，避免变成 tempo。")
    add_segment(rows, "cooldown", cooldown, "Z1_recovery", "轻松收操。")
    return rows


def threshold_structure(duration: int) -> list[dict[str, Any]]:
    warmup = 15
    cooldown = 10
    reps = 2 if duration >= 65 else 1
    work = 12 if duration >= 75 else 10
    recovery = 5
    block = reps * work + max(reps - 1, 0) * recovery
    fill = max(duration - warmup - cooldown - block, 0)
    rows: list[dict[str, Any]] = []
    add_segment(rows, "warmup", warmup, "Z1-Z2", "逐步激活，不抢强度。")
    add_segment(rows, "threshold_block", block, "threshold", f"{reps} 轮：{work} 分钟阈值附近 + {recovery} 分钟轻松恢复。")
    add_segment(rows, "endurance_fill", fill, "Z2_endurance", "剩余时间回到耐力强度。")
    add_segment(rows, "cooldown", cooldown, "Z1_recovery", "轻松收操。")
    return rows


def power_interval_structure(duration: int) -> list[dict[str, Any]]:
    warmup = 15
    cooldown = 10
    reps = 6 if duration >= 75 else 4
    work = 3
    recovery = 3
    block = reps * (work + recovery)
    fill = max(duration - warmup - cooldown - block, 0)
    rows: list[dict[str, Any]] = []
    add_segment(rows, "warmup", warmup, "Z1-Z2", "充分热身，确认腿部状态正常。")
    add_segment(rows, "power_intervals", block, "vo2_power", f"{reps} 轮：{work} 分钟高功率 + {recovery} 分钟轻松恢复。")
    add_segment(rows, "endurance_fill", fill, "Z2_endurance", "剩余时间回到可控耐力。")
    add_segment(rows, "cooldown", cooldown, "Z1_recovery", "轻松收操。")
    return rows


def draft_structure(workout_type: str, slot_type: str, duration: int) -> list[dict[str, Any]]:
    text = f"{workout_type} {slot_type}".lower()
    if "cadence" in text or slot_type == "technical":
        return technical_structure(duration)
    if "long" in text or slot_type == "long":
        return long_structure(duration)
    if "threshold" in text:
        return threshold_structure(duration)
    if "power interval" in text or "interval" in text or slot_type == "hard":
        return power_interval_structure(duration)
    return easy_structure(duration)


def focus_for(workout_type: str, slot_type: str) -> str:
    text = f"{workout_type} {slot_type}".lower()
    if "cadence" in text or slot_type == "technical":
        return "踏频控制和骑行经济性"
    if "long" in text or slot_type == "long":
        return "长时间有氧耐力和补给演练"
    if "threshold" in text:
        return "阈值附近可控输出"
    if "power interval" in text or "interval" in text or slot_type == "hard":
        return "短时间高功率刺激"
    return "有氧耐力底座"


def stop_conditions() -> list[str]:
    return [
        "出现疼痛、麻木、无力或动作变形时停止本课并人工复核。",
        "当天疲劳明显高于 preflight 记录时降级为恢复骑或取消。",
        "若固定跑步、游泳或力量训练临时变化，先回到 preflight 重新检查。",
    ]


def nutrition_notes(slot_type: str, duration: int) -> list[str]:
    notes = []
    if slot_type == "long" or duration >= 90:
        notes.append("长骑草案需要单独用补给模板复核，不在这里生成比赛补给处方。")
    if duration >= 60:
        notes.append("训练中只使用已经验证过的补给，不临时尝试新品。")
    return notes


def is_simulated_preflight(report: dict[str, Any], report_path: Path) -> bool:
    text = " ".join(
        str(value or "")
        for value in (
            report_path,
            report.get("source_preflight_input"),
            report.get("source_slots_csv"),
            report.get("source_fixed_sessions_csv"),
        )
    ).lower()
    return "simulated" in text


def scheduled_items_by_date(plan: dict[str, Any]) -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    result = {}
    for week in plan.get("weekly_plan") or []:
        for item in week.get("weekday_schedule") or []:
            date = item.get("date")
            if date:
                result[date] = (week, item)
    return result


def build_blocked_result(
    status: str,
    reason: str,
    plan_report: Path,
    preflight_report: Path,
    intake_report: Path,
    simulated: bool,
) -> dict[str, Any]:
    return {
        "status": status,
        "schema_version": SCHEMA_VERSION,
        "source_plan_report": rel_path(plan_report),
        "source_preflight_report": rel_path(preflight_report),
        "source_intake_report": rel_path(intake_report),
        "simulation_mode": simulated,
        "block_reason": reason,
        "daily_workouts": [],
        "weekly_summary": [],
        "guardrails": guardrails(simulated),
    }


def guardrails(simulated: bool) -> list[str]:
    rows = [
        "daily draft 只能在 preflight ready 后生成。",
        "这是草案，不是最终训练处方；必须人工复核疼痛、疲劳、跨项训练和真实可用时间。",
        "如果输入来自 simulated 文件，只能用于流程验证，不能直接用于真实训练。",
        "长骑补给只给复核提醒，不生成比赛补给处方。",
    ]
    if simulated:
        rows.insert(0, "当前报告来自 simulated 输入，只证明流程可达。")
    return rows


def build_daily_draft(
    plan_report: Path,
    preflight_report: Path,
    intake_report: Path,
    *,
    allow_simulated: bool = False,
) -> dict[str, Any]:
    plan = load_json(plan_report)
    preflight = load_json(preflight_report)
    intake = load_json(intake_report)
    simulated = is_simulated_preflight(preflight, preflight_report)

    if preflight.get("status") != "ready_for_daily_draft":
        return build_blocked_result(
            "blocked_by_preflight",
            f"preflight status is {preflight.get('status')}; daily draft requires ready_for_daily_draft",
            plan_report,
            preflight_report,
            intake_report,
            simulated,
        )
    if simulated and not allow_simulated:
        return build_blocked_result(
            "blocked_simulated_input_requires_confirmation",
            "simulated preflight requires --allow-simulated",
            plan_report,
            preflight_report,
            intake_report,
            simulated,
        )

    ftp, ftp_test_date = ftp_from_intake(intake)
    schedule = scheduled_items_by_date(plan)
    targets = zone_targets(ftp)
    workouts = []
    for preflight_week in preflight.get("weeks") or []:
        week_number = preflight_week.get("week")
        for ready_item in preflight_week.get("scheduled_bike_days") or []:
            date = ready_item.get("date")
            source_week, plan_item = schedule.get(date, ({}, {}))
            duration = as_int(ready_item.get("available_minutes"), 0)
            slot_type = str(plan_item.get("slot_type") or ready_item.get("slot_type") or "")
            workout_type = str(plan_item.get("workout_type") or ready_item.get("workout_type") or "")
            structure = draft_structure(workout_type, slot_type, duration)
            workouts.append(
                {
                    "week": week_number,
                    "week_start": preflight_week.get("week_start") or source_week.get("week_start"),
                    "date": date,
                    "day": ready_item.get("day") or plan_item.get("day"),
                    "day_label": ready_item.get("day_label") or plan_item.get("day_label"),
                    "slot_type": slot_type,
                    "workout_type": workout_type,
                    "intensity_class": plan_item.get("intensity_class", ""),
                    "available_minutes": duration,
                    "planned_duration_minutes": sum(as_int(row.get("duration_minutes")) for row in structure),
                    "primary_focus": focus_for(workout_type, slot_type),
                    "power_targets": {
                        "basis": "ftp" if ftp else "percent_ftp_only",
                        "ftp_w": ftp,
                        "ftp_test_date": ftp_test_date,
                        "zones": targets,
                    },
                    "structure": structure,
                    "nutrition_notes": nutrition_notes(slot_type, duration),
                    "stop_conditions": stop_conditions(),
                    "source_refs": plan_item.get("source_refs") or [],
                    "draft_boundary": "daily_workout_draft_requires_human_review_before_real_training",
                }
            )

    weekly_summary = []
    for week in preflight.get("weeks") or []:
        week_workouts = [item for item in workouts if item.get("week") == week.get("week")]
        weekly_summary.append(
            {
                "week": week.get("week"),
                "week_start": week.get("week_start"),
                "workout_count": len(week_workouts),
                "planned_minutes_total": sum(as_int(item.get("planned_duration_minutes")) for item in week_workouts),
                "workout_types": [item.get("workout_type") for item in week_workouts],
            }
        )

    return {
        "status": "daily_draft_generated",
        "schema_version": SCHEMA_VERSION,
        "source_plan_report": rel_path(plan_report),
        "source_preflight_report": rel_path(preflight_report),
        "source_intake_report": rel_path(intake_report),
        "source_plan_schema_version": plan.get("schema_version", ""),
        "source_preflight_schema_version": preflight.get("schema_version", ""),
        "simulation_mode": simulated,
        "athlete_context": {
            "athlete_id": plan.get("athlete_id", ""),
            "goal": plan.get("goal", {}),
            "ftp_w": ftp,
            "ftp_test_date": ftp_test_date,
        },
        "daily_workouts": workouts,
        "weekly_summary": weekly_summary,
        "summary": {
            "weeks_total": len(weekly_summary),
            "workouts_total": len(workouts),
            "planned_minutes_total": sum(as_int(item.get("planned_duration_minutes")) for item in workouts),
            "simulation_mode": simulated,
        },
        "guardrails": guardrails(simulated),
    }


def review_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in report.get("daily_workouts") or []:
        main = next((row for row in item.get("structure") or [] if row.get("segment") in {"main", "cadence_drills", "threshold_block", "power_intervals"}), {})
        rows.append(
            {
                "week": item.get("week"),
                "date": item.get("date"),
                "day_label": item.get("day_label"),
                "slot_type": item.get("slot_type"),
                "workout_type": item.get("workout_type"),
                "planned_duration_minutes": item.get("planned_duration_minutes"),
                "primary_focus": item.get("primary_focus"),
                "main_segment": main.get("notes", ""),
                "simulation_mode": report.get("simulation_mode"),
                "review_status": "",
                "review_comment": "",
            }
        )
    return rows


def write_review_files(report: dict[str, Any], review_md: Path, review_csv: Path) -> None:
    rows = review_rows(report)
    review_csv.parent.mkdir(parents=True, exist_ok=True)
    write_csv(
        review_csv,
        [
            "week",
            "date",
            "day_label",
            "slot_type",
            "workout_type",
            "planned_duration_minutes",
            "primary_focus",
            "main_segment",
            "simulation_mode",
            "review_status",
            "review_comment",
        ],
        rows,
    )
    lines = [
        "# Bike Daily Draft Review",
        "",
        f"- status: `{report.get('status')}`",
        f"- simulation_mode: `{report.get('simulation_mode')}`",
        f"- boundary: daily workout draft requires human review before real training",
        "",
        "| week | date | day | slot | workout | minutes | focus |",
        "| --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['week']} | {row['date']} | {row['day_label']} | {row['slot_type']} | "
            f"{row['workout_type']} | {row['planned_duration_minutes']} | {row['primary_focus']} |"
        )
    review_md.parent.mkdir(parents=True, exist_ok=True)
    review_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


def print_markdown(report: dict[str, Any]) -> None:
    summary = report.get("summary") or {}
    print(f"status: {report.get('status')}")
    print(f"schema_version: {report.get('schema_version')}")
    print(f"simulation_mode: {report.get('simulation_mode')}")
    if report.get("block_reason"):
        print(f"block_reason: {report.get('block_reason')}")
    print(
        f"weeks={summary.get('weeks_total', 0)} workouts={summary.get('workouts_total', 0)} "
        f"planned_minutes={summary.get('planned_minutes_total', 0)}"
    )
    for week in report.get("weekly_summary") or []:
        print(
            f"- week {week.get('week')} {week.get('week_start')}: "
            f"workouts={week.get('workout_count')} minutes={week.get('planned_minutes_total')}"
        )


def main() -> int:
    args = parse_args()
    report = build_daily_draft(
        args.plan_report,
        args.preflight_report,
        args.intake_report,
        allow_simulated=args.allow_simulated,
    )
    if args.write_output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.write_review_files:
        write_review_files(report, args.review_md, args.review_csv)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_markdown(report)
        if args.write_output:
            print(f"output={args.output}")
        if args.write_review_files:
            print(f"review_md={args.review_md}")
            print(f"review_csv={args.review_csv}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
