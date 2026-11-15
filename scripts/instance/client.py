"""A small client for the local instance: onboarding, REST and websocket calls."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import aiohttp

BASE = os.environ.get("HA_URL", "http://127.0.0.1:8123")
CLIENT_ID = BASE + "/"
USER, PASSWORD = "demo", "demo-pass-2026"
SESSION = Path(__file__).parent / "session.json"


class WsError(Exception):
    pass


class Instance:
    """REST and websocket access with the demo user's token (session.json)."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self.s = session
        self.token = token
        self.h = {"Authorization": f"Bearer {token}"}
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self._id = 0
        self._results: dict[int, asyncio.Future] = {}
        self._subs: dict[int, asyncio.Queue] = {}
        self._reader: asyncio.Task | None = None

    async def connect(self) -> None:
        self.ws = await self.s.ws_connect(f"{BASE}/api/websocket")
        await self.ws.receive_json()
        await self.ws.send_json({"type": "auth", "access_token": self.token})
        if (await self.ws.receive_json())["type"] != "auth_ok":
            raise WsError("auth failed")
        self._reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        async for msg in self.ws:
            data = json.loads(msg.data)
            if data.get("type") == "result" and data["id"] in self._results:
                self._results.pop(data["id"]).set_result(data)
            elif data.get("type") == "event" and data["id"] in self._subs:
                await self._subs[data["id"]].put(data["event"])

    async def call(self, msg: dict[str, Any], *, ok: bool = True) -> Any:
        self._id += 1
        fut = asyncio.get_running_loop().create_future()
        self._results[self._id] = fut
        await self.ws.send_json({"id": self._id, **msg})
        res = await asyncio.wait_for(fut, 30)
        if ok and not res["success"]:
            raise WsError(f"{msg['type']}: {res.get('error')}")
        return res.get("result") if res["success"] else res

    async def subscribe(self, msg: dict[str, Any]) -> asyncio.Queue:
        self._id += 1
        queue: asyncio.Queue = asyncio.Queue()
        self._subs[self._id] = queue
        fut = asyncio.get_running_loop().create_future()
        self._results[self._id] = fut
        await self.ws.send_json({"id": self._id, **msg})
        await asyncio.wait_for(fut, 30)
        return queue

    async def rest(self, method: str, path: str, body: Any = None) -> Any:
        async with self.s.request(method, f"{BASE}{path}", json=body, headers=self.h) as r:
            r.raise_for_status()
            return await r.json() if r.content_type == "application/json" else await r.text()

    async def state(self, entity_id: str) -> dict[str, Any] | None:
        try:
            return await self.rest("GET", f"/api/states/{entity_id}")
        except aiohttp.ClientResponseError:
            return None

    async def service(self, domain: str, service: str, data: dict[str, Any]) -> Any:
        return await self.call({"type": "call_service", "domain": domain, "service": service, "service_data": data})

    async def close(self) -> None:
        if self._reader:
            self._reader.cancel()
        if self.ws:
            await self.ws.close()


async def onboard(s: aiohttp.ClientSession) -> str:
    """Create the demo user on a fresh instance; returns a long-lived token."""
    r = await s.post(f"{BASE}/api/onboarding/users", json={
        "client_id": CLIENT_ID, "name": "Demo", "username": USER, "password": PASSWORD, "language": "en"})
    if r.status != 200:
        raise SystemExit(f"Onboarding failed ({r.status}): setup_demo.py needs a fresh config folder.")
    code = (await r.json())["auth_code"]
    r = await s.post(f"{BASE}/auth/token", data={"grant_type": "authorization_code", "code": code, "client_id": CLIENT_ID})
    token = (await r.json())["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    for step, body in (("core_config", {}), ("analytics", {}), ("integration", {"client_id": CLIENT_ID, "redirect_uri": CLIENT_ID})):
        await s.post(f"{BASE}/api/onboarding/{step}", json=body, headers=h)
    inst = Instance(s, token)
    await inst.connect()
    long_lived = await inst.call({"type": "auth/long_lived_access_token", "client_name": "home-timer-scripts", "lifespan": 3650})
    await inst.close()
    SESSION.write_text(json.dumps({"token": long_lived}))
    return long_lived


def token() -> str:
    return json.loads(SESSION.read_text())["token"]
