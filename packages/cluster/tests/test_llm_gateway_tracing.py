"""Reverse-proxy LLM tracing + room correlation (#420).

Drives the real proxy with a mocked upstream and a ``TracingService``
backed by an in-memory span exporter. Asserts that an LLM call made
while an engine call is in flight is (a) emitted as an ``llm.generation``
span and (b) stamped with the in-flight ``room_id`` on the usage row.
"""

from __future__ import annotations

import secrets
from typing import Any, AsyncIterator

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, AgentToken, Base, LLMGatewayUsage, Room
from anygarden.observability.tracing import SPAN_LLM, TracingService


class _FakeSupervisor:
    def __init__(self, master_key: str = "sk-fake-master", port: int = 4001) -> None:
        self._master_key = master_key
        self._port = port

    @property
    def master_key(self) -> str | None:
        return self._master_key

    @property
    def port(self) -> int:
        return self._port


@pytest_asyncio.fixture()
async def env() -> AsyncIterator[dict[str, Any]]:
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        llm_gateway_enabled=True,
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        agent = Agent(name="TraceTest", engine="codex")
        db.add(agent)
        # A real room — ``LLMGatewayUsage.room_id`` is an FK, so the
        # correlation target must exist (as it always does in prod,
        # where room_id comes from a live lifecycle frame).
        room = Room(name="TraceRoom")
        db.add(room)
        await db.flush()
        plain = generate_token()
        token_hash, lookup_hint = hash_agent_token(plain)
        db.add(AgentToken(agent_id=agent.id, token_hash=token_hash, lookup_hint=lookup_hint))
        await db.commit()
        agent_id = agent.id
        room_id = room.id

    app = create_app(config)
    app.state.session_factory = factory
    app.state.engine = engine

    # In-memory tracing (lifespan doesn't run under ASGITransport, so we
    # wire the service explicitly).
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app.state.tracing = TracingService(provider)

    # Fake upstream + supervisor.
    async def handler(_request: httpx.Request) -> Response:
        return Response(
            200,
            json={"id": "m1", "content": [], "usage": {"input_tokens": 7, "output_tokens": 3}},
        )

    app.state.llm_gateway_client = httpx.AsyncClient(
        transport=MockTransport(handler), base_url="http://127.0.0.1:4001"
    )
    app.state.llm_gateway_supervisor = _FakeSupervisor()

    yield {
        "app": app,
        "factory": factory,
        "agent_id": agent_id,
        "room_id": room_id,
        "agent_token": plain,
        "exporter": exporter,
    }
    await engine.dispose()


async def _post(app, token: str) -> Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        return await c.post(
            "/api/v1/llm/v1/messages",
            json={"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
            headers={"Authorization": f"Bearer {token}"},
        )


async def _usage_rows(factory) -> list[LLMGatewayUsage]:
    async with factory() as db:
        return list((await db.execute(select(LLMGatewayUsage))).scalars().all())


async def test_llm_call_correlates_room_when_engine_call_in_flight(env):
    tracing: TracingService = env["app"].state.tracing
    room_id = env["room_id"]
    tracing.start_request("req-1", room_id=room_id, agent_id=env["agent_id"])
    tracing.start_engine_call(
        "req-1", engine="codex", room_id=room_id, agent_id=env["agent_id"]
    )

    resp = await _post(env["app"], env["agent_token"])
    assert resp.status_code == 200

    rows = await _usage_rows(env["factory"])
    assert len(rows) == 1
    assert rows[0].room_id == room_id
    assert rows[0].prompt_tokens == 7
    assert rows[0].agent_id == env["agent_id"]

    llm_spans = [s for s in env["exporter"].get_finished_spans() if s.name == SPAN_LLM]
    assert len(llm_spans) == 1
    assert llm_spans[0].attributes["anygarden.correlation"] == "linked"
    assert llm_spans[0].attributes["anygarden.room_id"] == room_id
    assert llm_spans[0].attributes["gen_ai.request.model"] == "claude-sonnet-4-6"
    # prompt captured by default
    assert "gen_ai.prompt" in llm_spans[0].attributes


async def test_llm_call_without_inflight_leaves_room_null(env):
    resp = await _post(env["app"], env["agent_token"])
    assert resp.status_code == 200

    rows = await _usage_rows(env["factory"])
    assert len(rows) == 1
    assert rows[0].room_id is None

    llm_spans = [s for s in env["exporter"].get_finished_spans() if s.name == SPAN_LLM]
    assert llm_spans[0].attributes["anygarden.correlation"] == "none"


async def test_two_inflight_engine_calls_mark_ambiguous(env):
    tracing: TracingService = env["app"].state.tracing
    aid = env["agent_id"]
    tracing.start_request("r1", room_id="roomA", agent_id=aid)
    tracing.start_engine_call("r1", engine="codex", room_id="roomA", agent_id=aid)
    tracing.start_request("r2", room_id="roomB", agent_id=aid)
    tracing.start_engine_call("r2", engine="codex", room_id="roomB", agent_id=aid)

    resp = await _post(env["app"], env["agent_token"])
    assert resp.status_code == 200

    rows = await _usage_rows(env["factory"])
    assert rows[0].room_id is None
    llm_spans = [s for s in env["exporter"].get_finished_spans() if s.name == SPAN_LLM]
    assert llm_spans[0].attributes["anygarden.correlation"] == "ambiguous"
