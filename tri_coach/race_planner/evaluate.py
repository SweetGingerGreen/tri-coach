"""
Evaluate upcoming races for one athlete: which category she would enter, what
it costs, which prize ladder applies, and the finishing rank that covers costs.

There is no opponent data yet, so nothing here is a probability. The numbers
are money at stake and a break-even rank; the athlete judges her own odds.
All money is CNY. Pre-tax prizes are reduced by ``tax_rate`` (China withholds
20% on prize money as 偶然所得) before comparing them with costs.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any

from tri_coach.race_planner import geo, store

DEFAULT_PLANNER: dict[str, Any] = {
    "gender": None,
    "age_group": None,           # e.g. "F30-34", valid in age_group_year
    "age_group_year": None,
    "birth_year": None,          # better than age_group when known (age at 31 Dec of the race year)
    "home_city": None,
    "home_province": None,
    "ctsa_member": None,         # True: member fee; False/None: non-member fee
    "formats": ["olympic", "middle", "sprint"],   # categories she would enter, in order of preference
    "sports": ["triathlon"],
    "max_races_per_month": 2,
    "budget_cny": None,
    "tax_rate": 0.2,
    "travel_cny": {               # rough round-trip travel + bike transport, by distance from home
        "same_city": 100, "le_300km": 400, "le_800km": 900, "le_1500km": 1500, "far": 2500, "abroad": 6000,
    },
    "hotel_nights": {"supersprint": 0, "sprint": 1, "olympic": 1, "middle": 2, "full": 3, "default": 1},
    "hotel_per_night_cny": 400,
    "spacing_days": {             # minimum days between two races of these formats (the larger one wins)
        "supersprint": 5, "sprint": 7, "olympic": 10, "middle": 21, "full": 42,
        "hyrox": 10, "cycling": 3, "default": 7,
    },
}
SIDE_EVENT_WORDS = r"青少年|小铁人|儿童|亲子|家庭|接力|团体|团队|夫妻|骑跑|游跑|桨板|皮艇|雪地|充气|垂钓|体验|迷你|超短"
RANK_TOKENS = ("T100全程", "T100半程", "全程", "半程", "长距离", "中距离", "短距离", "标准", "奥运", "冲刺", "精英", "公开")


@dataclass
class Profile:
    athlete_key: str
    gender: str | None
    age_range_ref: tuple[int, int] | None
    age_ref_year: int | None
    birth_year: int | None
    home_city: str | None
    home_province: str | None
    ctsa_member: bool | None
    formats: list[str]
    sports: list[str]
    max_races_per_month: int
    budget_cny: float | None
    tax_rate: float
    travel_cny: dict[str, float]
    hotel_nights: dict[str, int]
    hotel_per_night_cny: float
    spacing_days: dict[str, int]
    raw: dict[str, Any] = field(default_factory=dict)

    def age_range(self, year: int) -> tuple[int, int] | None:
        if self.birth_year:
            age = year - self.birth_year
            return age, age
        if self.age_range_ref and self.age_ref_year:
            shift = year - self.age_ref_year
            return self.age_range_ref[0] + shift, self.age_range_ref[1] + shift
        return None

    def spacing(self, *formats: str | None) -> int:
        return max(self.spacing_days.get(fmt or "default", self.spacing_days["default"]) for fmt in formats)


def load_profile(conn: sqlite3.Connection, athlete_key: str = "self") -> Profile:
    row = conn.execute("SELECT data FROM athlete_profile WHERE athlete_key = ?", (athlete_key,)).fetchone()
    raw = json.loads(row["data"]) if row else {}
    planner = {**DEFAULT_PLANNER, **(raw.get("planner") or {})}
    for key in ("travel_cny", "hotel_nights", "spacing_days"):
        planner[key] = {**DEFAULT_PLANNER[key], **((raw.get("planner") or {}).get(key) or {})}
    age_group = planner["age_group"]
    age_year = planner["age_group_year"]
    if not age_group:
        for key, value in raw.items():
            match = re.fullmatch(r"age_group_(\d{4})", key)
            if match and isinstance(value, str):
                age_group, age_year = value, int(match.group(1))
    age_range = None
    if age_group:
        match = re.search(r"(\d{2})\s*-\s*(\d{2})", age_group)
        if match:
            age_range = (int(match.group(1)), int(match.group(2)))
    gender = planner["gender"] or raw.get("gender") or (age_group[0] if age_group and age_group[0] in "FM" else None)
    home_province, home_city = planner["home_province"], planner["home_city"]
    if not home_city and raw.get("home"):
        parts = str(raw["home"]).split()
        home_province = home_province or (parts[0] if len(parts) > 1 else None)
        home_city = parts[-1]
    return Profile(
        athlete_key=athlete_key, gender=gender, age_range_ref=age_range, age_ref_year=age_year,
        birth_year=planner["birth_year"], home_city=home_city, home_province=home_province,
        ctsa_member=planner["ctsa_member"], formats=list(planner["formats"]), sports=list(planner["sports"]),
        max_races_per_month=int(planner["max_races_per_month"]), budget_cny=planner["budget_cny"],
        tax_rate=float(planner["tax_rate"]), travel_cny=planner["travel_cny"], hotel_nights=planner["hotel_nights"],
        hotel_per_night_cny=float(planner["hotel_per_night_cny"]), spacing_days=planner["spacing_days"], raw=raw,
    )


@dataclass
class Evaluation:
    race: dict[str, Any]
    category: dict[str, Any] | None = None
    fee: float | None = None
    fee_basis: str | None = None
    travel: dict[str, Any] = field(default_factory=dict)
    cost: float | None = None
    cost_is_lower_bound: bool = False
    ladder: dict[str, Any] | None = None
    break_even_rank: int | None = None
    net_first: float | None = None
    calendar: dict[str, Any] | None = None
    conflicts: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    excluded: str | None = None

    @property
    def format(self) -> str | None:
        return (self.category or {}).get("format") or (self.ladder or {}).get("format")

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# --- category and fee --------------------------------------------------------

def _membership(name: str) -> str | None:
    if re.search(r"非\S*会员", name):
        return "non_member"
    if "会员" in name:
        return "member"
    return None


def _base_name(name: str) -> str:
    return re.sub(r"^(非)?中铁协注册个人会员\s*[-—－]\s*|^(非)?会员\s*[-—－]\s*", "", name).strip()


def choose_category(categories: list[dict[str, Any]], profile: Profile,
                    entered: str | None = None) -> tuple[dict[str, Any] | None, float | None, str | None]:
    """The category she would enter and its fee for her membership.

    ``entered`` is the category name from her own result or seed: when she
    already raced or entered this race, that category wins over format preference.
    """
    best = None
    if entered:
        key = re.sub(r"\s", "", entered)
        same = [c for c in categories if key in re.sub(r"\s", "", _base_name(c["name"])) or re.sub(r"\s", "", _base_name(c["name"])) in key]
        if same:
            same.sort(key=lambda c: (c.get("source") == "manual", c.get("bike_km") is None))
            best = same[0]
    if best is None:
        wanted = [fmt for fmt in profile.formats]
        fitting = [c for c in categories if c.get("format") in wanted and not re.search(SIDE_EVENT_WORDS, c["name"])]
        if not fitting:
            return None, None, None
        fitting.sort(key=lambda c: (wanted.index(c["format"]), c.get("source") == "manual", c.get("bike_km") is None))
        best = fitting[0]
    base = _base_name(best["name"])
    same = [c for c in categories if _base_name(c["name"]) == base and c.get("fee_cny")]
    wanted_membership = "member" if profile.ctsa_member else "non_member"
    fee, basis = None, None
    for candidate in same:
        if _membership(candidate["name"]) == wanted_membership:
            fee, basis = candidate["fee_cny"], "会员价" if wanted_membership == "member" else "非会员价"
            break
    if fee is None:
        priced = [c for c in same if c.get("fee_cny")] or ([best] if best.get("fee_cny") else [])
        if priced:
            fee = max(c["fee_cny"] for c in priced)
            basis = "报名费（未分会员）" if len(priced) == 1 else "报名费（取高的一档）"
    category = dict(best, name=base)
    return category, fee, basis


# --- prize ladder --------------------------------------------------------------

def _tokens(text: str) -> set[str]:
    found = set()
    for token in RANK_TOKENS:
        if token in text:
            found.add(token)
            text = text.replace(token, "")
    return found


def match_ladder(ladders: list[dict[str, Any]], category: dict[str, Any] | None, profile: Profile,
                 race_year: int) -> dict[str, Any] | None:
    """The prize ladder that would pay her, with a short reason; None if nothing fits."""
    ages = profile.age_range(race_year)
    category_tokens = _tokens(category["name"]) if category else set()
    scored = []
    for ladder in ladders:
        if ladder["status"] == "rejected":
            continue
        label = ladder["label"]
        if ladder.get("gender") in ("M", "F") and profile.gender and ladder["gender"] != profile.gender:
            continue
        if ladder.get("gender") == "X" or re.search(SIDE_EVENT_WORDS, label):
            continue
        if ladder.get("age_min") and ages and (ages[1] < ladder["age_min"] or ages[0] > ladder["age_max"]):
            continue
        ladder_tokens = _tokens(label)
        if category_tokens and ladder_tokens and not (category_tokens & ladder_tokens):
            continue  # e.g. 半程分龄组 when she enters the 全程
        # A table written for her gender and age beats a generic 分龄组 table at the same distance.
        score = 0
        reasons = []
        if category_tokens & ladder_tokens:
            score += 3
            reasons.append("组别对得上")
        if ladder.get("gender") == profile.gender and profile.gender:
            score += 2
            reasons.append("性别对得上")
        if ladder.get("age_min") and ages:
            score += 2
            reasons.append("年龄段对得上")
        if "分龄" in label:
            score += 1
            reasons.append("分龄组")
        if ladder["status"] in ("verified", "manual"):
            score += 1
        if not reasons:
            reasons.append("唯一可用的奖金表" if len(ladders) == 1 else "按排除法")
        scored.append((score, ladder, reasons))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    score, ladder, reasons = scored[0]
    return dict(ladder, match="、".join(reasons), match_score=score, alternatives=len(scored) - 1)


def net_ladder(ladder: dict[str, Any], tax_rate: float) -> tuple[dict[int, float], str]:
    ranks = {int(rank): float(amount) for rank, amount in ladder["ranks"].items()}
    if ladder.get("pre_tax") == 0:
        return ranks, "税后"
    note = "税前，按扣 {:.0%} 计".format(tax_rate) if ladder.get("pre_tax") == 1 else "未注明税前税后，按税前扣 {:.0%} 计".format(tax_rate)
    return {rank: round(amount * (1 - tax_rate)) for rank, amount in ranks.items()}, note


def break_even(net: dict[int, float], cost: float | None) -> int | None:
    """Lowest finishing position whose prize still covers the cost (None if even 1st does not)."""
    if cost is None or not net:
        return None
    covering = [rank for rank, amount in net.items() if amount >= cost]
    return max(covering) if covering else None


# --- travel --------------------------------------------------------------------

def travel_estimate(race: dict[str, Any], fmt: str | None, profile: Profile) -> dict[str, Any]:
    nights = profile.hotel_nights.get(fmt or "default", profile.hotel_nights["default"])
    hotel = nights * profile.hotel_per_night_cny
    if race.get("country") and race["country"] != "CN":
        return {"tier": "abroad", "distance_km": None, "cost": profile.travel_cny["abroad"] + hotel + 400, "basis": "境外"}
    if geo.same_city(profile.home_city, race.get("city")):
        return {"tier": "same_city", "distance_km": 0, "cost": profile.travel_cny["same_city"], "basis": "同城"}
    home, _ = geo.locate(profile.home_province, profile.home_city)
    point, basis = geo.locate(race.get("province"), race.get("city"), race.get("lat"), race.get("lng"))
    if home is None or point is None:
        return {"tier": "unknown", "distance_km": None, "cost": None, "basis": "地点不明"}
    distance = round(geo.km(home, point))
    tier = "le_300km" if distance <= 300 else "le_800km" if distance <= 800 else "le_1500km" if distance <= 1500 else "far"
    return {"tier": tier, "distance_km": distance, "cost": profile.travel_cny[tier] + hotel,
            "basis": f"直线约 {distance} km（{basis.split(':')[-1]}），含住 {nights} 晚"}


# --- per race ------------------------------------------------------------------

def _calendar(conn: sqlite3.Connection, athlete_key: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT e.race_id, e.role, e.status, e.note, r.name, r.start_date, r.end_date, r.sport,
                  (SELECT c.format FROM race_category c WHERE c.race_id = r.id AND c.format IS NOT NULL
                    ORDER BY c.source = 'manual' DESC LIMIT 1) AS format
           FROM calendar_entry e JOIN race r ON r.id = e.race_id WHERE e.athlete_key = ?""",
        (athlete_key,),
    ).fetchall()
    return [dict(row) for row in rows]


def _day(text: str | None) -> dt.date | None:
    return dt.date.fromisoformat(text) if text else None


def conflicts_for(race: dict[str, Any], fmt: str | None, calendar: list[dict[str, Any]], profile: Profile,
                  others: list[tuple[dt.date, str | None, str]] = ()) -> list[str]:
    day = _day(race.get("start_date"))
    if day is None:
        return []
    out = []
    for entry in calendar:
        if entry["race_id"] == race["id"] or entry["status"] == "dropped":
            continue
        other_day = _day(entry["start_date"])
        if other_day is None:
            continue
        other_format = entry["format"] if entry["sport"] == "triathlon" else entry["sport"]
        gap = abs((day - other_day).days)
        need = profile.spacing(fmt, other_format)
        if gap < need:
            role = f"{entry['role']} 赛" if entry["role"] else "已排的比赛"
            out.append(f"离{role}「{entry['name']}」{other_day.month}/{other_day.day} 只有 {gap} 天（至少要 {need} 天）")
    for other_day, other_format, other_name in others:
        gap = abs((day - other_day).days)
        need = profile.spacing(fmt, other_format)
        if gap < need:
            out.append(f"离已选的「{other_name}」只有 {gap} 天（至少要 {need} 天）")
    return out


def evaluate_races(conn: sqlite3.Connection, profile: Profile, *, today: dt.date, until: dt.date,
                   include_past: bool = False) -> list[Evaluation]:
    calendar = _calendar(conn, profile.athlete_key)
    calendar_by_race = {entry["race_id"]: entry for entry in calendar}
    start = dt.date(today.year, 1, 1) if include_past else today
    races = conn.execute(
        "SELECT * FROM race WHERE start_date BETWEEN ? AND ? ORDER BY start_date, id",
        (start.isoformat(), until.isoformat()),
    ).fetchall()
    out = []
    for row in races:
        race = dict(row)
        evaluation = Evaluation(race=race, calendar=calendar_by_race.get(race["id"]))
        out.append(evaluation)
        entry = evaluation.calendar
        if entry and entry["status"] == "dropped":
            evaluation.excluded = "本人已决定不去"
            continue
        if race.get("status") and "取消" in race["status"]:
            evaluation.excluded = "比赛取消"
            continue
        if race["audience"] != "adult":
            evaluation.excluded = "青少年赛事"
            continue
        if race["sport"] not in profile.sports:
            evaluation.excluded = f"项目是 {race['sport']}"
            continue
        if _day(race["start_date"]) < today and not include_past:
            evaluation.excluded = "已经比完"
            continue
        categories = [dict(c) for c in conn.execute("SELECT * FROM race_category WHERE race_id = ?", (race["id"],))]
        own = conn.execute("SELECT category FROM result WHERE race_id = ? AND athlete_key = ? AND category != ''",
                           (race["id"], profile.athlete_key)).fetchone()
        category, fee, basis = choose_category(categories, profile, own["category"] if own else None)
        if categories and category is None and not entry:
            evaluation.excluded = "没有你要报的距离"
            continue
        evaluation.category, evaluation.fee, evaluation.fee_basis = category, fee, basis
        ladders = []
        for prize in conn.execute("SELECT * FROM prize_table WHERE race_id = ?", (race["id"],)):
            item = dict(prize)
            item["ranks"] = store.prize_ladder(conn, prize["id"])
            ladders.append(item)
        ladder = match_ladder(ladders, category, profile, _day(race["start_date"]).year)
        evaluation.travel = travel_estimate(race, evaluation.format, profile)
        if evaluation.travel.get("cost") is not None:
            evaluation.cost = (fee or 0) + evaluation.travel["cost"]
            evaluation.cost_is_lower_bound = fee is None
        if ladder:
            net, tax_note = net_ladder(ladder, profile.tax_rate)
            ladder["net_ranks"] = net
            ladder["tax_note"] = tax_note
            evaluation.ladder = ladder
            evaluation.net_first = net.get(1)
            evaluation.break_even_rank = break_even(net, evaluation.cost)
            if ladder["status"] == "extracted":
                evaluation.flags.append("奖金表未校对")
            if ladder.get("group_count") is None and "分龄" not in ladder["label"] and not ladder.get("age_min") \
                    and ladder.get("method") != "manual":
                evaluation.flags.append("奖金表是否分年龄组不明")
        elif ladders:
            evaluation.flags.append("有奖金表但没有一张适用于你")
        elif race.get("rules_format") == "images":
            evaluation.flags.append("规程是图片，奖金未知")
        else:
            evaluation.flags.append("没找到奖金表")
        if fee is None:
            evaluation.flags.append("报名费未知，花费只算了差旅" if evaluation.cost is not None else "报名费未知")
        if evaluation.travel.get("cost") is None:
            evaluation.flags.append("差旅无法估算")
        reg_close = _day(race.get("reg_close"))
        if reg_close and reg_close < today and not entry:
            evaluation.flags.append(f"报名已于 {reg_close.isoformat()} 截止")
        elif race.get("status") and "截止" in race["status"] and not entry:
            evaluation.flags.append("报名已截止")
        evaluation.conflicts = conflicts_for(race, evaluation.format, calendar, profile)
        if race["sport"] == "triathlon" and profile.age_range(_day(race["start_date"]).year) and not profile.birth_year \
                and profile.age_ref_year and _day(race["start_date"]).year != profile.age_ref_year:
            evaluation.flags.append("年龄组可能已变（档案里没有出生年份）")
    return out


# --- season --------------------------------------------------------------------

@dataclass
class SeasonPlan:
    picked: list[Evaluation]
    fixed: list[Evaluation]
    skipped: list[tuple[Evaluation, str]]
    unknown_prize: list[Evaluation]
    not_worth: list[Evaluation]


def _money_key(evaluation: Evaluation) -> tuple:
    verified = 1 if evaluation.ladder and evaluation.ladder["status"] in ("verified", "manual") else 0
    margin = (evaluation.net_first or 0) - (evaluation.cost or 0)
    return (-(evaluation.break_even_rank or 0), -verified, -margin, evaluation.race["start_date"])


def _is_fixed(evaluation: Evaluation) -> bool:
    """Races already on her calendar; non-triathlon ones (HYROX, a gran fondo) still block dates."""
    entry = evaluation.calendar
    if not entry or entry["status"] not in ("planned", "entered", "done"):
        return False
    if not evaluation.excluded:
        return True
    return entry["status"] != "done" and evaluation.excluded.startswith("项目是")


def plan_season(evaluations: list[Evaluation], profile: Profile) -> SeasonPlan:
    """Greedy prize-money season: races where some finishing rank covers costs, spaced and capped."""
    fixed = [e for e in evaluations if _is_fixed(e)]
    candidates = [e for e in evaluations if not e.excluded and not e.calendar]
    unknown_prize = [e for e in candidates if e.ladder is None]
    not_worth = [e for e in candidates if e.ladder is not None and e.break_even_rank is None]
    ranked = sorted((e for e in candidates if e.break_even_rank), key=_money_key)
    picked: list[Evaluation] = []
    skipped: list[tuple[Evaluation, str]] = []
    spent = sum(e.cost or 0 for e in fixed)
    for evaluation in ranked:
        if evaluation.conflicts:
            skipped.append((evaluation, evaluation.conflicts[0]))
            continue
        if any("截止" in flag for flag in evaluation.flags):
            skipped.append((evaluation, "报名已截止"))
            continue
        day = _day(evaluation.race["start_date"])
        others = [(_day(e.race["start_date"]), e.format, e.race["name"]) for e in picked]
        clash = conflicts_for(evaluation.race, evaluation.format, [], profile, others)
        if clash:
            skipped.append((evaluation, clash[0]))
            continue
        same_month = [e for e in picked + fixed if _day(e.race["start_date"]).strftime("%Y-%m") == day.strftime("%Y-%m")]
        if len(same_month) >= profile.max_races_per_month:
            skipped.append((evaluation, f"{day.month} 月已经排了 {len(same_month)} 场"))
            continue
        if profile.budget_cny and evaluation.cost and spent + evaluation.cost > profile.budget_cny:
            skipped.append((evaluation, "超出预算"))
            continue
        picked.append(evaluation)
        spent += evaluation.cost or 0
    picked.sort(key=lambda e: e.race["start_date"])
    return SeasonPlan(picked=picked, fixed=fixed, skipped=skipped, unknown_prize=unknown_prize, not_worth=not_worth)
