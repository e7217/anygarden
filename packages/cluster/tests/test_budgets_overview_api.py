"""Admin budget overview read model — ``GET /api/v1/budgets/overview`` (USE-07).

The admin budget screen needs observed usage vs ceiling per policy, the
open incidents and the agents a hard breach paused. This endpoint is a
read-only aggregate over the existing tables.
"""

from __future__ import annotations

import secrets as _stdlib_secrets
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import pytest_asyncio
from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    Base,
    TokenBudgetIncident,
    TokenBudgetPolicy,
    UsageLedger,
    User,
)
from httpx import ASGITransport, AsyncClient


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

    now = datetime.now(timezone.utc)
    async with factory() as db:
        admin = User(email="admin@test", password_hash="x", is_admin=True)
        regular = User(email="user@test", password_hash="x", is_admin=False)
        paused = Agent(id="a-paused", name="Paused", engine="claude-code")
        paused.pause_reason = "budget"
        running = Agent(id="a-run", name="Running", engine="claude-code")
        db.add_all([admin, regular, paused, running])
        await db.flush()
        policy = TokenBudgetPolicy(
            scope_type="agent", scope_id="a-paused", token_ceiling=1000
        )
        db.add(policy)
        await db.flush()
        db.add_all(
            [
                # Counted: within window, successful.
                UsageLedger(
                    identity_kind="agent", identity_id="a-paused",
                    agent_id="a-paused", model_name="m",
                    prompt_tokens=300, completion_tokens=200,
                    status_code=200, timestamp=now - timedelta(hours=1),
                ),
                # Not counted: outside the rolling 24h window.
                UsageLedger(
                    identity_kind="agent", identity_id="a-paused",
                    agent_id="a-paused", model_name="m",
                    prompt_tokens=5000, completion_tokens=0,
                    status_code=200, timestamp=now - timedelta(hours=30),
                ),
                # Not counted: another agent.
                UsageLedger(
                    identity_kind="agent", identity_id="a-run",
                    agent_id="a-run", model_name="m",
                    prompt_tokens=70, completion_tokens=0,
                    status_code=200, timestamp=now - timedelta(hours=1),
                ),
                TokenBudgetIncident(
                    policy_id=policy.id, scope_type="agent", scope_id="a-paused",
                    window_start=now - timedelta(hours=24), threshold_type="hard",
                    status="open", observed_tokens=1200,
                ),
                TokenBudgetIncident(
                    policy_id=policy.id, scope_type="agent", scope_id="a-paused",
                    window_start=now - timedelta(hours=48), threshold_type="soft",
                    status="resolved", observed_tokens=900,
                ),
            ]
        )
        await db.commit()
        policy_id = policy.id
        admin_jwt = create_user_token(
            user_id=admin.id, email="admin@test", is_admin=True,
            secret=config.jwt_secret,
        )
        user_jwt = create_user_token(
            user_id=regular.id, email="user@test", is_admin=False,
            secret=config.jwt_secret,
        )

    app = create_app(config)
    app.state.session_factory = factory
    app.state.engine = engine
    yield {
        "app": app,
        "admin_jwt": admin_jwt,
        "user_jwt": user_jwt,
        "policy_id": policy_id,
    }
    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.req("USE-07")
async def test_overview_is_admin_only(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.get("/api/v1/budgets/overview", headers=_auth(env["user_jwt"]))
        assert resp.status_code == 403


@pytest.mark.req("USE-07")
async def test_overview_reports_usage_incidents_and_paused_agents(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.get(
            "/api/v1/budgets/overview", headers=_auth(env["admin_jwt"])
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(u["policy_id"], u["observed_tokens"]) for u in body["usage"]] == [
        (env["policy_id"], 500)
    ]
    assert len(body["incidents"]) == 1
    incident = body["incidents"][0]
    assert incident["threshold_type"] == "hard"
    assert incident["status"] == "open"
    assert incident["scope_id"] == "a-paused"
    assert body["paused_agents"] == [{"agent_id": "a-paused", "name": "Paused"}]
