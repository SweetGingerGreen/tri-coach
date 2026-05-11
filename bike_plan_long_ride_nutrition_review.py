#!/usr/bin/env python3
"""
Review long-ride nutrition needs from daily bike workout drafts.

This layer connects long-ride drafts to the approved cycling energy/carbohydrate
calculator template. It estimates energy and substrate demand, then blocks any
real fueling prescription until individual tolerance and environment data are
known.
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import rag_answer


ROOT = Path(__file__).parent.resolve()
DEFAULT_DAILY_DRAFT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_latest.json"
DEFAULT_DB = ROOT / "triathlon-knowledge" / "metadata" / "vectors" / "triathlon_core_v2_bge_m3.sqlite"
DEFAULT_OUTPUT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_long_ride_nutrition_review_latest.json"
DEFAULT_REVIEW_MD = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_long_ride_nutrition_review_latest.md"
DEFAULT_REVIEW_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_long_ride_nutrition_review_latest.csv"
SCHEMA_VERSION = "bike_plan_long_ride_nutrition_review_v0.1"
DEFAULT_LONG_RIDE_IF = 0.65
MISSING_FOR_PRESCRIPTION = [
    "body_weight_kg",
    "environment_temperature",
    "sweat_rate_l_per_hour",
    "fluid_tolerance_l_per_hour",
    "carb_tolerance_g_per_hour",
    "sodium_tolerance_or_salty_sweater_status",
    "previous_long_ride_fueling_log",
    "gi_symptoms_history",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-draft-report", type=Path, default=DEFAULT_DAILY_DRAFT_REPORT)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--review-md", type=Path, default=DEFAULT_REVIEW_MD)
    parser.add_argument("--review-csv", type=Path, default=DEFAULT_REVIEW_CSV)
    parser.add_argument("--estimated-if", type=float, default=DEFAULT_LONG_RIDE_IF)
    parser.add_argument("--allow-simulated", action="store_true")
    parser.add_argument("--write-output", action="store_true")
    parser.add_argument("--write-review-files", action="store_true")
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


def as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def round_metrics(values: dict[str, float]) -> dict[str, float]:
    return {
        "intensity_factor": round(values["intensity_factor"], 3),
        "mechanical_kj": round(values["mechanical_kj"], 1),
        "mechanical_kj_per_hour": round(values["mechanical_kj_per_hour"], 1),
        "total_kcal": round(values["total_kcal"], 1),
        "qr": round(values["qr"], 3),
        "carb_fraction": round(values["carb_fraction"], 3),
        "carb_g": round(values["carb_g"], 1),
        "carb_g_per_min": round(values["carb_g_per_min"], 2),
        "fat_g": round(values["fat_g"], 1),
        "fat_g_per_min": round(values["fat_g_per_min"], 2),
    }


def calculator_sources(db_path: Path) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    rows = conn.execute(
        """
        SELECT chunk_id, text, metadata_json
        FROM chunks
        WHERE metadata_json LIKE '%骑行热量与碳水消耗计算模板%'
        ORDER BY chunk_id
        """
    ).fetchall()
    conn.close()
    sources = []
    for index, (chunk_id, text, metadata_json) in enumerate(rows, start=1):
        meta = json.loads(metadata_json)
        sources.append(
            {
                "label": f"C{index}",
                "chunk_id": chunk_id,
                "title": meta.get("title", ""),
                "domain": meta.get("domain", ""),
                "trust_level": meta.get("trust_level", ""),
                "heading": meta.get("heading", ""),
                "source_path": meta.get("source_path", ""),
                "excerpt": rag_answer.vector_store.snippet(text, 360),
            }
        )
    return sources


def long_ride_workouts(draft: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in draft.get("daily_workouts") or []
        if item.get("slot_type") == "long" or "long" in str(item.get("workout_type") or "").lower()
    ]


def blocked_result(
    status: str,
    reason: str,
    daily_draft_report: Path,
    db_path: Path,
    simulation_mode: bool,
) -> dict[str, Any]:
    return {
        "status": status,
        "schema_version": SCHEMA_VERSION,
        "source_daily_draft_report": rel_path(daily_draft_report),
        "source_db": rel_path(db_path),
        "simulation_mode": simulation_mode,
        "block_reason": reason,
        "calculator_sources": [],
        "long_ride_reviews": [],
        "summary": {
            "long_rides_total": 0,
            "reviewed_long_rides": 0,
            "simulation_mode": simulation_mode,
        },
        "guardrails": guardrails(simulation_mode),
    }


def guardrails(simulation_mode: bool) -> list[str]:
    rows = [
        "本层只做长骑能量与碳水消耗复核，不生成比赛补给处方。",
        "平均功率缺失时只能使用 IF 估算，必须在真实训练前用实际目标功率或历史长骑功率复核。",
        "缺少体重、排汗率、碳水耐受、液体/钠耐受和既往补给记录时，不给每小时摄入克数或比赛日时间表。",
    ]
    if simulation_mode:
        rows.insert(0, "当前输入来自 simulated daily draft，只能用于流程验证。")
    return rows


def review_one_long_ride(
    workout: dict[str, Any],
    calculator_refs: list[dict[str, Any]],
    estimated_if: float,
) -> dict[str, Any]:
    duration = as_int(workout.get("planned_duration_minutes") or workout.get("available_minutes"), 0)
    power_targets = workout.get("power_targets") or {}
    ftp = as_float(power_targets.get("ftp_w"))
    avg_power = ftp * estimated_if if ftp and duration > 0 else None
    calculation = None
    if ftp and avg_power and duration > 0:
        calculation = round_metrics(
            rag_answer.bike_calculator_result(
                {
                    "ftp": ftp,
                    "avg_power": avg_power,
                    "duration_minutes": float(duration),
                }
            )
        )

    return {
        "week": workout.get("week"),
        "date": workout.get("date"),
        "day": workout.get("day"),
        "day_label": workout.get("day_label"),
        "workout_type": workout.get("workout_type"),
        "planned_duration_minutes": duration,
        "calculation_status": "estimated_from_draft" if calculation else "missing_ftp_or_duration",
        "calculation_inputs": {
            "ftp_w": ftp,
            "duration_minutes": duration,
            "estimated_if": estimated_if,
            "estimated_avg_power_w": round(avg_power, 1) if avg_power else None,
            "avg_power_source": "estimated_from_long_ride_z2_if_not_real_power",
            "efficiency": 0.21,
        },
        "estimated_energy": calculation,
        "calculator_refs": calculator_refs,
        "nutrition_prescription_status": "blocked_missing_individual_tolerance_data",
        "missing_for_prescription": MISSING_FOR_PRESCRIPTION,
        "review_questions": [
            "这次长骑真实目标平均功率或历史同类长骑平均功率是多少？",
            "训练环境温度和预计排汗率是多少？",
            "过去长骑每小时能稳定吃多少碳水且不胃胀？",
            "这次准备使用哪些已经验证过的补给？",
        ],
        "decision_boundary": "energy_carb_estimate_only_no_hourly_fueling_prescription",
    }


def build_review(
    daily_draft_report: Path,
    db_path: Path,
    *,
    estimated_if: float = DEFAULT_LONG_RIDE_IF,
    allow_simulated: bool = False,
) -> dict[str, Any]:
    draft = load_json(daily_draft_report)
    simulation_mode = bool(draft.get("simulation_mode"))
    if draft.get("status") != "daily_draft_generated":
        return blocked_result(
            "blocked_by_daily_draft",
            f"daily draft status is {draft.get('status')}; nutrition review requires daily_draft_generated",
            daily_draft_report,
            db_path,
            simulation_mode,
        )
    if simulation_mode and not allow_simulated:
        return blocked_result(
            "blocked_simulated_input_requires_confirmation",
            "simulated daily draft requires --allow-simulated",
            daily_draft_report,
            db_path,
            simulation_mode,
        )

    refs = calculator_sources(db_path)
    workouts = long_ride_workouts(draft)
    reviews = [review_one_long_ride(item, refs, estimated_if) for item in workouts]
    return {
        "status": "long_ride_nutrition_review_generated",
        "schema_version": SCHEMA_VERSION,
        "source_daily_draft_report": rel_path(daily_draft_report),
        "source_daily_draft_schema_version": draft.get("schema_version", ""),
        "source_db": rel_path(db_path),
        "simulation_mode": simulation_mode,
        "calculator_template": {
            "status": "available" if refs else "missing",
            "title": "骑行热量与碳水消耗计算模板",
            "source_chunks": refs,
        },
        "long_ride_reviews": reviews,
        "summary": {
            "long_rides_total": len(workouts),
            "reviewed_long_rides": len(reviews),
            "estimated_if": estimated_if,
            "simulation_mode": simulation_mode,
            "nutrition_prescriptions_generated": 0,
        },
        "guardrails": guardrails(simulation_mode),
    }


def review_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in report.get("long_ride_reviews") or []:
        calc = item.get("estimated_energy") or {}
        inputs = item.get("calculation_inputs") or {}
        rows.append(
            {
                "week": item.get("week"),
                "date": item.get("date"),
                "day_label": item.get("day_label"),
                "workout_type": item.get("workout_type"),
                "duration_minutes": item.get("planned_duration_minutes"),
                "estimated_avg_power_w": inputs.get("estimated_avg_power_w"),
                "estimated_if": inputs.get("estimated_if"),
                "total_kcal": calc.get("total_kcal"),
                "carb_g": calc.get("carb_g"),
                "carb_g_per_min": calc.get("carb_g_per_min"),
                "fat_g": calc.get("fat_g"),
                "prescription_status": item.get("nutrition_prescription_status"),
                "simulation_mode": report.get("simulation_mode"),
                "review_status": "",
                "review_comment": "",
            }
        )
    return rows


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_review_files(report: dict[str, Any], review_md: Path, review_csv: Path) -> None:
    rows = review_rows(report)
    write_csv(
        review_csv,
        [
            "week",
            "date",
            "day_label",
            "workout_type",
            "duration_minutes",
            "estimated_avg_power_w",
            "estimated_if",
            "total_kcal",
            "carb_g",
            "carb_g_per_min",
            "fat_g",
            "prescription_status",
            "simulation_mode",
            "review_status",
            "review_comment",
        ],
        rows,
    )
    lines = [
        "# Long Ride Nutrition Review",
        "",
        f"- status: `{report.get('status')}`",
        f"- simulation_mode: `{report.get('simulation_mode')}`",
        "- boundary: energy/carb estimate only, no hourly fueling prescription",
        "",
        "| week | date | day | duration | avg power | kcal | carb g | status |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['week']} | {row['date']} | {row['day_label']} | {row['duration_minutes']} | "
            f"{row['estimated_avg_power_w']} | {row['total_kcal']} | {row['carb_g']} | {row['prescription_status']} |"
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
        f"long_rides={summary.get('long_rides_total', 0)} "
        f"reviewed={summary.get('reviewed_long_rides', 0)} "
        f"prescriptions={summary.get('nutrition_prescriptions_generated', 0)}"
    )
    for item in report.get("long_ride_reviews") or []:
        calc = item.get("estimated_energy") or {}
        inputs = item.get("calculation_inputs") or {}
        print(
            f"- week {item.get('week')} {item.get('date')}: "
            f"avg_power={inputs.get('estimated_avg_power_w')}W "
            f"kcal={calc.get('total_kcal')} carb_g={calc.get('carb_g')} "
            f"status={item.get('nutrition_prescription_status')}"
        )


def main() -> int:
    args = parse_args()
    report = build_review(
        args.daily_draft_report,
        args.db,
        estimated_if=args.estimated_if,
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
