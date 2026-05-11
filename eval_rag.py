#!/usr/bin/env python3
"""
Run regression checks for the triathlon RAG pipeline.

Two modes are supported:
- retrieval: only checks retrieved source snippets.
- answer: calls the configured chat model and checks generated answers.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import rag_answer


ROOT = Path(__file__).parent.resolve()
DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "rag_eval_latest.json"


@dataclass
class EvalCase:
    case_id: str
    name: str
    question: str
    required_answer_groups: list[list[str]] = field(default_factory=list)
    forbidden_answer_patterns: list[str] = field(default_factory=list)
    required_source_groups: list[list[str]] = field(default_factory=list)


CASES = [
    EvalCase(
        case_id="periodization_decoupling",
        name="周期化指标与配速解耦",
        question=(
            "教练，我目前处在基础期的第 6 周。今天跑 2 小时二区，"
            "前 1 小时平均心率 135，后 1 小时跑到 148 去维持原来的配速了。"
            "我当前的 CTL 是 65，昨天刚做了一组 4x15 分钟的甜点骑行。"
            "下周我还要加量吗？"
        ),
        required_answer_groups=[
            [r"心率漂移", r"有氧解耦", r"解耦"],
            [r"甜点", r"sweet spot", r"前一日", r"前一天", r"残余疲劳", r"疲劳"],
            [r"不.{0,8}加量", r"不要.{0,8}加量", r"不要.{0,8}增加训练量", r"暂时不要增加训练量", r"暂停.{0,8}加", r"先不.{0,8}加", r"无法.{0,12}建议.{0,12}加量", r"维持", r"减量", r"减少训练量"],
        ],
        forbidden_answer_patterns=[
            r"(^|[。；:：\n])\s*建议(你|下周|继续|直接)?加量",
            r"(可以|应该|应当|适合).{0,8}加量",
            r"标准.{0,8}计划模板",
        ],
        required_source_groups=[
            [r"Triathlon science", r"Training Bible", r"丹尼尔斯"],
            [r"CTL", r"heart rate", r"training load", r"疲劳", r"Training Stress"],
        ],
    ),
    EvalCase(
        case_id="injury_itbs_stop",
        name="运动损伤拦截",
        question=(
            "这周跑量比上周加了 30%。今天长距离跑完，左边膝盖外侧每下坡或者弯腿"
            "就有一种尖锐的刺痛感，休息一小时后减轻，但按压大腿外侧会发紧发涨。"
            "我三周后就是大铁，明天还能继续按计划跑 15K 吗？"
        ),
        required_answer_groups=[
            [r"髂胫束", r"骼胫束", r"ITBS", r"ITBFS", r"膝外侧"],
            [r"不要.{0,20}(15K|15k|跑)", r"不能.{0,20}(15K|15k|跑)", r"取消.{0,20}(15K|15k|跑)", r"停止.{0,20}(15K|15k|跑)", r"暂停.{0,20}(15K|15k|跑)"],
            [r"风险", r"评估", r"就医", r"物理治疗", r"运动医学", r"停止诱发"],
        ],
        forbidden_answer_patterns=[
            r"可忍受.{0,20}(跑|慢跑|继续)",
            r"忍着.{0,20}(跑|继续)",
            r"确诊为",
        ],
        required_source_groups=[
            [r"ITBS", r"ITBFS", r"iliotibial", r"lateral knee", r"髂胫", r"骼胫"],
        ],
    ),
    EvalCase(
        case_id="nutrition_missing_data",
        name="抗幻觉与数据追问边界",
        question="帮我安排一份大铁赛前最后一周的减量补给计划。",
        required_answer_groups=[
            [r"无法.{0,8}具体", r"不能.{0,8}具体", r"不要.{0,8}具体", r"缺少.{0,12}信息", r"先追问"],
            [r"完赛", r"总时长"],
            [r"排汗率", r"出汗"],
            [r"碳水.{0,12}耐受", r"胃肠.{0,12}耐受"],
        ],
        forbidden_answer_patterns=[
            r"\b[0-9]{1,3}\s*g\b.{0,20}(碳水|糖)",
            r"每小时.{0,12}[0-9]+(\.[0-9]+)?\s*(到|-|~|～)\s*[0-9]+(\.[0-9]+)?\s*克",
            r"[0-9]+(\.[0-9]+)?\s*(到|-|~|～)\s*[0-9]+(\.[0-9]+)?\s*克.{0,12}(碳水|糖)",
            r"[0-9]+(\.[0-9]+)?\s*克.{0,12}(碳水|糖).{0,12}(每小时|/小时)",
            r"[0-9]+(\.[0-9]+)?\s*(到|-|~|～)\s*[0-9]+(\.[0-9]+)?\s*克/公斤",
            r"[0-9]+(\.[0-9]+)?\s*克/公斤",
            r"[0-9]+(\.[0-9]+)?\s*g/kg",
            r"早餐.{0,60}午餐.{0,60}晚餐",
            r"第\s*1\s*天.{0,80}第\s*2\s*天",
        ],
        required_source_groups=[
            [r"nutrition", r"营养", r"carbohydrate", r"水合", r"hydration"],
        ],
    ),
]


def compile_flags() -> int:
    return re.IGNORECASE | re.DOTALL


def any_pattern(patterns: list[str], text: str) -> bool:
    return any(re.search(pattern, text, compile_flags()) for pattern in patterns)


def source_text(result: dict[str, Any]) -> str:
    lines = []
    for source in result["sources"]:
        lines.append(json.dumps(source, ensure_ascii=False))
    return "\n".join(lines)


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


def run_case(case: EvalCase, config: rag_answer.RagConfig, mode: str) -> dict[str, Any]:
    result = rag_answer.answer_question(case.question, config)
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
    parser.add_argument("--lexical-weight", type=float, default=0.55)

    parser.add_argument("--provider", choices=["ollama", "openai-compatible", "dry-run"], default=os.getenv("TRI_RAG_PROVIDER", "ollama"))
    parser.add_argument("--chat-model", default=os.getenv("TRI_RAG_CHAT_MODEL", rag_answer.DEFAULT_CHAT_MODEL))
    parser.add_argument("--chat-base-url", default=os.getenv("TRI_RAG_CHAT_BASE_URL", rag_answer.DEFAULT_EMBEDDING_OLLAMA_URL))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=900)
    parser.add_argument("--timeout", type=int, default=240)
    return parser.parse_args()


def build_config(args: argparse.Namespace) -> rag_answer.RagConfig:
    provider = args.provider
    if args.mode == "retrieval":
        provider = "dry-run"
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
    )


def print_summary(report: dict[str, Any]) -> None:
    passed = sum(1 for item in report["results"] if item["passed"])
    total = len(report["results"])
    print(f"RAG eval: {passed}/{total} passed ({report['mode']})")
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
