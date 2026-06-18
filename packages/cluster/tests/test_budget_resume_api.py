"""Admin resume endpoint tests for ``/api/v1/budgets`` (#455, Wave 2a).

Covers the non-admin 403 gate and the resume behaviour: clear
``pause_reason``, resolve open agent-scope incidents, and call
``lifecycle.request_start`` through a mock lifecycle.
"""

from __future__ import annotations

import secrets as _stdlib_secrets
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    Base,
    TokenBudgetIncident,
    User,
)


class _MockLifecycle:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.stopped: list[str] = []

    async def request_start(self, agent_id: str) -> None:
        self.started.append(agent_id)

    async def request_stop(self, agent_id: str) -> None:
        self.stopped.append(agent_id)


@pytest_asyncio.fixture()
async def env() -> AsyncIterator[dict[str, Any]]:
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=_stdlib_secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        admin = User(email="admin@test", password_hash="x", is_admin=True)
        regular = User(email="user@test", password_hash="x", is_admin=False)
        db.add_all([admin, regular])
        # A budget-paused agent with two open incidents (one soft, one
        # hard) plus an already-resolved one that resume must leave alone.
        agent = Agent(id="paused1", name="Paused", engine="claude-code")
        agent.pause_reason = "budget"
        db.add(agent)
        await db.flush()
        db.add_all(
            [
                TokenBudgetIncident(
                    policy_id="pol-h",
                    scope_type="agent",
                    scope_id="paused1",
                    window_start=datetime.now(timezone.utc),
                    threshold_type="hard",
                    status="open",
                    observed_tokens=150,
                ),
                TokenBudgetIncident(
                    policy_id="pol-s",
                    scope_type="agent",
                    scope_id="paused1",
                    window_start=datetime.now(timezone.utc),
                    threshold_type="soft",
                    status="open",
                    observed_tokens=90,
                ),
                # Unrelated already-resolved incident — must stay resolved.
                TokenBudgetIncident(
                    policy_id="pol-old",
                    scope_type="agent",
                    scope_id="paused1",
                    window_start=datetime.now(timezone.utc),
                    threshold_type="hard",
                    status="resolved",
                    observed_tokens=200,
                    resolved_at=datetime.now(timezone.utc),
                ),
            ]
        )
        await db.commit()
        admin_id = admin.id
        regular_id = regular.id

    admin_jwt = create_user_token(
        user_id=admin_id, email="admin@test", is_admin=True, secret=config.jwt_secret
    )
    user_jwt = create_user_token(
        user_id=regular_id, email="user@test", is_admin=False, secret=config.jwt_secret
    )

    app = create_app(config)
    app.state.session_factory = factory
    app.state.engine = engine
    lifecycle = _MockLifecycle()
    app.state.agent_lifecycle = lifecycle

    yield {
        "app": app,
        "factory": factory,
        "admin_jwt": admin_jwt,
        "user_jwt": user_jwt,
        "lifecycle": lifecycle,
    }
    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_resume_non_admin_is_rejected(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/budgets/agents/paused1/resume",
            headers=_auth(env["user_jwt"]),
        )
    assert resp.status_code == 403, resp.text
    # Side-effect-free on rejection.
    assert env["lifecycle"].started == []


async def test_resume_missing_agent_404(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/budgets/agents/does-not-exist/resume",
            headers=_auth(env["admin_jwt"]),
        )
    assert resp.status_code == 404


async def test_resume_clears_pause_resolves_incidents_and_starts(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/budgets/agents/paused1/resume",
            headers=_auth(env["admin_jwt"]),
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["agent_id"] == "paused1"
    # Both open incidents resolved; the pre-resolved one is untouched.
    assert body["incidents_resolved"] == 2

    # request_start was called through the lifecycle.
    assert env["lifecycle"].started == ["paused1"]

    async with env["factory"]() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == "paused1"))
        ).scalar_one()
        assert agent.pause_reason is None

        incidents = (
            await db.execute(select(TokenBudgetIncident))
        ).scalars().all()
        statuses = sorted(i.status for i in incidents)
        # All three now resolved (two newly + one pre-existing).
        assert statuses == ["resolved", "resolved", "resolved"]
        for i in incidents:
            assert i.resolved_at is not None
