"""A fake hOn cloud for the tests: same endpoints as the real one, data from tests/fixtures.

It also speaks the little MQTT that the real-time channel needs (AWS IoT side) and simulates what an
appliance does when it receives a command. Nothing here talks to the real cloud.

Standalone:  python tests/fake_cloud.py --port 8765 [--data DIR]
then start Home Assistant with HON_HUB_API_URL=http://host:8765 and HON_HUB_MQTT_URL=ws://host:8765/mqtt
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import json
import struct
import time
from pathlib import Path

from aiohttp import WSMsgType, web

FIXTURES = Path(__file__).parent / "fixtures"
EMAIL, PASSWORD = "casa@example.com", "segreta"
DEFAULT_SET = (("wd_209", "Lavasciuga"), ("dw_12658", ""), ("ih_654", "Piano cottura"))
I18N = {"PROGRAMS": {
    "WM_WD": {"ECO_40_60_NEW_ENERGY_LABEL": "Eco 40-60", "ECO_40_60_NEW_ENERGY_LABEL_DESCRIPTION": "Cotone normalmente sporco",
              "CARE_30": "Care 30'", "IOT_CHECKUP": "Check-Up"},
    "DW": {"ECO": "Eco", "HYGIENE": "Igienizzante", "INTENSIVE": "Intensivo", "IOT_CHECKUP": "Check-Up"},
    "IH": {"IOT_STANDARD_BOILING": "Bollitura"}}}


def jwt(seconds: int, **claims) -> str:
    def b64(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    now = int(time.time())
    return f"{b64({'alg': 'none'})}.{b64({'iat': now, 'exp': now + seconds, **claims})}.x"


def topics(mac: str) -> list[str]:
    return [f"$aws/events/presence/disconnected/{mac}", f"$aws/events/presence/connected/{mac}",
            f"haier/things/{mac}/event/appliancestatus/update", f"haier/things/{mac}/event/discovery/update"]


class FakeCloud:
    def __init__(self, data: Path | None = None, token_seconds: int = 900, i18n: Path | None = None) -> None:
        self.token_seconds = token_seconds
        self.i18n_text = i18n.read_text() if i18n else json.dumps(I18N)
        self.password = PASSWORD
        self.appliances: dict[str, dict] = {}
        self.sent: list[dict] = []           # commands received
        self.calls: list[tuple[str, str]] = []   # (method, path) of every request
        self.logins = 0
        self.refreshes = 0
        self.session_ids: set[str] = set()
        self.cognito: set[str] = set()
        self.id_tokens: set[str] = set()
        self.sockets: list[tuple[web.WebSocketResponse, set[str]]] = []
        self.fail_refresh = False
        self.outage = False                  # every request of the app answers 503
        self.refuse_subscriptions = False
        self.base = ""
        self._load(data)

    def _load(self, data: Path | None) -> None:
        if data is not None:
            dirs = [(d, "") for d in sorted(data.iterdir()) if (d / "appliance_data.json").exists()]
        else:
            dirs = [(FIXTURES / name, nick) for name, nick in DEFAULT_SET]
        for n, (folder, nick) in enumerate(dirs, 1):
            def read(name, default):
                f = folder / f"{name}.json"
                return json.loads(f.read_text()) if f.exists() else default
            info = read("appliance_data", {})
            mac = info.get("macAddress") or ""
            if not mac or "x" in mac:
                mac = f"aa-bb-cc-00-00-{n:02d}"
            info = {**info, "macAddress": mac, "applianceModelId": info.get("applianceModelId", info.get("id")),
                    "topics": {"publish": [], "subscribe": topics(mac)}}
            if data is None:
                info["nickName"] = nick or None
            self.appliances[mac] = {"info": info, "commands": read("commands", {}), "context": read("attributes", {}),
                                    "maintenance": read("maintenance", {}), "statistics": read("statistics", {})}

    # ── helpers ──────────────────────────────────────────────────────────────
    def shadow(self, mac: str) -> dict:
        return self.appliances[mac]["context"].setdefault("shadow", {}).setdefault("parameters", {})

    def set_params(self, mac: str, params: dict, publish: bool = True) -> None:
        shadow = self.shadow(mac)
        for key, value in params.items():
            shadow[key] = {"parNewVal": str(value), "lastUpdate": "2026-01-01T00:00:00.0Z"}
        if publish:
            self.publish(f"haier/things/{mac}/event/appliancestatus/update", {
                "macAddress": mac, "parameters": [{"parName": k, "parNewVal": str(v)} for k, v in params.items()]})

    def set_presence(self, mac: str, connected: bool) -> None:
        self.appliances[mac]["context"]["lastConnEvent"] = {
            "macAddress": mac, "category": "CONNECTED" if connected else "DISCONNECTED"}
        kind = "connected" if connected else "disconnected"
        self.publish(f"$aws/events/presence/{kind}/{mac}", {"clientId": mac, "eventType": kind})

    def publish(self, topic: str, payload: dict) -> int:
        raw = json.dumps(payload).encode()
        t = topic.encode()
        body = struct.pack("!H", len(t)) + t + raw
        packet = bytes([0x30]) + _varint(len(body)) + body
        n = 0
        for ws, subscribed in list(self.sockets):
            if topic in subscribed and not ws.closed:
                asyncio.ensure_future(ws.send_bytes(packet))
                n += 1
        return n

    async def drop_sockets(self) -> None:
        for ws, _ in list(self.sockets):
            await ws.close()

    def _authorized(self, request: web.Request) -> bool:
        return (request.headers.get("cognito-token") in self.cognito
                and request.headers.get("id-token") in self.id_tokens)

    def _tokens(self) -> dict:
        cognito, id_token = jwt(self.token_seconds, kind="cognito"), jwt(8 * 3600, kind="id", n=time.time())
        self.cognito.add(cognito)
        self.id_tokens.add(id_token)
        return {"tokens": {"access_token": "a" * 20, "refresh_token": "r" * 20, "cognito_token": cognito,
                           "id_token": id_token}, "user": {}}

    def expire_tokens(self) -> None:
        self.cognito.clear()

    # ── http ─────────────────────────────────────────────────────────────────
    def app(self) -> web.Application:
        @web.middleware
        async def guard(request, handler):
            self.calls.append((request.method, request.path))
            if self.outage and not request.path.startswith(("/_test", "/mqtt")):
                return web.json_response({"message": "Service Unavailable"}, status=503)
            open_paths = ("/ciam/", "/app-config", "/_", "/mqtt")
            if not request.path.startswith(open_paths) and not self._authorized(request):
                return web.json_response({"message": "Forbidden"}, status=403)
            return await handler(request)

        app = web.Application(middlewares=[guard])
        r = app.router
        r.add_get("/ciam/authorize", self.authorize)
        r.add_post("/ciam/token", self.token)
        r.add_post("/ciam/refresh", self.refresh)
        r.add_get("/auth/v1/introspection", self.introspection)
        r.add_post("/unified-api/v1/view/appliance-list", self.appliance_list)
        r.add_get("/commands/v1/retrieve", self.retrieve)
        r.add_get("/commands/v1/context", self.context)
        r.add_get("/commands/v1/statistics", self.statistics)
        r.add_get("/commands/v1/maintenance-cycle", self.maintenance)
        r.add_post("/commands/v1/send", self.send)
        r.add_post("/commands/v1/resetCounter", self.reset_counter)
        r.add_post("/notification/v1/filter-cleaning/{mac}", self.filter_cleaning)
        r.add_post("/utils/v1/tabs/{mac}", self.tabs)
        r.add_post("/app-config", self.app_config)
        r.add_get("/_i18n/{lang}.json", self.i18n)
        r.add_get("/mqtt", self.mqtt)
        r.add_get("/_test/state", self.t_state)
        r.add_post("/_test/params/{mac}", self.t_params)
        r.add_post("/_test/presence/{mac}", self.t_presence)
        r.add_post("/_test/expire", self.t_expire)
        r.add_post("/_test/drop", self.t_drop)
        r.add_post("/_test/password", self.t_password)
        r.add_post("/_test/outage", self.t_outage)
        return app

    async def authorize(self, request):
        q = request.query
        if q.get("username") != EMAIL or q.get("password") != self.password or not q.get("code_challenge"):
            return web.json_response({"error": "invalid_grant"}, status=403)
        self.logins += 1
        sid = f"sid-{self.logins}-{time.time()}"
        self.session_ids.add(sid)
        return web.json_response({"session_id": sid})

    async def token(self, request):
        body = await request.json()
        if body.get("session_id") not in self.session_ids or not body.get("code_verifier"):
            return web.json_response({"error": "invalid"}, status=400)
        return web.json_response(self._tokens())

    async def refresh(self, request):
        body = await request.json()
        if self.fail_refresh or body.get("session_id") not in self.session_ids:
            return web.json_response({"error": "invalid_session"}, status=401)
        self.refreshes += 1
        return web.json_response(self._tokens())

    async def introspection(self, request):
        return web.json_response({"payload": {"tokenSigned": "firma+di/prova=="}})

    async def appliance_list(self, request):
        body = await request.json()
        assert body.get("deviceId")
        return web.json_response({"modules": {"applianceList": {"payload": {
            "appliances": [a["info"] for a in self.appliances.values()]}}}})

    def _mac(self, request) -> str:
        mac = request.query.get("macAddress") or request.match_info.get("mac") or ""
        if mac not in self.appliances:
            raise web.HTTPBadRequest(text="unknown appliance")
        return mac

    async def retrieve(self, request):
        return web.json_response({"payload": {**self.appliances[self._mac(request)]["commands"], "resultCode": "0"}})

    async def context(self, request):
        return web.json_response({"payload": self.appliances[self._mac(request)]["context"]})

    async def statistics(self, request):
        return web.json_response({"payload": self.appliances[self._mac(request)]["statistics"]})

    async def maintenance(self, request):
        mac = self._mac(request)
        if self.appliances[mac]["info"].get("applianceTypeName") in ("IH", "OV"):
            return web.json_response({"message": "Invalid applianceType"}, status=400)
        return web.json_response({"payload": self.appliances[mac]["maintenance"]})

    async def send(self, request):
        body = await request.json()
        mac = body.get("macAddress")
        if mac not in self.appliances:
            return web.json_response({"payload": {"resultCode": "1"}})
        self.sent.append(body)
        name, params = body.get("commandName"), body.get("parameters") or {}
        if name == "startProgram":
            delayed = str(params.get("delayTime", "0")) not in ("0", "")
            changes = {**params, "machMode": "4" if delayed else "2", "prPhase": "1", "remainingTimeMM": "95"}
            self.appliances[mac]["context"]["activity"] = {"category": "CYCLE", "ttl": int(time.time()) + 86400, "attributes": {
                "prCode": params.get("prCode"), "programName": body.get("programName", "")}}
        elif name == "pauseProgram":
            changes = {"machMode": "3"}
        elif name == "resumeProgram":
            changes = {"machMode": "2"}
        elif name == "stopProgram":
            changes = {"machMode": "1", "prPhase": "0", "remainingTimeMM": "0"}
        else:
            changes = dict(params)
        self.set_params(mac, changes)
        return web.json_response({"payload": {"resultCode": "0"}})

    def _reset(self, mac: str, counter: str) -> None:
        c = self.appliances[mac]["maintenance"].get(counter)
        if isinstance(c, dict):
            c.update(count=0, remaining=c.get("tot", 0), percentage=0)

    async def reset_counter(self, request):
        body = await request.json()
        self.sent.append({"commandName": "resetCounter", **body})
        if body.get("macAddress") in self.appliances:
            self._reset(body["macAddress"], body.get("counterType", ""))
        return web.json_response({"payload": [{"Attributes": {"cycles": 0}}]})

    async def filter_cleaning(self, request):
        mac = self._mac(request)
        self.sent.append({"commandName": "filterCleaning", "macAddress": mac})
        self._reset(mac, "filterCleaning")
        return web.json_response({"payload": {}})

    async def tabs(self, request):
        mac = self._mac(request)
        body = await request.json()
        self.sent.append({"commandName": "tabs", "macAddress": mac, **body})
        self.appliances[mac]["maintenance"]["tabs"] = body.get("amount")
        return web.json_response({"payload": {}})

    async def app_config(self, request):
        body = await request.json()
        return web.json_response({"payload": {"language": {"jsonPath": f"{self.base}/_i18n/{body.get('languageCode')}.json"}}})

    async def i18n(self, request):
        return web.Response(text=self.i18n_text, content_type="application/json")

    # ── test controls ────────────────────────────────────────────────────────
    async def t_state(self, request):
        return web.json_response({"sent": self.sent, "logins": self.logins, "refreshes": self.refreshes,
                                  "sockets": len([1 for ws, _ in self.sockets if not ws.closed]),
                                  "macs": {m: a["info"].get("applianceTypeName") for m, a in self.appliances.items()}})

    async def t_params(self, request):
        self.set_params(self._mac(request), await request.json())
        return web.json_response({"ok": True})

    async def t_presence(self, request):
        self.set_presence(self._mac(request), bool((await request.json()).get("connected")))
        return web.json_response({"ok": True})

    async def t_expire(self, request):
        self.expire_tokens()
        return web.json_response({"ok": True})

    async def t_drop(self, request):
        await self.drop_sockets()
        return web.json_response({"ok": True})

    async def t_outage(self, request):
        self.outage = bool((await request.json()).get("on"))
        return web.json_response({"ok": True})

    async def t_password(self, request):
        """The user changed the password in the app: every session and token dies."""
        self.password = (await request.json())["password"]
        self.session_ids.clear()
        self.cognito.clear()
        self.id_tokens.clear()
        await self.drop_sockets()
        return web.json_response({"ok": True})

    # ── mqtt over websocket (the AWS IoT side) ───────────────────────────────
    async def mqtt(self, request):
        h = request.headers
        if (h.get("X-Amz-CustomAuthorizer-Name") != "candy-iot-authorizer" or not h.get("X-Amz-CustomAuthorizer-Signature")
                or h.get("token") not in self.id_tokens):
            return web.Response(status=403)
        ws = web.WebSocketResponse(protocols=("mqtt",))
        await ws.prepare(request)
        subscribed: set[str] = set()
        self.sockets.append((ws, subscribed))
        buf = b""
        async for msg in ws:
            if msg.type != WSMsgType.BINARY:
                continue
            buf += msg.data
            while len(buf) >= 2:
                length, mult, i = 0, 1, 1
                while True:
                    byte = buf[i]
                    length += (byte & 127) * mult
                    mult *= 128
                    i += 1
                    if not byte & 128:
                        break
                if len(buf) < i + length:
                    break
                kind, body, buf = buf[0] >> 4, buf[i:i + length], buf[i + length:]
                if kind == 1:
                    assert body[:7] == b"\x00\x04MQTT\x04", "the client must speak MQTT 3.1.1"
                    await ws.send_bytes(b"\x20\x02\x00\x00")
                elif kind == 8:
                    pid, rest, codes = body[:2], body[2:], []
                    while rest:
                        n = struct.unpack("!H", rest[:2])[0]
                        subscribed.add(rest[2:2 + n].decode())
                        rest = rest[2 + n + 1:]
                        codes.append(0x80 if self.refuse_subscriptions else 0)
                    assert len(codes) <= 8, "AWS IoT accepts at most 8 topics per SUBSCRIBE"
                    await ws.send_bytes(b"\x90" + _varint(2 + len(codes)) + pid + bytes(codes))
                elif kind == 12:
                    await ws.send_bytes(b"\xd0\x00")
                elif kind == 14:
                    await ws.close()
        return ws


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        n, byte = divmod(n, 128)
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


async def start(cloud: FakeCloud, port: int = 0, host: str = "127.0.0.1") -> tuple[web.AppRunner, str]:
    runner = web.AppRunner(cloud.app())
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    port = runner.addresses[0][1]
    cloud.base = f"http://{host if host != '0.0.0.0' else '127.0.0.1'}:{port}"
    return runner, cloud.base


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--data", type=Path, default=None, help="folder with one sub-folder per appliance (same files as tests/fixtures)")
    ap.add_argument("--i18n", type=Path, default=None, help="translation file of the hOn app to serve")
    args = ap.parse_args()

    async def main():
        cloud = FakeCloud(args.data, i18n=args.i18n)
        _, base = await start(cloud, args.port, args.host)
        print(f"fake hOn cloud on {base}  (login: {EMAIL} / {PASSWORD})", flush=True)
        await asyncio.Event().wait()

    asyncio.run(main())
