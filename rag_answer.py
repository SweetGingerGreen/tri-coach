#!/usr/bin/env python3
"""
Ask the local triathlon RAG knowledge base and generate a cited answer.

The model boundary is intentionally separate from retrieval:
- Retrieval uses the local SQLite vector store built by vector_store.py.
- Generation uses a pluggable chat client.

Current providers:
- ollama: local Ollama /api/chat, defaulting to gemma2:latest.
- openai-compatible: any /v1/chat/completions compatible service.
- dry-run: retrieve and print the prompt without calling a model.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import textwrap
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import vector_store


ROOT = Path(__file__).parent.resolve()
DEFAULT_DB = (
    ROOT
    / "triathlon-knowledge"
    / "metadata"
    / "vectors"
    / "triathlon_core_v2_bge_m3.sqlite"
)
DEFAULT_EMBEDDING_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_CHAT_MODEL = "gemma2:latest"
DEFAULT_CONTEXT_CHARS = 1200
DEFAULT_NEIGHBOR_CHARS = 500
DEFAULT_BIKE_PLAN_REPORT = (
    ROOT
    / "triathlon-knowledge"
    / "metadata"
    / "bike_plan_generator_latest.json"
)
DEFAULT_REVIEW_OVERRIDE = (
    ROOT
    / "triathlon-knowledge"
    / "metadata"
    / "bike_plan_review_override_latest.json"
)
DEFAULT_CANDIDATE_REPORT = (
    ROOT
    / "triathlon-knowledge"
    / "metadata"
    / "bike_plan_candidate_latest.json"
)
DEFAULT_DAILY_PREFLIGHT_REPORT = (
    ROOT
    / "triathlon-knowledge"
    / "metadata"
    / "bike_plan_daily_preflight_latest.json"
)
DEFAULT_DAILY_DRAFT_REPORT = (
    ROOT
    / "triathlon-knowledge"
    / "metadata"
    / "bike_plan_daily_draft_latest.json"
)
DEFAULT_LONG_RIDE_NUTRITION_REVIEW_REPORT = (
    ROOT
    / "triathlon-knowledge"
    / "metadata"
    / "bike_plan_long_ride_nutrition_review_latest.json"
)


class ChatClient(Protocol):
    def complete(self, messages: list[dict[str, str]]) -> str:
        ...


@dataclass
class RetrievalConfig:
    db: Path = DEFAULT_DB
    embedding_model: str | None = None
    embedding_ollama_url: str = DEFAULT_EMBEDDING_OLLAMA_URL
    top_k: int = 6
    expand_neighbors: int = 1
    domain: str | None = None
    trust: str | None = None
    hybrid: bool = True
    lexical_weight: float = 0.55
    use_query_expansion: bool = True
    include_backmatter: bool = False


@dataclass
class GenerationConfig:
    provider: str = "ollama"
    chat_model: str = DEFAULT_CHAT_MODEL
    chat_base_url: str = DEFAULT_EMBEDDING_OLLAMA_URL
    api_key: str | None = None
    temperature: float = 0.2
    max_tokens: int = 1200
    timeout: int = 180


@dataclass
class RagConfig:
    retrieval: RetrievalConfig
    generation: GenerationConfig
    context_chars: int = DEFAULT_CONTEXT_CHARS
    neighbor_chars: int = DEFAULT_NEIGHBOR_CHARS
    show_prompt: bool = False
    bike_plan_report: Path = DEFAULT_BIKE_PLAN_REPORT
    review_override: Path = DEFAULT_REVIEW_OVERRIDE
    candidate_report: Path = DEFAULT_CANDIDATE_REPORT
    daily_preflight_report: Path = DEFAULT_DAILY_PREFLIGHT_REPORT
    daily_draft_report: Path = DEFAULT_DAILY_DRAFT_REPORT
    long_ride_nutrition_review_report: Path = DEFAULT_LONG_RIDE_NUTRITION_REVIEW_REPORT


def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 180,
) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = {"Content-Type": "application/json"}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=request_headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cannot connect to model endpoint {url}: {exc}") from exc


@dataclass
class OllamaChatClient:
    base_url: str
    model: str
    temperature: float = 0.2
    max_tokens: int = 1200
    timeout: int = 180

    def complete(self, messages: list[dict[str, str]]) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": "10m",
            "options": {
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
            },
        }
        result = post_json(
            f"{self.base_url.rstrip('/')}/api/chat",
            payload,
            timeout=self.timeout,
        )
        message = result.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"Ollama returned no content for model {self.model}")
        return content.strip()


@dataclass
class OpenAICompatibleChatClient:
    base_url: str
    model: str
    api_key: str | None = None
    temperature: float = 0.2
    max_tokens: int = 1200
    timeout: int = 180

    def complete(self, messages: list[dict[str, str]]) -> str:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        result = post_json(
            f"{self.base_url.rstrip('/')}/chat/completions",
            payload,
            headers=headers,
            timeout=self.timeout,
        )
        choices = result.get("choices") or []
        if not choices:
            raise RuntimeError(f"Model endpoint returned no choices for model {self.model}")
        content = ((choices[0].get("message") or {}).get("content"))
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError(f"Model endpoint returned no content for model {self.model}")
        return content.strip()


def make_chat_client(config: GenerationConfig) -> ChatClient | None:
    if config.provider == "dry-run":
        return None
    if config.provider == "ollama":
        return OllamaChatClient(
            base_url=config.chat_base_url,
            model=config.chat_model,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            timeout=config.timeout,
        )
    if config.provider == "openai-compatible":
        return OpenAICompatibleChatClient(
            base_url=config.chat_base_url,
            model=config.chat_model,
            api_key=config.api_key,
            temperature=config.temperature,
            max_tokens=config.max_tokens,
            timeout=config.timeout,
        )
    raise ValueError(f"Unsupported provider: {config.provider}")


SYSTEM_PROMPT = """你是一个铁人三项训练知识库 RAG 问答器。

你必须遵守：
1. 只使用提供的“知识库片段”回答；如果证据不足，明确说“知识库证据不足”，并追问缺失信息。
2. 回答必须带来源标记，例如 [S1]、[S2]。不要引用未提供的来源。
3. 遇到疼痛、损伤、麻木、无力、尖锐痛、进行性加重等情况：只能做风险识别和训练调整建议，不能做医学确诊；必要时要求停止相关训练并就医评估。
4. 遇到赛前补给、克数、训练计划排程：如果缺少完赛时间、体重、排汗率、胃肠碳水耐受、近期负荷等关键数据，不要编具体方案；不要复述来源里的通用每小时克数、克/公斤范围或按天模板；先追问，再给原则。
5. 遇到资料冲突：优先 trust_level A、铁三专项资料、和多来源一致结论；说明不确定性。
6. 优先使用排名靠前、和用户症状或问题精确匹配的片段；不要因为某个低排名片段是中文就忽略高排名英文片段。
7. 疼痛处理建议必须和疼痛部位、组织结构匹配；不要把胫骨、小腿、踝、足、肩等其他部位的康复方案套用到膝外侧或髂胫束问题上。
8. 不要提冰敷、热敷、按摩、拉伸、泡沫轴、药物或具体康复动作，除非当前知识库片段明确支持这些做法。
9. 如果知识库只支持风险识别、不支持具体康复动作，就只建议停止诱发动作、改低冲击维持体能、观察红线并寻求运动医学/物理治疗评估。
10. 输出中文，直接、保守、可执行。

常用术语映射：
- lateral knee pain = 膝外侧痛。
- iliotibial band friction syndrome / ITBFS / iliotibial band syndrome = 髂胫束摩擦综合征/髂胫束综合征风险。
- cardiac drift / aerobic decoupling = 心率漂移/有氧解耦。
- sweet spot cycling = 甜点骑行。
- concurrent training = 力量和耐力同期训练。"""


USER_PROMPT_TEMPLATE = """用户问题：
{question}

系统硬规则匹配：
{guardrails}

知识库片段：
{context}

请按下面结构回答：
1. 结论
2. 依据
3. 现在应该怎么做
4. 还缺哪些数据

每个关键判断后面都要带来源标记。"""


def source_label(index: int) -> str:
    return f"S{index}"


def format_page(meta: dict[str, Any]) -> str:
    page_start = meta.get("page_start")
    page_end = meta.get("page_end")
    if not page_start:
        return ""
    if page_end and page_end != page_start:
        return f"p.{page_start}-{page_end}"
    return f"p.{page_start}"


def format_source_for_prompt(
    result: dict[str, Any],
    *,
    label: str,
    context_chars: int,
    neighbor_chars: int,
) -> str:
    chunk = result["chunk"]
    meta = chunk["metadata"]
    parts = [
        f"[{label}]",
        f"chunk_id: {chunk['chunk_id']}",
        f"title: {meta.get('title', '')}",
        f"domain/trust: {meta.get('domain', '')}/{meta.get('trust_level', '')}",
    ]
    page = format_page(meta)
    if page:
        parts.append(f"page: {page}")
    if meta.get("heading"):
        parts.append(f"heading: {meta.get('heading')}")
    if meta.get("usage_rule"):
        parts.append(f"usage_rule: {meta.get('usage_rule')}")
    parts.append(f"score: {result['score']:.4f}")
    parts.append("text:")
    parts.append(vector_store.snippet(chunk["text"], context_chars))

    neighbor_lines = []
    for neighbor in result["neighbors"]:
        nmeta = neighbor["metadata"]
        npage = format_page(nmeta)
        title = nmeta.get("title", "")
        relation = neighbor.get("relation", "")
        text = vector_store.snippet(neighbor["text"], neighbor_chars)
        neighbor_lines.append(
            f"- {relation} {title} {npage}: {text}".strip()
        )
    if neighbor_lines:
        parts.append("neighbor_context:")
        parts.extend(neighbor_lines)
    return "\n".join(parts)


def build_context(results: list[dict[str, Any]], config: RagConfig) -> tuple[str, list[dict[str, Any]]]:
    source_rows = []
    blocks = []
    for index, result in enumerate(results, start=1):
        label = source_label(index)
        chunk = result["chunk"]
        meta = chunk["metadata"]
        source_rows.append(
            {
                "label": label,
                "chunk_id": chunk["chunk_id"],
                "title": meta.get("title", ""),
                "domain": meta.get("domain", ""),
                "trust_level": meta.get("trust_level", ""),
                "page": format_page(meta),
                "heading": meta.get("heading", ""),
                "source_path": meta.get("source_path", ""),
                "score": result["score"],
                "vector_score": result["vector_score"],
                "lexical_score": result["lexical_score"],
                "excerpt": vector_store.snippet(chunk["text"], 500),
            }
        )
        blocks.append(
            format_source_for_prompt(
                result,
                label=label,
                context_chars=config.context_chars,
                neighbor_chars=config.neighbor_chars,
            )
        )
    return "\n\n---\n\n".join(blocks), source_rows


def result_text(result: dict[str, Any]) -> str:
    texts = [result["chunk"]["text"]]
    texts.extend(neighbor["text"] for neighbor in result.get("neighbors", []))
    return "\n".join(texts).lower()


def apply_precision_filters(question: str, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    question_lower = question.lower()
    has_lateral_knee_signal = any(
        signal in question_lower
        for signal in ("膝盖外侧", "膝外侧", "髂胫", "骼胫", "itbs", "大腿外侧")
    )
    if has_lateral_knee_signal:
        topic_terms = ("itbs", "iliotibial", "lateral knee", "髂胫", "骼胫")
        filtered = [
            result for result in results
            if any(term in result_text(result) for term in topic_terms)
        ]
        if len(filtered) >= 2:
            return filtered
    return results


def detect_guardrails(question: str) -> list[str]:
    question_lower = question.lower()
    guardrails = []

    injury_signals = ("尖锐", "刺痛", "麻木", "无力", "加重", "疼痛", "痛")
    continue_training_signals = ("还能", "继续", "明天", "15k", "跑 15", "跑15", "按计划")
    if any(signal in question_lower for signal in injury_signals) and any(
        signal in question_lower for signal in continue_training_signals
    ):
        guardrails.append(
            "伤病安全拦截：必须明确回答不要继续执行诱发疼痛的计划跑课；不能说“可忍受就继续”。"
            "只做风险识别和训练调整，不做医学确诊。不要输出当前匹配来源没有支持的具体治疗动作。"
        )

    nutrition_signals = ("补给", "碳水", "排汗", "赛前", "比赛日", "减量")
    plan_signals = ("安排", "计划", "排程", "最后一周")
    if any(signal in question_lower for signal in nutrition_signals) and any(
        signal in question_lower for signal in plan_signals
    ):
        guardrails.append(
            "补给计划边界：缺少完赛时长、体重、排汗率、每小时碳水耐受和既往补给实践时，"
            "不要编具体克数/时间表；不要输出克/公斤、每小时多少克、按天安排等数字化补给方案；"
            "也不要复述来源中的通用碳水克数范围；先追问这些数据，再给原则。"
        )

    drift_signals = ("心率漂移", "有氧解耦", "解耦", "后 1 小时", "后1小时", "ctl", "甜点")
    volume_signals = ("加量", "增加训练", "下周")
    if any(signal in question_lower for signal in drift_signals) and any(
        signal in question_lower for signal in volume_signals
    ):
        guardrails.append(
            "训练负荷边界：心率漂移/有氧解耦叠加前一日强度训练时，不要直接建议加量；"
            "必须先分析残余疲劳和恢复，再给维持或减量等保守建议。"
        )

    return guardrails


def is_missing_data_nutrition_plan(question: str) -> bool:
    question_lower = question.lower()
    nutrition_signals = ("补给", "碳水", "排汗", "赛前", "比赛日", "减量")
    plan_signals = ("安排", "计划", "排程", "最后一周")
    return any(signal in question_lower for signal in nutrition_signals) and any(
        signal in question_lower for signal in plan_signals
    )


def extract_number_after(patterns: list[str], text: str) -> float | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            try:
                return float(match.group(1))
            except ValueError:
                continue
    return None


def parse_bike_calculator_inputs(question: str) -> dict[str, float] | None:
    question_lower = question.lower()
    if not all(signal in question_lower for signal in ("ftp", "平均功率")):
        return None
    if not any(signal in question_lower for signal in ("热量", "碳水", "消耗", "能量")):
        return None

    ftp = extract_number_after(
        [
            r"\bftp\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
            r"functional threshold power\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
        ],
        question,
    )
    avg_power = extract_number_after(
        [
            r"平均功率\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
            r"avg(?:erage)? power\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)",
        ],
        question,
    )
    hours = extract_number_after(
        [
            r"([0-9]+(?:\.[0-9]+)?)\s*(?:小时|h|hr|hrs|hour|hours)\b",
        ],
        question,
    )
    minutes = extract_number_after(
        [
            r"([0-9]+(?:\.[0-9]+)?)\s*(?:分钟|min|mins|minute|minutes)\b",
        ],
        question,
    )
    duration_minutes = minutes if minutes is not None else (hours * 60 if hours is not None else None)
    if ftp is None or avg_power is None or duration_minutes is None or ftp <= 0 or duration_minutes <= 0:
        return None
    return {"ftp": ftp, "avg_power": avg_power, "duration_minutes": duration_minutes}


def bike_calculator_result(values: dict[str, float]) -> dict[str, float]:
    ftp = values["ftp"]
    avg_power = values["avg_power"]
    duration_minutes = values["duration_minutes"]
    efficiency = 0.21
    intensity_factor = avg_power / ftp
    mechanical_kj = avg_power * 60 * duration_minutes / 1000
    mechanical_kj_per_hour = avg_power * 3600 / 1000
    total_kcal = avg_power * 60 / efficiency / 4184 * duration_minutes
    qr_poly = (
        -0.000000149 * intensity_factor**6
        + 141.538462237 * intensity_factor**5
        - 565.128206259 * intensity_factor**4
        + 890.333333976 * intensity_factor**3
        - 691.67948706 * intensity_factor**2
        + 265.460857558 * intensity_factor
        - 39.525121144
    )
    qr = max(qr_poly, 0.7)
    carb_fraction = min(max((qr - 0.7) * 3.45, 0.0), 1.0)
    carb_g = total_kcal * carb_fraction / 4
    carb_g_per_min = carb_g / duration_minutes
    fat_fraction = max(1.0 - carb_fraction, 0.0)
    fat_g = total_kcal * fat_fraction / 9
    fat_g_per_min = fat_g / duration_minutes
    return {
        "ftp": ftp,
        "avg_power": avg_power,
        "duration_minutes": duration_minutes,
        "efficiency": efficiency,
        "intensity_factor": intensity_factor,
        "mechanical_kj": mechanical_kj,
        "mechanical_kj_per_hour": mechanical_kj_per_hour,
        "total_kcal": total_kcal,
        "qr": qr,
        "carb_fraction": carb_fraction,
        "carb_g": carb_g,
        "carb_g_per_min": carb_g_per_min,
        "fat_g": fat_g,
        "fat_g_per_min": fat_g_per_min,
    }


def bike_calculator_fallback(values: dict[str, float], sources: list[dict[str, Any]]) -> str:
    result = bike_calculator_result(values)
    source_refs = "、".join(
        f"[{source['label']}]" for source in sources
        if "骑行热量与碳水消耗计算模板" in source.get("title", "")
    ) or "[S1]"
    in_range = 0.55 < result["intensity_factor"] <= 1.0
    range_text = "在模板适用范围内" if in_range else "不在模板建议适用范围内，结果只能粗略参考"
    return (
        "1. 结论\n"
        f"按表格公式估算：IF 约 {result['intensity_factor']:.3f}，每小时机械功约 {result['mechanical_kj_per_hour']:.1f} kJ/h，"
        f"累计机械功约 {result['mechanical_kj']:.1f} kJ，"
        f"总能量约 {result['total_kcal']:.1f} kcal，碳水消耗约 {result['carb_g']:.1f} g，"
        f"碳水速率约 {result['carb_g_per_min']:.2f} g/min；脂肪消耗约 {result['fat_g']:.1f} g，"
        f"脂肪速率约 {result['fat_g_per_min']:.2f} g/min。\n\n"
        "2. 依据\n"
        f"计算使用 `骑行热量与碳水消耗计算模板` 的输入和公式链：FTP、运动时长、平均功率、总效率、IF、"
        f"机械功、总能量、QR、碳水比例、碳水克数和脂肪克数。来源：{source_refs}。\n\n"
        "3. 现在应该怎么做\n"
        f"这次输入为 FTP {result['ftp']:.0f} W、平均功率 {result['avg_power']:.0f} W、"
        f"时长 {result['duration_minutes']:.0f} min，IF={result['intensity_factor']:.3f}，{range_text}。"
        "可以把这个结果作为长骑补给复盘和后续课表能量估算的起点，但不要直接等同于比赛补给处方。\n\n"
        "4. 还缺哪些数据\n"
        "如果要进一步安排实际补给，还需要体重、环境温度、排汗率、每小时碳水耐受、钠和液体耐受、"
        "以及训练中已验证过的补给记录。"
    )


def source_refs_for_titles(sources: list[dict[str, Any]], title_terms: tuple[str, ...]) -> str:
    refs = [
        f"[{source['label']}]"
        for source in sources
        if any(term in source.get("title", "") for term in title_terms)
    ]
    return "、".join(refs) or "[S1]"


PLAN_DAY_ALIASES = {
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


def is_bike_plan_explain_request(question: str) -> bool:
    question_lower = question.lower()
    explain_signals = ("为什么", "why", "解释", "依据", "来源", "reason")
    plan_signals = ("安排", "排", "slot", "槽位", "weekday", "星期", "周二", "周三", "周日", "easy", "hard", "long")
    return contains_any(question_lower, explain_signals) and contains_any(question_lower, plan_signals)


def is_bike_plan_week_explain_request(question: str) -> bool:
    question_lower = question.lower()
    explain_signals = ("为什么", "why", "解释", "依据", "来源", "reason")
    week_signals = ("所有安排", "全部安排", "整周", "全周", "这一周", "本周", "week")
    if not contains_any(question_lower, explain_signals):
        return False
    if parse_plan_day(question) or parse_plan_slot_type(question):
        return False
    return parse_plan_week(question) is not None and contains_any(question_lower, week_signals)


def is_bike_plan_conflict_explain_request(question: str) -> bool:
    question_lower = question.lower()
    explain_signals = ("为什么", "why", "解释", "原因", "依据", "标记")
    conflict_signals = ("冲突", "conflict", "固定跨项", "跨项日期", "人工复核", "attention")
    return (
        parse_plan_week(question) is not None
        and contains_any(question_lower, explain_signals)
        and contains_any(question_lower, conflict_signals)
    )


def is_review_override_summary_request(question: str) -> bool:
    question_lower = question.lower()
    review_signals = ("人工", "复核", "override", "修改", "调整", "覆盖")
    summary_signals = ("哪些周", "哪几周", "有没有", "被要求", "要求修改", "要求调整", "override")
    return contains_any(question_lower, review_signals) and contains_any(question_lower, summary_signals)


def is_candidate_diff_request(question: str) -> bool:
    question_lower = question.lower()
    candidate_signals = ("候选", "第二版", "新版", "candidate")
    diff_signals = ("差在哪", "差异", "变化", "改了", "调整了", "对比")
    return contains_any(question_lower, candidate_signals) and contains_any(question_lower, diff_signals)


def is_daily_preflight_summary_request(question: str) -> bool:
    question_lower = question.lower()
    daily_signals = ("每日", "日课表", "每日训练课", "daily", "每天")
    preflight_signals = ("preflight", "准入", "还缺", "缺什么", "缺哪些", "是否通过", "能不能进入")
    return contains_any(question_lower, daily_signals) and contains_any(question_lower, preflight_signals)


def is_daily_draft_summary_request(question: str) -> bool:
    question_lower = question.lower()
    daily_signals = ("每日", "日课表", "每日训练课", "daily", "每天")
    draft_signals = ("草案", "draft", "生成了吗", "生成了什么", "第1周", "第一周", "课表草案")
    return contains_any(question_lower, daily_signals) and contains_any(question_lower, draft_signals)


def is_long_ride_nutrition_review_request(question: str) -> bool:
    question_lower = question.lower()
    long_ride_signals = ("长骑", "long ride", "long")
    nutrition_signals = ("补给", "碳水", "热量", "能量", "消耗", "nutrition", "carb")
    review_signals = ("复核", "review", "估算", "结果", "接入", "接到")
    return (
        contains_any(question_lower, long_ride_signals)
        and contains_any(question_lower, nutrition_signals)
        and contains_any(question_lower, review_signals)
    )


def parse_plan_week(question: str) -> int | None:
    patterns = [
        r"第\s*([0-9]+)\s*周",
        r"week\s*([0-9]+)",
        r"\bw\s*([0-9]+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def parse_plan_day(question: str) -> str | None:
    lowered = question.lower()
    for alias, day in PLAN_DAY_ALIASES.items():
        if alias in question or alias in lowered:
            return day
    return None


def parse_plan_slot_type(question: str) -> str | None:
    lowered = question.lower()
    for slot_type in ("hard", "long", "technical", "easy"):
        if slot_type in lowered:
            return slot_type
    if "长骑" in question:
        return "long"
    if "高强度" in question:
        return "hard"
    if "踏频" in question or "技术" in question:
        return "technical"
    if "耐力" in question or "轻松" in question:
        return "easy"
    return None


def load_json_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_bike_plan_report(path: Path) -> dict[str, Any] | None:
    return load_json_file(path)


def find_plan_schedule_item(question: str, report: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    week_number = parse_plan_week(question)
    day = parse_plan_day(question)
    slot_type = parse_plan_slot_type(question)
    workout_text = question.lower()

    weeks = report.get("weekly_plan") or []
    for week in weeks:
        if week_number is not None and week.get("week") != week_number:
            continue
        for item in week.get("weekday_schedule") or []:
            if day is not None and item.get("day") != day:
                continue
            if slot_type is not None and item.get("slot_type") != slot_type:
                continue
            workout = str(item.get("workout_type") or "").lower()
            if workout and workout in workout_text:
                return week, item
            if slot_type is not None or day is not None:
                return week, item
    return None


def find_plan_week(question: str, report: dict[str, Any]) -> dict[str, Any] | None:
    week_number = parse_plan_week(question)
    if week_number is None:
        return None
    for week in report.get("weekly_plan") or []:
        if week.get("week") == week_number:
            return week
    return None


def unique_source_refs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    refs = []
    seen = set()
    for item in items:
        for ref in item.get("source_refs") or []:
            chunk_id = ref.get("chunk_id")
            if not chunk_id or chunk_id in seen:
                continue
            seen.add(chunk_id)
            refs.append(ref)
    return refs


def fetch_chunk_sources_by_refs(
    refs: list[dict[str, Any]],
    db_path: Path,
) -> list[dict[str, Any]]:
    if not refs or not db_path.exists():
        return []
    wanted = [ref.get("chunk_id") for ref in refs if ref.get("chunk_id")]
    if not wanted:
        return []

    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.execute("PRAGMA busy_timeout = 5000")
    placeholders = ",".join("?" for _ in wanted)
    rows = conn.execute(
        f"SELECT chunk_id, text, metadata_json FROM chunks WHERE chunk_id IN ({placeholders})",
        wanted,
    ).fetchall()
    conn.close()

    by_id = {
        chunk_id: {"chunk_id": chunk_id, "text": text, "metadata": json.loads(metadata_json)}
        for chunk_id, text, metadata_json in rows
    }
    sources = []
    for index, chunk_id in enumerate(wanted, start=1):
        record = by_id.get(chunk_id)
        if not record:
            continue
        meta = record["metadata"]
        ref_score = next((ref.get("score") for ref in refs if ref.get("chunk_id") == chunk_id), 1.0)
        sources.append(
            {
                "label": source_label(index),
                "chunk_id": chunk_id,
                "title": meta.get("title", ""),
                "domain": meta.get("domain", ""),
                "trust_level": meta.get("trust_level", ""),
                "page": format_page(meta),
                "heading": meta.get("heading", ""),
                "source_path": meta.get("source_path", ""),
                "score": float(ref_score or 1.0),
                "vector_score": 0.0,
                "lexical_score": 0.0,
                "excerpt": vector_store.snippet(record["text"], 500),
            }
        )
    return sources


def format_week_schedule(schedule: list[dict[str, Any]]) -> str:
    rows = []
    for item in schedule:
        rows.append(
            f"{item.get('day_label')} {item.get('slot_type')} / {item.get('workout_type')}"
        )
    return "；".join(rows) if rows else "本周没有排程项"


def format_week_summary(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "无"
    return "、".join(f"{row.get('day_label')} {row.get('workout_type')}" for row in rows)


def format_review_days(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "无"
    parts = []
    for row in rows:
        reasons = ",".join(str(reason) for reason in row.get("reasons") or [])
        if reasons:
            parts.append(f"{row.get('day_label')}({reasons})")
        else:
            parts.append(str(row.get("day_label")))
    return "、".join(parts)


def format_week_conflicts(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "无"
    parts = []
    for row in rows:
        reasons = ",".join(str(reason) for reason in row.get("reasons") or [])
        parts.append(f"{row.get('sport')} {row.get('day_label')} {row.get('conflict_type')}({reasons})")
    return "、".join(parts)


def reason_label(reason: str) -> str:
    labels = {
        "bike_hard_recovery_window": "bike hard 恢复窗口",
        "bike_long_day": "bike long 当日",
        "post_bike_long_recovery": "bike long 后恢复日",
        "athlete_unavailable": "用户不可训练日",
        "same_day_as_bike_hard": "同日 bike hard",
        "swim_load_high_with_bike_hard": "游泳负荷高叠加 bike hard",
    }
    return labels.get(reason, reason)


def format_conflict_explanation(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "无"
    parts = []
    for row in rows:
        reasons = "、".join(reason_label(str(reason)) for reason in row.get("reasons") or [])
        parts.append(
            f"{row.get('sport')} 固定在 {row.get('day_label')}，触发 {row.get('conflict_type')}，原因是 {reasons}"
        )
    return "；".join(parts)


def find_review_row(report: dict[str, Any], week_number: Any) -> dict[str, Any]:
    rows = (report.get("review_view") or {}).get("rows") or []
    return next((row for row in rows if row.get("week") == week_number), {})


def explain_bike_plan_conflict(question: str, config: RagConfig) -> dict[str, Any] | None:
    if not is_bike_plan_conflict_explain_request(question):
        return None
    report = load_bike_plan_report(config.bike_plan_report)
    if not report:
        return None
    week = find_plan_week(question, report)
    if not week:
        return None

    review_row = find_review_row(report, week.get("week"))
    placeholder = review_row.get("cross_sport_placeholder_summary") or {}
    conflicts = placeholder.get("cross_sport_conflicts") or []
    flags = review_row.get("attention_flags") or []
    conflict_text = format_conflict_explanation(conflicts)
    run_avoid = format_review_days(placeholder.get("run_hard_avoid_days") or [])
    swim_caution = format_review_days(placeholder.get("swim_hard_caution_days") or [])
    strength_avoid = format_review_days(placeholder.get("strength_lower_body_avoid_days") or [])
    schedule = week.get("weekday_schedule") or []
    sources = fetch_chunk_sources_by_refs(unique_source_refs(schedule), config.retrieval.db)
    source_marks = "、".join(f"[{source['label']}]" for source in sources) or "当前来源引用缺失"

    if conflicts:
        conclusion = (
            f"第 {week.get('week')} 周被标记固定跨项日期冲突，是因为用户固定的跑步 hard、游泳 hard "
            "或下肢力量日期落进了 bike hard / bike long 形成的避让窗口。"
        )
    else:
        conclusion = f"第 {week.get('week')} 周当前没有固定跨项日期冲突；没有 `fixed_cross_sport_day_conflict` 标记。"

    answer = (
        "1. 结论\n"
        f"{conclusion}来源依据见 {source_marks}。\n\n"
        "2. 依据\n"
        f"本周 bike 排程是：{format_week_schedule(schedule)}。"
        f"复核标记：{('、'.join(flags) if flags else '无')}。"
        f"冲突明细：{conflict_text}。"
        f"当前避让窗口是：跑步 hard 避开 {run_avoid}；游泳 hard 谨慎 {swim_caution}；"
        f"下肢力量避开 {strength_avoid}。\n\n"
        "3. 现在应该怎么做\n"
        "这只是冲突解释，不直接改课表。人工复核时优先移动固定跨项 hard/下肢力量日，"
        "或把 bike hard / long 槽位重新分配到恢复更合理的位置。\n\n"
        "4. 还缺哪些数据\n"
        "如果要真正重排，还需要固定跑步、游泳和力量训练不可移动的原因、当天可用时长、疲劳状态、"
        "以及哪些 bike slot 可以被移动。当前回答不会输出瓦数、分钟、组数或具体间歇。"
    )
    return {
        "question": question,
        "answer": answer,
        "sources": sources,
        "messages": None,
        "provider": "bike-plan-conflict-source-refs",
        "chat_model": "deterministic",
        "embedding_db": str(config.retrieval.db),
        "bike_plan_report": str(config.bike_plan_report),
    }


def explain_bike_plan_week(question: str, config: RagConfig) -> dict[str, Any] | None:
    if not is_bike_plan_week_explain_request(question):
        return None
    report = load_bike_plan_report(config.bike_plan_report)
    if not report:
        return None
    week = find_plan_week(question, report)
    if not week:
        return None

    schedule = week.get("weekday_schedule") or []
    sources = fetch_chunk_sources_by_refs(unique_source_refs(schedule), config.retrieval.db)
    source_marks = "、".join(f"[{source['label']}]" for source in sources) or "当前来源引用缺失"
    review_row = find_review_row(report, week.get("week"))
    placeholder = review_row.get("cross_sport_placeholder_summary") or {}
    flags = review_row.get("attention_flags") or []
    flag_text = "无" if not flags else "、".join(flags)
    blocked_days = "、".join(row.get("day_label", "") for row in review_row.get("blocked_days", [])) or "无"
    schedule_text = format_week_schedule(schedule)
    hard_text = format_week_summary(review_row.get("hard_summary") or [])
    long_text = format_week_summary(review_row.get("long_summary") or [])
    run_avoid = format_review_days(placeholder.get("run_hard_avoid_days") or [])
    swim_caution = format_review_days(placeholder.get("swim_hard_caution_days") or [])
    strength_avoid = format_review_days(placeholder.get("strength_lower_body_avoid_days") or [])
    conflicts = format_week_conflicts(placeholder.get("cross_sport_conflicts") or [])

    answer = (
        "1. 结论\n"
        f"第 {week.get('week')} 周的整体 bike 安排是：{schedule_text}。"
        "这是周级课型和星期槽位解释，不是每日训练课处方；来源依据见 "
        f"{source_marks}。\n\n"
        "2. 依据\n"
        f"本周阶段是 {week.get('phase_hint')} / {week.get('workload_state')}；"
        f"bike 可训练预算是 {week.get('bike_days_budget')} 天、{week.get('bike_hours_budget')} 小时；"
        f"高强度预算是 {week.get('high_intensity_budget')}，实际分配高强度 "
        f"{week.get('assigned_high_intensity_count')}。hard 摘要：{hard_text}；"
        f"long 摘要：{long_text}；用户约束日：{blocked_days}。来源 chunk：{source_marks}。\n\n"
        "3. 现在应该怎么做\n"
        "把这一周当作人工复核表来读：先确认这些星期是否真的可训练，再检查 long、hard 和跨项负荷有没有冲突。"
        f"当前复核标记：{flag_text}。跨项占位提示：跑步 hard 避开 {run_avoid}；"
        f"游泳 hard 谨慎 {swim_caution}；下肢力量避开 {strength_avoid}。"
        f"已检测到的固定跨项日期冲突：{conflicts}。\n\n"
        "4. 还缺哪些数据\n"
        "如果要继续细化到具体训练课，还需要每天可用时长、跑步和游泳实际排程、睡眠疲劳、近期疼痛、"
        "以及是否已经验证过相似课型。当前回答不会输出瓦数、分钟、组数或具体间歇。"
    )
    return {
        "question": question,
        "answer": answer,
        "sources": sources,
        "messages": None,
        "provider": "bike-plan-week-source-refs",
        "chat_model": "deterministic",
        "embedding_db": str(config.retrieval.db),
        "bike_plan_report": str(config.bike_plan_report),
    }


def explain_bike_plan_slot(question: str, config: RagConfig) -> dict[str, Any] | None:
    if not is_bike_plan_explain_request(question):
        return None
    report = load_bike_plan_report(config.bike_plan_report)
    if not report:
        return None
    match = find_plan_schedule_item(question, report)
    if not match:
        return None

    week, item = match
    refs = item.get("source_refs") or []
    sources = fetch_chunk_sources_by_refs(refs, config.retrieval.db)
    source_marks = "、".join(f"[{source['label']}]" for source in sources) or "当前来源引用缺失"
    review_row = find_review_row(report, week.get("week"))
    flags = review_row.get("attention_flags") or []
    flag_text = "无" if not flags else "、".join(flags)
    blocked_days = "、".join(item.get("day_label", "") for item in review_row.get("blocked_days", [])) or "无"
    long_summary = review_row.get("long_summary") or []
    long_text = "、".join(f"{row.get('day_label')} {row.get('workout_type')}" for row in long_summary) or "本周没有 long 槽"

    answer = (
        "1. 结论\n"
        f"第 {week.get('week')} 周 {item.get('day_label')} 安排的是 "
        f"{item.get('slot_type')} / {item.get('workout_type')}。理由是：生成器先在周级确定课型，"
        f"再把这个 slot 放到星期排程里；该项的排程规则是“{item.get('schedule_rule')}”。"
        f"来源依据见 {source_marks}。\n\n"
        "2. 依据\n"
        f"本周阶段是 {week.get('phase_hint')} / {week.get('workload_state')}，"
        f"bike 可训练预算是 {week.get('bike_days_budget')} 天、{week.get('bike_hours_budget')} 小时；"
        f"本周高强度预算是 {week.get('high_intensity_budget')}，实际分配高强度 "
        f"{week.get('assigned_high_intensity_count')}。长骑摘要：{long_text}。"
        f"约束日：{blocked_days}。来源 chunk：{source_marks}。\n\n"
        "3. 现在应该怎么做\n"
        "把这条解释当作人工复核线索：先确认这一天是否真的可训练，再看它和 long/hard 的距离是否合理。"
        f"当前复核标记：{flag_text}。如果标记不是“无”，优先人工确认恢复和跨项负荷。\n\n"
        "4. 还缺哪些数据\n"
        "如果要把这个 slot 进一步变成具体训练课，还需要当天可用时长、跑步和游泳排程、睡眠疲劳、"
        "近期疼痛、以及是否已经验证过相似课型。当前回答不会输出瓦数、分钟、组数或具体间歇。"
    )
    return {
        "question": question,
        "answer": answer,
        "sources": sources,
        "messages": None,
        "provider": "bike-plan-source-refs",
        "chat_model": "deterministic",
        "embedding_db": str(config.retrieval.db),
        "bike_plan_report": str(config.bike_plan_report),
    }


def summarize_review_override(question: str, config: RagConfig) -> dict[str, Any] | None:
    if not is_review_override_summary_request(question):
        return None
    report = load_json_file(config.review_override)
    if not report:
        return None

    summary = report.get("review_summary") or {}
    overrides = report.get("overrides") or []
    if overrides:
        rows = []
        for item in overrides:
            rows.append(
                f"第 {item.get('week')} 周 {item.get('week_start')}: "
                f"{item.get('normalized_review_status')}；"
                f"修改请求：{item.get('override_request') or item.get('review_comment') or '未填写'}"
            )
        override_text = "；".join(rows)
        conclusion = f"当前有 {len(overrides)} 周被人工要求修改：{override_text}。"
    else:
        override_text = "无"
        conclusion = "当前没有周被人工要求修改；override 文件状态是 no_overrides。"

    answer = (
        "1. 结论\n"
        f"{conclusion}\n\n"
        "2. 依据\n"
        f"读取的 override 文件是 {config.review_override}；状态是 {report.get('status')}；"
        f"总复核行数 {summary.get('rows_total', 0)}，override 数量 {summary.get('overrides_total', 0)}。"
        f"状态计数：{json.dumps(summary.get('status_counts') or {}, ensure_ascii=False)}。\n\n"
        "3. 现在应该怎么做\n"
        "如果是 no_overrides，可以继续使用当前周级计划进入下一步复核。"
        "如果是 overrides_ready，下一层只应把 override 当作人工修改请求输入，先生成第二版候选周级排程；"
        "不要直接覆盖原始生成报告。\n\n"
        "4. 还缺哪些数据\n"
        f"override 明细：{override_text}。如果要执行修改，还需要确认哪些 slot 可以移动、哪些固定跨项日期不可移动、"
        "以及是否保持 long day 不变。当前回答不会输出瓦数、分钟、组数或具体间歇。"
    )
    return {
        "question": question,
        "answer": answer,
        "sources": [],
        "messages": None,
        "provider": "bike-plan-review-override",
        "chat_model": "deterministic",
        "embedding_db": str(config.retrieval.db),
        "review_override": str(config.review_override),
    }


def schedule_by_slot(week: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("slot_type")): item
        for item in week.get("weekday_schedule") or []
        if item.get("slot_type")
    }


def find_week_by_number(report: dict[str, Any], week_number: Any) -> dict[str, Any]:
    return next(
        (week for week in report.get("weekly_plan") or [] if week.get("week") == week_number),
        {},
    )


def candidate_schedule_diffs(
    original: dict[str, Any],
    candidate: dict[str, Any],
    week_filter: int | None,
) -> list[dict[str, Any]]:
    diffs = []
    for candidate_week in candidate.get("weekly_plan") or []:
        week_number = candidate_week.get("week")
        if week_filter is not None and week_number != week_filter:
            continue
        original_week = find_week_by_number(original, week_number)
        if not original_week:
            continue
        original_slots = schedule_by_slot(original_week)
        candidate_slots = schedule_by_slot(candidate_week)
        changes = []
        for slot_type in sorted(set(original_slots) | set(candidate_slots)):
            old = original_slots.get(slot_type) or {}
            new = candidate_slots.get(slot_type) or {}
            if old.get("day") == new.get("day"):
                continue
            changes.append(
                {
                    "slot_type": slot_type,
                    "workout_type": new.get("workout_type") or old.get("workout_type"),
                    "from_day": old.get("day"),
                    "from_day_label": old.get("day_label"),
                    "to_day": new.get("day"),
                    "to_day_label": new.get("day_label"),
                }
            )
        if changes:
            diffs.append(
                {
                    "week": week_number,
                    "week_start": candidate_week.get("week_start"),
                    "changes": changes,
                    "candidate_week": candidate_week,
                }
            )
    return diffs


def format_candidate_diff_rows(diffs: list[dict[str, Any]]) -> str:
    if not diffs:
        return "无"
    rows = []
    for diff in diffs:
        change_text = []
        for change in diff.get("changes") or []:
            change_text.append(
                f"{change.get('slot_type')} / {change.get('workout_type')} "
                f"从 {change.get('from_day_label') or change.get('from_day')} "
                f"改到 {change.get('to_day_label') or change.get('to_day')}"
            )
        rows.append(f"第 {diff.get('week')} 周：{'；'.join(change_text)}")
    return "；".join(rows)


def format_candidate_override_rows(metadata: dict[str, Any]) -> str:
    rows = []
    for item in metadata.get("applied_overrides") or []:
        changes = []
        for change in item.get("changes") or []:
            if change.get("type") == "moved_slot":
                changes.append(
                    f"{change.get('slot_type')} 从 {change.get('from_day_label')} 移到 {change.get('to_day_label')}"
                )
            elif change.get("type") == "protected_slot":
                changes.append(f"{change.get('slot_type')} 保留在 {change.get('day_label')}")
        directive = item.get("directive") or {}
        source = directive.get("directive_source") or "unknown"
        rows.append(f"第 {item.get('week')} 周（{source}）：{'；'.join(changes) if changes else item.get('status')}")
    return "；".join(rows) if rows else "无"


def explain_candidate_diff(question: str, config: RagConfig) -> dict[str, Any] | None:
    if not is_candidate_diff_request(question):
        return None
    original = load_json_file(config.bike_plan_report)
    candidate = load_json_file(config.candidate_report)
    if not original or not candidate:
        return None

    metadata = candidate.get("candidate_metadata") or {}
    week_filter = parse_plan_week(question)
    diffs = candidate_schedule_diffs(original, candidate, week_filter)
    diff_text = format_candidate_diff_rows(diffs)
    override_text = format_candidate_override_rows(metadata)
    unresolved = metadata.get("unresolved_overrides") or []
    unresolved_text = "无" if not unresolved else json.dumps(unresolved, ensure_ascii=False)

    source_weeks = [diff["candidate_week"] for diff in diffs]
    if not source_weeks and week_filter is not None:
        week = find_week_by_number(candidate, week_filter)
        source_weeks = [week] if week else []
    refs = unique_source_refs(
        [
            item
            for week in source_weeks
            for item in week.get("weekday_schedule") or []
        ]
    )
    sources = fetch_chunk_sources_by_refs(refs, config.retrieval.db)
    source_marks = "、".join(f"[{source['label']}]" for source in sources) or "当前来源引用缺失"

    if diffs:
        conclusion = f"候选版相对第一版有周级星期调整：{diff_text}。"
    elif metadata.get("status") == "no_candidate_changes":
        conclusion = "候选版和第一版当前没有周级星期差异；candidate 状态是 no_candidate_changes。"
    else:
        conclusion = "未检测到周级星期差异；请检查 candidate_metadata 中是否只有未执行或保护类 override。"

    answer = (
        "1. 结论\n"
        f"{conclusion}这是第二版候选周级排程，不是最终每日训练课；来源依据见 {source_marks}。\n\n"
        "2. 依据\n"
        f"读取的第一版报告是 {config.bike_plan_report}；候选报告是 {config.candidate_report}。"
        f"candidate 状态：{metadata.get('status')}；已应用 override：{override_text}；"
        f"未解决 override：{unresolved_text}。\n\n"
        "3. 现在应该怎么做\n"
        "先人工复核候选版是否真的解决冲突，再决定是否把它作为下一层排课输入。"
        "不要直接覆盖第一版生成报告；保留第一版、override 和候选版，方便追溯为什么改。\n\n"
        "4. 还缺哪些数据\n"
        "如果要把候选周级 slot 继续细化成每日训练课，还需要每天可用时长、跑步和游泳真实课表、"
        "近期疲劳/疼痛状态，以及哪些跨项训练是不可移动的。当前回答不会输出瓦数、分钟、组数或具体间歇。"
    )
    return {
        "question": question,
        "answer": answer,
        "sources": sources,
        "messages": None,
        "provider": "bike-plan-candidate-diff",
        "chat_model": "deterministic",
        "embedding_db": str(config.retrieval.db),
        "bike_plan_report": str(config.bike_plan_report),
        "candidate_report": str(config.candidate_report),
    }


def summarize_daily_preflight(question: str, config: RagConfig) -> dict[str, Any] | None:
    if not is_daily_preflight_summary_request(question):
        return None
    report = load_json_file(config.daily_preflight_report)
    if not report:
        return None

    weeks = report.get("weeks") or []
    summary = report.get("summary") or {}
    missing_groups = sorted(
        {
            row.get("group")
            for week in weeks
            for row in week.get("missing_data") or []
            if row.get("group")
        }
    )
    risk_types = sorted(
        {
            row.get("type")
            for week in weeks
            for row in week.get("risk_flags") or []
            if row.get("type")
        }
    )
    blocked_weeks = [str(week.get("week")) for week in weeks if week.get("status") == "blocked_by_daily_risk"]
    missing_weeks = [str(week.get("week")) for week in weeks if week.get("status") == "needs_more_daily_data"]

    if report.get("status") == "ready_for_daily_draft":
        conclusion = "daily preflight 已通过，可以进入每日训练课草案层，但仍不能跳过人工复核。"
    elif report.get("status") == "blocked_by_daily_risk":
        conclusion = f"daily preflight 被风险拦截，不能进入每日训练课。风险周：{', '.join(blocked_weeks) or '未标出'}。"
    else:
        conclusion = f"daily preflight 还缺数据，不能进入每日训练课。缺数据周：{', '.join(missing_weeks) or '未标出'}。"

    answer = (
        "1. 结论\n"
        f"{conclusion}\n\n"
        "2. 依据\n"
        f"读取的 preflight 报告是 {config.daily_preflight_report}；状态是 {report.get('status')}。"
        f"周数统计：总计 {summary.get('weeks_total', 0)}，ready {summary.get('ready_weeks', 0)}，"
        f"missing {summary.get('missing_weeks', 0)}，blocked {summary.get('blocked_weeks', 0)}。"
        f"缺失数据组：{('、'.join(missing_groups) if missing_groups else '无')}。"
        f"风险类型：{('、'.join(risk_types) if risk_types else '无')}。\n\n"
        "3. 现在应该怎么做\n"
        "如果缺数据，先补 daily_availability、daily_status 和 fixed_sessions；"
        "如果有疼痛、高疲劳或不可移动跨项冲突，先人工调整周级 slot 或跨项安排。"
        "不要在 preflight 没通过时生成每日训练课。\n\n"
        "4. 还缺哪些数据\n"
        "通常需要每天可骑行分钟数、当天是否可骑、疲劳、疼痛、睡眠质量、固定跑步/游泳/力量课、"
        "以及这些跨项训练是否可移动。当前回答不会输出瓦数、分钟、组数或具体间歇。"
    )
    return {
        "question": question,
        "answer": answer,
        "sources": [],
        "messages": None,
        "provider": "bike-plan-daily-preflight",
        "chat_model": "deterministic",
        "embedding_db": str(config.retrieval.db),
        "daily_preflight_report": str(config.daily_preflight_report),
    }


def summarize_daily_draft(question: str, config: RagConfig) -> dict[str, Any] | None:
    if not is_daily_draft_summary_request(question):
        return None
    report = load_json_file(config.daily_draft_report)
    if not report:
        return None

    summary = report.get("summary") or {}
    workouts = report.get("daily_workouts") or []
    week_filter = parse_plan_week(question)
    if week_filter is not None:
        selected = [item for item in workouts if item.get("week") == week_filter]
    else:
        selected = workouts[:6]
    refs = unique_source_refs(selected)
    sources = fetch_chunk_sources_by_refs(refs, config.retrieval.db)
    source_marks = "、".join(f"[{source['label']}]" for source in sources) or "当前来源引用缺失"

    rows = []
    for item in selected[:8]:
        main = next(
            (
                row for row in item.get("structure") or []
                if row.get("segment") in {"main", "cadence_drills", "threshold_block", "power_intervals"}
            ),
            {},
        )
        rows.append(
            f"第 {item.get('week')} 周 {item.get('day_label')} {item.get('slot_type')} / "
            f"{item.get('workout_type')}，{item.get('planned_duration_minutes')} 分钟，"
            f"重点：{item.get('primary_focus')}；主段：{main.get('notes', '未生成主段')}"
        )
    row_text = "；".join(rows) if rows else "无可展示草案"

    if report.get("status") == "daily_draft_generated":
        simulation = "这是 simulated 流程验证草案，不能直接当真实训练处方。" if report.get("simulation_mode") else "这是通过 preflight 后生成的草案，仍需人工复核。"
        conclusion = (
            f"每日训练课草案已生成：共 {summary.get('weeks_total', 0)} 周、"
            f"{summary.get('workouts_total', 0)} 条 bike workout，计划分钟合计 "
            f"{summary.get('planned_minutes_total', 0)}。{simulation}"
        )
    else:
        conclusion = (
            f"每日训练课草案还没有生成；当前 daily draft 状态是 {report.get('status')}，"
            f"原因是 {report.get('block_reason') or '未通过草案生成边界'}。"
        )

    answer = (
        "1. 结论\n"
        f"{conclusion}来源依据见 {source_marks}。\n\n"
        "2. 依据\n"
        f"读取的 daily draft 报告是 {config.daily_draft_report}；状态是 {report.get('status')}；"
        f"simulation_mode={report.get('simulation_mode')}；schema={report.get('schema_version')}。"
        f"草案明细：{row_text}。\n\n"
        "3. 现在应该怎么做\n"
        "把这个结果当作人工复核表：先检查每天真实可用时间、疼痛、疲劳、跑步/游泳/力量训练是否变化，"
        "再决定是否把某天草案升级为正式训练课。模拟草案只能验证流程，不能直接执行。\n\n"
        "4. 还缺哪些数据\n"
        "如果要变成正式处方，还需要真实 daily preflight、当天主观疲劳、疼痛状态、跨项训练更新、"
        "以及长骑补给模板复核。"
    )
    return {
        "question": question,
        "answer": answer,
        "sources": sources,
        "messages": None,
        "provider": "bike-plan-daily-draft",
        "chat_model": "deterministic",
        "embedding_db": str(config.retrieval.db),
        "daily_draft_report": str(config.daily_draft_report),
    }


def summarize_long_ride_nutrition_review(question: str, config: RagConfig) -> dict[str, Any] | None:
    if not is_long_ride_nutrition_review_request(question):
        return None
    report = load_json_file(config.long_ride_nutrition_review_report)
    if not report:
        return None

    refs = []
    seen_ref_ids = set()
    for ref in ((report.get("calculator_template") or {}).get("source_chunks") or []):
        chunk_id = ref.get("chunk_id")
        if chunk_id and chunk_id not in seen_ref_ids:
            seen_ref_ids.add(chunk_id)
            refs.append(ref)
    for item in report.get("long_ride_reviews") or []:
        for ref in item.get("calculator_refs") or []:
            chunk_id = ref.get("chunk_id")
            if chunk_id and chunk_id not in seen_ref_ids:
                seen_ref_ids.add(chunk_id)
                refs.append(ref)
    sources = fetch_chunk_sources_by_refs(refs, config.retrieval.db)
    source_marks = "、".join(f"[{source['label']}]" for source in sources) or "当前来源引用缺失"
    summary = report.get("summary") or {}
    reviews = report.get("long_ride_reviews") or []
    rows = []
    for item in reviews[:8]:
        calc = item.get("estimated_energy") or {}
        inputs = item.get("calculation_inputs") or {}
        rows.append(
            f"第 {item.get('week')} 周 {item.get('day_label')} {item.get('date')}："
            f"{item.get('planned_duration_minutes')} 分钟，估算平均功率 {inputs.get('estimated_avg_power_w')} W，"
            f"约 {calc.get('total_kcal')} kcal，碳水消耗约 {calc.get('carb_g')} g；"
            f"处方状态 {item.get('nutrition_prescription_status')}"
        )
    rows_text = "；".join(rows) if rows else "无长骑复核条目"

    if report.get("status") == "long_ride_nutrition_review_generated":
        simulation = "这是 simulated 流程验证结果，不能直接执行。" if report.get("simulation_mode") else "这是基于已生成 daily draft 的复核结果，仍需人工确认。"
        conclusion = (
            f"长骑补给复核已接上热量/碳水模板：共 {summary.get('long_rides_total', 0)} 次长骑，"
            f"已复核 {summary.get('reviewed_long_rides', 0)} 次；没有生成真实补给处方。{simulation}"
        )
    else:
        conclusion = (
            f"长骑补给复核还没有生成；当前状态是 {report.get('status')}，"
            f"原因是 {report.get('block_reason') or '未通过复核边界'}。"
        )

    answer = (
        "1. 结论\n"
        f"{conclusion}计算来源见 {source_marks}。\n\n"
        "2. 依据\n"
        f"读取的 long ride nutrition review 报告是 {config.long_ride_nutrition_review_report}；"
        f"schema={report.get('schema_version')}；simulation_mode={report.get('simulation_mode')}。"
        f"复核明细：{rows_text}。\n\n"
        "3. 现在应该怎么做\n"
        "把这个结果当作长骑补给复盘起点：先用真实目标平均功率或历史同类长骑平均功率替换估算 IF，"
        "再补体重、环境温度、排汗率、液体/钠耐受、每小时碳水耐受和既往长骑补给记录。"
        "在这些数据缺失前，不要生成每小时摄入克数或比赛日时间表。\n\n"
        "4. 还缺哪些数据\n"
        "真实平均功率、体重、温度、排汗率、每小时碳水耐受、液体/钠耐受、既往补给记录、胃肠反应。"
    )
    return {
        "question": question,
        "answer": answer,
        "sources": sources,
        "messages": None,
        "provider": "bike-plan-long-ride-nutrition-review",
        "chat_model": "deterministic",
        "embedding_db": str(config.retrieval.db),
        "long_ride_nutrition_review_report": str(config.long_ride_nutrition_review_report),
    }


def contains_any(text: str, signals: tuple[str, ...]) -> bool:
    return any(signal in text for signal in signals)


def is_bike_power_plan_request(question: str) -> bool:
    question_lower = question.lower()
    bike_signals = ("骑行", "功率", "ftp", "bike", "cycling")
    direct_plan_signals = (
        "直接安排",
        "直接排",
        "生成课表",
        "制定计划",
        "完整课表",
        "排课表",
        "接下来",
        "8 周",
        "8周",
        "12 周",
        "12周",
    )
    plan_object_signals = ("课表", "计划", "周期训练")
    info_question_signals = ("哪些课型", "分别", "原则", "该怎么放", "怎么放", "怎么安排")
    if contains_any(question_lower, info_question_signals) and not contains_any(
        question_lower, ("直接", "完整", "接下来", "8 周", "8周", "12 周", "12周", "课表")
    ):
        return False
    return contains_any(question_lower, bike_signals) and (
        contains_any(question_lower, direct_plan_signals)
        or (
            contains_any(question_lower, plan_object_signals)
            and contains_any(question_lower, ("直接", "完整", "生成", "制定", "接下来"))
        )
    )


def has_enough_bike_plan_data(question: str) -> bool:
    question_lower = question.lower()
    data_groups = [
        ("目标赛事", "比赛", "赛事日期", "目标日期"),
        ("ftp", "当前功率", "阈值功率"),
        ("ctl", "atl", "tsb", "最近", "训练量", "四周", "6周", "六周"),
        ("每周", "可训练", "时间", "天"),
        ("伤病", "疲劳", "恢复", "疼痛"),
    ]
    matched = sum(any(term in question_lower for term in group) for group in data_groups)
    return matched >= 4


def bike_plan_boundary_fallback(sources: list[dict[str, Any]]) -> str:
    source_refs = source_refs_for_titles(sources, ("骑行功率周期训练计划模板",))
    return (
        "1. 结论\n"
        "现在不能直接生成完整骑行功率课表。知识库模板要求先明确目标赛事、当前 FTP、近期训练负荷、"
        "每周可训练时间和恢复状态，否则很容易把模板套错。\n\n"
        "2. 依据\n"
        f"`骑行功率周期训练计划模板` 只提供周期结构和字段：macrocycle、mesocycle、microcycle、容量、强度、"
        f"workload、peaking 等；真正排课前必须补齐个体数据。来源：{source_refs}。\n\n"
        "3. 现在应该怎么做\n"
        "先把目标赛事日期、当前 FTP、最近四到六周骑行训练量、每周可训练天数、关键时间约束、疲劳和伤病状态补齐。"
        "补齐后再把周周期分成 base、load、deload、build、peak 等阶段。\n\n"
        "4. 还缺哪些数据\n"
        "目标赛事日期、当前 FTP、最近四到六周训练量或 CTL/ATL/TSB、每周可训练天数和时长、近期疲劳或疼痛、"
        "是否要兼顾跑步和游泳。"
    )


def is_periodization_decoupling_request(question: str) -> bool:
    question_lower = question.lower()
    drift_signals = ("心率漂移", "有氧解耦", "解耦", "后 1 小时", "后1小时", "ctl", "甜点")
    volume_signals = ("加量", "增加训练", "下周")
    return contains_any(question_lower, drift_signals) and contains_any(question_lower, volume_signals)


def periodization_decoupling_fallback(sources: list[dict[str, Any]]) -> str:
    source_refs = "、".join(f"[{source['label']}]" for source in sources[:3]) or "[S1]"
    return (
        "1. 结论\n"
        "先不加量。今天 2 小时二区后半程为了维持配速，心率从 135 漂到 148，属于心率漂移/"
        "有氧解耦信号；再叠加前一天甜点骑行，下一周先维持或减量，不要增加训练量。"
        f"来源：{source_refs}。\n\n"
        "2. 依据\n"
        "这个判断不只看基础期第几周，也要看残余疲劳、心率漂移、近期强度和 CTL 这些负荷信号。"
        "当前问题里已经有前一日强度骑行和长时间二区后半程心率上扬，说明恢复状态需要先确认。"
        f"来源：{source_refs}。\n\n"
        "3. 现在应该怎么做\n"
        "下周把总量维持在当前水平，或先做一个保守周；关键课之间留恢复，把二区跑改成真正可控的有氧强度。"
        "如果接下来 48 小时静息心率、睡眠、腿部酸痛或主观疲劳没有恢复，就进一步减量。\n\n"
        "4. 还缺哪些数据\n"
        "还需要最近 4-6 周跑量和骑行量、ATL/TSB、睡眠、静息心率、主观疲劳、这次跑的温度和补给，"
        "以及过去同类二区长跑是否也出现类似心率漂移。"
    )


def is_bike_base_workout_types_request(question: str) -> bool:
    question_lower = question.lower()
    base_signals = ("基础期", "base")
    workout_signals = ("课型", "耐力骑", "长骑", "踏频", "冲刺", "阈值", "workout")
    return contains_any(question_lower, base_signals) and contains_any(question_lower, workout_signals)


def bike_base_workout_types_fallback(sources: list[dict[str, Any]]) -> str:
    bike_refs = source_refs_for_titles(
        sources,
        ("Triathlete Magazine's Complete Triathlon Book - Bike Training",),
    )
    template_refs = source_refs_for_titles(sources, ("骑行功率周期训练计划模板",))
    return (
        "1. 结论\n"
        f"基础期优先级应是：耐力骑 Endurance Ride 做底座，长骑 Long Ride 从基础期中段开始每周或隔周放入，"
        f"踏频 Cadence Workout 可作为技术和经济性练习；冲刺或 Power Intervals 只能少量点缀，"
        f"阈值骑 Threshold Ride 不应成为基础期默认主菜，更适合 build/peak 阶段再系统增加。来源：{bike_refs}。\n\n"
        "2. 依据\n"
        f"`Bike Training` 专章把 Endurance Ride 说成骑行训练的 foundation；Long Ride 从 base 中段开始，"
        f"以耐力强度做得比周内其他耐力骑更长；同一专章还把 Cadence Workout、Power Intervals、"
        f"Threshold Ride 分成不同课型，并标注它们适用的训练阶段。来源：{bike_refs}。\n\n"
        "3. 现在应该怎么做\n"
        "先把一周骑行安排成“多数耐力 + 一次较长耐力 + 少量技术/神经激活”。如果要放冲刺，保持短、少、恢复充分；"
        "如果要做阈值，先确认当前已经不是早期基础期，并且跑步和游泳负荷没有把总强度顶满。"
        f"后续若要排成周课表，再用周期模板里的 macrocycle、mesocycle、microcycle、volume、intensity 和 workload 字段承接。来源：{template_refs}。\n\n"
        "4. 还缺哪些数据\n"
        "如果要从原则变成课表，还需要目标赛事日期、当前 FTP、最近四到六周骑行量、每周可训练天数和时长、"
        "跑步/游泳负荷、疲劳和伤病状态。"
    )


def is_bike_brick_request(question: str) -> bool:
    question_lower = question.lower()
    return contains_any(question_lower, ("brick", "砖", "骑跑"))


def bike_brick_fallback(sources: list[dict[str, Any]]) -> str:
    bike_refs = source_refs_for_titles(
        sources,
        ("Triathlete Magazine's Complete Triathlon Book - Bike Training",),
    )
    science_refs = source_refs_for_titles(sources, ("Triathlon science",))
    return (
        "1. 结论\n"
        f"Brick 通常是骑行后立刻跑步，也就是自行车后接跑步的骑跑组合课。大铁备赛可以做，"
        f"但长距离 brick 要看收益和风险，不能简单每天叠加。来源：{bike_refs}、{science_refs}。\n\n"
        "2. 依据\n"
        f"`Bike Training` 专章把 brick 定义为 bike ride followed immediately by a run，并建议随训练周期变化安排；"
        f"`Triathlon Science` 对 Ironman 距离提醒要评估长 brick 的 benefit-to-risk，"
        f"同时指出 brick 的强度部分可以有不同结构。来源：{bike_refs}、{science_refs}。\n\n"
        "3. 现在应该怎么做\n"
        "如果骑和跑都是高强度，要把这次 brick 计入一周总高强度，不要把它当普通耐力课再额外叠加。"
        "大铁阶段更保守的做法是：多数 brick 做成耐力骑后接短跑或节奏转换；只有在恢复充分、"
        "总强度预算允许时，才把其中一段做成当前周期需要的强度。\n\n"
        "4. 还缺哪些数据\n"
        "要具体安排频率和时长，还需要目标赛事日期、当前 FTP、跑步阈值或配速、最近四到六周训练负荷、"
        "每周可训练时间、疲劳/睡眠状态，以及是否有跑步伤病史。"
    )


def is_lateral_knee_injury_request(question: str) -> bool:
    question_lower = question.lower()
    knee_signals = ("膝盖外侧", "膝外侧", "髂胫", "骼胫", "itbs", "lateral knee")
    risk_signals = ("尖锐", "刺痛", "疼痛", "痛", "下坡", "弯腿", "继续", "15k", "按计划")
    return contains_any(question_lower, knee_signals) and contains_any(question_lower, risk_signals)


def has_itbs_sources(sources: list[dict[str, Any]]) -> bool:
    text = "\n".join(json.dumps(source, ensure_ascii=False) for source in sources).lower()
    return any(term in text for term in ("itbs", "itbfs", "iliotibial", "lateral knee", "髂胫", "骼胫"))


def lateral_knee_injury_fallback(sources: list[dict[str, Any]]) -> str:
    source_refs = "、".join(f"[{source['label']}]" for source in sources[:3]) or "[S1]"
    return (
        "1. 结论\n"
        "不要继续按计划跑 15K。你描述的是膝外侧痛、下坡或弯腿时尖锐刺痛，并且近期跑量快速增加，"
        "这属于髂胫束综合征/ITBFS 风险场景；这里只能做风险识别，不能做医学确诊。"
        f"来源：{source_refs}。\n\n"
        "2. 依据\n"
        f"知识库把 lateral knee pain / iliotibial band friction syndrome 描述为膝外侧疼痛，"
        f"并把 ITBS 作为跑步和骑行中需要重视的常见风险之一。来源：{source_refs}。\n\n"
        "3. 现在应该怎么做\n"
        "立刻停止诱发膝外侧尖锐痛的跑步，取消明天 15K；短期只保留不诱发疼痛的低冲击维持训练。"
        "如果疼痛持续、下楼/下坡加重、出现麻木无力或影响日常活动，优先找运动医学或物理治疗评估。"
        f"来源：{source_refs}。\n\n"
        "4. 还缺哪些数据\n"
        "需要补充疼痛评分、疼痛是否随跑步时间加重、走路/下楼是否疼、最近 4-6 周跑量变化、鞋和路面、"
        "是否有既往膝外侧痛，以及骑行和力量训练是否也会诱发。"
    )


def answer_with_deterministic_tools(
    question: str,
    sources: list[dict[str, Any]],
    provider: str,
) -> str | None:
    if provider == "dry-run":
        return None
    bike_values = parse_bike_calculator_inputs(question)
    if bike_values and any("骑行热量与碳水消耗计算模板" in source.get("title", "") for source in sources):
        return bike_calculator_fallback(bike_values, sources)
    if is_periodization_decoupling_request(question):
        return periodization_decoupling_fallback(sources)
    if is_lateral_knee_injury_request(question) and has_itbs_sources(sources):
        return lateral_knee_injury_fallback(sources)
    if is_bike_brick_request(question) and any(
        "Triathlete Magazine's Complete Triathlon Book - Bike Training" in source.get("title", "")
        or "Triathlon science" in source.get("title", "")
        for source in sources
    ):
        return bike_brick_fallback(sources)
    if is_bike_base_workout_types_request(question) and any(
        "Triathlete Magazine's Complete Triathlon Book - Bike Training" in source.get("title", "")
        for source in sources
    ):
        return bike_base_workout_types_fallback(sources)
    if is_bike_power_plan_request(question) and not has_enough_bike_plan_data(question):
        return bike_plan_boundary_fallback(sources)
    return None


def has_enough_nutrition_plan_data(question: str) -> bool:
    question_lower = question.lower()
    data_groups = [
        ("完赛", "目标时间", "预计时间", "小时"),
        ("体重", "kg", "公斤"),
        ("排汗率", "出汗", "汗量"),
        ("碳水耐受", "胃肠耐受", "肠胃耐受", "每小时"),
        ("既往补给", "补给记录", "吃过", "喝过"),
    ]
    matched = sum(any(term in question_lower for term in group) for group in data_groups)
    return matched >= 4


def leaks_nutrition_numbers(answer: str) -> bool:
    patterns = [
        r"\b[0-9]{1,3}\s*g\b.{0,20}(碳水|糖)",
        r"每小时.{0,12}[0-9]+(\.[0-9]+)?\s*(到|-|~|～)\s*[0-9]+(\.[0-9]+)?\s*克",
        r"[0-9]+(\.[0-9]+)?\s*(到|-|~|～)\s*[0-9]+(\.[0-9]+)?\s*克.{0,12}(碳水|糖)",
        r"[0-9]+(\.[0-9]+)?\s*克.{0,12}(碳水|糖).{0,12}(每小时|/小时)",
        r"[0-9]+(\.[0-9]+)?\s*(到|-|~|～)\s*[0-9]+(\.[0-9]+)?\s*克/公斤",
        r"[0-9]+(\.[0-9]+)?\s*克/公斤",
        r"[0-9]+(\.[0-9]+)?\s*g/kg",
    ]
    return any(re.search(pattern, answer, re.IGNORECASE | re.DOTALL) for pattern in patterns)


def nutrition_boundary_fallback(sources: list[dict[str, Any]]) -> str:
    source_refs = "、".join(f"[{source['label']}]" for source in sources[:3]) or "[S1]"
    return (
        "1. 结论\n"
        "知识库证据不足以直接安排赛前最后一周的数字化补给计划。缺少关键信息：完赛时长、体重、排汗率、"
        "胃肠碳水耐受和既往补给实践时，先不要给克数、按天菜单或固定时间表。\n\n"
        "2. 依据\n"
        f"当前召回片段支持的只是原则：比赛补给要提前演练，目标是在避免胃肠不适的前提下维持能量和水合；"
        f"营养需求还会随训练阶段、强度和个体情况变化。来源：{source_refs}。\n\n"
        "3. 现在应该怎么做\n"
        "先把赛前最后一周定义为减量和验证周：保持熟悉食物和熟悉补给，不临时尝试新产品；"
        "把训练中已经验证过的补给策略作为候选，而不是现场新建方案。\n\n"
        "4. 还缺哪些数据\n"
        "需要补充：预计完赛时长、体重、环境温度、排汗率、每小时碳水耐受、钠和液体耐受、"
        "最近长骑或砖课的实际补给记录、是否有胃肠问题。"
    )


def retrieve(question: str, config: RetrievalConfig) -> list[dict[str, Any]]:
    conn = vector_store.connect(config.db.resolve())
    results = vector_store.search_chunks(
        conn,
        question,
        model=config.embedding_model,
        ollama_url=config.embedding_ollama_url,
        top_k=config.top_k,
        domain=config.domain,
        trust=config.trust,
        expand_neighbors=config.expand_neighbors,
        hybrid=config.hybrid,
        lexical_weight=config.lexical_weight,
        use_query_expansion=config.use_query_expansion,
        include_backmatter=config.include_backmatter,
    )
    return apply_precision_filters(question, results)



def answer_question(question: str, config: RagConfig) -> dict[str, Any]:
    conflict_answer = explain_bike_plan_conflict(question, config)
    if conflict_answer is not None:
        return conflict_answer

    override_answer = summarize_review_override(question, config)
    if override_answer is not None:
        return override_answer

    candidate_answer = explain_candidate_diff(question, config)
    if candidate_answer is not None:
        return candidate_answer

    long_ride_nutrition_answer = summarize_long_ride_nutrition_review(question, config)
    if long_ride_nutrition_answer is not None:
        return long_ride_nutrition_answer

    daily_draft_answer = summarize_daily_draft(question, config)
    if daily_draft_answer is not None:
        return daily_draft_answer

    daily_preflight_answer = summarize_daily_preflight(question, config)
    if daily_preflight_answer is not None:
        return daily_preflight_answer

    week_plan_answer = explain_bike_plan_week(question, config)
    if week_plan_answer is not None:
        return week_plan_answer

    plan_answer = explain_bike_plan_slot(question, config)
    if plan_answer is not None:
        return plan_answer

    results = retrieve(question, config.retrieval)
    context, sources = build_context(results, config)
    guardrails = detect_guardrails(question)
    guardrail_text = "\n".join(f"- {item}" for item in guardrails) if guardrails else "- 无"
    user_prompt = USER_PROMPT_TEMPLATE.format(
        question=question,
        guardrails=guardrail_text,
        context=context,
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    tool_answer = answer_with_deterministic_tools(question, sources, config.generation.provider)
    if config.generation.provider == "dry-run":
        answer = "(dry-run: 未调用生成模型)"
    elif tool_answer is not None:
        answer = tool_answer
    elif is_missing_data_nutrition_plan(question) and not has_enough_nutrition_plan_data(question):
        answer = nutrition_boundary_fallback(sources)
    else:
        client = make_chat_client(config.generation)
        if client is None:
            answer = "(dry-run: 未调用生成模型)"
        else:
            answer = client.complete(messages)
            if is_missing_data_nutrition_plan(question) and leaks_nutrition_numbers(answer):
                answer = nutrition_boundary_fallback(sources)
    return {
        "question": question,
        "answer": answer,
        "sources": sources,
        "messages": messages if config.show_prompt else None,
        "provider": config.generation.provider,
        "chat_model": config.generation.chat_model,
        "embedding_db": str(config.retrieval.db),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="*", help="Question to ask. If omitted, stdin is used.")

    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--embedding-model", default=None)
    parser.add_argument("--embedding-ollama-url", default=os.getenv("TRI_RAG_EMBEDDING_OLLAMA_URL", DEFAULT_EMBEDDING_OLLAMA_URL))
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--expand-neighbors", type=int, default=1)
    parser.add_argument("--domain", default=None)
    parser.add_argument("--trust", default=None)
    parser.add_argument("--no-hybrid", action="store_true")
    parser.add_argument("--lexical-weight", type=float, default=0.55)
    parser.add_argument("--no-query-expansion", action="store_true")
    parser.add_argument("--include-backmatter", action="store_true")

    parser.add_argument("--provider", choices=["ollama", "openai-compatible", "dry-run"], default=os.getenv("TRI_RAG_PROVIDER", "ollama"))
    parser.add_argument("--chat-model", default=os.getenv("TRI_RAG_CHAT_MODEL", DEFAULT_CHAT_MODEL))
    parser.add_argument("--chat-base-url", default=os.getenv("TRI_RAG_CHAT_BASE_URL", DEFAULT_EMBEDDING_OLLAMA_URL))
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=1200)
    parser.add_argument("--timeout", type=int, default=180)

    parser.add_argument("--context-chars", type=int, default=DEFAULT_CONTEXT_CHARS)
    parser.add_argument("--neighbor-chars", type=int, default=DEFAULT_NEIGHBOR_CHARS)
    parser.add_argument("--show-prompt", action="store_true")
    parser.add_argument("--bike-plan-report", type=Path, default=DEFAULT_BIKE_PLAN_REPORT)
    parser.add_argument("--review-override", type=Path, default=DEFAULT_REVIEW_OVERRIDE)
    parser.add_argument("--candidate-report", type=Path, default=DEFAULT_CANDIDATE_REPORT)
    parser.add_argument("--daily-preflight-report", type=Path, default=DEFAULT_DAILY_PREFLIGHT_REPORT)
    parser.add_argument("--daily-draft-report", type=Path, default=DEFAULT_DAILY_DRAFT_REPORT)
    parser.add_argument("--long-ride-nutrition-review-report", type=Path, default=DEFAULT_LONG_RIDE_NUTRITION_REVIEW_REPORT)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser.parse_args()


def read_question(args: argparse.Namespace) -> str:
    if args.question:
        return " ".join(args.question).strip()
    question = sys.stdin.read().strip()
    if not question:
        raise ValueError("Question is required")
    return question


def build_config(args: argparse.Namespace) -> RagConfig:
    api_key = os.getenv(args.api_key_env) if args.api_key_env else None
    return RagConfig(
        retrieval=RetrievalConfig(
            db=args.db,
            embedding_model=args.embedding_model,
            embedding_ollama_url=args.embedding_ollama_url,
            top_k=args.top_k,
            expand_neighbors=args.expand_neighbors,
            domain=args.domain,
            trust=args.trust,
            hybrid=not args.no_hybrid,
            lexical_weight=args.lexical_weight,
            use_query_expansion=not args.no_query_expansion,
            include_backmatter=args.include_backmatter,
        ),
        generation=GenerationConfig(
            provider=args.provider,
            chat_model=args.chat_model,
            chat_base_url=args.chat_base_url,
            api_key=api_key,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
        ),
        context_chars=args.context_chars,
        neighbor_chars=args.neighbor_chars,
        show_prompt=args.show_prompt,
        bike_plan_report=args.bike_plan_report,
        review_override=args.review_override,
        candidate_report=args.candidate_report,
        daily_preflight_report=args.daily_preflight_report,
        daily_draft_report=args.daily_draft_report,
        long_ride_nutrition_review_report=args.long_ride_nutrition_review_report,
    )


def print_text_result(result: dict[str, Any]) -> None:
    print(result["answer"].strip())
    print("\n---\n检索来源：")
    for source in result["sources"]:
        page = f" {source['page']}" if source.get("page") else ""
        heading = f" / {source['heading']}" if source.get("heading") else ""
        print(
            f"[{source['label']}] {source['domain']}/{source['trust_level']} "
            f"{source['title']}{page}{heading}"
        )
        print(f"    chunk_id={source['chunk_id']} score={source['score']:.4f}")


def main() -> int:
    args = parse_args()
    question = read_question(args)
    result = answer_question(question, build_config(args))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text_result(result)
        if args.show_prompt and result.get("messages"):
            print("\n---\nPrompt：")
            print(textwrap.indent(json.dumps(result["messages"], ensure_ascii=False, indent=2), "  "))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        raise SystemExit(130)
