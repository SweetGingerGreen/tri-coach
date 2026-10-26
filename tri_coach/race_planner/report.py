"""Markdown report for the athlete: what changed, the money table, a proposed season, and what is unknown."""
from __future__ import annotations

import datetime as dt
from typing import Any

from tri_coach.race_planner.evaluate import Evaluation, Profile, SeasonPlan

STATUS_NAMES = {"done": "已比完", "planned": "计划中", "entered": "已报名", "dropped": "不去了", "considering": "在考虑"}
FORMAT_NAMES = {"supersprint": "超短", "sprint": "冲刺/半程", "olympic": "标准", "middle": "中长(113/T100)",
                "full": "全程大铁", "relay": "接力", "duathlon": "骑跑", "aquathlon": "游跑", "youth": "青少年"}


def _money(value: float | None) -> str:
    return "—" if value is None else f"¥{value:,.0f}"


def _place(race: dict[str, Any]) -> str:
    return " ".join(part for part in (race.get("province"), race.get("city")) if part) or "—"


def _date(race: dict[str, Any]) -> str:
    return race.get("start_date") or "日期待定"


def _cell(text: str) -> str:
    return text.replace("|", "／").replace("\n", " ")


def _category(evaluation: Evaluation) -> str:
    category = evaluation.category
    if not category:
        return "—"
    legs = "/".join(f"{category[key]:g}" for key in ("swim_km", "bike_km", "run_km") if category.get(key) is not None)
    name = FORMAT_NAMES.get(category.get("format"), category.get("format") or "")
    return f"{name} {legs}".strip()


def _break_even(evaluation: Evaluation) -> str:
    if evaluation.break_even_rank:
        return f"前 {evaluation.break_even_rank} 名"
    if evaluation.ladder and evaluation.cost is not None:
        return "冠军也不够"
    return "—"


def money_table(evaluations: list[Evaluation]) -> list[str]:
    lines = ["| 日期 | 比赛 | 地点 | 组别 | 报名费 | 差旅估 | 冠军到手 | 保本名次 | 提示 |", "|---|---|---|---|---|---|---|---|---|"]
    for e in evaluations:
        notes = (["已在你的日历里"] if e.calendar else []) + e.conflicts[:1] + e.flags[:2]
        lines.append("| " + " | ".join(_cell(x) for x in (
            _date(e.race), e.race["name"][:30], _place(e.race), _category(e), _money(e.fee),
            _money((e.travel or {}).get("cost")), _money(e.net_first), _break_even(e) + ("（花费是下限）" if e.cost_is_lower_bound and e.break_even_rank else ""),
            "；".join(notes) or "",
        )) + " |")
    return lines


def render(*, profile: Profile, today: dt.date, until: dt.date, evaluations: list[Evaluation], plan: SeasonPlan,
           digest: dict[str, Any] | None, stats: dict[str, Any] | None) -> str:
    who = profile.raw.get("name") or profile.athlete_key
    lines = [f"# 赛事与奖金周报 · {who}", "",
             f"生成于 {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}，看的是 {today.isoformat()} 到 {until.isoformat()} 的比赛。", ""]
    if digest is not None:
        lines += ["## 这次有什么变化", ""]
        if digest.get("previous_run") is None:
            lines.append("这是第一次运行，没有可以比较的上一次。")
        body = []
        for item in digest.get("new_races", []):
            body.append(f"- **新赛事**：{item['start_date'] or '日期待定'} {item['name']}")
        for item in digest.get("changes", []):
            parts = "，".join(f"{field} {old or '空'} → {new or '空'}" for field, (old, new) in item["fields"].items())
            body.append(f"- **有改动**：{item['name']}：{parts}")
        for item in digest.get("new_prizes", []):
            body.append(f"- **新奖金表**：{item['name']}：{'、'.join(item['labels'])}")
        for item in digest.get("deadlines", []):
            body.append(f"- **报名快截止**：{item['name']}，{item['reg_close']} 截止，还剩 {item['days_left']} 天")
        for item in digest.get("opening", []):
            body.append(f"- **即将开放报名**：{item['name']}，{item['reg_open']} 开始")
        if body:
            lines += body
        elif digest.get("previous_run") is not None:
            lines.append("和上次相比没有变化。")
        lines.append("")
    lines += ["## 建议赛程（按奖金）", ""]
    if plan.fixed:
        lines.append("已经在你日历里的：")
        for e in plan.fixed:
            role = f"{e.calendar['role']} 赛，" if e.calendar.get("role") else ""
            lines.append(f"- {_date(e.race)} {e.race['name']}（{role}{STATUS_NAMES.get(e.calendar['status'], e.calendar['status'])}）")
        lines.append("")
    if plan.picked:
        lines.append("建议加上的（都是有名次能保本的比赛，按日期排）：")
        for e in plan.picked:
            first = _money(e.net_first)
            lines.append(f"- {_date(e.race)} {e.race['name']}（{_place(e.race)}）：冠军到手 {first}，"
                         f"总花费约 {_money(e.cost)}，{_break_even(e)}保本。奖金表「{e.ladder['label']}」，{e.ladder['match']}。")
    else:
        lines.append("现在没有能建议加上的比赛：要么还没公布，要么奖金不够覆盖花费，要么和已排的比赛冲突。")
    if plan.skipped:
        lines += ["", "有钱赚但没排进去的："]
        for e, reason in plan.skipped[:10]:
            lines.append(f"- {_date(e.race)} {e.race['name']}：{reason}")
    lines.append("")
    money = [e for e in evaluations if not e.excluded and e.ladder]
    if money:
        lines += ["## 有奖金表的比赛", ""] + money_table(money) + [""]
    if plan.unknown_prize:
        lines += ["## 奖金未知的比赛", ""]
        for e in plan.unknown_prize:
            lines.append(f"- {_date(e.race)} {e.race['name']}（{_place(e.race)}）：{'；'.join(e.flags[:2]) or '没有奖金信息'}")
        lines.append("")
    if plan.not_worth:
        lines += ["## 冠军奖金也不够花费的比赛", ""]
        for e in plan.not_worth:
            lines.append(f"- {_date(e.race)} {e.race['name']}：冠军到手 {_money(e.net_first)}，花费约 {_money(e.cost)}")
        lines.append("")
    excluded = [e for e in evaluations if e.excluded and e.excluded not in ("已经比完",) and not e.calendar]
    if excluded:
        counts: dict[str, int] = {}
        for e in excluded:
            counts[e.excluded] = counts.get(e.excluded, 0) + 1
        lines += ["## 没考虑的比赛", "", "；".join(f"{reason} {count} 场" for reason, count in counts.items()) + "。", ""]
    lines += ["## 这些数字怎么来的", "",
              "- 还没有对手成绩，所以这里没有夺奖概率，只有钱数和保本名次。能不能拿到那个名次，你自己判断。",
              f"- 奖金按扣 {profile.tax_rate:.0%} 个税算到手；规程没写税前税后的，也按税前算。",
              "- 差旅是按直线距离分档的粗估（300 km 内 ¥400、800 km 内 ¥900、1500 km 内 ¥1,500、更远 ¥2,500，另加每晚 ¥400 住宿），在档案的 planner.travel_cny 里可以改。",
              "- 标着「奖金表未校对」的表是程序从规程里读出来的，报名前对一下原文。"]
    if stats:
        lines += ["", f"本次抓取：{stats}"]
    return "\n".join(lines) + "\n"
