"""Admin account management, suspension, sign-up policy, password change (#1058)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from anygarden.app import create_app
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, User
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus

PASSWORD = "first-password"


@pytest_asyncio.fixture()
async def env():
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.machine_bus = bus
    app.state.agent_lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield {"client": client, "sessions": factory, "config": config}
    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _register(client: AsyncClient, email: str, password: str = PASSWORD) -> dict:
    resp = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": password}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _admin_and_member(client: AsyncClient) -> tuple[dict, dict]:
    admin = await _register(client, "admin@example.com")
    member = await _register(client, "member@example.com")
    return admin, member


# ── AUTH-17: list / create / update / reset ───────────────────────────


@pytest.mark.req("AUTH-17")
async def test_admin_lists_and_searches_registered_users(env):
    client = env["client"]
    admin, member = await _admin_and_member(client)

    resp = await client.get("/api/v1/admin/users", headers=_auth(admin["token"]))
    assert resp.status_code == 200
    rows = resp.json()
    assert [r["email"] for r in rows] == ["admin@example.com", "member@example.com"]
    assert rows[0]["is_admin"] is True and rows[1]["is_admin"] is False
    assert rows[1]["disabled"] is False

    resp = await client.get(
        "/api/v1/admin/users", params={"q": "MEMBER"}, headers=_auth(admin["token"])
    )
    assert [r["email"] for r in resp.json()] == ["member@example.com"]


@pytest.mark.req("AUTH-17")
async def test_non_admin_cannot_manage_users(env):
    client = env["client"]
    _, member = await _admin_and_member(client)
    headers = _auth(member["token"])

    assert (await client.get("/api/v1/admin/users", headers=headers)).status_code == 403
    resp = await client.post(
        "/api/v1/admin/users",
        json={"email": "x@example.com", "password": PASSWORD},
        headers=headers,
    )
    assert resp.status_code == 403
    resp = await client.put(
        "/api/v1/admin/account-settings",
        json={"registration_open": False},
        headers=headers,
    )
    assert resp.status_code == 403


@pytest.mark.req("AUTH-17")
async def test_admin_creates_account_that_can_log_in(env):
    client = env["client"]
    admin, _ = await _admin_and_member(client)

    resp = await client.post(
        "/api/v1/admin/users",
        json={"email": "new@example.com", "password": "initial-pass", "is_admin": True},
        headers=_auth(admin["token"]),
    )
    assert resp.status_code == 201
    assert resp.json()["is_admin"] is True

    dup = await client.post(
        "/api/v1/admin/users",
        json={"email": "new@example.com", "password": "initial-pass"},
        headers=_auth(admin["token"]),
    )
    assert dup.status_code == 409

    login = await client.post(
        "/api/v1/auth/login", json={"email": "new@example.com", "password": "initial-pass"}
    )
    assert login.status_code == 200
    assert login.json()["user"]["is_admin"] is True


@pytest.mark.req("AUTH-17")
async def test_admin_grants_and_removes_admin(env):
    client = env["client"]
    admin, member = await _admin_and_member(client)
    member_id = member["user_id"]

    resp = await client.patch(
        f"/api/v1/admin/users/{member_id}",
        json={"is_admin": True},
        headers=_auth(admin["token"]),
    )
    assert resp.status_code == 200 and resp.json()["is_admin"] is True
    me = await client.get("/api/v1/auth/me", headers=_auth(member["token"]))
    assert me.json()["is_admin"] is True

    resp = await client.patch(
        f"/api/v1/admin/users/{member_id}",
        json={"is_admin": False},
        headers=_auth(admin["token"]),
    )
    assert resp.json()["is_admin"] is False


@pytest.mark.req("AUTH-17", "AUTH-21")
async def test_admin_password_reset_ends_old_sessions(env):
    client = env["client"]
    admin, member = await _admin_and_member(client)

    resp = await client.post(
        f"/api/v1/admin/users/{member['user_id']}/password",
        json={"password": "reset-password"},
        headers=_auth(admin["token"]),
    )
    assert resp.status_code == 204

    assert (await client.get("/api/v1/auth/me", headers=_auth(member["token"]))).status_code == 401
    old = await client.post(
        "/api/v1/auth/login", json={"email": "member@example.com", "password": PASSWORD}
    )
    assert old.status_code == 401
    new = await client.post(
        "/api/v1/auth/login",
        json={"email": "member@example.com", "password": "reset-password"},
    )
    assert new.status_code == 200
    me = await client.get("/api/v1/auth/me", headers=_auth(new.json()["token"]))
    assert me.status_code == 200


@pytest.mark.req("AUTH-17")
async def test_unknown_or_guest_user_is_404(env):
    client = env["client"]
    admin, _ = await _admin_and_member(client)
    async with env["sessions"]() as db:
        guest = User(is_anonymous=True, display_name="guest")
        db.add(guest)
        await db.commit()
        guest_id = guest.id

    for user_id in ("missing", guest_id):
        resp = await client.patch(
            f"/api/v1/admin/users/{user_id}",
            json={"disabled": True},
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 404
    rows = (await client.get("/api/v1/admin/users", headers=_auth(admin["token"]))).json()
    assert guest_id not in {r["id"] for r in rows}


# ── AUTH-18: suspension ──────────────────────────────────────────────


@pytest.mark.req("AUTH-18")
async def test_suspended_account_is_locked_out_until_restored(env):
    client = env["client"]
    admin, member = await _admin_and_member(client)

    key = await client.post(
        "/api/v1/auth/api-keys",
        json={"name": "cli", "scope": "read"},
        headers=_auth(member["token"]),
    )
    assert key.status_code == 201, key.text
    api_key = key.json()["key"]

    resp = await client.patch(
        f"/api/v1/admin/users/{member['user_id']}",
        json={"disabled": True},
        headers=_auth(admin["token"]),
    )
    assert resp.status_code == 200 and resp.json()["disabled"] is True

    assert (await client.get("/api/v1/auth/me", headers=_auth(member["token"]))).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=_auth(api_key))).status_code == 401
    login = await client.post(
        "/api/v1/auth/login", json={"email": "member@example.com", "password": PASSWORD}
    )
    assert login.status_code == 403
    assert login.json()["detail"] == "Account disabled"

    await client.patch(
        f"/api/v1/admin/users/{member['user_id']}",
        json={"disabled": False},
        headers=_auth(admin["token"]),
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": "member@example.com", "password": PASSWORD}
    )
    assert login.status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=_auth(api_key))).status_code == 200


@pytest.mark.req("AUTH-18")
async def test_login_records_last_login(env):
    client = env["client"]
    admin, _ = await _admin_and_member(client)
    await client.post(
        "/api/v1/auth/login", json={"email": "member@example.com", "password": PASSWORD}
    )
    rows = (await client.get("/api/v1/admin/users", headers=_auth(admin["token"]))).json()
    member = next(r for r in rows if r["email"] == "member@example.com")
    assert member["last_login_at"] is not None


# ── AUTH-19: guards ──────────────────────────────────────────────────


@pytest.mark.req("AUTH-19")
async def test_admin_cannot_suspend_or_demote_self(env):
    client = env["client"]
    admin, member = await _admin_and_member(client)
    # A second admin exists, so only the self-guard can refuse.
    await client.patch(
        f"/api/v1/admin/users/{member['user_id']}",
        json={"is_admin": True},
        headers=_auth(admin["token"]),
    )
    for body in ({"disabled": True}, {"is_admin": False}):
        resp = await client.patch(
            f"/api/v1/admin/users/{admin['user_id']}",
            json=body,
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 409


@pytest.mark.req("AUTH-19")
async def test_admin_can_demote_and_suspend_another_admin(env):
    client = env["client"]
    admin, member = await _admin_and_member(client)
    await client.patch(
        f"/api/v1/admin/users/{member['user_id']}",
        json={"is_admin": True},
        headers=_auth(admin["token"]),
    )
    resp = await client.patch(
        f"/api/v1/admin/users/{member['user_id']}",
        json={"is_admin": False, "disabled": True},
        headers=_auth(admin["token"]),
    )
    assert resp.status_code == 200
    assert resp.json()["is_admin"] is False and resp.json()["disabled"] is True


# ── AUTH-20: sign-up policy ──────────────────────────────────────────


@pytest.mark.req("AUTH-20")
async def test_closed_registration_blocks_sign_up_but_not_admin_create(env):
    client = env["client"]
    admin = await _register(client, "admin@example.com")

    resp = await client.put(
        "/api/v1/admin/account-settings",
        json={"registration_open": False},
        headers=_auth(admin["token"]),
    )
    assert resp.status_code == 200 and resp.json() == {"registration_open": False}

    setup = await client.get("/api/v1/auth/setup")
    assert setup.json() == {"needs_first_account": False, "registration_open": False}

    resp = await client.post(
        "/api/v1/auth/register", json={"email": "late@example.com", "password": PASSWORD}
    )
    assert resp.status_code == 403

    resp = await client.post(
        "/api/v1/admin/users",
        json={"email": "late@example.com", "password": PASSWORD},
        headers=_auth(admin["token"]),
    )
    assert resp.status_code == 201

    resp = await client.put(
        "/api/v1/admin/account-settings",
        json={"registration_open": True},
        headers=_auth(admin["token"]),
    )
    assert resp.json() == {"registration_open": True}
    assert (await client.get("/api/v1/auth/setup")).json()["registration_open"] is True


@pytest.mark.req("AUTH-20")
async def test_first_account_registers_even_when_closed(env):
    from anygarden.auth.settings import set_registration_open

    async with env["sessions"]() as db:
        await set_registration_open(db, False)
        await db.commit()
    client = env["client"]
    setup = await client.get("/api/v1/auth/setup")
    assert setup.json() == {"needs_first_account": True, "registration_open": True}
    first = await _register(client, "admin@example.com")
    me = await client.get("/api/v1/auth/me", headers=_auth(first["token"]))
    assert me.json()["is_admin"] is True


# ── AUTH-21: password change ─────────────────────────────────────────


@pytest.mark.req("AUTH-21")
async def test_user_changes_password_and_other_sessions_end(env):
    client = env["client"]
    _, member = await _admin_and_member(client)
    other = await client.post(
        "/api/v1/auth/login", json={"email": "member@example.com", "password": PASSWORD}
    )
    other_token = other.json()["token"]

    wrong = await client.post(
        "/api/v1/auth/password",
        json={"current_password": "not-it", "new_password": "second-password"},
        headers=_auth(member["token"]),
    )
    assert wrong.status_code == 400

    resp = await client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "second-password"},
        headers=_auth(member["token"]),
    )
    assert resp.status_code == 200
    fresh = resp.json()["token"]

    assert (await client.get("/api/v1/auth/me", headers=_auth(other_token))).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=_auth(fresh))).status_code == 200
    login = await client.post(
        "/api/v1/auth/login",
        json={"email": "member@example.com", "password": "second-password"},
    )
    assert login.status_code == 200


@pytest.mark.req("AUTH-21")
async def test_session_issued_before_change_is_rejected(env):
    """A token minted a moment before the change is cut off."""
    client = env["client"]
    _, member = await _admin_and_member(client)
    async with env["sessions"]() as db:
        user = await db.get(User, member["user_id"])
        user.credentials_changed_at = datetime.now(timezone.utc) + timedelta(seconds=2)
        await db.commit()
    assert (await client.get("/api/v1/auth/me", headers=_auth(member["token"]))).status_code == 401


@pytest.mark.req("AUTH-21")
async def test_password_rules(env):
    client = env["client"]
    short = await client.post(
        "/api/v1/auth/register", json={"email": "a@example.com", "password": "short"}
    )
    assert short.status_code == 422
    bad_email = await client.post(
        "/api/v1/auth/register", json={"email": "not-an-email", "password": PASSWORD}
    )
    assert bad_email.status_code == 422

    member = await _register(client, "a@example.com")
    resp = await client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "short"},
        headers=_auth(member["token"]),
    )
    assert resp.status_code == 422


@pytest.mark.req("AUTH-21")
async def test_api_key_cannot_change_password(env):
    client = env["client"]
    _, member = await _admin_and_member(client)
    key = await client.post(
        "/api/v1/auth/api-keys",
        json={"name": "cli", "scope": "write"},
        headers=_auth(member["token"]),
    )
    resp = await client.post(
        "/api/v1/auth/password",
        json={"current_password": PASSWORD, "new_password": "second-password"},
        headers=_auth(key.json()["key"]),
    )
    assert resp.status_code == 403
