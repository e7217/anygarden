"""Admin API tests for ``/api/v1/budgets`` (#453, Wave 1d).

Non-admin 403 gate + CRUD round-trip, mirroring
``test_llm_gateway_admin_api.py``.
"""

from __future__ import annotations

import secrets as _stdlib_secrets
from typing import Any, AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, User


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

    yield {
        "app": app,
        "factory": factory,
        "admin_jwt": admin_jwt,
        "user_jwt": user_jwt,
    }
    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_non_admin_is_rejected(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        for method, path in (
            ("get", "/api/v1/budgets"),
            ("post", "/api/v1/budgets"),
        ):
            resp = await getattr(c, method)(path, headers=_auth(env["user_jwt"]))
            assert resp.status_code == 403, f"{method.upper()} {path}: {resp.text}"


async def test_unauthenticated_is_rejected(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.get("/api/v1/budgets")
        assert resp.status_code in (401, 403)


async def test_policy_crud_round_trip(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        # Create — defaults hard_stop_enabled False.
        create_resp = await c.post(
            "/api/v1/budgets",
            headers=_auth(env["admin_jwt"]),
            json={
                "scope_type": "agent",
                "scope_id": "agent-123",
                "token_ceiling": 100000,
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        created = create_resp.json()
        policy_id = created["id"]
        assert created["hard_stop_enabled"] is False
        assert created["is_active"] is True
        assert created["warn_percent"] == 80
        assert created["window_kind"] == "rolling_24h"

        # List
        list_resp = await c.get("/api/v1/budgets", headers=_auth(env["admin_jwt"]))
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        # Update — flip the kill switch on and bump ceiling.
        patch_resp = await c.patch(
            f"/api/v1/budgets/{policy_id}",
            headers=_auth(env["admin_jwt"]),
            json={"hard_stop_enabled": True, "token_ceiling": 250000},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        assert patch_resp.json()["hard_stop_enabled"] is True
        assert patch_resp.json()["token_ceiling"] == 250000

        # Delete
        del_resp = await c.delete(
            f"/api/v1/budgets/{policy_id}", headers=_auth(env["admin_jwt"])
        )
        assert del_resp.status_code == 204

        # Gone
        list_after = await c.get(
            "/api/v1/budgets", headers=_auth(env["admin_jwt"])
        )
        assert list_after.json() == []


async def test_global_policy_drops_scope_id(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/budgets",
            headers=_auth(env["admin_jwt"]),
            json={
                "scope_type": "global",
                "scope_id": "ignored",
                "token_ceiling": 5000,
            },
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["scope_id"] is None


async def test_agent_scope_requires_scope_id(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/budgets",
            headers=_auth(env["admin_jwt"]),
            json={"scope_type": "agent", "token_ceiling": 5000},
        )
        assert resp.status_code == 422, resp.text


async def test_update_missing_policy_404(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.patch(
            "/api/v1/budgets/does-not-exist",
            headers=_auth(env["admin_jwt"]),
            json={"is_active": False},
        )
        assert resp.status_code == 404
