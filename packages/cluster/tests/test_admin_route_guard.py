"""AUTH-03 — every admin route rejects user, guest and agent tokens with 403.

Rather than hand-listing endpoints (which drifts), the routes are discovered
from the app: any HTTP route whose dependency tree includes
``get_admin_identity`` is called with each non-admin identity.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.invite_token import hash_invite_token
from anygarden.auth.jwt import create_guest_token, create_user_token
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentToken,
    Base,
    Project,
    Room,
    RoomInviteLink,
    User,
)
from anygarden.dependencies import get_admin_identity

pytestmark = pytest.mark.req("AUTH-03")


def _uses_admin_guard(dependant: Dependant) -> bool:
    for dep in dependant.dependencies:
        if dep.call is get_admin_identity or _uses_admin_guard(dep):
            return True
    return False


def _admin_routes(app) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for route in app.routes:
        if not isinstance(route, APIRoute) or not _uses_admin_guard(route.dependant):
            continue
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            found.append((method, path))
    return found


@pytest_asyncio.fixture()
async def env(config: AnygardenSettings) -> AsyncIterator[dict]:
    engine = build_engine(config.db_url)
    sessions = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    secret = config.jwt_secret
    agent_token = generate_token()
    async with sessions() as db:
        user = User(email="user@anygarden.io", password_hash="x")
        project = Project(name="proj")
        agent = Agent(name="bot", engine="codex", actual_state="running")
        db.add_all([user, project, agent])
        await db.flush()
        room = Room(project_id=project.id, name="main")
        db.add(room)
        await db.flush()
        token_hash, hint = hash_invite_token("inv_" + "g" * 40)
        invite = RoomInviteLink(
            room_id=room.id,
            created_by_user_id=user.id,
            token_hash=token_hash,
            lookup_hint=hint,
        )
        hashed, agent_hint = hash_agent_token(agent_token)
        db.add_all(
            [invite, AgentToken(agent_id=agent.id, token_hash=hashed, lookup_hint=agent_hint)]
        )
        await db.commit()
        for obj in (user, room, invite):
            await db.refresh(obj)

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions
    # Peering/sharing routes 503 before auth when their service is absent;
    # a sentinel lets the request reach the admin guard (it never gets past).
    app.state.peer_service = object()
    app.state.channel_service = object()
    tokens = {
        "user": create_user_token(user.id, user.email, False, secret=secret),
        "guest": create_guest_token(
            user_id="guest-user",
            room_id=room.id,
            invite_id=invite.id,
            display_name="Guest",
            secret=secret,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        ),
        "agent": agent_token,
    }
    yield {"app": app, "tokens": tokens}
    await engine.dispose()


async def test_admin_routes_are_discovered(env):
    routes = _admin_routes(env["app"])
    # Guard against the discovery silently matching nothing.
    assert len(routes) >= 20
    assert ("GET", "/api/v1/usage") in routes


@pytest.mark.parametrize("kind", ["user", "guest", "agent"])
async def test_non_admin_tokens_get_403_on_every_admin_route(env, kind):
    headers = {"Authorization": f"Bearer {env['tokens'][kind]}"}
    transport = ASGITransport(app=env["app"])
    failures: list[str] = []
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for method, path in _admin_routes(env["app"]):
            resp = await client.request(method, path, headers=headers, json={})
            if resp.status_code != 403:
                failures.append(f"{method} {path} -> {resp.status_code}")
    assert failures == [], "\n".join(failures)
