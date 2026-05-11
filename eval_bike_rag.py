#!/usr/bin/env python3
"""
Run bike-specific regression checks for the triathlon RAG pipeline.

This suite verifies the second-layer bike ingest:
- cycling energy/carbohydrate calculator retrieval and deterministic answer.
- power-periodization template retrieval and missing-data guardrail.
- bike workout and brick training source coverage.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import bike_plan_generator
import bike_plan_intake
import bike_plan_candidate
import bike_plan_daily_draft
import bike_plan_daily_preflight
import bike_plan_long_ride_nutrition_review
import bike_plan_review
import rag_answer
from eval_bike_plan_intake import COMPLETE_PAYLOAD


ROOT = Path(__file__).parent.resolve()
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_rag_eval_latest.json"
DEFAULT_CONFLICT_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_conflict_rag_eval_latest.json"
DEFAULT_OVERRIDE_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_override_rag_eval_latest.json"
DEFAULT_OVERRIDE_REVIEW_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_override_rag_eval_input.csv"
DEFAULT_OVERRIDE_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_review_override_rag_eval_plan.json"
DEFAULT_CANDIDATE_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_rag_eval_plan.json"
DEFAULT_CANDIDATE_OVERRIDE_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_rag_eval_override.json"
DEFAULT_CANDIDATE_REVIEW_CSV = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_rag_eval_input.csv"
DEFAULT_CANDIDATE_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_candidate_rag_eval_latest.json"
DEFAULT_DAILY_PREFLIGHT_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_rag_eval_plan.json"
DEFAULT_DAILY_PREFLIGHT_INPUT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_rag_eval_input.json"
DEFAULT_DAILY_PREFLIGHT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_preflight_rag_eval_latest.json"
DEFAULT_DAILY_DRAFT_PLAN_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_rag_eval_plan.json"
DEFAULT_DAILY_DRAFT_INTAKE_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_rag_eval_intake.json"
DEFAULT_DAILY_DRAFT_PREFLIGHT_INPUT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_rag_eval_input.json"
DEFAULT_DAILY_DRAFT_PREFLIGHT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_rag_eval_preflight.json"
DEFAULT_DAILY_DRAFT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_daily_draft_rag_eval_latest.json"
DEFAULT_LONG_RIDE_NUTRITION_REVIEW_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "bike_plan_long_ride_nutrition_review_rag_eval_latest.json"


@dataclass
class BikeEvalCase:
    case_id: str
    name: str
    question: str
    required_answer_groups: list[list[str]] = field(default_factory=list)
    forbidden_answer_patterns: list[str] = field(default_factory=list)
    required_source_groups: list[list[str]] = field(default_factory=list)
    fixture: str | None = None


CASES = [
    BikeEvalCase(
        case_id="bike_energy_calculator",
        name="骑行能量与碳水估算",
        question="骑行 4 小时，FTP 193，平均功率 125，帮我估算这次骑行的热量和碳水消耗，并说明适用边界。",
        required_source_groups=[
            [r"骑行热量与碳水消耗计算模板"],
            [r"IF", r"碳水总量", r"计算链", r"calculator_template"],
        ],
        required_answer_groups=[
            [r"IF.{0,12}0\.648", r"0\.648.{0,12}IF"],
            [r"2048", r"2049"],
            [r"248\.4", r"248"],
            [r"IF.{0,20}(1|0\.55)", r"0\.55.{0,20}IF", r"适用范围"],
        ],
        forbidden_answer_patterns=[
            r"无法.{0,8}(直接)?计算",
            r"需要更多信息.{0,12}(估算|计算)",
        ],
    ),
    BikeEvalCase(
        case_id="bike_periodization_missing_data",
        name="骑行功率课表缺数据拦截",
        question="帮我直接安排接下来 8 周的骑行功率训练课表。",
        required_source_groups=[
            [r"骑行功率周期训练计划模板"],
            [r"Macrocycle", r"Microcycle", r"Volume", r"Intensity", r"WorkLoad", r"peaking"],
        ],
        required_answer_groups=[
            [r"不能.{0,12}直接", r"无法.{0,12}直接", r"不要.{0,12}直接", r"不能直接生成"],
            [r"目标赛事", r"赛事日期", r"比赛"],
            [r"FTP", r"当前 FTP"],
            [r"最近.{0,12}训练", r"四到六周", r"CTL", r"ATL", r"TSB"],
            [r"每周.{0,12}(时间|天)", r"可训练"],
        ],
        forbidden_answer_patterns=[
            r"第\s*1\s*周.{0,120}第\s*2\s*周",
            r"周一.{0,80}周二.{0,80}周三",
            r"VO2.{0,40}甜点.{0,40}阈值.{0,40}恢复",
        ],
    ),
    BikeEvalCase(
        case_id="bike_base_workout_types",
        name="基础期骑行课型召回",
        question="基础期骑行训练应该优先安排哪些课型？耐力骑、长骑、踏频、冲刺和阈值骑分别该怎么放？",
        required_source_groups=[
            [r"Triathlete Magazine's Complete Triathlon Book - Bike Training"],
            [r"Endurance Ride", r"Long Ride", r"Cadence Workout", r"Power Intervals", r"Threshold Ride"],
        ],
        required_answer_groups=[
            [r"耐力骑", r"Endurance Ride"],
            [r"长骑", r"Long Ride"],
            [r"踏频", r"Cadence"],
            [r"冲刺", r"Power Intervals", r"sprint"],
        ],
        forbidden_answer_patterns=[
            r"只做.{0,8}阈值",
            r"每天.{0,12}(冲刺|阈值|VO2)",
        ],
    ),
    BikeEvalCase(
        case_id="bike_brick_guardrail",
        name="Brick 训练与高强度计数",
        question="大铁备赛里 brick 训练怎么安排？如果骑跑都是高强度，需要怎么计入一周总强度？",
        required_source_groups=[
            [r"Triathlete Magazine's Complete Triathlon Book - Bike Training"],
            [r"brick", r"bike ride followed immediately by a run", r"high-intensity workouts"],
        ],
        required_answer_groups=[
            [r"骑.{0,8}跑", r"bike.{0,20}run", r"自行车.{0,12}跑步"],
            [r"高强度", r"强度"],
            [r"计入", r"算入", r"不要.{0,16}叠加"],
        ],
        forbidden_answer_patterns=[
            r"每天.{0,12}brick",
            r"不需要.{0,12}考虑.{0,12}强度",
        ],
    ),
    BikeEvalCase(
        case_id="bike_plan_slot_explanation",
        name="周级课表槽位来源解释",
        question="为什么第1周周二安排 easy / Endurance Ride？",
        required_source_groups=[
            [r"Triathlete Magazine's Complete Triathlon Book - Bike Training"],
            [r"Endurance Ride", r"endurance rides", r"foundation"],
        ],
        required_answer_groups=[
            [r"第\s*1\s*周", r"周二"],
            [r"easy", r"Endurance Ride"],
            [r"base", r"load"],
            [r"周一", r"约束"],
            [r"周日", r"Long Ride"],
            [r"\[S1\]"],
        ],
        forbidden_answer_patterns=[
            r"[0-9]{2,4}\s*W",
            r"目标功率",
            r"target watts",
            r"[0-9]+\s*组",
            r"4x",
        ],
    ),
    BikeEvalCase(
        case_id="bike_plan_week_explanation",
        name="周级课表整周来源解释",
        question="解释第1周所有安排的依据。",
        required_source_groups=[
            [r"Triathlete Magazine's Complete Triathlon Book - Bike Training"],
            [r"Endurance Ride", r"Long Ride", r"Cadence Workout"],
        ],
        required_answer_groups=[
            [r"第\s*1\s*周", r"整体"],
            [r"周二.{0,20}easy.{0,20}Endurance Ride"],
            [r"周三.{0,20}technical.{0,20}Cadence Workout"],
            [r"周日.{0,20}long.{0,20}Long Ride"],
            [r"base", r"load"],
            [r"跑步 hard", r"下肢力量"],
            [r"\[S1\]"],
        ],
        forbidden_answer_patterns=[
            r"[0-9]{2,4}\s*W",
            r"目标功率",
            r"target watts",
            r"[0-9]+\s*组",
            r"4x",
        ],
    ),
    BikeEvalCase(
        case_id="bike_plan_conflict_explanation",
        name="固定跨项日期冲突来源解释",
        question="第1周为什么标记了固定跨项日期冲突？",
        fixture="conflict_plan",
        required_source_groups=[
            [r"Triathlete Magazine's Complete Triathlon Book - Bike Training"],
            [r"Endurance Ride", r"Long Ride", r"Threshold Ride"],
        ],
        required_answer_groups=[
            [r"第\s*1\s*周", r"固定跨项日期冲突"],
            [r"周二", r"bike hard", r"恢复窗口"],
            [r"周日", r"bike long"],
            [r"跑步 hard", r"游泳 hard", r"下肢力量"],
            [r"人工复核", r"不直接改课表", r"不直接修改"],
        ],
        forbidden_answer_patterns=[
            r"[0-9]{2,4}\s*W",
            r"目标功率",
            r"target watts",
            r"[0-9]+\s*组",
            r"4x",
        ],
    ),
    BikeEvalCase(
        case_id="bike_review_override_summary",
        name="人工复核 override 汇总",
        question="哪些周被人工要求修改？",
        fixture="override",
        required_answer_groups=[
            [r"第\s*1\s*周"],
            [r"override_requested", r"人工要求修改"],
            [r"避开周二", r"保留周日 long"],
            [r"不要直接覆盖", r"不要直接修改", r"不直接覆盖"],
        ],
        forbidden_answer_patterns=[
            r"[0-9]{2,4}\s*W",
            r"目标功率",
            r"target watts",
            r"[0-9]+\s*组",
            r"4x",
        ],
    ),
    BikeEvalCase(
        case_id="bike_candidate_diff_explanation",
        name="第二版候选排程差异解释",
        question="第二版候选排程和第一版差在哪？",
        fixture="candidate_diff",
        required_source_groups=[
            [r"Triathlete Magazine's Complete Triathlon Book - Bike Training"],
            [r"Threshold Ride", r"Long Ride"],
        ],
        required_answer_groups=[
            [r"第\s*1\s*周"],
            [r"第一版", r"候选版", r"第二版"],
            [r"hard", r"周二"],
            [r"周日", r"long", r"保留"],
            [r"不要直接覆盖", r"不要直接修改", r"不直接覆盖"],
        ],
        forbidden_answer_patterns=[
            r"[0-9]{2,4}\s*W",
            r"目标功率",
            r"target watts",
            r"[0-9]+\s*组",
            r"4x",
        ],
    ),
    BikeEvalCase(
        case_id="bike_daily_preflight_missing_summary",
        name="每日课表 preflight 缺数据解释",
        question="进入每日训练课前还缺哪些 daily preflight 数据？",
        fixture="daily_preflight",
        required_answer_groups=[
            [r"needs_more_daily_data", r"还缺数据"],
            [r"daily_availability"],
            [r"daily_status"],
            [r"fixed_sessions"],
            [r"不要.{0,8}生成每日训练课", r"不能进入每日训练课"],
        ],
        forbidden_answer_patterns=[
            r"[0-9]{2,4}\s*W",
            r"目标功率",
            r"target watts",
            r"[0-9]+\s*组",
            r"4x",
        ],
    ),
    BikeEvalCase(
        case_id="bike_daily_draft_summary",
        name="每日训练课草案解释",
        question="第1周每日训练课草案生成了什么？",
        fixture="daily_draft",
        required_source_groups=[
            [r"Triathlete Magazine's Complete Triathlon Book - Bike Training"],
            [r"Endurance Ride", r"Long Ride", r"Cadence Workout"],
        ],
        required_answer_groups=[
            [r"daily_draft_generated", r"草案已生成"],
            [r"第\s*1\s*周"],
            [r"周二.{0,40}Endurance Ride"],
            [r"周三.{0,40}Cadence Workout"],
            [r"周日.{0,40}Long Ride"],
            [r"分钟"],
            [r"人工复核"],
        ],
    ),
    BikeEvalCase(
        case_id="bike_long_ride_nutrition_review_summary",
        name="长骑补给复核解释",
        question="长骑补给复核结果是什么？",
        fixture="long_ride_nutrition_review",
        required_source_groups=[
            [r"骑行热量与碳水消耗计算模板"],
            [r"IF", r"碳水总量", r"计算链", r"calculator_template"],
        ],
        required_answer_groups=[
            [r"长骑补给复核已接上", r"热量/碳水模板"],
            [r"没有生成真实补给处方", r"不要生成每小时摄入克数"],
            [r"kcal"],
            [r"碳水消耗"],
            [r"平均功率"],
            [r"排汗率", r"碳水耐受"],
        ],
        forbidden_answer_patterns=[
            r"每小时摄入\s*[0-9]+",
            r"[0-9]+\s*g/h",
        ],
    ),
]


def compile_flags() -> int:
    return re.IGNORECASE | re.DOTALL


def any_pattern(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text, compile_flags()) for pattern in patterns)


def source_text(result: dict[str, Any]) -> str:
    return "\n".join(json.dumps(source, ensure_ascii=False) for source in result["sources"])


def check_groups(groups: list[list[str]], text: str, prefix: str) -> list[str]:
    failures = []
    for index, patterns in enumerate(groups, start=1):
        if not any_pattern(patterns, text):
            failures.append(f"{prefix} group {index} not matched: {patterns}")
    return failures


def check_forbidden(patterns: list[str], text: str) -> list[str]:
    failures = []
    for pattern in patterns:
        if re.search(pattern, text, compile_flags()):
            failures.append(f"forbidden pattern matched: {pattern}")
    return failures


def ready_payload(input_payload: dict[str, Any], weeks: int = 8) -> dict[str, Any]:
    intake = bike_plan_intake.evaluate(input_payload, requested_weeks=weeks)
    return bike_plan_intake.result_to_dict(intake)


def prepare_conflict_plan_fixture() -> Path:
    payload = ready_payload(
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
    )
    result = bike_plan_generator.build_generator_output(payload)
    DEFAULT_CONFLICT_PLAN_REPORT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_CONFLICT_PLAN_REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return DEFAULT_CONFLICT_PLAN_REPORT


def prepare_override_fixture() -> Path:
    payload = ready_payload(COMPLETE_PAYLOAD, weeks=8)
    result = bike_plan_generator.build_generator_output(payload)
    DEFAULT_OVERRIDE_PLAN_REPORT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OVERRIDE_PLAN_REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = bike_plan_generator.build_review_export_rows(result)
    rows[0].update(
        {
            "human_review_status": "override_requested",
            "review_comment": "周二已经固定跑步 hard，需要人工调整本周 bike 排程。",
            "override_request": "第1周避开周二 bike hard，保留周日 long。",
        }
    )
    with DEFAULT_OVERRIDE_REVIEW_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=bike_plan_generator.REVIEW_EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    override = bike_plan_review.build_review_override(DEFAULT_OVERRIDE_REVIEW_CSV, DEFAULT_OVERRIDE_PLAN_REPORT)
    DEFAULT_OVERRIDE_REPORT.write_text(json.dumps(override, ensure_ascii=False, indent=2), encoding="utf-8")
    return DEFAULT_OVERRIDE_REPORT


def prepare_candidate_fixture() -> tuple[Path, Path, Path]:
    payload = ready_payload(
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
    )
    source_plan = bike_plan_generator.build_generator_output(payload)
    DEFAULT_CANDIDATE_PLAN_REPORT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_CANDIDATE_PLAN_REPORT.write_text(json.dumps(source_plan, ensure_ascii=False, indent=2), encoding="utf-8")
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
    with DEFAULT_CANDIDATE_REVIEW_CSV.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=bike_plan_generator.REVIEW_EXPORT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    override = bike_plan_review.build_review_override(DEFAULT_CANDIDATE_REVIEW_CSV, DEFAULT_CANDIDATE_PLAN_REPORT)
    DEFAULT_CANDIDATE_OVERRIDE_REPORT.write_text(json.dumps(override, ensure_ascii=False, indent=2), encoding="utf-8")
    candidate = bike_plan_candidate.build_candidate(DEFAULT_CANDIDATE_PLAN_REPORT, DEFAULT_CANDIDATE_OVERRIDE_REPORT)
    DEFAULT_CANDIDATE_REPORT.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
    return DEFAULT_CANDIDATE_PLAN_REPORT, DEFAULT_CANDIDATE_OVERRIDE_REPORT, DEFAULT_CANDIDATE_REPORT


def prepare_daily_preflight_fixture() -> Path:
    payload = ready_payload(COMPLETE_PAYLOAD, weeks=8)
    source_plan = bike_plan_generator.build_generator_output(payload)
    DEFAULT_DAILY_PREFLIGHT_PLAN_REPORT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_DAILY_PREFLIGHT_PLAN_REPORT.write_text(json.dumps(source_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    DEFAULT_DAILY_PREFLIGHT_INPUT.write_text("{}", encoding="utf-8")
    result = bike_plan_daily_preflight.build_preflight(
        DEFAULT_DAILY_PREFLIGHT_PLAN_REPORT,
        DEFAULT_DAILY_PREFLIGHT_INPUT,
    )
    DEFAULT_DAILY_PREFLIGHT_REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return DEFAULT_DAILY_PREFLIGHT_REPORT


def daily_draft_preflight_input(plan: dict[str, Any]) -> dict[str, Any]:
    daily_availability = []
    daily_status = []
    for week in plan.get("weekly_plan") or []:
        for item in week.get("weekday_schedule") or []:
            daily_availability.append(
                {
                    "week": week.get("week"),
                    "date": item.get("date"),
                    "day": item.get("day"),
                    "available_minutes": 120 if item.get("slot_type") == "long" else 60,
                    "can_bike": True,
                }
            )
            daily_status.append(
                {
                    "week": week.get("week"),
                    "date": item.get("date"),
                    "day": item.get("day"),
                    "fatigue": "normal",
                    "pain_status": "none",
                    "sleep_quality": "good",
                }
            )
    return {
        "schema_version": bike_plan_daily_preflight.INPUT_SCHEMA_VERSION,
        "daily_availability": daily_availability,
        "daily_status": daily_status,
        "fixed_sessions": [],
    }


def prepare_daily_draft_fixture() -> Path:
    intake = ready_payload(COMPLETE_PAYLOAD, weeks=8)
    source_plan = bike_plan_generator.build_generator_output(intake)
    DEFAULT_DAILY_DRAFT_PLAN_REPORT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_DAILY_DRAFT_INTAKE_REPORT.write_text(json.dumps(intake, ensure_ascii=False, indent=2), encoding="utf-8")
    DEFAULT_DAILY_DRAFT_PLAN_REPORT.write_text(json.dumps(source_plan, ensure_ascii=False, indent=2), encoding="utf-8")
    DEFAULT_DAILY_DRAFT_PREFLIGHT_INPUT.write_text(
        json.dumps(daily_draft_preflight_input(source_plan), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    preflight = bike_plan_daily_preflight.build_preflight(
        DEFAULT_DAILY_DRAFT_PLAN_REPORT,
        DEFAULT_DAILY_DRAFT_PREFLIGHT_INPUT,
    )
    DEFAULT_DAILY_DRAFT_PREFLIGHT_REPORT.write_text(json.dumps(preflight, ensure_ascii=False, indent=2), encoding="utf-8")
    draft = bike_plan_daily_draft.build_daily_draft(
        DEFAULT_DAILY_DRAFT_PLAN_REPORT,
        DEFAULT_DAILY_DRAFT_PREFLIGHT_REPORT,
        DEFAULT_DAILY_DRAFT_INTAKE_REPORT,
    )
    DEFAULT_DAILY_DRAFT_REPORT.write_text(json.dumps(draft, ensure_ascii=False, indent=2), encoding="utf-8")
    return DEFAULT_DAILY_DRAFT_REPORT


def prepare_long_ride_nutrition_review_fixture() -> Path:
    daily_draft_report = prepare_daily_draft_fixture()
    review = bike_plan_long_ride_nutrition_review.build_review(
        daily_draft_report,
        bike_plan_long_ride_nutrition_review.DEFAULT_DB,
    )
    DEFAULT_LONG_RIDE_NUTRITION_REVIEW_REPORT.write_text(
        json.dumps(review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return DEFAULT_LONG_RIDE_NUTRITION_REVIEW_REPORT


def config_for_case(config: rag_answer.RagConfig, case: BikeEvalCase) -> rag_answer.RagConfig:
    if case.fixture == "conflict_plan":
        return replace(config, bike_plan_report=prepare_conflict_plan_fixture())
    if case.fixture == "override":
        return replace(config, review_override=prepare_override_fixture())
    if case.fixture == "candidate_diff":
        plan_report, override_report, candidate_report = prepare_candidate_fixture()
        return replace(
            config,
            bike_plan_report=plan_report,
            review_override=override_report,
            candidate_report=candidate_report,
        )
    if case.fixture == "daily_preflight":
        return replace(config, daily_preflight_report=prepare_daily_preflight_fixture())
    if case.fixture == "daily_draft":
        return replace(config, daily_draft_report=prepare_daily_draft_fixture())
    if case.fixture == "long_ride_nutrition_review":
        return replace(
            config,
            long_ride_nutrition_review_report=prepare_long_ride_nutrition_review_fixture(),
        )
    return config


def run_case(case: BikeEvalCase, config: rag_answer.RagConfig, mode: str) -> dict[str, Any]:
    case_config = config_for_case(config, case)
    result = rag_answer.answer_question(case.question, case_config)
    failures = []
    sources = source_text(result)
    failures.extend(check_groups(case.required_source_groups, sources, "source"))
    if mode == "answer":
        answer = result["answer"]
        failures.extend(check_groups(case.required_answer_groups, answer, "answer"))
        failures.extend(check_forbidden(case.forbidden_answer_patterns, answer))
    return {
        "case_id": case.case_id,
        "name": case.name,
        "passed": not failures,
        "failures": failures,
        "answer": result["answer"],
        "sources": result["sources"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["retrieval", "answer"], default="answer")
    parser.add_argument("--case", choices=[case.case_id for case in CASES], default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true")

    parser.add_argument("--db", type=Path, default=rag_answer.DEFAULT_DB)
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument("--embedding-ollama-url", default=os.getenv("TRI_RAG_EMBEDDING_OLLAMA_URL", rag_answer.DEFAULT_EMBEDDING_OLLAMA_URL))
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--expand-neighbors", type=int, default=1)
    parser.add_argument("--lexical-weight", type=float, default=0.6)

    parser.add_argument("--provider", choices=["ollama", "openai-compatible", "dry-run"], default=os.getenv("TRI_RAG_PROVIDER", "ollama"))
    parser.add_argument("--chat-model", default=os.getenv("TRI_RAG_CHAT_MODEL", rag_answer.DEFAULT_CHAT_MODEL))
    parser.add_argument("--chat-base-url", default=os.getenv("TRI_RAG_CHAT_BASE_URL", rag_answer.DEFAULT_EMBEDDING_OLLAMA_URL))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=900)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--bike-plan-report", type=Path, default=rag_answer.DEFAULT_BIKE_PLAN_REPORT)
    parser.add_argument("--review-override", type=Path, default=rag_answer.DEFAULT_REVIEW_OVERRIDE)
    parser.add_argument("--candidate-report", type=Path, default=rag_answer.DEFAULT_CANDIDATE_REPORT)
    parser.add_argument("--daily-preflight-report", type=Path, default=rag_answer.DEFAULT_DAILY_PREFLIGHT_REPORT)
    parser.add_argument("--daily-draft-report", type=Path, default=rag_answer.DEFAULT_DAILY_DRAFT_REPORT)
    parser.add_argument("--long-ride-nutrition-review-report", type=Path, default=rag_answer.DEFAULT_LONG_RIDE_NUTRITION_REVIEW_REPORT)
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> rag_answer.RagConfig:
    provider = "dry-run" if args.mode == "retrieval" else args.provider
    return rag_answer.RagConfig(
        retrieval=rag_answer.RetrievalConfig(
            db=args.db,
            embedding_model=args.embedding_model,
            embedding_ollama_url=args.embedding_ollama_url,
            top_k=args.top_k,
            expand_neighbors=args.expand_neighbors,
            hybrid=True,
            lexical_weight=args.lexical_weight,
        ),
        generation=rag_answer.GenerationConfig(
            provider=provider,
            chat_model=args.chat_model,
            chat_base_url=args.chat_base_url,
            api_key=os.getenv(args.api_key_env) if args.api_key_env else None,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
        ),
        bike_plan_report=args.bike_plan_report,
        review_override=args.review_override,
        candidate_report=args.candidate_report,
        daily_preflight_report=args.daily_preflight_report,
        daily_draft_report=args.daily_draft_report,
        long_ride_nutrition_review_report=args.long_ride_nutrition_review_report,
    )


def print_summary(report: dict[str, Any]) -> None:
    passed = sum(1 for item in report["results"] if item["passed"])
    total = len(report["results"])
    print(f"Bike RAG eval: {passed}/{total} passed ({report['mode']})")
    for item in report["results"]:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"- {status} {item['case_id']}: {item['name']}")
        for failure in item["failures"]:
            print(f"  - {failure}")


def main() -> int:
    args = parse_args()
    selected = [case for case in CASES if args.case in (None, case.case_id)]
    config = build_config(args)
    results = [run_case(case, config, args.mode) for case in selected]
    report = {
        "mode": args.mode,
        "provider": config.generation.provider,
        "chat_model": config.generation.chat_model,
        "db": str(config.retrieval.db),
        "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_summary(report)
        print(f"report={args.report}")
    return 0 if all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
