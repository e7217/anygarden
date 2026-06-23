"""Transport-compliance tests for the agent-facing MCP endpoint (#352).

The cluster exposes its tools (``mark_task_status`` etc.) over a single
``POST /mcp/rpc`` JSON-RPC endpoint that every engine's MCP client
connects to. The MCP *Streamable HTTP* transport requires the server to
acknowledge JSON-RPC **notifications** (messages with no ``id``, e.g.
``notifications/initialized``) with ``202 Accepted`` and an empty body —
NOT with a JSON-RPC response.

Before #352 the endpoint fell through to a ``-32601 method not found``
*response* for every unrecognised method, including notifications. A
lenient client (claude-code) tolerated that, but a strict
``streamable_http_client`` (codex's rmcp) aborts the
``initialize → notifications/initialized → tools/list`` handshake when a
notification draws a response, so the cluster tools never reach the
agent's tool list. These tests pin the compliant behaviour while
guarding the existing single-shot JSON-RPC request paths used by the
already-working engines.
"""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, AgentToken, Base
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.skills_library.service import SkillLibraryService


@pytest_asyncio.fixture()
async def mcp_env():
    """A live app + authenticated agent token for ``/mcp/rpc`` calls."""
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.machine_bus = bus
    app.state.agent_lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)
    app.state.skill_library_service = SkillLibraryService(factory)

    async with factory() as session:
        agent = Agent(name="bot", engine="echo")
        session.add(agent)
        await session.flush()
        plain = generate_token()
        token_hash, hint = hash_agent_token(plain)
        session.add(
            AgentToken(agent_id=agent.id, token_hash=token_hash, lookup_hint=hint)
        )
        await session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "token": plain}
    await engine.dispose()


def _auth(env) -> dict[str, str]:
    return {"Authorization": f"Bearer {env['token']}"}


@pytest.mark.asyncio
async def test_initialized_notification_is_acked_with_202_no_body(mcp_env) -> None:
    """``notifications/initialized`` (no ``id``) must return 202 + empty body.

    This is the exact handshake step that strict MCP Streamable HTTP
    clients send after ``initialize``; a JSON-RPC response here breaks
    the session (#352).
    """
    resp = await mcp_env["client"].post(
        "/mcp/rpc",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=_auth(mcp_env),
    )
    assert resp.status_code == 202
    assert resp.content == b""


@pytest.mark.asyncio
async def test_idless_message_is_acked_with_202(mcp_env) -> None:
    """Any JSON-RPC message without an ``id`` is a notification → 202."""
    resp = await mcp_env["client"].post(
        "/mcp/rpc",
        json={"jsonrpc": "2.0", "method": "notifications/cancelled",
              "params": {"requestId": 1}},
        headers=_auth(mcp_env),
    )
    assert resp.status_code == 202
    assert resp.content == b""


@pytest.mark.asyncio
async def test_initialize_request_still_returns_capabilities(mcp_env) -> None:
    """Regression: the single-shot ``initialize`` request path is preserved."""
    resp = await mcp_env["client"].post(
        "/mcp/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        headers=_auth(mcp_env),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 1
    assert "protocolVersion" in body["result"]
    assert body["result"]["capabilities"]["tools"] is not None


@pytest.mark.asyncio
async def test_tools_list_request_still_returns_mark_task_status(mcp_env) -> None:
    """Regression: ``tools/list`` still announces the cluster tools."""
    resp = await mcp_env["client"].post(
        "/mcp/rpc",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers=_auth(mcp_env),
    )
    assert resp.status_code == 200
    body = resp.json()
    names = {t["name"] for t in body["result"]["tools"]}
    assert "mark_task_status" in names


@pytest.mark.asyncio
async def test_unknown_request_with_id_still_returns_method_not_found(mcp_env) -> None:
    """Regression: an unknown *request* (has ``id``) keeps the -32601 error.

    Only id-less notifications get the 202 ACK; real requests must still
    receive a JSON-RPC error so clients see the failure.
    """
    resp = await mcp_env["client"].post(
        "/mcp/rpc",
        json={"jsonrpc": "2.0", "id": 9, "method": "no/such/method"},
        headers=_auth(mcp_env),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == 9
    assert body["error"]["code"] == -32601
