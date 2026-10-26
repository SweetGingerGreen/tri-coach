#!/usr/bin/env python3
"""
Offline regression checks for tri_coach/race_planner (no network).

Fixtures are small hand-written pages that copy the layouts seen on the real
sites, not copies of those pages. The checks keep the planner honest:
- every prize-table layout found so far reads into the right rank -> amount ladder;
- points tables, schedules, fee tables and after-tax copies never become prize ladders;
- the same race on two sites lands on one record, and youth races don't merge into adult ones;
- re-extraction never overwrites a ladder a person has verified;
- the fetcher reuses snapshots and honours robots.txt.
"""
from __future__ import annotations

import argparse
import json
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable

from tri_coach.paths import ROOT
from tri_coach.race_planner import categories as cat
from tri_coach.race_planner import ingest, prizes, seed, store
from tri_coach.race_planner.fetch import Fetcher, FetchError
from tri_coach.race_planner.sources import chinatriathlon, wildto, zuicool
from tri_coach.race_planner.textutil import amount_of, normalize_name, parse_date, rank_of, rank_span_of

DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "race_planner_eval_latest.json"


def check(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def ladder_map(extraction: prizes.Extraction) -> dict[str, dict[int, float]]:
    return {ladder.label: dict(sorted(ladder.ranks.items())) for ladder in extraction.ladders}


# --- fixtures ----------------------------------------------------------------

ROW_LADDER = """
<p><span>五、奖励办法</span></p>
<p><span>2.全程分龄组奖金分配方式如下：</span></p><p>（单位：人民币/元）</p>
<table><tr><td><p><span>组别</span></p></td><td>1</td><td>2</td><td>3</td><td>4</td></tr>
<tr><td><p>全程分龄组</p><p><span>（共</span><span>1</span><span>2组）</span></p></td><td>1,600</td><td>1200</td><td>800</td><td>600</td></tr>
<tr><td>半程分龄组（共9组）</td><td>800</td><td>600</td><td>400</td><td>300</td></tr>
<tr><td>女子精英组</td><td>3000</td><td>2000</td><td>1000</td><td></td></tr>
<tr><td>税后(元)</td><td>2400</td><td>1600</td><td>800</td><td></td></tr>
</table>
<p>注：以上奖金为税前奖金，实发奖金为税后奖金。</p>
<p>六、报名</p>
<table><tr><td>身份类别</td><td>全程分龄组</td><td>半程分龄组</td></tr><tr><td>非会员</td><td>550</td><td>450</td></tr></table>
"""

CHUNKED_WITH_POINTS = """
<p>七、名次录取及奖励</p><p>2.比赛奖金分配方案</p>
<p>公开组男子、女子单人三项赛（单位：22300×2=44600元）</p>
<table><tr><td>第1名</td><td>第2名</td><td>第3名</td></tr><tr><td>7000</td><td>5000</td><td>4000</td></tr>
<tr><td>第4名</td><td>第5名</td></tr><tr><td>2000</td><td>1000</td></tr></table>
<span>挑战组两人三项团队赛（单位：35050元）</span><br/>
<table><tr><td>第1名</td><td>第2名</td><td>第3名</td></tr><tr><td>10000</td><td>8000</td><td>6000</td></tr></table>
<p>（二）全年总积分奖励</p><p>1.积分赛事包括系列赛各站比赛。</p>
<p><table><tr><td>第1名</td><td>第2名</td><td>第3名</td></tr><tr><td>20</td><td>18</td><td>16</td></tr></table></p>
<p>附件：赛事日程</p>
<table><tr><td>日期</td><td>时间</td><td>内容</td></tr><tr><td>6月13日</td><td>9:00</td><td>报到</td></tr></table>
"""

COLUMN_LADDER = """
<p>六、奖励规则</p><p>（一）精英组前八名获得奖金及证书，奖金如下：</p>
<table><tr><td>桨板耐力赛(元)</td></tr>
<tr><td>名次</td><td>男子组</td><td>女子组</td><td>税后(元)</td></tr>
<tr><td>第一名</td><td>800</td><td>800</td><td>640</td></tr>
<tr><td>第二名</td><td>700</td><td>700</td><td>560</td></tr>
<tr><td>第三名</td><td>600</td><td>600</td><td>480</td></tr></table>
<p>五、奖励办法</p><p>本次赛事按团队总用时排名，录取前 10 组选手给予奖励。具体奖励如下:</p>
<table><tr><td>序号</td><td>名次</td><td>奖金金额</td><td>其它奖励</td></tr>
<tr><td>1</td><td>冠军(1组)</td><td>10000</td><td>奖杯1座</td></tr>
<tr><td>2</td><td>亚军(1组)</td><td>8000</td><td>奖杯1座</td></tr>
<tr><td>3</td><td>第3名(1组)</td><td>6000</td><td>证书</td></tr>
<tr><td>4</td><td>第4-6名(3组)</td><td>1000/组</td><td>证书</td></tr></table>
"""

PROSE_ONLY = "<p>八、奖项设置</p><p>男女分组录取前三名，冠军奖金5000元，亚军3000元，季军1,500元，颁发奖杯。</p>"
IMAGES_ONLY = '<p><img src="a.jpg"/></p><p><img src="b.jpg"/></p>'

DISTANCE_TABLES = """
<table><tr><td>项目</td><td>总距离</td><td>游泳</td><td>自行车</td><td>跑步</td></tr>
<tr><td>全程距离</td><td>51.5公里</td><td>1.5公里</td><td>40公里</td><td>10公里</td></tr>
<tr><td>骑跑两项</td><td>30公里</td><td>5公里(跑步)</td><td>20公里</td><td>5公里</td></tr>
<tr><td>迷你距离</td><td>4.1公里</td><td>100米</td><td>3.2公里</td><td>0.8公里</td></tr></table>
<table><tr><td>身份类别</td><td>全程分龄组</td><td>骑跑两项组</td></tr>
<tr><td>非中铁协注册个人会员</td><td>550</td><td>200</td></tr><tr><td>中铁协注册个人会员</td><td>450</td><td>150</td></tr></table>
"""

ZUICOOL_LIST = """
<div class="event-body"><h4 class="name"><a href="https://zuicool.com/event/28013">2026启东TRITON全球铁人三项赛</a></h4>
<div class="info"><p>2026.10.31 &middot; 江苏 南通市 启东市 启东圆陀角旅游度假区</p></div></div>
<div class="event-body"><h4 class="name"><a href="https://zuicool.com/event/45957">2026广东省青少年铁人三项赛第一站</a></h4>
<div class="info"><p>2026.06.21 &middot; 广东 广州市 黄埔区 九龙湖</p></div></div>
"""

ZUICOOL_EVENT = """
<html><head><title>2026年“铁人中国”T100黄山公开赛</title>
<meta name="description" content="设T100全程分龄组、T100半程分龄组等组别"></head><body>
<nav class="event-tab2"><span class="pkg">已截止</span></nav>
<div class="section event-pkgs"><div class="pkgs-list">
<div class="pkg2"><a href="#"><h4 class="name">中铁协注册个人会员-T100全程分龄组</h4>
<small class="highlight_short">总距离107公里：游泳2公里+自行车87公里+跑步18公里</small></a>
<div class="price"> 800.00 </div><a class="btn reg-btn">已截止</a></div>
<div class="pkg2"><a href="#"><h4 class="name">铁三角接力组</h4></a><div class="price">500.00</div><a class="reg-btn">报名</a></div>
</div></div>
<a href="https://zuicool.com/news/archives/312660">报名 | 2026年“铁人中国”T100黄山公开赛</a>
</body></html>
"""

CHINATRI_LIST = """
<a href="https://chinatriathlon.com/events/28013" class="group block"><img src="x.png"/>
<h3>2026启东TRITON全球铁人三项赛</h3><span> 2026年10月31日 </span><span> 江苏・南通市启东市・圆陀角 </span></a>
<a href="https://chinatriathlon.com/events/99">相关赛事</a>
"""

CHINATRI_EVENT = """
<html><head><title>2026启东TRITON全球铁人三项赛 | 中国铁人三项</title></head><body>
<div>比赛日期</div><div>2026年10月31日</div><div>-</div><div>2026年11月01日</div>
<div>比赛地点</div><div>江苏・南通市启东市・启东圆陀角旅游度假区</div>
<div>赛事类型</div><div>铁人三项·多项赛</div><div>国家/地区</div><div>中国大陆</div>
<div>赛事简介</div><div>设长距离组。</div>
<a href="https://zuicool.com/event/28013?utm_source=chinatriathlon.com">赛事详情</a>
</body></html>
"""

WILDTO_SCREEN = json.dumps({"result": 1, "data": [{
    "wEventId": 11143, "wName": "2026启东TRITON1全球铁人三项赛", "wCountry": "CN", "wAddress": "启东恒大威尼斯",
    "wLng": 120.9, "wLat": 31.98, "wContentUrl": "event/content/abc.html", "wApplyBeginTime": "2026-05-28T00:00:00+08:00",
    "wApplyEndTime": "2026-09-26T23:55:00+08:00", "wEventBeginTime": "2026-10-30T09:00:00+08:00",
    "wEventEndTime": "2026-11-01T14:00:00+08:00", "sortName": "A级", "status": None, "provinceName": "江苏",
    "cityName": "南通", "regionName": None,
}]}, ensure_ascii=False)


# --- cases -------------------------------------------------------------------

def case_text_helpers(failures: list[str]) -> None:
    check([rank_of(x) for x in ("1", "第1名", "第十二名", "冠军", "季军", "组别", "1000")] == [1, 1, 12, 1, 3, None, None],
          "rank_of reads digits, Chinese ordinals and podium words", failures)
    check(rank_span_of("第6-10名(5组)") == (6, 10) and rank_span_of("冠军(1组)") == (1, 1) and rank_span_of("序号") is None,
          "rank_span_of reads ranges and strips group notes", failures)
    check([amount_of(x) for x in ("1,600", "1600元", "1.2万", "5000元+奖杯", "1000/组", "奖杯", "20%", "3名")]
          == [1600, 1600, 12000, 5000, 1000, None, None, None], "amount_of keeps cash, drops trophies and percentages", failures)
    check(parse_date("2026.10.31") == parse_date("2026年10月31日") == "2026-10-31", "dates from both sites agree", failures)
    check(normalize_name("2026年“铁人中国”T100黄山公开赛暨黄山国际铁人三项赛") == "铁人中国t100黄山公开赛",
          "name normalisation drops year, quotes and the 暨 co-title", failures)


def case_row_ladder(failures: list[str]) -> None:
    result = prizes.extract(ROW_LADDER)
    ladders = ladder_map(result)
    check(ladders.get("全程分龄组") == {1: 1600, 2: 1200, 3: 800, 4: 600}, f"row ladder read: {ladders}", failures)
    full = next((ladder for ladder in result.ladders if ladder.label == "全程分龄组"), None)
    check(full is not None and full.group_count == 12, "（共12组） split over spans gives group_count 12", failures)
    check(ladders.get("女子精英组") == {1: 3000, 2: 2000, 3: 1000}, "empty cells leave ranks out", failures)
    check(not any(label.startswith("税后") for label in ladders), "after-tax row is not its own ladder", failures)
    check(result.pre_tax == 1, "税前 detected", failures)
    check("奖励办法" in result.rules_text and "六、报名" not in result.rules_text, "rules section stops at the next heading", failures)
    check(len(result.ladders) == 3, f"fee table must not become a prize ladder: {list(ladders)}", failures)


def case_chunked_and_points(failures: list[str]) -> None:
    result = prizes.extract(CHUNKED_WITH_POINTS)
    ladders = ladder_map(result)
    check(ladders.get("公开组男子、女子单人三项赛") == {1: 7000, 2: 5000, 3: 4000, 4: 2000, 5: 1000},
          f"alternating rank/amount rows join into one ladder: {ladders}", failures)
    check(ladders.get("挑战组两人三项团队赛") == {1: 10000, 2: 8000, 3: 6000},
          "a bare <span> between tables captions the second table", failures)
    check(len(result.ladders) == 2, f"points table and schedule are skipped: {list(ladders)}", failures)
    check(result.rules_text.startswith("七、名次录取及奖励"), "heading with the prize word at the end is found", failures)


def case_column_ladder(failures: list[str]) -> None:
    result = prizes.extract(COLUMN_LADDER)
    ladders = ladder_map(result)
    check(ladders.get("桨板耐力赛 男子组") == {1: 800, 2: 700, 3: 600}, f"in-table caption prefixes columns: {ladders}", failures)
    check(not any("税后" in label for label in ladders), "after-tax column skipped", failures)
    sup = next((ladder for ladder in result.ladders if ladder.label == "桨板耐力赛 女子组"), None)
    check(sup is not None and sup.pre_tax == 1 and sup.gender == "F", "a 税后 column marks the others pre-tax", failures)
    team = next((ranks for label, ranks in ladders.items() if ranks.get(1) == 10000), None)
    check(team == {1: 10000, 2: 8000, 3: 6000, 4: 1000, 5: 1000, 6: 1000},
          f"名次 column beats 序号, and 第4-6名 fills 4..6: {team}", failures)
    check(all(label != "本次赛事按团队总用时排名，录取前 10 组选手给予奖励。具体奖励如下:" for label in ladders),
          "a sentence is never used as a label", failures)


def case_prose_and_images(failures: list[str]) -> None:
    result = prizes.extract(PROSE_ONLY)
    check(ladder_map(result) == {"未标明组别": {1: 5000, 2: 3000, 3: 1500}}, "prose fallback reads 冠军/亚军/季军", failures)
    check(result.ladders and result.ladders[0].confidence < 0.5, "prose ladders get low confidence", failures)
    images = prizes.extract(IMAGES_ONLY)
    check(images.rules_format == "images" and not images.ladders, "image-only rules are flagged, not guessed", failures)


def case_categories(failures: list[str]) -> None:
    distances = cat.parse_distances("游泳2.1公里+自行车88公里+跑步20公里")
    check(distances == {"swim_km": 2.1, "bike_km": 88.0, "run_km": 20.0}, f"distances: {distances}", failures)
    check(cat.parse_distances("游泳300米+跑步2公里")["swim_km"] == 0.3, "metres become km", failures)
    check(cat.classify_format("T100全程分龄组", 2, 87, 18) == "middle", "87 km bike is middle distance", failures)
    check(cat.classify_format("全程分龄组") is None, "CTSA 全程 alone stays unclassified", failures)
    check(cat.classify_sport("2026长岛铁人三项赛(半程)", "骑跑两项组") == "triathlon", "name decides the sport", failures)
    check(cat.classify_sport("“骑跑中国”2026骑跑两项全民系列赛") == "duathlon", "duathlon by name", failures)
    check(cat.classify_audience("2026北京平谷金海湖三夫小铁人三项赛") == "youth", "kids race is youth", failures)
    from tri_coach.race_planner.textutil import soup_of, table_rows
    tables = [table_rows(table) for table in soup_of(DISTANCE_TABLES).find_all("table")]
    found = {item["name"]: item for item in cat.categories_from_tables(tables)}
    duathlon = found.get("骑跑两项", {})
    check(duathlon.get("swim_km") is None and duathlon.get("bike_km") == 20.0, f"'5公里(跑步)' is not a swim: {duathlon}", failures)
    check(found.get("全程距离", {}).get("fee_cny") == 550.0, "non-member fee joins the category", failures)
    check(found.get("迷你距离", {}).get("swim_km") == 0.1, "100米 swim read in a distance table", failures)


def case_source_parsers(failures: list[str]) -> None:
    listed = zuicool.parse_list(ZUICOOL_LIST)
    check([item["id"] for item in listed] == ["28013", "45957"] and listed[0]["start_date"] == "2026-10-31",
          f"zuicool list: {listed}", failures)
    check(zuicool.split_location(listed[0]["location"])["province"] == "江苏", "zuicool location split", failures)
    event = zuicool.parse_event(ZUICOOL_EVENT)
    first = event["categories"][0] if event["categories"] else {}
    check(first.get("fee_cny") == 800.0 and first.get("bike_km") == 87.0 and first.get("reg_status") == "已截止",
          f"zuicool category: {first}", failures)
    check(event["news"] and event["news"][0]["url"].endswith("/312660"), "zuicool rules post link found", failures)
    cards = chinatriathlon.parse_list(CHINATRI_LIST)
    check(len(cards) == 1 and cards[0]["start_date"] == "2026-10-31", f"chinatriathlon cards (related links skipped): {cards}", failures)
    detail = chinatriathlon.parse_event(CHINATRI_EVENT)
    check(detail["end_date"] == "2026-11-01" and detail["zuicool_id"] == "28013" and detail["province"] == "江苏",
          f"chinatriathlon detail: {detail}", failures)
    items = wildto.parse_screen(WILDTO_SCREEN)
    check(items[0]["start_date"] == "2026-10-30" and items[0]["reg_close"] == "2026-09-26" and items[0]["level"] == "A级",
          f"wildto screen item: {items}", failures)


def case_matching_and_reviews(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = store.connect(Path(tmp) / "t.sqlite")
        qidong, how = ingest.attach_race(conn, {"name": "2026启东TRITON全球铁人三项赛", "start_date": "2026-10-31",
                                                 "audience": "adult"}, source="zuicool", source_id="28013")
        check(how == "new", "first sighting creates a race", failures)
        again, how = ingest.attach_race(conn, wildto.race_fields(wildto.parse_screen(WILDTO_SCREEN)[0]),
                                        source="wildto", source_id="11143")
        check(again == qidong and how.startswith("matched"), f"TRITON1 on wildto matches TRITON on zuicool ({how})", failures)
        linked, how = ingest.attach_race(conn, {"name": "完全不同的名字", "start_date": "2026-10-31"},
                                         source="chinatriathlon", source_id="28013", linked=[("zuicool", "28013")])
        check(linked == qidong and how == "linked:zuicool", "an explicit zuicool link wins over the name", failures)
        kids, _ = ingest.attach_race(conn, {"name": "2026启东TRITON全球小铁人三项赛", "start_date": "2026-10-31",
                                            "audience": "youth"}, source="zuicool", source_id="1")
        check(kids != qidong, "a youth race never merges into the adult race", failures)
        row = conn.execute("SELECT reg_close, province FROM race WHERE id = ?", (qidong,)).fetchone()
        check(row["reg_close"] == "2026-09-26" and row["province"] == "江苏", "second source fills empty fields", failures)

        ladders = [ladder.as_dict() for ladder in prizes.extract(ROW_LADDER).ladders]
        store.replace_extracted_prizes(conn, qidong, "wildto", ladders, snapshot_id=None, pre_tax=1)
        full_id = conn.execute("SELECT id FROM prize_table WHERE label = '全程分龄组'").fetchone()["id"]
        store.set_prize_status(conn, full_id, "verified", "checked against the PDF")
        changed = [dict(item, ranks={1: 1}) if item["label"] == "全程分龄组" else item for item in ladders]
        counts = store.replace_extracted_prizes(conn, qidong, "wildto", changed, snapshot_id=None, pre_tax=1)
        check(counts["kept_reviewed"] == 1, "re-extraction reports the kept review", failures)
        check(store.prize_ladder(conn, full_id).get(1) == 1600, "a verified ladder is not overwritten", failures)
        check(conn.execute("SELECT count(*) FROM prize_table").fetchone()[0] == 3, "unreviewed ladders are replaced, not duplicated", failures)


def case_seed(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = store.connect(Path(tmp) / "t.sqlite")
        crawled, _ = ingest.attach_race(conn, {"name": "2026年大连大黑石铁人三项公开赛", "start_date": "2026-07-12"},
                                        source="wildto", source_id="11099")
        report = seed.load(conn, ROOT / "examples" / "race_planner" / "seed_example.json")
        by_key = {item["key"]: item for item in report["races"]}
        check(by_key["dalian-dahei-2026"]["race_id"] == crawled, "seed joins the crawled race through its link", failures)
        result = conn.execute("SELECT finish_s, rank_division FROM result WHERE race_id = ?", (crawled,)).fetchone()
        check(result is not None and result["finish_s"] == 2 * 3600 + 41 * 60 + 10, "seed result times parsed", failures)
        manual = conn.execute("SELECT status FROM prize_table WHERE source = 'manual'").fetchone()
        check(manual is not None and manual["status"] == "manual", "seeded prizes are stored as manual", failures)
        seed.load(conn, ROOT / "examples" / "race_planner" / "seed_example.json")
        check(conn.execute("SELECT count(*) FROM race").fetchone()[0] == 2, "seeding twice is idempotent", failures)
        orphan = store.insert_race(conn, {"name": "dup", "start_date": "2026-07-12"})
        store.link_source(conn, race_id=orphan, source="zuicool", source_id="999")
        store.merge_races(conn, crawled, orphan)
        check(store.race_by_source(conn, "zuicool", "999") == crawled, "merge_races moves sources", failures)


class FakeResponse:
    def __init__(self, status: int, text: str, content_type: str = "text/html; charset=utf-8") -> None:
        self.status_code = status
        self.text = text
        self.content = text.encode("utf-8")
        self.headers = {"Content-Type": content_type}


class FakeSession:
    def __init__(self, robots: str | None) -> None:
        self.robots = robots
        self.calls: list[str] = []

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(url)
        if url.endswith("/robots.txt"):
            if self.robots is None:
                return FakeResponse(404, "not found")
            return FakeResponse(200, self.robots, "text/plain")
        return FakeResponse(200, f"<p>{url}</p>")

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(url)
        return FakeResponse(200, '{"data": []}', "application/json")


def case_fetcher(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        conn = store.connect(Path(tmp) / "t.sqlite")
        session = FakeSession("User-agent: *\nDisallow: /private/\n")
        fetcher = Fetcher(conn, Path(tmp), min_interval_s=0, max_age_s=3600, session=session)
        first = fetcher.get("https://example.org/a")
        second = fetcher.get("https://example.org/a")
        check(not first.from_cache and second.from_cache, "second fetch comes from the snapshot", failures)
        check((Path(tmp) / store.get_snapshot(conn, first.snapshot_id)["path"]).exists(), "snapshot file written", failures)
        try:
            fetcher.get("https://example.org/private/x")
            failures.append("robots.txt Disallow must block the fetch")
        except FetchError:
            pass
        page_calls = [call for call in session.calls if not call.endswith("robots.txt")]
        check(page_calls == ["https://example.org/a"], f"only one network fetch of the page: {session.calls}", failures)
        fetcher.post("https://example.org/screen", {"b": "2", "a": "1"})
        cached = fetcher.post("https://example.org/screen", {"a": "1", "b": "2"})
        check(cached.from_cache, "POST bodies are cached regardless of key order", failures)
        offline = Fetcher(conn, Path(tmp), offline=True, session=FakeSession(None))
        check(offline.get("https://example.org/a").from_cache, "offline mode serves snapshots", failures)
        try:
            offline.get("https://example.org/never")
            failures.append("offline mode must not hit the network")
        except FetchError:
            pass


CASES: list[tuple[str, Callable[[list[str]], None]]] = [
    ("text_helpers", case_text_helpers),
    ("row_ladder", case_row_ladder),
    ("chunked_and_points", case_chunked_and_points),
    ("column_ladder", case_column_ladder),
    ("prose_and_images", case_prose_and_images),
    ("categories", case_categories),
    ("source_parsers", case_source_parsers),
    ("matching_and_reviews", case_matching_and_reviews),
    ("seed", case_seed),
    ("fetcher", case_fetcher),
]


def run_case(name: str, func: Callable[[list[str]], None]) -> dict[str, Any]:
    failures: list[str] = []
    try:
        func(failures)
    except Exception:  # noqa: BLE001 - report, don't crash the eval
        failures.append(traceback.format_exc())
    return {"case": name, "passed": not failures, "failures": failures}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def print_summary(report: dict[str, Any]) -> None:
    passed = sum(1 for item in report["results"] if item["passed"])
    total = len(report["results"])
    print(f"race_planner eval: {passed}/{total} cases passed")
    for item in report["results"]:
        mark = "PASS" if item["passed"] else "FAIL"
        print(f"  [{mark}] {item['case']}")
        for failure in item["failures"]:
            print(f"      - {failure}")


def main() -> int:
    args = parse_args()
    results = [run_case(*case) for case in CASES]
    report = {"results": results}
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
