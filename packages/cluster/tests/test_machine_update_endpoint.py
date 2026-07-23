"""Tests for POST /api/v1/machines/{id}/update — server-driven update (#550)."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Machine, User


class _FakeBus:
    """Records sent frames; ``connected`` controls the send() return value."""

    def __init__(self, connected: bool = True) -> None:
        self.connected = connected
        self.sent: list[tuple[str, dict]] = []

    async def send(self, machine_id: str, frame: dict) -> bool:
        self.sent.append((machine_id, frame))
        return self.connected


@pytest_asyncio.fixture()
async def env():
    from cryptography.fernet import Fernet

    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
        mcp_secrets_key=Fernet.generate_key().decode("ascii"),
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        owner = User(email="owner@test.com", password_hash="x", is_admin=False)
        other = User(email="other@test.com", password_hash="x", is_admin=False)
        admin = User(email="admin@test.com", password_hash="x", is_admin=True)
        db.add_all([owner, other, admin])
        await db.flush()
        machine = Machine(
            name="m1", hostname="h1", owner_user_id=owner.id, status="online"
        )
        db.add(machine)
        await db.flush()
        tokens = {
            "owner": create_user_token(owner.id, owner.email, False, secret=config.jwt_secret),
            "other": create_user_token(other.id, other.email, False, secret=config.jwt_secret),
            "admin": create_user_token(admin.id, admin.email, True, secret=config.jwt_secret),
        }
        machine_id = machine.id
        await db.commit()

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    bus = _FakeBus(connected=True)
    app.state.machine_bus = bus

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        yield {
            "client": client,
            "tokens": tokens,
            "machine_id": machine_id,
            "bus": bus,
            "factory": factory,
        }
    finally:
        await client.aclose()
        await engine.dispose()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_owner_triggers_update(env) -> None:
    resp = await env["client"].post(
        f"/api/v1/machines/{env['machine_id']}/update",
        headers=_auth(env["tokens"]["owner"]),
        json={},
    )
    assert resp.status_code == 200, resp.text
    # A self_update frame reached the machine bus.
    assert len(env["bus"].sent) == 1
    mid, frame = env["bus"].sent[0]
    assert mid == env["machine_id"]
    assert frame == {"type": "self_update", "target_version": None}
    # Status recorded.
    async with env["factory"]() as db:
        m = await db.get(Machine, env["machine_id"])
        assert m.update_status == "updating"
        assert m.update_started_at is not None


@pytest.mark.asyncio
async def test_target_version_forwarded(env) -> None:
    resp = await env["client"].post(
        f"/api/v1/machines/{env['machine_id']}/update",
        headers=_auth(env["tokens"]["owner"]),
        json={"target_version": "0.13.0"},
    )
    assert resp.status_code == 200
    assert env["bus"].sent[0][1]["target_version"] == "0.13.0"


@pytest.mark.asyncio
async def test_invalid_target_version_rejected(env) -> None:
    resp = await env["client"].post(
        f"/api/v1/machines/{env['machine_id']}/update",
        headers=_auth(env["tokens"]["owner"]),
        json={"target_version": "not-a-version"},
    )
    assert resp.status_code == 400
    assert env["bus"].sent == []  # nothing sent on rejection


@pytest.mark.asyncio
async def test_offline_machine_returns_409(env) -> None:
    env["bus"].connected = False
    resp = await env["client"].post(
        f"/api/v1/machines/{env['machine_id']}/update",
        headers=_auth(env["tokens"]["owner"]),
        json={},
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_non_owner_forbidden(env) -> None:
    resp = await env["client"].post(
        f"/api/v1/machines/{env['machine_id']}/update",
        headers=_auth(env["tokens"]["other"]),
        json={},
    )
    assert resp.status_code == 403
    assert env["bus"].sent == []


@pytest.mark.asyncio
async def test_admin_can_update_any_machine(env) -> None:
    resp = await env["client"].post(
        f"/api/v1/machines/{env['machine_id']}/update",
        headers=_auth(env["tokens"]["admin"]),
        json={},
    )
    assert resp.status_code == 200
