"""Reverse-proxy budget gate tests (#453, Wave 1d).

Exercises the invocation-block gate wired into
:func:`anygarden.llm_gateway.reverse_proxy.proxy`:

- with an active hard-stop policy over its ceiling, the call is refused
  with 429 and the upstream is NEVER reached,
- a 429 usage row is written for the refusal,
- DEFAULT (no policy) passes through to the upstream — the
  no-behaviour-change-by-default invariant,
- a policy that exists but is not hard_stop_enabled also passes through.

Mirrors ``test_llm_gateway_reverse_proxy.py``'s MockTransport + fake
supervisor harness.
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
    TokenBudgetPolicy,
)


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
        agent = Agent(name="BudgetProxyTest", engine="claude-code")
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

    clear_observed_cache()
    yield {
        "app": app,
        "factory": factory,
        "agent_id": agent_id,
        "agent_token": plain,
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


async def _seed_usage(factory, *, agent_id: str, prompt: int) -> None:
    async with factory() as db:
        db.add(
            LLMGatewayUsage(
                identity_kind="agent",
                identity_id=agent_id,
                agent_id=agent_id,
                model_name="claude-sonnet-4-6",
                prompt_tokens=prompt,
                completion_tokens=0,
                status_code=200,
            )
        )
        await db.commit()


async def _seed_policy(
    factory, *, agent_id: str, ceiling: int, hard_stop: bool = True
) -> None:
    async with factory() as db:
        db.add(
            TokenBudgetPolicy(
                scope_type="agent",
                scope_id=agent_id,
                token_ceiling=ceiling,
                hard_stop_enabled=hard_stop,
                is_active=True,
            )
        )
        await db.commit()


# ── 1. over-ceiling hard-stop policy → 429, upstream not reached ───────


async def test_over_ceiling_policy_refuses_with_429(gateway_env) -> None:
    app = gateway_env["app"]
    agent_id = gateway_env["agent_id"]
    reached_upstream = {"flag": False}

    async def handler(request: httpx.Request) -> Response:
        reached_upstream["flag"] = True
        return Response(200, json={"usage": {"input_tokens": 1, "output_tokens": 1}})

    _install_fake_upstream(app, handler)
    await _seed_usage(gateway_env["factory"], agent_id=agent_id, prompt=1000)
    await _seed_policy(gateway_env["factory"], agent_id=agent_id, ceiling=100)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/llm/v1/messages",
            json={"model": "claude-sonnet-4-6"},
            headers={"Authorization": f"Bearer {gateway_env['agent_token']}"},
        )

    assert resp.status_code == 429, resp.text
    assert resp.headers.get("Retry-After") == "60"
    assert resp.json()["error"]["type"] == "token_budget_exceeded"
    # The chokepoint: upstream LLM is never called for a blocked request.
    assert reached_upstream["flag"] is False


async def test_blocked_call_writes_429_usage_row(gateway_env) -> None:
    app = gateway_env["app"]
    agent_id = gateway_env["agent_id"]

    async def handler(request: httpx.Request) -> Response:
        return Response(200, json={"usage": {"input_tokens": 1, "output_tokens": 1}})

    _install_fake_upstream(app, handler)
    await _seed_usage(gateway_env["factory"], agent_id=agent_id, prompt=1000)
    await _seed_policy(gateway_env["factory"], agent_id=agent_id, ceiling=100)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/llm/v1/messages",
            json={"model": "claude-sonnet-4-6"},
            headers={"Authorization": f"Bearer {gateway_env['agent_token']}"},
        )
    assert resp.status_code == 429

    async with gateway_env["factory"]() as db:
        rows = (
            await db.execute(
                select(LLMGatewayUsage).where(LLMGatewayUsage.status_code == 429)
            )
        ).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.agent_id == agent_id
    assert row.error == "budget:agent"
    assert row.prompt_tokens is None
    assert row.completion_tokens is None


# ── 2. DEFAULT (no policy) → pass-through (no-behaviour-change) ────────


async def test_default_no_policy_passes_through(gateway_env) -> None:
    """THE invariant: with no policy at all, even a wildly over-spending
    agent reaches the upstream exactly as before this PR."""
    app = gateway_env["app"]
    agent_id = gateway_env["agent_id"]
    reached_upstream = {"flag": False}

    async def handler(request: httpx.Request) -> Response:
        reached_upstream["flag"] = True
        return Response(
            200, json={"usage": {"input_tokens": 5, "output_tokens": 5}}
        )

    _install_fake_upstream(app, handler)
    # Pile on usage that would trip any sane ceiling — but no policy.
    await _seed_usage(gateway_env["factory"], agent_id=agent_id, prompt=10_000_000)

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/llm/v1/messages",
            json={"model": "claude-sonnet-4-6"},
            headers={"Authorization": f"Bearer {gateway_env['agent_token']}"},
        )

    assert resp.status_code == 200, resp.text
    assert reached_upstream["flag"] is True


async def test_inactive_or_warn_only_policy_passes_through(gateway_env) -> None:
    """A policy that is active but NOT hard_stop_enabled is observe-only
    and must not refuse the call — the kill-switch default."""
    app = gateway_env["app"]
    agent_id = gateway_env["agent_id"]
    reached_upstream = {"flag": False}

    async def handler(request: httpx.Request) -> Response:
        reached_upstream["flag"] = True
        return Response(
            200, json={"usage": {"input_tokens": 5, "output_tokens": 5}}
        )

    _install_fake_upstream(app, handler)
    await _seed_usage(gateway_env["factory"], agent_id=agent_id, prompt=1000)
    await _seed_policy(
        gateway_env["factory"], agent_id=agent_id, ceiling=100, hard_stop=False
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/llm/v1/messages",
            json={"model": "claude-sonnet-4-6"},
            headers={"Authorization": f"Bearer {gateway_env['agent_token']}"},
        )

    assert resp.status_code == 200, resp.text
    assert reached_upstream["flag"] is True
