"""Tests for engine check/update endpoints + WS result handlers (#553)."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Base,
    Machine,
    MachineEngine,
    MachineEngineStatus,
    User,
)


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
        db.add_all([owner, other])
        await db.flush()
        machine = Machine(
            name="m1", hostname="h1", owner_user_id=owner.id, status="online"
        )
        db.add(machine)
        await db.flush()
        tokens = {
            "owner": create_user_token(
                owner.id, owner.email, False, secret=config.jwt_secret
            ),
            "other": create_user_token(
                other.id, other.email, False, secret=config.jwt_secret
            ),
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


async def _status(env, engine: str = "codex-cli") -> MachineEngineStatus | None:
    async with env["factory"]() as db:
        return (
            await db.execute(
                select(MachineEngineStatus).where(
                    MachineEngineStatus.machine_id == env["machine_id"],
                    MachineEngineStatus.engine == engine,
                )
            )
        ).scalar_one_or_none()


# ── D2: REST endpoints ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_sends_frame(env):
    resp = await env["client"].post(
        f"/api/v1/machines/{env['machine_id']}/engines/codex-cli/check",
        headers=_auth(env["tokens"]["owner"]),
    )
    assert resp.status_code == 202, resp.text
    assert env["bus"].sent[0][1] == {"type": "engine_check", "engine": "codex-cli"}


@pytest.mark.asyncio
async def test_update_sends_frame_and_records_updating(env):
    resp = await env["client"].post(
        f"/api/v1/machines/{env['machine_id']}/engines/codex-cli/update",
        headers=_auth(env["tokens"]["owner"]),
    )
    assert resp.status_code == 202, resp.text
    assert env["bus"].sent[0][1] == {"type": "engine_update", "engine": "codex-cli"}
    st = await _status(env)
    assert st is not None
    assert st.update_status == "updating"
    assert st.update_started_at is not None


@pytest.mark.asyncio
async def test_update_offline_returns_409(env):
    env["bus"].connected = False
    resp = await env["client"].post(
        f"/api/v1/machines/{env['machine_id']}/engines/codex-cli/update",
        headers=_auth(env["tokens"]["owner"]),
    )
    assert resp.status_code == 409


# ── D3: WS result handlers ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_result_records_availability(env):
    from anygarden.ws.machine_handler import _handle_engine_check_result

    await _handle_engine_check_result(
        env["factory"],
        env["machine_id"],
        {"engine": "codex-cli", "current_version": "0.1.0", "latest_version": "0.2.0"},
    )
    st = await _status(env)
    assert st.latest_version == "0.2.0"
    assert st.update_available is True
    assert st.latest_checked_at is not None


@pytest.mark.asyncio
async def test_check_result_no_update_when_current_is_latest(env):
    from anygarden.ws.machine_handler import _handle_engine_check_result

    await _handle_engine_check_result(
        env["factory"],
        env["machine_id"],
        {"engine": "codex-cli", "current_version": "0.2.0", "latest_version": "0.2.0"},
    )
    st = await _status(env)
    assert st.update_available is False


@pytest.mark.asyncio
async def test_update_result_success_clears_availability(env):
    from anygarden.ws.machine_handler import _handle_engine_update_result

    await _handle_engine_update_result(
        env["factory"], env["machine_id"], {"engine": "codex-cli", "status": "success"}
    )
    st = await _status(env)
    assert st.update_status == "success"
    assert st.update_available is False


@pytest.mark.asyncio
async def test_update_result_failed_records_error(env):
    from anygarden.ws.machine_handler import _handle_engine_update_result

    await _handle_engine_update_result(
        env["factory"],
        env["machine_id"],
        {"engine": "codex-cli", "status": "failed", "error": "npm ERR! boom"},
    )
    st = await _status(env)
    assert st.update_status == "failed"
    assert "boom" in st.update_error


@pytest.mark.asyncio
async def test_status_survives_register_wipe(env):
    """#553 결정3: register가 machine_engines를 delete+recreate해도, 상태는
    별도 테이블(machine_engine_status)이라 살아남아야 한다."""
    from anygarden.ws.machine_handler import (
        _handle_engine_update_result,
        _handle_register,
    )

    await _handle_engine_update_result(
        env["factory"], env["machine_id"], {"engine": "codex-cli", "status": "success"}
    )
    # register wipes + recreates machine_engines
    await _handle_register(
        env["factory"],
        env["machine_id"],
        {
            "capabilities": [{"engine": "codex-cli", "version": "0.2.0"}],
            "daemon_version": "0.13.0",
        },
    )
    async with env["factory"]() as db:
        engines = (
            await db.execute(
                select(MachineEngine).where(
                    MachineEngine.machine_id == env["machine_id"]
                )
            )
        ).scalars().all()
        assert any(e.engine == "codex-cli" for e in engines)  # recreated

    st = await _status(env)
    assert st is not None  # survived the wipe
    assert st.update_status == "success"
