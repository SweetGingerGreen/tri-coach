#!/usr/bin/env python3
"""
Offline regression checks for tri_coach/strava (no network, no real tokens).

These checks keep the post-workout log safe:
- the athlete's own description is never overwritten by default.
- token refresh persists the rotated refresh token with 0600 permissions.
- exported photos are upright and carry no EXIF (no GPS).
- the data card renders at feed size and counts time past the cap honestly.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import stat
import subprocess
import tempfile
import traceback
from pathlib import Path
from typing import Any, Callable

import numpy as np
from PIL import Image

from tri_coach.paths import ROOT
from tri_coach.strava import api, card, description, photos
from tri_coach.strava.__main__ import _activity_window


DEFAULT_REPORT = ROOT / "triathlon-knowledge" / "metadata" / "strava_log_eval_latest.json"
CST = dt.timezone(dt.timedelta(hours=8))


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> Any:
        return self.payload


class FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append(("POST", url, kwargs))
        return FakeResponse({"access_token": "new-access", "refresh_token": "rotated-refresh", "expires_at": 10_000})

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((method, url, kwargs))
        return FakeResponse({"id": 1, "description": (kwargs.get("json") or {}).get("description", "")})


def check(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def case_description_modes(failures: list[str]) -> None:
    plan = description.plan_description
    check(plan("", "新日志") == ("新日志", "filled"), "fill writes into an empty description", failures)
    check(plan("自己写的一句", "新日志") == (None, "has-own-description"), "fill must not touch the athlete's words", failures)
    check(plan("自己写的一句", "新日志", "append") == ("自己写的一句\n\n新日志", "appended"), "append keeps existing text", failures)
    check(plan("自己写的一句\n\n新日志", "新日志", "append") == (None, "already-present"), "append is idempotent", failures)
    check(plan("同样", " 同样 ", "replace") == (None, "unchanged"), "replace skips identical text", failures)
    for bad in (("", "  ", "fill"), ("", "x", "overwrite")):
        try:
            plan(*bad)
            failures.append(f"plan_description{bad} should raise")
        except ValueError:
            pass


def case_token_refresh(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = api.TokenStore(Path(tmp) / "nested" / "tokens.json")
        store.save({"client_id": "1", "client_secret": "s", "access_token": "old", "refresh_token": "r1", "expires_at": 1000})
        session = FakeSession()
        client = api.StravaClient(store, session=session, clock=lambda: 990.0)
        check(client.access_token() == "new-access", "token inside the refresh margin is refreshed", failures)
        saved = store.load()
        check(saved["refresh_token"] == "rotated-refresh", "rotated refresh token is persisted", failures)
        mode = stat.S_IMODE(store.path.stat().st_mode)
        check(mode == 0o600, f"token file mode is {oct(mode)}, want 0o600", failures)
        session.calls.clear()
        client.clock = lambda: 5000.0
        client.update_activity(42, description="日志")
        method, url, kwargs = session.calls[-1]
        check(not any(call[0] == "POST" for call in session.calls), "fresh token is not refreshed", failures)
        check(method == "PUT" and url.endswith("/activities/42"), "update is PUT /activities/{id}", failures)
        check(kwargs.get("json") == {"description": "日志"}, "update sends only the given fields", failures)
        check(kwargs["headers"]["Authorization"] == "Bearer new-access", "bearer token is sent", failures)


def case_authorize_url(failures: list[str]) -> None:
    url = api.build_authorize_url("123", "http://localhost:8723/exchange_token", "abc")
    check("scope=read%2Cactivity%3Aread_all%2Cactivity%3Awrite" in url, "requests read_all + write scopes", failures)
    check("state=abc" in url and "approval_prompt=force" in url, "state and forced prompt are set", failures)


def case_activity_window(failures: list[str]) -> None:
    start, end = _activity_window({"start_date": "2026-01-01T22:00:00Z", "utc_offset": 28800, "elapsed_time": 5000})
    check(start.isoformat() == "2026-01-02T06:00:00+08:00", f"start {start.isoformat()} in local offset", failures)
    check((end - start).total_seconds() == 5000, "end = start + elapsed", failures)


def _jpeg(path: Path, taken: str | None, offset: str | None = None, size: tuple[int, int] = (400, 300)) -> None:
    exif = Image.Exif()
    if taken:
        exif[0x0112] = 6
        sub = exif.get_ifd(0x8769)
        sub[0x9003] = taken
        if offset:
            sub[0x9011] = offset
        gps = exif.get_ifd(0x8825)
        gps[1], gps[2], gps[3], gps[4] = "N", (48.0, 51.0, 0.0), "E", (2.0, 21.0, 0.0)
    Image.new("RGB", size, (200, 100, 50)).save(path, exif=exif)


def case_photos(failures: list[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        inbox = Path(tmp) / "inbox"
        inbox.mkdir()
        _jpeg(inbox / "during.jpg", "2026:01:02 06:30:00", "+08:00")
        _jpeg(inbox / "coffee_after.jpg", "2026:01:02 08:00:00")  # no offset -> read in the activity's zone
        _jpeg(inbox / "yesterday.jpg", "2026:01:01 06:30:00", "+08:00")
        _jpeg(inbox / "utc_camera.jpg", "2026:01:01 22:40:00", "+00:00")  # 06:40 CST
        Image.new("RGB", (50, 50)).save(inbox / "screenshot.png")
        if shutil.which("sips"):
            subprocess.run(["sips", "-s", "format", "heic", str(inbox / "during.jpg"), "--out", str(inbox / "phone.heic")],
                           check=True, capture_output=True)
        start = dt.datetime(2026, 1, 2, 6, 0, 0, tzinfo=CST)
        end = start + dt.timedelta(seconds=5000)
        matched, undated = photos.find_photos([inbox], start, end)
        names = [photo.path.name for photo in matched]
        expected = {"during.jpg", "utc_camera.jpg", "coffee_after.jpg"} | ({"phone.heic"} if shutil.which("sips") else set())
        check(set(names) == expected, f"matched {names}, want {sorted(expected)}", failures)
        check([path.name for path in undated] == ["screenshot.png"], "screenshot without EXIF is reported undated", failures)
        out = photos.export_photo(matched[0].path, Path(tmp) / "out" / "a_01.jpg")
        with Image.open(out) as image:
            check(image.size == (300, 400), f"export is rotated upright, got {image.size}", failures)
            exif = image.getexif()
            check(len(exif) == 0 and not exif.get_ifd(0x8825), "export carries no EXIF/GPS", failures)
        try:
            photos.find_photos([inbox], start.replace(tzinfo=None), end)
            failures.append("naive start must raise")
        except ValueError:
            pass


def _synthetic_run() -> dict[str, list[Any]]:
    t = list(range(0, 1800))
    hr = [135 + 20 * (i / 1800) + 3 * np.sin(i / 40) for i in t]
    velocity = [3.0 + 0.2 * np.sin(i / 200) if not 900 <= i < 960 else 0.0 for i in t]
    return {"time": t, "heart_rate": hr, "velocity_smooth": velocity}


def case_card(failures: list[str]) -> None:
    streams = _synthetic_run()
    series = card.series_from_streams(streams, [{"kind": "heartrate", "band": [130, 150]}, {"kind": "pace", "band": [300, 360]}])
    check([item["kind"] for item in series] == ["heartrate", "pace"], "MCP stream names map to panels", failures)
    pace = series[1]["y"]
    check(pace[0] is not None and abs(pace[0] - 1000 / 3.0) < 1e-6, "pace is seconds per km", failures)
    check(pace[930] is None, "stopped samples become gaps, not 0:00 pace", failures)

    t = np.arange(0, 600, dtype=float)
    y = np.where((t >= 100) & (t < 220), 160.0, 140.0)
    mask = card.flag_mask(y, [130, 150], "above")
    check(abs(card.flagged_seconds(t, mask) - 120) < 1e-6, "time above cap is counted from samples", failures)
    check(card.flagged_spans(t, mask) == [(100.0, 220.0)], "flagged span edges", failures)

    with tempfile.TemporaryDirectory() as tmp:
        spec = {
            "lang": "zh",
            "title": "示例 · 有氧跑",
            "subtitle": "2026-01-01 06:00 · 5.4 km · 30:00",
            "stats": [{"label": "平均心率", "value": "145"}, {"label": "配速", "value": "5:33"}],
            "series": series,
            "verdict": {"status": "serious", "text": "后段心率超上限"},
            "footer": "tri-coach",
        }
        out = card.render_card(spec, Path(tmp) / "card.png")
        with Image.open(out) as image:
            check(image.size == (card.WIDTH_PX, card.HEIGHT_PX), f"card size {image.size}", failures)
        stats_only = card.render_card({"title": "Swim", "stats": [{"label": "Distance", "value": "1,350 m"}],
                                       "lang": "en", "verdict": {"status": "good", "text": "Pace cap held"}},
                                      Path(tmp) / "swim.png")
        check(stats_only.exists(), "stats-only card renders without series", failures)


CASES: list[tuple[str, str, Callable[[list[str]], None]]] = [
    ("description-modes", "the athlete's own description is never overwritten by default", case_description_modes),
    ("token-refresh", "refresh, persist rotated token, PUT shape", case_token_refresh),
    ("authorize-url", "consent URL asks for the write scope", case_authorize_url),
    ("activity-window", "activity window uses the activity's UTC offset", case_activity_window),
    ("photos", "photo window, HEIC, undated files, clean export", case_photos),
    ("card", "streams -> panels, cap accounting, PNG size", case_card),
]


def run_case(case_id: str, name: str, func: Callable[[list[str]], None]) -> dict[str, Any]:
    failures: list[str] = []
    try:
        func(failures)
    except Exception:  # noqa: BLE001 - report, don't crash the eval
        failures.append(traceback.format_exc(limit=3))
    return {"case_id": case_id, "name": name, "passed": not failures, "failures": failures}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def print_summary(report: dict[str, Any]) -> None:
    passed = sum(1 for item in report["results"] if item["passed"])
    total = len(report["results"])
    print(f"Strava log eval: {passed}/{total} passed")
    for item in report["results"]:
        status = "PASS" if item["passed"] else "FAIL"
        print(f"- {status} {item['case_id']}: {item['name']}")
        for failure in item["failures"]:
            print(f"  - {failure}")


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
