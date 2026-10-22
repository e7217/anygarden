"""Tests for /api/v1/system version + update endpoints (#546)."""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, User
from anygarden.system import version_service


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
        admin = User(email="admin@test.com", password_hash="x", is_admin=True)
        member = User(email="member@test.com", password_hash="x", is_admin=False)
        db.add_all([admin, member])
        await db.flush()
        admin_token = create_user_token(
            admin.id, admin.email, admin.is_admin, secret=config.jwt_secret
        )
        member_token = create_user_token(
            member.id, member.email, member.is_admin, secret=config.jwt_secret
        )
        await db.commit()

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory

    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    try:
        yield {"client": client, "admin": admin_token, "member": member_token}
    finally:
        await client.aclose()
        await engine.dispose()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── GET /version ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_version_returns_server_version(env) -> None:
    import anygarden

    resp = await env["client"].get("/api/v1/system/version", headers=_auth(env["member"]))
    assert resp.status_code == 200
    assert resp.json()["version"] == anygarden.__version__


@pytest.mark.asyncio
async def test_version_requires_login(env) -> None:
    # No Authorization header ⇒ unauthenticated (401), rejected before it
    # could reach a handler — the endpoint is not open to anonymous callers.
    resp = await env["client"].get("/api/v1/system/version")
    assert resp.status_code == 401


# ── GET /updates ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_updates_admin_only(env) -> None:
    resp = await env["client"].get("/api/v1/system/updates", headers=_auth(env["member"]))
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.req("SYS-01")
async def test_updates_empty_cache(env) -> None:
    resp = await env["client"].get("/api/v1/system/updates", headers=_auth(env["admin"]))
    assert resp.status_code == 200
    body = resp.json()
    packages = {row["package"] for row in body}
    assert "anygarden" in packages
    # Never checked ⇒ no latest, no update signal.
    ag = next(r for r in body if r["package"] == "anygarden")
    assert ag["latest"] is None
    assert ag["update_available"] is False
    assert ag["checked_at"] is None


# ── POST /check-updates ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_updates_admin_only(env) -> None:
    resp = await env["client"].post(
        "/api/v1/system/check-updates", headers=_auth(env["member"])
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.req("SYS-01")
async def test_updates_get_never_calls_pypi(env, monkeypatch) -> None:
    calls: list[str] = []

    async def fake_fetch(package, *, client=None):
        calls.append(package)
        return "999.0.0"

    monkeypatch.setattr(version_service, "fetch_pypi_latest", fake_fetch)

    resp = await env["client"].get("/api/v1/system/updates", headers=_auth(env["admin"]))
    assert resp.status_code == 200
    assert calls == []  # GET reads the cache only — no outbound call

    resp = await env["client"].post(
        "/api/v1/system/check-updates", headers=_auth(env["admin"])
    )
    assert resp.status_code == 200
    assert "anygarden" in calls  # only the explicit check reaches PyPI


@pytest.mark.asyncio
@pytest.mark.req("SYS-01")
async def test_check_updates_populates_cache(env, monkeypatch) -> None:
    async def fake_fetch(package, *, client=None):
        return "999.0.0"  # always newer than the installed version

    monkeypatch.setattr(version_service, "fetch_pypi_latest", fake_fetch)

    resp = await env["client"].post(
        "/api/v1/system/check-updates", headers=_auth(env["admin"])
    )
    assert resp.status_code == 200
    ag = next(r for r in resp.json() if r["package"] == "anygarden")
    assert ag["latest"] == "999.0.0"
    assert ag["update_available"] is True
    assert ag["checked_at"] is not None

    # Cache persists: a subsequent GET (no outbound call) reflects it.
    resp2 = await env["client"].get("/api/v1/system/updates", headers=_auth(env["admin"]))
    ag2 = next(r for r in resp2.json() if r["package"] == "anygarden")
    assert ag2["latest"] == "999.0.0"
    assert ag2["update_available"] is True


@pytest.mark.asyncio
@pytest.mark.req("SYS-02")
async def test_check_updates_records_error_on_failure(env, monkeypatch) -> None:
    async def fake_fetch(package, *, client=None):
        return None  # PyPI unreachable

    monkeypatch.setattr(version_service, "fetch_pypi_latest", fake_fetch)

    resp = await env["client"].post(
        "/api/v1/system/check-updates", headers=_auth(env["admin"])
    )
    assert resp.status_code == 200
    ag = next(r for r in resp.json() if r["package"] == "anygarden")
    assert ag["error"] == "unreachable"
    assert ag["update_available"] is False

    # The error is persisted: the cache-only GET still surfaces it.
    resp2 = await env["client"].get("/api/v1/system/updates", headers=_auth(env["admin"]))
    assert resp2.status_code == 200
    ag2 = next(r for r in resp2.json() if r["package"] == "anygarden")
    assert ag2["error"] == "unreachable"


# ── /node-update (#927) ───────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.req("NODE-17")
async def test_node_update_is_unavailable_outside_an_integrated_node(env) -> None:
    client = env["client"]
    status = await client.get("/api/v1/system/node-update", headers=_auth(env["admin"]))
    assert status.status_code == 200
    body = status.json()
    assert body["supported"] is False
    assert body["reason"] == "not_integrated_node"
    assert body["status"] == "idle"

    resp = await client.post("/api/v1/system/node-update", headers=_auth(env["admin"]))
    assert resp.status_code == 409
    assert resp.json()["code"] == "NODE_UPDATE_UNAVAILABLE"


@pytest.mark.asyncio
@pytest.mark.req("AUTH-03")
async def test_node_update_is_admin_only(env) -> None:
    client = env["client"]
    for method in ("get", "post"):
        resp = await getattr(client, method)(
            "/api/v1/system/node-update", headers=_auth(env["member"])
        )
        assert resp.status_code == 403


@pytest.fixture()
def updatable(env, monkeypatch):
    """Pretend this app is an integrated node whose install can update."""
    from anygarden.node.self_update import NodeUpdater

    app = env["client"]._transport.app
    restarts: list[bool] = []
    installed: list[str] = []

    async def install(self, target):
        installed.append(target)

    monkeypatch.setattr(NodeUpdater, "_install", install)
    monkeypatch.setattr(version_service, "get_local_version", lambda pkg: "0.23.0")
    app.state.node_update_reason = None
    app.state.node_shutdown_callback = lambda: restarts.append(True)
    return {"app": app, "restarts": restarts, "installed": installed}


@pytest.mark.asyncio
@pytest.mark.req("NODE-17")
async def test_node_update_installs_the_cached_latest_and_restarts(env, updatable) -> None:
    from datetime import UTC, datetime

    from anygarden.system import version_store

    async with updatable["app"].state.session_factory() as db:
        await version_store.upsert(
            db,
            package="anygarden",
            latest_version="0.24.0",
            checked_at=datetime.now(UTC),
        )
        await db.commit()

    resp = await env["client"].post(
        "/api/v1/system/node-update", headers=_auth(env["admin"])
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["target_version"] == "0.24.0"
    await updatable["app"].state.node_updater._task
    assert updatable["installed"] == ["0.24.0"]
    assert updatable["restarts"] == [True]
    assert updatable["app"].state.node_restart_requested is True
    status = await env["client"].get(
        "/api/v1/system/node-update", headers=_auth(env["admin"])
    )
    assert status.json()["status"] == "restarting"


@pytest.mark.asyncio
async def test_node_update_without_a_known_release_asks_for_a_check(env, updatable) -> None:
    resp = await env["client"].post(
        "/api/v1/system/node-update", headers=_auth(env["admin"])
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "NODE_UPDATE_NO_TARGET"


@pytest.mark.asyncio
async def test_node_update_refuses_a_downgrade(env, updatable) -> None:
    resp = await env["client"].post(
        "/api/v1/system/node-update",
        headers=_auth(env["admin"]),
        json={"target_version": "0.22.0"},
    )
    assert resp.status_code == 409
    assert resp.json()["code"] == "NODE_UPDATE_REJECTED"
    assert updatable["restarts"] == []
