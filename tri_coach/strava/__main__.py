#!/usr/bin/env python3
"""
Post-workout Strava log CLI.

  auth                       one-time OAuth (run it yourself; asks for the client secret)
  show ID                    name, description and photo count of an activity
  describe ID --text ...     write the public description (dry run unless --yes)
  card SPEC -o OUT.png       render the data card; --activity ID fills panels from the API
  photos --activity ID       find photos taken during the activity; --out exports clean JPEGs

Photos and Private Notes cannot go through the public API; upload those on the
activity's edit page on strava.com.
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import sys
from pathlib import Path
from typing import Any

from tri_coach.strava import api, card, description, photos


def _client() -> api.StravaClient:
    return api.StravaClient(api.TokenStore(api.token_file_path()))


def _print(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_auth(args: argparse.Namespace) -> int:
    client_id = os.environ.get("STRAVA_CLIENT_ID") or input("Strava client ID: ").strip()
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET") or getpass.getpass("Strava client secret (hidden): ").strip()
    store = api.TokenStore(api.token_file_path())
    data = api.authorize(client_id, client_secret, store, port=args.port, open_browser=not args.no_browser)
    print(f"Connected athlete {data.get('athlete_id')} with scopes {', '.join(data['scope'])}. Tokens: {store.path}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    activity = _client().get_activity(args.activity_id)
    _print(
        {
            "id": activity.get("id"),
            "name": activity.get("name"),
            "sport_type": activity.get("sport_type"),
            "start_date_local": activity.get("start_date_local"),
            "description": activity.get("description") or "",
            "photo_count": (activity.get("photos") or {}).get("count", 0),
        }
    )
    return 0


def cmd_describe(args: argparse.Namespace) -> int:
    text = args.text if args.text is not None else Path(args.file).read_text(encoding="utf-8")
    client = _client()
    activity = client.get_activity(args.activity_id)
    current = activity.get("description") or ""
    final, reason = description.plan_description(current, text, args.mode)
    result: dict[str, Any] = {
        "activity_id": args.activity_id,
        "name": activity.get("name"),
        "mode": args.mode,
        "reason": reason,
        "current": current,
        "proposed": final,
        "written": False,
    }
    if final is None or not args.yes:
        _print(result)
        return 0
    client.update_activity(args.activity_id, description=final)
    readback = client.get_activity(args.activity_id).get("description") or ""
    result["written"] = description.normalize(readback) == description.normalize(final)
    result["readback"] = readback
    _print(result)
    if not result["written"]:
        print("Read-back does not match what was sent.", file=sys.stderr)
        return 2
    if args.ledger:
        ledger = Path(args.ledger)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "logged_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "activity_id": str(args.activity_id),
            "field": "description",
            "mode": args.mode,
            "text": final,
        }
        with ledger.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return 0


def cmd_card(args: argparse.Namespace) -> int:
    spec = card.load_spec(Path(args.spec))
    if args.activity:
        panels = spec.get("panels") or [{"kind": "heartrate"}]
        keys = ["time", "heartrate", "watts", "velocity_smooth"]
        streams = _client().get_streams(args.activity, keys)
        spec["series"] = card.series_from_streams(streams, panels, spec.get("lang", "zh"))
    out = card.render_card(spec, Path(args.out))
    print(out)
    return 0


def _activity_window(activity: dict[str, Any]) -> tuple[dt.datetime, dt.datetime]:
    start_utc = dt.datetime.fromisoformat(activity["start_date"].replace("Z", "+00:00"))
    offset = dt.timezone(dt.timedelta(seconds=int(activity.get("utc_offset", 0))))
    start = start_utc.astimezone(offset)
    return start, start + dt.timedelta(seconds=int(activity.get("elapsed_time", 0)))


def cmd_photos(args: argparse.Namespace) -> int:
    if args.activity:
        start, end = _activity_window(_client().get_activity(args.activity))
    elif args.start and args.end:
        start, end = dt.datetime.fromisoformat(args.start), dt.datetime.fromisoformat(args.end)
    else:
        print("Pass --activity, or both --start and --end with a UTC offset.", file=sys.stderr)
        return 1
    dirs = [Path(path).expanduser() for path in (args.dir or ["~/Downloads"])]
    matched, undated = photos.find_photos(
        dirs, start, end, dt.timedelta(minutes=args.before_min), dt.timedelta(minutes=args.after_min)
    )
    exported: list[str] = []
    if args.out:
        prefix = args.prefix or (args.activity or start.strftime("%Y%m%d_%H%M"))
        for index, photo in enumerate(matched, start=1):
            out = photos.export_photo(photo.path, Path(args.out) / f"{prefix}_{index:02d}.jpg")
            exported.append(str(out))
    _print(
        {
            "window": [start.isoformat(), end.isoformat()],
            "matched": [{"path": str(photo.path), "taken_at": photo.taken_at.isoformat()} for photo in matched],
            "exported": exported,
            "undated": [str(path) for path in undated],
        }
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python3 -m tri_coach.strava", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="one-time OAuth setup")
    auth.add_argument("--port", type=int, default=api.REDIRECT_PORT)
    auth.add_argument("--no-browser", action="store_true")
    auth.set_defaults(func=cmd_auth)

    show = sub.add_parser("show", help="print an activity's name and description")
    show.add_argument("activity_id")
    show.set_defaults(func=cmd_show)

    describe = sub.add_parser("describe", help="write the public description")
    describe.add_argument("activity_id")
    source = describe.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file")
    describe.add_argument("--mode", choices=description.MODES, default="fill",
                          help="fill: only if empty (default); append: keep existing text; replace: overwrite")
    describe.add_argument("--yes", action="store_true", help="actually write; without it this is a dry run")
    describe.add_argument("--ledger", help="append a JSON line per write to this file")
    describe.set_defaults(func=cmd_describe)

    card_cmd = sub.add_parser("card", help="render the data card PNG")
    card_cmd.add_argument("spec")
    card_cmd.add_argument("-o", "--out", required=True)
    card_cmd.add_argument("--activity", help="fill series from this activity's streams (spec 'panels' picks them)")
    card_cmd.set_defaults(func=cmd_card)

    photos_cmd = sub.add_parser("photos", help="find photos taken during an activity")
    photos_cmd.add_argument("--activity")
    photos_cmd.add_argument("--start", help="ISO time with offset, e.g. 2026-01-02T06:00:00+08:00")
    photos_cmd.add_argument("--end")
    photos_cmd.add_argument("--dir", action="append", help="folder to scan (repeatable; default ~/Downloads)")
    photos_cmd.add_argument("--before-min", type=int, default=30)
    photos_cmd.add_argument("--after-min", type=int, default=60)
    photos_cmd.add_argument("--out", help="export clean JPEG copies here")
    photos_cmd.add_argument("--prefix")
    photos_cmd.set_defaults(func=cmd_photos)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return args.func(args)
    except api.StravaError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
