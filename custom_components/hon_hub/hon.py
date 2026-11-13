"""Client for the hOn cloud (Haier / Candy / Hoover). Pure aiohttp, no Home Assistant imports.

Login is the CIAM flow of the official app (2.31.10): GET ciam/authorize with a PKCE challenge returns a
session_id, POST ciam/token exchanges it for the tokens. The cognito token lasts 15 minutes and is renewed
like the app does, with POST ciam/refresh {session_id}: the password is sent only at the first login and
when the session is no longer valid.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

# the two environment variables exist only to point the tests at a fake cloud
API_URL = os.environ.get("HON_HUB_API_URL", "https://api-iot.he.services").rstrip("/")
API_KEY = "GRCqFhC6Gk@ikWXm1RmnSmX1cm,MxY-configuration"   # public key of the app, used only by app-config
APP_VERSION = "2.27.9"
OS = "android"
OS_VERSION = 31
DEVICE_MODEL = "exynos9820"
USER_AGENT = ("Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/120.0 Mobile Safari/537.36")
TIMEOUT = 30
TOKEN_MARGIN = 60          # renew this many seconds before the cognito token expires
TOKEN_FALLBACK_TTL = 600   # used when the expiry cannot be read from the token
RENEW_MIN = 20             # after a 401/403 renew the tokens at most this often (seconds)
RELOGIN_MIN = 300          # ...and send the password again at most this often


class HonError(Exception):
    """Generic hOn error."""


class HonAuthError(HonError):
    """Wrong email or password."""


class HonConnectionError(HonError):
    """The hOn cloud cannot be reached (network problem, timeout)."""


class HonCommandError(HonError):
    """The cloud rejected a command."""


def _jwt_exp(token: str) -> float:
    try:
        part = token.split(".")[1]
        return float(json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))).get("exp") or 0)
    except (IndexError, ValueError, TypeError, AttributeError):
        return 0.0


def _loads(text: str) -> Any:
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


class HonClient:
    """One hOn account."""

    def __init__(self, email: str, password: str, session: aiohttp.ClientSession | None = None, *,
                 mobile_id: str | None = None, api_url: str | None = None) -> None:
        self.email = email
        self.password = password
        self.mobile_id = mobile_id or secrets.token_hex(8)
        self._api = (api_url or API_URL).rstrip("/")
        self._session = session
        self._own_session = session is None
        self._tokens: dict[str, str] = {}
        self._session_id = ""
        self._expires = 0.0
        self._forced_at = 0.0
        self._forced_full_at = 0.0
        self.auth_failed = False          # the cloud refused email and password: nothing more is sent
        self._lock = asyncio.Lock()

    # ── connection ───────────────────────────────────────────────────────────
    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._own_session = True
        return self._session

    async def close(self) -> None:
        if self._own_session and self._session and not self._session.closed:
            await self._session.close()

    @property
    def id_token(self) -> str:
        return self._tokens.get("id_token", "")

    async def _raw(self, method: str, path: str, *, params: dict | None = None, body: Any = None,
                   headers: dict | None = None, timeout: float = TIMEOUT) -> tuple[int, str]:
        url = path if path.startswith("http") else f"{self._api}{path}"
        try:
            async with self.session.request(
                    method, url, params=params, json=body, headers={"User-Agent": USER_AGENT, **(headers or {})},
                    timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                return resp.status, await resp.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HonConnectionError(f"hOn cloud unreachable: {type(err).__name__} {err}".strip()) from err

    # ── login ────────────────────────────────────────────────────────────────
    async def login(self, force: bool = False, full: bool = False, used: str | None = None) -> None:
        """Make sure the tokens are valid.

        force: renew them now (the cloud answered 401/403 to a request made with the token `used`);
        full: do not use the saved session, send email and password again.
        """
        async with self._lock:
            if self.auth_failed:
                # do not keep trying a refused password (every attempt is a failed login on the account):
                # a new client is created when the user enters the credentials again
                raise HonAuthError("Wrong hOn email or password")
            now = time.time()
            if not force and self._tokens and now < self._expires:
                return
            if force and self._tokens:
                if used is not None and used != self._tokens.get("cognito_token"):
                    return          # another request has already renewed the tokens
                # if the cloud keeps refusing, do not hammer the login service
                if now - (self._forced_full_at if full else self._forced_at) < (RELOGIN_MIN if full else RENEW_MIN):
                    return
                if full:
                    self._forced_full_at = now
                else:
                    self._forced_at = now
            if not full and self._session_id and await self._refresh():
                return
            await self._authorize()

    def _store(self, tokens: dict) -> None:
        self._tokens = {**self._tokens, **{k: v for k, v in tokens.items() if isinstance(v, str) and v}}
        exp = _jwt_exp(self._tokens.get("cognito_token", ""))
        now = time.time()
        self._expires = exp - TOKEN_MARGIN if exp > now + TOKEN_MARGIN else now + TOKEN_FALLBACK_TTL

    async def _authorize(self) -> None:
        verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        status, text = await self._raw("GET", "/ciam/authorize", params={
            "username": self.email, "password": self.password, "code_challenge": challenge})
        if status not in (200, 400, 401, 403):
            raise HonError(f"hOn login failed (HTTP {status})")
        session_id = (_loads(text) or {}).get("session_id") if status == 200 else None
        if not session_id:
            self._tokens, self._session_id, self._expires = {}, "", 0.0
            self.auth_failed = True
            raise HonAuthError("Wrong hOn email or password")
        status, text = await self._raw("POST", "/ciam/token",
                                       body={"session_id": session_id, "code_verifier": verifier})
        tokens = ((_loads(text) or {}).get("tokens")) or {}
        if not tokens.get("id_token") or not tokens.get("cognito_token"):
            raise HonError(f"hOn login failed: no tokens in the answer (HTTP {status})")
        self._session_id = session_id
        self._tokens = {}
        self._store(tokens)
        _LOGGER.debug("hOn login ok")

    async def _refresh(self) -> bool:
        status, text = await self._raw("POST", "/ciam/refresh", body={"session_id": self._session_id}, timeout=20)
        tokens = ((_loads(text) or {}).get("tokens")) or {} if status == 200 else {}
        if not tokens.get("cognito_token"):
            _LOGGER.debug("hOn session no longer valid (HTTP %s): logging in again", status)
            self._session_id = ""
            return False
        self._store(tokens)
        _LOGGER.debug("hOn tokens renewed")
        return True

    async def _request(self, method: str, path: str, *, params: dict | None = None, body: Any = None) -> Any:
        used = None
        for attempt in range(3):
            # 1st try: current tokens; 2nd: renewed tokens (what the app does on 403); 3rd: brand new login
            await self.login(force=attempt > 0, full=attempt > 1, used=used)
            used = self._tokens.get("cognito_token", "")
            status, text = await self._raw(method, path, params=params, body=body, headers={
                "Content-Type": "application/json",
                "cognito-token": self._tokens.get("cognito_token", ""),
                "id-token": self._tokens.get("id_token", "")})
            if status not in (401, 403):
                break
        if status >= 400:
            raise HonError(f"hOn {path.split('?')[0]}: HTTP {status} {text[:200]}")
        return _loads(text)

    # ── reading ──────────────────────────────────────────────────────────────
    async def appliances(self) -> list[dict]:
        data = await self._request("POST", "/unified-api/v1/view/appliance-list", body={"deviceId": self.mobile_id})
        try:
            items = data["modules"]["applianceList"]["payload"]["appliances"] or []
        except (KeyError, TypeError) as err:
            raise HonError(f"Cannot read the appliance list: {str(data)[:200]}") from err
        if not isinstance(items, list):
            raise HonError(f"Cannot read the appliance list: {str(items)[:200]}")
        return [a for a in items if isinstance(a, dict) and a.get("macAddress") and a.get("applianceTypeName")]

    async def commands(self, a: dict) -> dict:
        params = {"applianceType": a.get("applianceTypeName"), "code": a.get("code", ""),
                  "applianceModelId": a.get("applianceModelId", ""), "macAddress": a["macAddress"],
                  "os": OS, "appVersion": APP_VERSION}
        for src, dst in (("eepromId", "firmwareId"), ("fwVersion", "fwVersion"), ("series", "series")):
            if a.get(src):
                params[dst] = a[src]
        # the cloud sometimes has null where a text is expected: a null cannot go in a query string
        params = {k: v for k, v in params.items() if v is not None}
        data = await self._request("GET", "/commands/v1/retrieve", params=params)
        payload = (data or {}).get("payload") if isinstance(data, dict) else None
        if not isinstance(payload, dict) or payload.pop("resultCode", "0") != "0":
            return {}
        return payload

    async def context(self, a: dict) -> dict:
        data = await self._request("GET", "/commands/v1/context", params={
            "macAddress": a["macAddress"], "applianceType": a.get("applianceTypeName"), "category": "CYCLE"})
        return (data or {}).get("payload") or {}

    async def statistics(self, a: dict) -> dict:
        data = await self._request("GET", "/commands/v1/statistics", params={
            "macAddress": a["macAddress"], "applianceType": a.get("applianceTypeName")})
        return (data or {}).get("payload") or {}

    async def maintenance(self, a: dict) -> dict:
        data = await self._request("GET", "/commands/v1/maintenance-cycle", params={"macAddress": a["macAddress"]})
        return (data or {}).get("payload") or {}

    async def aws_token(self) -> str:
        """Signature for the AWS IoT custom authorizer (real-time channel)."""
        data = await self._request("GET", "/auth/v1/introspection")
        return ((data or {}).get("payload") or {}).get("tokenSigned", "")

    # ── maintenance ──────────────────────────────────────────────────────────
    async def reset_counter(self, a: dict, counter_type: str) -> Any:
        """Reset a maintenance counter like the confirm button of the official app."""
        data = await self._request("POST", "/commands/v1/resetCounter",
                                   body={"macAddress": a["macAddress"], "counterType": counter_type})
        return (data or {}).get("payload") or {}

    async def clear_filter_cleaning(self, a: dict) -> Any:
        """Filter of washing machines and washer dryers: the app uses the notification service."""
        data = await self._request("POST", f"/notification/v1/filter-cleaning/{a['macAddress']}")
        return (data or {}).get("payload") if isinstance(data, dict) else data

    async def set_tabs(self, a: dict, amount: int) -> Any:
        """Dishwasher tablets left in the pantry."""
        data = await self._request("POST", f"/utils/v1/tabs/{a['macAddress']}", body={"amount": amount})
        return (data or {}).get("payload") if isinstance(data, dict) else data

    # ── commands ─────────────────────────────────────────────────────────────
    async def send(self, a: dict, command: str, parameters: dict, ancillary: dict,
                   options: dict | None = None, program_name: str = "", display_name: str = "") -> dict:
        """Send a command. display_name: the name of the program as the user sees it (the app sends it too)."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        body = {
            "macAddress": a["macAddress"],
            "timestamp": now,
            "commandName": command,
            "transactionId": f"{a['macAddress']}_{now}",
            "applianceOptions": options or {},
            "device": {"mobileId": self.mobile_id, "mobileOs": OS, "osVersion": OS_VERSION,
                       "appVersion": APP_VERSION, "deviceModel": DEVICE_MODEL},
            "attributes": {"channel": "mobileApp", "origin": "standardProgram", "energyLabel": "0"},
            "ancillaryParameters": ancillary,
            "parameters": parameters,
            "applianceType": a.get("applianceTypeName"),
        }
        if command == "startProgram" and program_name:
            body["programName"] = program_name.upper()
        if command == "startProgram" and display_name:
            body["attributes"]["prStr"] = display_name
        _LOGGER.debug("sending %s %s", command, program_name)
        data = await self._request("POST", "/commands/v1/send", body=body)
        result = (data or {}).get("payload") or {}
        if result.get("resultCode") != "0":
            raise HonCommandError(f"Command rejected by hOn: {str(data)[:200]}")
        return result

    # ── translations ─────────────────────────────────────────────────────────
    async def translations(self, lang: str = "en") -> dict:
        """Texts of the hOn app in the given language (public endpoint, no login)."""
        status, text = await self._raw("POST", "/app-config", headers={
            "x-api-key": API_KEY, "Content-Type": "application/json"}, body={
            "languageCode": lang, "beta": True, "appVersion": APP_VERSION, "os": OS}, timeout=60)
        url = ((((_loads(text) or {}).get("payload")) or {}).get("language") or {}).get("jsonPath")
        if status != 200 or not url:
            return {}
        status, text = await self._raw("GET", url, timeout=120)
        if status != 200:
            return {}
        # several megabytes of json: parse them outside the event loop
        data = await asyncio.get_running_loop().run_in_executor(None, _loads, text)
        return data if isinstance(data, dict) else {}
