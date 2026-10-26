"""
Polite HTTP for the race planner.

Every response is written to disk and recorded in the ``snapshot`` table, so a
parser can be rerun on stored pages without touching the network. Requests to
the same host are spaced out, robots.txt is honoured, and a cached copy younger
than ``max_age_s`` is reused instead of refetching.
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from tri_coach.race_planner import store

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 tri-coach-race-planner/0.1"
)
DEFAULT_MIN_INTERVAL_S = 2.0
DEFAULT_MAX_AGE_S = 20 * 3600
HTTP_TIMEOUT_S = 30
EXTENSIONS = {"text/html": ".html", "application/json": ".json", "application/xml": ".xml", "text/xml": ".xml"}


class FetchError(RuntimeError):
    pass


@dataclass
class Page:
    url: str
    status: int
    content_type: str | None
    body: bytes
    snapshot_id: int
    from_cache: bool

    @property
    def text(self) -> str:
        return decode(self.body, self.content_type)


def decode(body: bytes, content_type: str | None = None) -> str:
    charset = None
    if content_type and "charset=" in content_type.lower():
        charset = content_type.lower().split("charset=", 1)[1].split(";")[0].strip()
    for candidate in (charset, "utf-8", "gb18030"):
        if not candidate:
            continue
        try:
            return body.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def make_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3, connect=3, read=2, backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504), allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6"})
    return session


class Fetcher:
    def __init__(
        self, conn: sqlite3.Connection, data_dir: Path, *, min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        max_age_s: float | None = DEFAULT_MAX_AGE_S, offline: bool = False, session: requests.Session | None = None,
        log=None,
    ) -> None:
        self.conn = conn
        self.data_dir = data_dir
        self.min_interval_s = min_interval_s
        self.max_age_s = max_age_s
        self.offline = offline
        self.session = session or make_session()
        self.log = log or (lambda message: None)
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self.network_calls = 0

    # -- public ------------------------------------------------------------
    def get(self, url: str, *, max_age_s: float | None = -1.0) -> Page:
        return self._fetch("GET", url, body="", data=None, headers=None, max_age_s=max_age_s)

    def post(self, url: str, data: dict[str, str], *, headers: dict[str, str] | None = None,
             max_age_s: float | None = -1.0) -> Page:
        body = urllib.parse.urlencode(sorted(data.items()))
        return self._fetch("POST", url, body=body, data=data, headers=headers, max_age_s=max_age_s)

    def load(self, snapshot_id: int) -> Page:
        row = store.get_snapshot(self.conn, snapshot_id)
        if row is None:
            raise FetchError(f"no snapshot {snapshot_id}")
        return self._page_from_row(row)

    # -- internals ---------------------------------------------------------
    def _fetch(self, method: str, url: str, *, body: str, data, headers, max_age_s) -> Page:
        max_age = self.max_age_s if max_age_s == -1.0 else max_age_s
        cached = store.find_snapshot(self.conn, url, method, body)
        if cached is not None and (self.offline or self._fresh(cached["fetched_at"], max_age)):
            return self._page_from_row(cached)
        if self.offline:
            raise FetchError(f"offline and no snapshot for {method} {url}")
        if not self._allowed(url):
            raise FetchError(f"robots.txt disallows {url}")
        self._throttle(url)
        self.log(f"{method} {url}" + (f" {body}" if body else ""))
        try:
            if method == "GET":
                response = self.session.get(url, timeout=HTTP_TIMEOUT_S, headers=headers)
            else:
                response = self.session.post(url, data=data, timeout=HTTP_TIMEOUT_S, headers=headers)
        except requests.RequestException as exc:
            raise FetchError(f"{method} {url}: {exc}") from exc
        finally:
            self.network_calls += 1
            self._last_hit[self._host(url)] = time.monotonic()
        if response.status_code >= 400:
            raise FetchError(f"{method} {url}: HTTP {response.status_code}")
        content_type = response.headers.get("Content-Type")
        snapshot_id = self._save(url, method, body, response.status_code, content_type, response.content)
        self.conn.commit()
        return Page(url, response.status_code, content_type, response.content, snapshot_id, from_cache=False)

    def _save(self, url: str, method: str, body: str, status: int, content_type: str | None, content: bytes) -> int:
        digest = hashlib.sha256(content).hexdigest()
        ext = EXTENSIONS.get((content_type or "").split(";")[0].strip().lower(), ".bin")
        rel = Path("snapshots") / self._host(url) / digest[:2] / f"{digest}{ext}"
        path = self.data_dir / rel
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return store.add_snapshot(
            self.conn, url=url, method=method, body=body, status=status, content_type=content_type,
            sha256=digest, path=str(rel),
        )

    def _page_from_row(self, row: sqlite3.Row) -> Page:
        path = self.data_dir / row["path"]
        if not path.exists():
            raise FetchError(f"snapshot file missing: {path}")
        return Page(row["url"], int(row["status"]), row["content_type"], path.read_bytes(), int(row["id"]), from_cache=True)

    @staticmethod
    def _fresh(fetched_at: str, max_age_s: float | None) -> bool:
        if max_age_s is None:
            return True
        if max_age_s <= 0:
            return False
        try:
            fetched = store.dt.datetime.fromisoformat(fetched_at)
        except ValueError:
            return False
        age = (store.dt.datetime.now(fetched.tzinfo) - fetched).total_seconds()
        return age <= max_age_s

    @staticmethod
    def _host(url: str) -> str:
        return urllib.parse.urlsplit(url).netloc.lower()

    def _throttle(self, url: str) -> None:
        last = self._last_hit.get(self._host(url))
        if last is not None:
            wait = self.min_interval_s - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)

    def _allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            parser: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
            try:
                self._throttle(url)
                response = self.session.get(f"{origin}/robots.txt", timeout=HTTP_TIMEOUT_S, allow_redirects=True)
                self._last_hit[self._host(url)] = time.monotonic()
                is_text = "text/plain" in (response.headers.get("Content-Type") or "")
                if response.status_code >= 400 or not is_text:
                    parser = None  # no robots.txt: nothing is disallowed
                else:
                    parser.parse(response.text.splitlines())
            except requests.RequestException:
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        return True if parser is None else parser.can_fetch(USER_AGENT, url)
