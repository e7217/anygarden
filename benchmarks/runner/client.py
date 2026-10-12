"""HTTP/WebSocket client and gate bookkeeping shared by every scenario."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

POLL_SECONDS = 5
GET_RETRIES = 3


class ApiError(RuntimeError):
    pass


class Api:
    """Authenticated REST client; ``transport`` lets tests use an ASGI app."""

    def __init__(
        self,
        base: str,
        token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base = base.rstrip("/")
        self.token = token
        self.client = httpx.AsyncClient(
            base_url=self.base,
            timeout=60,
            transport=transport,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def __call__(
        self, path: str, method: str = "GET", body: Any = None, params: dict | None = None
    ) -> Any:
        # Polling every few seconds races uvicorn's 5 s keep-alive: a pooled
        # connection the server just closed fails with RemoteProtocolError.
        # Reads are idempotent, so retry them; never replay a write.
        attempts = GET_RETRIES if method == "GET" else 1
        for attempt in range(attempts):
            try:
                resp = await self.client.request(method, path, json=body, params=params)
                break
            except httpx.TransportError:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
        if resp.status_code >= 400:
            raise ApiError(f"{method} {path} -> {resp.status_code} {resp.text[:400]}")
        return resp.json() if resp.content else None

    async def close(self) -> None:
        await self.client.aclose()


async def login(
    base: str,
    email: str,
    password: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Api:
    """Log in, registering the account first on a fresh node."""
    async with httpx.AsyncClient(base_url=base, transport=transport) as c:
        creds = {"email": email, "password": password}
        resp = await c.post("/api/v1/auth/login", json=creds)
        if resp.status_code == 401:
            resp = await c.post("/api/v1/auth/register", json=creds)
        if resp.status_code >= 400:
            raise ApiError(f"login -> {resp.status_code} {resp.text[:400]}")
        return Api(base, resp.json()["token"], transport)


async def send_user(
    api: Api, room_id: str, content: str, thread_root_id: str | None = None
) -> dict:
    """Post as the user over the room WebSocket and return the echoed message.

    REST message posts do not create agent turns, so scenarios must use the
    socket like the real client does.
    """
    import websockets

    frame: dict[str, Any] = {"type": "send", "content": content}
    if thread_root_id:
        frame["thread_root_id"] = thread_root_id
    ws_base = api.base.replace("http://", "ws://").replace("https://", "wss://")
    async with websockets.connect(
        f"{ws_base}/ws/rooms/{room_id}",
        subprotocols=["anygarden.v1", f"bearer.{api.token}"],
    ) as ws:
        deadline = time.monotonic() + 30
        await ws.send(json.dumps(frame))
        while time.monotonic() < deadline:
            out = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            if out.get("type") == "message" and out.get("content") == content:
                return out
    raise ApiError("user message echo never arrived")


async def poll(
    probe: Callable[[], Awaitable[Any]], timeout: float, interval: float = POLL_SECONDS
) -> Any:
    """Await ``probe`` until it returns something truthy or ``timeout`` passes."""
    start = time.monotonic()
    while True:
        value = await probe()
        if value or time.monotonic() - start >= timeout:
            return value
        await asyncio.sleep(interval)


@dataclass
class Gates:
    """Ordered gate log for one scenario run.

    ``hard`` gates decide pass/fail. ``info`` entries are evidence only.
    ``expected`` is the number of hard gates a complete run records, so a
    run that stopped early (a gate timed out and later gates never ran)
    cannot pass.
    """

    expected: int
    entries: list[dict] = field(default_factory=list)

    def check(self, gate: str, ok: bool, value: Any = None) -> bool:
        self.entries.append({"gate": gate, "ok": bool(ok), "value": value})
        return bool(ok)

    def info(self, gate: str, value: Any) -> None:
        self.entries.append({"gate": gate, "info": True, "value": value})

    async def wait(
        self,
        gate: str,
        probe: Callable[[], Awaitable[Any]],
        timeout: float,
        interval: float = POLL_SECONDS,
    ) -> Any:
        start = time.monotonic()
        value = await poll(probe, timeout, interval)
        self.entries.append(
            {"gate": gate, "ok": bool(value), "seconds": round(time.monotonic() - start)}
        )
        return value or None

    @property
    def hard(self) -> list[dict]:
        return [e for e in self.entries if not e.get("info")]

    @property
    def passed(self) -> bool:
        hard = self.hard
        return len(hard) == self.expected and all(e["ok"] for e in hard)


def save(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def items(payload: Any) -> list:
    """Normalize list endpoints that may wrap rows in an object."""
    if isinstance(payload, list):
        return payload
    for key in ("items", "executions", "messages", "requests", "approvals"):
        if isinstance(payload, dict) and isinstance(payload.get(key), list):
            return payload[key]
    return []
