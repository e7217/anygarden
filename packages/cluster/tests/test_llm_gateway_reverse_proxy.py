"""Tests for :mod:`doorae.llm_gateway.reverse_proxy` (#197).

Uses ``httpx.MockTransport`` to stand in for the LiteLLM subprocess
and a tiny fake supervisor for the runtime state. No real subprocess,
no real network. The tests exercise the observable contract:

- the proxy forwards method/path/body to the upstream,
- the caller's doorae token is replaced with the gateway master key,
- a usage row is written after a successful response,
- SSE streaming responses are relayed chunk-by-chunk,
- unauthenticated requests are rejected before reaching the upstream.
"""

from __future__ import annotations

import secrets
from typing import Any, AsyncIterator

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from sqlalchemy import select

from doorae.app import create_app
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Agent, AgentToken, Base, LLMGatewayUsage
from doorae.auth.token import generate_token, hash_agent_token


@pytest_asyncio.fixture()
async def gateway_env() -> AsyncIterator[dict[str, Any]]:
    """App + DB + in-memory agent token, with the gateway flag on."""
    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
        llm_gateway_enabled=True,
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Seed one agent + token so the proxy has a valid caller.
    async with factory() as db:
        agent = Agent(
            name="ProxyTest",
            engine="claude-code",
        )
        db.add(agent)
        await db.flush()

        plain = generate_token()
        token_hash, lookup_hint = hash_agent_token(plain)
        db.add(
            AgentToken(
                agent_id=agent.id,
                token_hash=token_hash,
                lookup_hint=lookup_hint,
            )
        )
        await db.commit()
        agent_id = agent.id

    app = create_app(config)
    app.state.session_factory = factory
    app.state.engine = engine

    yield {
        "app": app,
        "engine": engine,
        "factory": factory,
        "agent_id": agent_id,
        "agent_token": plain,
    }

    await engine.dispose()


class _FakeSupervisor:
    """Minimal surface the reverse proxy reads from.

    Production is :class:`LLMGatewaySupervisor`. Keeping the tests
    decoupled here lets us drive the master key / port / running
    state deterministically without driving the real state machine.
    """

    def __init__(self, master_key: str = "sk-fake-master", port: int = 4001) -> None:
        self._master_key = master_key
        self._port = port

    @property
    def master_key(self) -> str | None:
        return self._master_key

    @property
    def port(self) -> int:
        return self._port


def _install_fake_upstream(
    app,
    handler,
    *,
    master_key: str = "sk-fake-master",
    port: int = 4001,
) -> None:
    """Bind a ``MockTransport`` + fake supervisor onto ``app.state``."""
    app.state.llm_gateway_client = httpx.AsyncClient(
        transport=MockTransport(handler),
        base_url=f"http://127.0.0.1:{port}",
    )
    app.state.llm_gateway_supervisor = _FakeSupervisor(
        master_key=master_key, port=port
    )


# ── 1. forwarding: method, path, body ──────────────────────────────────


async def test_proxy_forwards_method_path_and_body(gateway_env) -> None:
    app = gateway_env["app"]
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["body"] = await request.aread()
        return Response(
            200,
            json={"id": "msg_1", "content": [], "usage": {"input_tokens": 1, "output_tokens": 2}},
        )

    _install_fake_upstream(app, handler)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/llm/v1/messages",
            json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {gateway_env['agent_token']}"},
        )

    assert resp.status_code == 200
    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/messages"
    assert b'"model":"claude-sonnet-4-6"' in captured["body"]


# ── 2. master key replacement ──────────────────────────────────────────


async def test_proxy_replaces_authorization_with_master_key(gateway_env) -> None:
    app = gateway_env["app"]
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> Response:
        captured["authorization"] = request.headers.get("authorization")
        return Response(200, json={"usage": {"input_tokens": 0, "output_tokens": 0}})

    _install_fake_upstream(app, handler, master_key="sk-secret-gateway")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        await c.post(
            "/api/v1/llm/v1/messages",
            json={"model": "x"},
            headers={"Authorization": f"Bearer {gateway_env['agent_token']}"},
        )

    # Caller's doorae token must not leak upstream, and the upstream
    # must see the gateway's own master key.
    assert captured["authorization"] == "Bearer sk-secret-gateway"
    assert gateway_env["agent_token"] not in (captured["authorization"] or "")


# ── 3. usage logging ───────────────────────────────────────────────────


async def test_successful_response_writes_usage_row(gateway_env) -> None:
    app = gateway_env["app"]

    async def handler(request: httpx.Request) -> Response:
        return Response(
            200,
            json={
                "id": "msg_1",
                "content": [],
                "usage": {"input_tokens": 111, "output_tokens": 22},
            },
        )

    _install_fake_upstream(app, handler)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/llm/v1/messages",
            json={"model": "claude-sonnet-4-6"},
            headers={"Authorization": f"Bearer {gateway_env['agent_token']}"},
        )
    assert resp.status_code == 200

    async with gateway_env["factory"]() as db:
        rows = (await db.execute(select(LLMGatewayUsage))).scalars().all()

    assert len(rows) == 1
    row = rows[0]
    assert row.identity_kind == "agent"
    assert row.identity_id == gateway_env["agent_id"]
    assert row.agent_id == gateway_env["agent_id"]
    assert row.model_name == "claude-sonnet-4-6"
    assert row.prompt_tokens == 111
    assert row.completion_tokens == 22
    assert row.status_code == 200


# ── 4. SSE streaming ───────────────────────────────────────────────────


async def test_sse_response_is_relayed_in_chunks(gateway_env) -> None:
    app = gateway_env["app"]

    sse_body = (
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"usage":{"input_tokens":5}}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type":"content_block_delta","delta":{"text":"hi"}}\n\n'
        b'event: message_delta\n'
        b'data: {"type":"message_delta","usage":{"output_tokens":3}}\n\n'
    )

    async def handler(request: httpx.Request) -> Response:
        return Response(
            200,
            content=sse_body,
            headers={"content-type": "text/event-stream"},
        )

    _install_fake_upstream(app, handler)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/llm/v1/messages",
            json={"model": "claude-sonnet-4-6", "stream": True},
            headers={"Authorization": f"Bearer {gateway_env['agent_token']}"},
        )

    assert resp.status_code == 200
    assert resp.headers.get("content-type", "").startswith("text/event-stream")
    body = resp.content
    # The whole upstream body must arrive at the client; the exact
    # chunk boundaries aren't important, just that every segment is
    # present and in order.
    assert b"message_start" in body
    assert b"content_block_delta" in body
    assert b"message_delta" in body


# ── 5. unauthenticated request rejected ────────────────────────────────


async def test_unauthenticated_request_is_rejected(gateway_env) -> None:
    app = gateway_env["app"]
    reached_upstream = {"flag": False}

    async def handler(request: httpx.Request) -> Response:
        reached_upstream["flag"] = True
        return Response(200)

    _install_fake_upstream(app, handler)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.post(
            "/api/v1/llm/v1/messages",
            json={"model": "claude-sonnet-4-6"},
            # No Authorization header.
        )

    assert resp.status_code in (401, 403)
    assert reached_upstream["flag"] is False
