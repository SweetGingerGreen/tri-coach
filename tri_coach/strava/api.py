"""
Strava API v3 client for a single athlete: one-time OAuth, token refresh, and
reading or updating that athlete's own activities.

One-time setup is done by the athlete, not by an agent:

1. Create an API application at https://www.strava.com/settings/api and set
   "Authorization Callback Domain" to ``localhost``.
2. Run ``python3 -m tri_coach.strava auth``. It asks for the client ID and
   secret (the secret is read without echo), opens Strava's consent page, and
   stores the tokens in ``~/.config/tri-coach/strava_tokens.json`` (mode 0600).

After that every command refreshes the six-hour access token on its own and
persists the rotated refresh token.
"""
from __future__ import annotations

import http.server
import json
import os
import secrets
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


API_BASE = "https://www.strava.com/api/v3"
AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
DEFAULT_TOKEN_FILE = Path.home() / ".config" / "tri-coach" / "strava_tokens.json"
REQUESTED_SCOPES = ("read", "activity:read_all", "activity:write")
REQUIRED_SCOPES = ("activity:read_all", "activity:write")
REDIRECT_PORT = 8723
REFRESH_MARGIN_S = 120
HTTP_TIMEOUT_S = 30


class StravaError(RuntimeError):
    pass


def token_file_path() -> Path:
    override = os.environ.get("STRAVA_TOKEN_FILE")
    return Path(override).expanduser() if override else DEFAULT_TOKEN_FILE


@dataclass
class TokenStore:
    path: Path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            raise StravaError(f"No Strava tokens at {self.path}. Run `python3 -m tri_coach.strava auth` first.")
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2)
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)


def _json_or_raise(response: requests.Response, what: str) -> Any:
    if response.status_code >= 400:
        hint = ""
        if response.status_code == 401:
            hint = " (token rejected or scope missing; re-run `python3 -m tri_coach.strava auth`)"
        raise StravaError(f"{what}: HTTP {response.status_code}{hint}: {response.text[:300]}")
    return response.json()


def retrying_session() -> requests.Session:
    """Session that retries dropped connections (seen as SSL EOF behind a local proxy) with backoff."""
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=(502, 503, 504),
        allowed_methods=frozenset({"GET", "PUT"}),
        raise_on_status=False,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


class StravaClient:
    def __init__(
        self,
        store: TokenStore,
        session: requests.Session | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.session = session or retrying_session()
        self.clock = clock

    def access_token(self) -> str:
        data = self.store.load()
        if float(data.get("expires_at", 0)) - REFRESH_MARGIN_S <= self.clock():
            data = self._refresh(data)
        return data["access_token"]

    def _refresh(self, data: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(
            TOKEN_URL,
            data={
                "client_id": data["client_id"],
                "client_secret": data["client_secret"],
                "grant_type": "refresh_token",
                "refresh_token": data["refresh_token"],
            },
            timeout=HTTP_TIMEOUT_S,
        )
        payload = _json_or_raise(response, "POST /oauth/token (refresh)")
        data = {
            **data,
            "access_token": payload["access_token"],
            "refresh_token": payload["refresh_token"],
            "expires_at": payload["expires_at"],
        }
        self.store.save(data)
        return data

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.session.request(
            method,
            API_BASE + path,
            headers={"Authorization": f"Bearer {self.access_token()}"},
            timeout=HTTP_TIMEOUT_S,
            **kwargs,
        )
        return _json_or_raise(response, f"{method} {path}")

    def get_activity(self, activity_id: str | int) -> dict[str, Any]:
        return self._request("GET", f"/activities/{activity_id}")

    def update_activity(self, activity_id: str | int, **fields: Any) -> dict[str, Any]:
        """PUT the given UpdatableActivity fields (name, description, ...)."""
        return self._request("PUT", f"/activities/{activity_id}", json=fields)

    def get_streams(self, activity_id: str | int, keys: list[str]) -> dict[str, list[Any]]:
        """Return {stream key: data list}, e.g. {"time": [...], "heartrate": [...]}."""
        payload = self._request(
            "GET",
            f"/activities/{activity_id}/streams",
            params={"keys": ",".join(keys), "key_by_type": "true"},
        )
        return {key: value.get("data", []) for key, value in payload.items() if isinstance(value, dict)}


def build_authorize_url(client_id: str, redirect_uri: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "response_type": "code",
            "redirect_uri": redirect_uri,
            "approval_prompt": "force",
            "scope": ",".join(REQUESTED_SCOPES),
            "state": state,
        }
    )
    return f"{AUTHORIZE_URL}?{query}"


def _wait_for_redirect(port: int, timeout_s: int) -> dict[str, str]:
    captured: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (stdlib hook name)
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/exchange_token":
                self.send_response(404)
                self.end_headers()
                return
            captured.update({key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()})
            body = "Strava connected. You can close this tab.".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 1
    deadline = time.monotonic() + timeout_s
    try:
        while not captured and time.monotonic() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if not captured:
        raise StravaError(f"Timed out after {timeout_s} s waiting for Strava to redirect back.")
    return captured


def authorize(
    client_id: str,
    client_secret: str,
    store: TokenStore,
    port: int = REDIRECT_PORT,
    open_browser: bool = True,
    timeout_s: int = 300,
) -> dict[str, Any]:
    state = secrets.token_urlsafe(16)
    url = build_authorize_url(client_id, f"http://localhost:{port}/exchange_token", state)
    print(f"Opening Strava's consent page. If no browser opens, visit:\n{url}\n")
    if open_browser:
        webbrowser.open(url)
    params = _wait_for_redirect(port, timeout_s)
    if params.get("state") != state:
        raise StravaError("OAuth state mismatch; start `auth` again.")
    if "error" in params:
        raise StravaError(f"Strava returned error={params['error']}; authorization was not granted.")
    granted = {scope for scope in params.get("scope", "").split(",") if scope}
    missing = [scope for scope in REQUIRED_SCOPES if scope not in granted]
    if missing:
        raise StravaError(
            f"Missing scope(s): {', '.join(missing)}. Run `auth` again and leave every permission box ticked."
        )
    response = requests.post(
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": params["code"],
            "grant_type": "authorization_code",
        },
        timeout=HTTP_TIMEOUT_S,
    )
    payload = _json_or_raise(response, "POST /oauth/token")
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": payload["expires_at"],
        "scope": sorted(granted),
        "athlete_id": (payload.get("athlete") or {}).get("id"),
    }
    store.save(data)
    return data
