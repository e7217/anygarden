"""Reverse-proxy → evaluate_cost_event integration (#455, Wave 2a).

Proves the success path chains the post-cost budget evaluation (and the
active stop fires through a mock lifecycle), while the default-OFF path
leaves no incident and no stop. Mirrors
``test_budget_gate_reverse_proxy.py``'s harness.
"""

from __future__ import annotations

import secrets
from typing import Any, AsyncIterator

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.budgets.ledger import clear_observed_cache
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentToken,
    Base,
    LLMGatewayUsage,
    TokenBudgetIncident,
    TokenBudgetPolicy,
)


class _MockLifecycle:
    def __init__(self) -> None:
        self.stopped: list[str] = []
        self.started: list[str] = []

    async def request_stop(self, agent_id: str) -> None:
        self.stopped.append(agent_id)

    async def request_start(self, agent_id: str) -> None:
        self.started.append(agent_id)


@pytest_asyncio.fixture()
async def gateway_env() -> AsyncIterator[dict[str, Any]]:
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
        llm_gateway_enabled=True,
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        agent = Agent(name="CostEventProxyTest", engine="claude-code")
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
    lifecycle = _MockLifecycle()
    app.state.agent_lifecycle = lifecycle

    clear_observed_cache()
    yield {
        "app": app,
        "factory": factory,
        "agent_id": agent_id,
        "agent_token": plain,
        "lifecycle": lifecycle,
    }
    await engine.dispose()
    clear_observed_cache()


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


def _install_fake_upstream(app, handler, *, port: int = 4001) -> None:
    app.state.llm_gateway_client = httpx.AsyncClient(
        transport=MockTransport(handler),
        base_url=f"http://127.0.0.1:{port}",
    )
    app.state.llm_gateway_supervisor = _FakeSupervisor(port=port)


async def _seed_policy(factory, *, agent_id: str, ceiling: int) -> None:
    async with factory() as db:
        db.add(
            TokenBudgetPolicy(
                scope_type="agent",
                scope_id=agent_id,
                token_ceiling=ceiling,
                hard_stop_enabled=True,
                is_active=True,
            )
        )
        await db.commit()


async def test_success_call_triggers_active_stop(gateway_env) -> None:
    """A successful call whose usage pushes the agent over its hard
    ceiling triggers evaluate_cost_event in the background → the mock
    lifecycle.request_stop is called and a hard incident is recorded."""
    app = gateway_env["app"]
    agent_id = gateway_env["agent_id"]

    async def handler(request: httpx.Request) -> Response:
        # Real, over-ceiling usage in the response body.
        return Response(
            200, json={"usage": {"input_tokens": 500, "output_tokens": 500}}
        )

    _install_fake_upstream(app, handler)
    await _seed_policy(gateway_env["factory"], agent_id=agent_id, ceiling=100)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/llm/v1/messages",
            json={"model": "claude-sonnet-4-6"},
            headers={"Authorization": f"Bearer {gateway_env['agent_token']}"},
        )
    assert resp.status_code == 200, resp.text

    # Background tasks (usage write + cost eval) run after the response
    # under ASGITransport. The active stop should have fired.
    assert gateway_env["lifecycle"].stopped == [agent_id]

    async with gateway_env["factory"]() as db:
        incidents = (
            await db.execute(select(TokenBudgetIncident))
        ).scalars().all()
        assert len(incidents) == 1
        assert incidents[0].threshold_type == "hard"
        assert incidents[0].scope_type == "agent"
        # The agent is now paused for budget.
        agent = (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one()
        assert agent.pause_reason == "budget"


async def test_default_no_policy_no_stop_no_incident(gateway_env) -> None:
    """THE invariant: with no policy, a successful (even huge) call writes
    a usage row but creates no incident and stops nothing."""
    app = gateway_env["app"]
    agent_id = gateway_env["agent_id"]

    async def handler(request: httpx.Request) -> Response:
        return Response(
            200,
            json={"usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000}},
        )

    _install_fake_upstream(app, handler)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/llm/v1/messages",
            json={"model": "claude-sonnet-4-6"},
            headers={"Authorization": f"Bearer {gateway_env['agent_token']}"},
        )
    assert resp.status_code == 200, resp.text

    assert gateway_env["lifecycle"].stopped == []
    async with gateway_env["factory"]() as db:
        incidents = (
            await db.execute(select(TokenBudgetIncident))
        ).scalars().all()
        assert incidents == []
        # The usage row WAS written (success path) — proving the chain
        # ran but did nothing because no hard-stop policy is active.
        usage = (
            await db.execute(
                select(LLMGatewayUsage).where(LLMGatewayUsage.status_code == 200)
            )
        ).scalars().all()
        assert len(usage) == 1
        agent = (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one()
        assert agent.pause_reason is None
