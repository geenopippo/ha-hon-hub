"""Helpers to drive a throw-away Home Assistant over its API (end-to-end checks against the fake cloud).

Not collected by pytest. Typical use:
    tests/ha_test_env.sh                 # fake cloud + fresh Home Assistant container
    python tests/e2e_ha.py setup         # onboarding in Italian + the integration with the fake account
    (cd tests && python e2e_scenario.py) # the whole scenario
    tests/ha_test_env.sh stop
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import aiohttp

BASE = "http://127.0.0.1:8123"
CLOUD = "http://127.0.0.1:8765"
TOKEN_FILE = Path(__file__).resolve().parent.parent / ".ha-test" / "token"
USER, PASSWORD = "prova", "prova-prova-123"


class HA:
    def __init__(self, session: aiohttp.ClientSession, token: str = "") -> None:
        self.s = session
        self.token = token or (TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else "")

    @property
    def h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    async def onboard(self, language: str = "it") -> None:
        client = BASE + "/"
        async with self.s.post(BASE + "/api/onboarding/users", json={
                "client_id": client, "name": "Prova", "username": USER, "password": PASSWORD, "language": language}) as r:
            code = (await r.json())["auth_code"]
        async with self.s.post(BASE + "/auth/token", data={
                "client_id": client, "grant_type": "authorization_code", "code": code}) as r:
            access = (await r.json())["access_token"]
        self.token = access
        for step, body in (("core_config", None), ("analytics", None),
                           ("integration", {"client_id": client, "redirect_uri": client + "?auth_callback=1"})):
            async with self.s.post(BASE + f"/api/onboarding/{step}", headers=self.h, json=body) as r:
                assert r.status == 200, (step, r.status, await r.text())
        result = await self.ws({"type": "auth/long_lived_access_token", "client_name": "e2e", "lifespan": 365})
        self.token = result
        TOKEN_FILE.write_text(result)
        await self.ws({"type": "config/core/update", "language": language, "country": "IT", "time_zone": "Europe/Rome",
                       "currency": "EUR"})

    async def ws(self, message: dict):
        async with self.s.ws_connect(BASE + "/api/websocket") as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": self.token})
            assert (await ws.receive_json())["type"] == "auth_ok"
            await ws.send_json({"id": 1, **message})
            while True:
                msg = await ws.receive_json()
                if msg.get("id") == 1 and msg.get("type") == "result":
                    if not msg.get("success"):
                        raise RuntimeError(msg.get("error"))
                    return msg.get("result")

    async def get(self, path: str):
        async with self.s.get(BASE + path, headers=self.h) as r:
            return await r.json()

    async def post(self, path: str, body: dict | None = None, expect: tuple[int, ...] = (200, 201)):
        async with self.s.post(BASE + path, headers=self.h, json=body or {}) as r:
            text = await r.text()
            if r.status not in expect:
                raise RuntimeError(f"{path}: HTTP {r.status} {text[:400]}")
            try:
                return json.loads(text)
            except ValueError:
                return text

    async def add_integration(self, email: str, password: str) -> dict:
        flow = await self.post("/api/config/config_entries/flow", {"handler": "hon_hub", "show_advanced_options": False})
        return await self.post(f"/api/config/config_entries/flow/{flow['flow_id']}", {"email": email, "password": password})

    async def states(self, prefix: str = "") -> dict[str, dict]:
        return {s["entity_id"]: s for s in await self.get("/api/states") if s["entity_id"].split(".", 1)[1].startswith(prefix)}

    async def state(self, entity_id: str) -> str | None:
        async with self.s.get(BASE + f"/api/states/{entity_id}", headers=self.h) as r:
            return (await r.json()).get("state") if r.status == 200 else None

    async def call(self, service: str, data: dict, response: bool = False):
        domain, name = service.split(".")
        return await self.post(f"/api/services/{domain}/{name}" + ("?return_response" if response else ""), data)

    async def template(self, text: str) -> str:
        return await self.post("/api/template", {"template": text})

    async def cloud(self, path: str, body: dict | None = None):
        if body is None:
            async with self.s.get(CLOUD + path) as r:
                return await r.json()
        async with self.s.post(CLOUD + path, json=body) as r:
            return await r.json()

    async def wait(self, entity_id: str, expected: str, seconds: float = 10) -> bool:
        for _ in range(int(seconds * 5)):
            if await self.state(entity_id) == expected:
                return True
            await asyncio.sleep(0.2)
        return False


async def main() -> None:
    async with aiohttp.ClientSession() as session:
        ha = HA(session)
        if sys.argv[1:] == ["setup"]:
            await ha.onboard()
            print(json.dumps(await ha.add_integration("casa@example.com", "segreta"), ensure_ascii=False)[:600])
        elif sys.argv[1:2] == ["states"]:
            for entity_id, s in sorted((await ha.states(sys.argv[2] if len(sys.argv) > 2 else "")).items()):
                print(f"{entity_id:62s} {s['state']!s:26s} {s['attributes'].get('friendly_name', '')}")


if __name__ == "__main__":
    asyncio.run(main())
