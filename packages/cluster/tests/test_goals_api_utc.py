"""Regression test for the goals API timezone bug (Wave 0, #445 item 5).

The create / update / resume routes used to compute ``next_run_at`` from
``datetime.utcnow().astimezone()`` — which attaches the *host* local zone
to a naive UTC instant, shifting the resulting absolute time by the host
offset on any non-UTC host. ``compute_next_run_at`` documents that
``after`` must be a timezone-aware UTC instant; mixing naive/local-offset
datetimes silently drifts.

On ``TZ=Asia/Seoul`` (UTC+9, no DST), ``datetime.utcnow().astimezone()``
re-stamps the naive UTC wall-clock with ``+09:00``, so the absolute
instant is 9 hours behind true UTC. On PostgreSQL (production) that
offset is preserved and every interval goal's first fire lands 9 hours in
the past — the scheduler then fires it immediately on creation.

Note: an in-memory SQLite round-trip *masks* the bug because
``DateTime(timezone=True)`` drops the offset on write and ``UtcDateTime``
re-stamps the naive wall-clock as ``+00:00`` on read (see db/types.py).
So we assert at the genuine seam: the ``after`` instant the route hands
to ``compute_next_run_at`` must be true UTC (within tolerance of
``datetime.now(timezone.utc)``), *not* shifted by the host offset.
"""

from __future__ import annotations

import secrets
import time
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import anygarden.api.v1.goals as goals_module
from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    Base,
    Participant,
    Room,
    User,
)


@pytest_asyncio.fixture()
async def goals_env() -> AsyncIterator[dict]:
    """App + DB + a room with an owner user and an agent participant."""
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        owner = User(email="owner@test.com", password_hash="x", is_admin=True)
        db.add(owner)
        await db.flush()

        agent = Agent(name="bot", engine="echo")
        db.add(agent)
        await db.flush()

        room = Room(name="r")
        db.add(room)
        await db.flush()

        owner_p = Participant(room_id=room.id, user_id=owner.id, role="member")
        agent_p = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add_all([owner_p, agent_p])
        await db.commit()

        owner_id, owner_email, owner_admin = owner.id, owner.email, owner.is_admin
        agent_id = agent.id
        room_id = room.id

    token = create_user_token(
        owner_id, owner_email, owner_admin, secret=config.jwt_secret
    )

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "token": token,
            "agent_id": agent_id,
            "room_id": room_id,
        }

    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def seoul_tz():
    """Force the process TZ to Asia/Seoul (UTC+9, no DST) for the test.

    This is the condition under which ``datetime.utcnow().astimezone()``
    silently drifts: the naive UTC instant gets re-stamped with the +09:00
    host offset, shifting the absolute instant 9 hours.
    """
    import os

    prev = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Seoul"
    time.tzset()
    try:
        yield
    finally:
        if prev is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = prev
        time.tzset()


@pytest.mark.asyncio
async def test_create_interval_passes_true_utc_to_compute_next_run(
    goals_env, seoul_tz, monkeypatch
) -> None:
    """On a non-UTC host the create route must hand ``compute_next_run_at``
    a true-UTC ``after`` instant — not one shifted by the host offset.

    We capture the ``after`` argument at the policy boundary because the
    SQLite test DB normalises stored offsets and would otherwise hide the
    regression (the bug only bites on PostgreSQL in production)."""
    client = goals_env["client"]
    agent_id = goals_env["agent_id"]
    room_id = goals_env["room_id"]

    captured: dict[str, datetime] = {}
    real_compute = goals_module.compute_next_run_at

    def _capture(trigger_type, config, *, after):
        captured["after"] = after
        return real_compute(trigger_type, config, after=after)

    monkeypatch.setattr(goals_module, "compute_next_run_at", _capture)

    before = datetime.now(timezone.utc)
    resp = await client.post(
        f"/api/v1/agents/{agent_id}/goals",
        json={
            "title": "ping",
            "spec": "say hi",
            "trigger_type": "interval",
            "trigger_config": {"interval_seconds": 600},
            "report_room_id": room_id,
        },
        headers=_auth(goals_env["token"]),
    )
    after = datetime.now(timezone.utc)
    assert resp.status_code == 201, resp.text

    passed = captured["after"]
    # The route must pass an aware UTC instant whose absolute value lands
    # in [before, after]. The buggy utcnow().astimezone() under KST yields
    # an instant ~9h (32400s) behind, which falls far outside this window.
    assert passed.tzinfo is not None, "after must be timezone-aware"
    passed_utc = passed.astimezone(timezone.utc)
    assert before <= passed_utc <= after, (
        f"after={passed.isoformat()} (={passed_utc.isoformat()} UTC) is "
        f"outside [{before.isoformat()}, {after.isoformat()}] — likely "
        f"shifted by the host TZ offset (datetime.utcnow().astimezone())"
    )
