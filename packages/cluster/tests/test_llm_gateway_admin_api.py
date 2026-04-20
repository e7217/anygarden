"""Tests for the ``/api/v1/llm-gateway`` admin router (#197 Phase 3).

Covers the essential contract: admin-only auth gate, model CRUD
round-trip, secret encrypt/mask/delete, status read, apply → restart
side effect, and usage aggregation. The supervisor / httpx client are
fakes — the admin endpoints drive them, they don't drive a real
subprocess.
"""

from __future__ import annotations

import secrets as _stdlib_secrets
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient, MockTransport, Response
from sqlalchemy import select

from doorae.app import create_app
from doorae.auth.jwt import create_user_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Base,
    LLMGatewayModel,
    LLMGatewaySecret,
    LLMGatewayUsage,
    User,
)
from doorae.mcp_templates.encryption import MCPSecrets


class _FakeSupervisor:
    """In-memory stand-in — records restart() calls for assertions."""

    def __init__(self) -> None:
        from doorae.llm_gateway.supervisor import GatewayState, GatewayStatus

        self._state = GatewayState.RUNNING
        self._status = GatewayStatus(
            state=GatewayState.RUNNING,
            pid=9999,
            port=4001,
            crash_count=0,
        )
        self.restart_count = 0

    @property
    def state(self):
        return self._state

    @property
    def master_key(self) -> str:
        return "sk-fake-master"

    @property
    def port(self) -> int:
        return 4001

    def status(self):
        return self._status

    async def restart(self) -> None:
        self.restart_count += 1
        # Keep state stable — tests assert restart was called, not the
        # state machine's own transitions (covered by supervisor tests).


@pytest_asyncio.fixture()
async def env() -> AsyncIterator[dict[str, Any]]:
    fernet_key = Fernet.generate_key().decode("ascii")
    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=_stdlib_secrets.token_urlsafe(32),
        log_level="DEBUG",
        mcp_secrets_key=fernet_key,
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
        user_id=admin_id, email="admin@test", is_admin=True,
        secret=config.jwt_secret,
    )
    user_jwt = create_user_token(
        user_id=regular_id, email="user@test", is_admin=False,
        secret=config.jwt_secret,
    )

    app = create_app(config)
    app.state.session_factory = factory
    app.state.engine = engine

    # Wire up mcp_template_service with MCPSecrets so /secrets
    # endpoints can encrypt/decrypt. Mirror app.py's lifespan pattern.
    class _StubMCPTemplateService:
        def __init__(self, secrets_obj):
            self._secrets = secrets_obj

    app.state.mcp_template_service = _StubMCPTemplateService(
        MCPSecrets.from_config_key(fernet_key, dev_mode=False)
    )
    app.state.llm_gateway_supervisor = _FakeSupervisor()

    yield {
        "app": app,
        "factory": factory,
        "admin_jwt": admin_jwt,
        "user_jwt": user_jwt,
        "supervisor": app.state.llm_gateway_supervisor,
    }

    await engine.dispose()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ── Admin-only gate ────────────────────────────────────────────────────


async def test_non_admin_user_is_rejected(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        for method, path in (
            ("get", "/api/v1/llm-gateway/models"),
            ("post", "/api/v1/llm-gateway/models"),
            ("get", "/api/v1/llm-gateway/status"),
            ("post", "/api/v1/llm-gateway/apply"),
        ):
            resp = await getattr(c, method)(path, headers=_auth(env["user_jwt"]))
            assert resp.status_code == 403, f"{method.upper()} {path}: {resp.text}"


async def test_unauthenticated_is_rejected(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.get("/api/v1/llm-gateway/models")
        assert resp.status_code in (401, 403)


# ── Models CRUD ────────────────────────────────────────────────────────


async def test_model_crud_round_trip(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        # Create
        create_resp = await c.post(
            "/api/v1/llm-gateway/models",
            headers=_auth(env["admin_jwt"]),
            json={
                "model_name": "claude-sonnet-4-6",
                "provider": "anthropic",
                "upstream_model": "anthropic/claude-sonnet-4-6",
                "api_key_ref": "ANTHROPIC_API_KEY",
            },
        )
        assert create_resp.status_code == 201
        model_id = create_resp.json()["id"]

        # List
        list_resp = await c.get(
            "/api/v1/llm-gateway/models", headers=_auth(env["admin_jwt"])
        )
        assert list_resp.status_code == 200
        rows = list_resp.json()
        assert len(rows) == 1
        assert rows[0]["model_name"] == "claude-sonnet-4-6"

        # Update
        patch_resp = await c.patch(
            f"/api/v1/llm-gateway/models/{model_id}",
            headers=_auth(env["admin_jwt"]),
            json={"enabled": False},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["enabled"] is False

        # Delete
        del_resp = await c.delete(
            f"/api/v1/llm-gateway/models/{model_id}",
            headers=_auth(env["admin_jwt"]),
        )
        assert del_resp.status_code == 204

        # Gone
        list_after = await c.get(
            "/api/v1/llm-gateway/models", headers=_auth(env["admin_jwt"])
        )
        assert list_after.json() == []


async def test_duplicate_model_name_returns_409(env) -> None:
    body = {
        "model_name": "dupe",
        "provider": "anthropic",
        "upstream_model": "anthropic/x",
        "api_key_ref": "ANTHROPIC_API_KEY",
    }
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        first = await c.post(
            "/api/v1/llm-gateway/models", headers=_auth(env["admin_jwt"]),
            json=body,
        )
        assert first.status_code == 201
        second = await c.post(
            "/api/v1/llm-gateway/models", headers=_auth(env["admin_jwt"]),
            json=body,
        )
        assert second.status_code == 409


# ── Secrets ────────────────────────────────────────────────────────────


async def test_secret_post_encrypts_and_list_masks(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/llm-gateway/secrets",
            headers=_auth(env["admin_jwt"]),
            json={
                "env_var_name": "ANTHROPIC_API_KEY",
                "value": "sk-ant-api03-AbCdEfGh1234",
            },
        )
        assert resp.status_code == 201
        created = resp.json()
        # Preview must not equal plaintext.
        assert created["value_preview"] != "sk-ant-api03-AbCdEfGh1234"
        # Still a recognisable hint.
        assert created["value_preview"].startswith("sk-ant-api03")
        assert created["value_preview"].endswith("1234")

        # List returns masked value, never the ciphertext.
        list_resp = await c.get(
            "/api/v1/llm-gateway/secrets", headers=_auth(env["admin_jwt"])
        )
        assert list_resp.status_code == 200
        entry = list_resp.json()[0]
        assert entry["env_var_name"] == "ANTHROPIC_API_KEY"
        assert "sk-ant-api03" in entry["value_preview"]
        assert "AbCdEfGh" not in entry["value_preview"]  # middle chars hidden

        # DB row has ciphertext, not plaintext.
        async with env["factory"]() as db:
            row = await db.get(LLMGatewaySecret, "ANTHROPIC_API_KEY")
            assert row is not None
            assert b"sk-ant-api03" not in row.encrypted_value


async def test_secret_delete(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        await c.post(
            "/api/v1/llm-gateway/secrets",
            headers=_auth(env["admin_jwt"]),
            json={"env_var_name": "OPENAI_API_KEY", "value": "sk-proj-xxx"},
        )
        resp = await c.delete(
            "/api/v1/llm-gateway/secrets/OPENAI_API_KEY",
            headers=_auth(env["admin_jwt"]),
        )
        assert resp.status_code == 204

        async with env["factory"]() as db:
            row = await db.get(LLMGatewaySecret, "OPENAI_API_KEY")
            assert row is None


# ── Runtime endpoints ──────────────────────────────────────────────────


async def test_status_reflects_supervisor_snapshot(env) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.get(
            "/api/v1/llm-gateway/status", headers=_auth(env["admin_jwt"])
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "running"
        assert body["pid"] == 9999
        assert body["port"] == 4001


async def test_apply_triggers_supervisor_restart(env) -> None:
    sup = env["supervisor"]
    before = sup.restart_count

    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/llm-gateway/apply", headers=_auth(env["admin_jwt"])
        )

    assert resp.status_code == 200
    assert sup.restart_count == before + 1


async def test_restart_triggers_supervisor_restart(env) -> None:
    sup = env["supervisor"]
    before = sup.restart_count

    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.post(
            "/api/v1/llm-gateway/restart", headers=_auth(env["admin_jwt"])
        )

    assert resp.status_code == 200
    assert sup.restart_count == before + 1


async def test_status_503_when_supervisor_absent(env) -> None:
    # Remove the supervisor to simulate flag off.
    env["app"].state.llm_gateway_supervisor = None

    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.get(
            "/api/v1/llm-gateway/status", headers=_auth(env["admin_jwt"])
        )
    assert resp.status_code == 503


# ── Usage aggregation ────────────────────────────────────────────────


async def test_usage_aggregates_by_model_and_agent(env) -> None:
    from doorae.db.models import Agent

    now = datetime.now(timezone.utc)
    # Three requests: two for claude from agent-A, one for gpt from agent-B.
    # Create real Agent rows first so the FK on agent_id resolves.
    async with env["factory"]() as db:
        agent_a = Agent(name="A", engine="claude-code")
        agent_b = Agent(name="B", engine="codex")
        db.add_all([agent_a, agent_b])
        await db.flush()
        a_id, b_id = agent_a.id, agent_b.id

        rows = [
            LLMGatewayUsage(
                timestamp=now - timedelta(minutes=5),
                identity_kind="agent", identity_id=a_id,
                agent_id=a_id,
                model_name="claude-sonnet-4-6",
                prompt_tokens=100, completion_tokens=50,
                duration_ms=800, status_code=200,
            ),
            LLMGatewayUsage(
                timestamp=now - timedelta(minutes=4),
                identity_kind="agent", identity_id=a_id,
                agent_id=a_id,
                model_name="claude-sonnet-4-6",
                prompt_tokens=200, completion_tokens=80,
                duration_ms=900, status_code=200,
            ),
            LLMGatewayUsage(
                timestamp=now - timedelta(minutes=3),
                identity_kind="agent", identity_id=b_id,
                agent_id=b_id,
                model_name="gpt-5.4",
                prompt_tokens=75, completion_tokens=20,
                duration_ms=500, status_code=200,
            ),
            # 2-day-old row must be outside the default 24h window.
            LLMGatewayUsage(
                timestamp=now - timedelta(days=2),
                identity_kind="agent", identity_id=a_id,
                agent_id=a_id,
                model_name="claude-sonnet-4-6",
                prompt_tokens=1, completion_tokens=1,
                duration_ms=10, status_code=200,
            ),
        ]
        db.add_all(rows)
        await db.commit()

    async with AsyncClient(
        transport=ASGITransport(app=env["app"]), base_url="http://test"
    ) as c:
        resp = await c.get(
            "/api/v1/llm-gateway/usage", headers=_auth(env["admin_jwt"])
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["window_hours"] == 24
    assert body["total_requests"] == 3  # old row excluded

    by_model = {row["key"]: row for row in body["by_model"]}
    assert by_model["claude-sonnet-4-6"]["request_count"] == 2
    assert by_model["claude-sonnet-4-6"]["prompt_tokens"] == 300
    assert by_model["claude-sonnet-4-6"]["completion_tokens"] == 130
    assert by_model["gpt-5.4"]["request_count"] == 1

    by_agent = {row["key"]: row for row in body["by_agent"]}
    assert by_agent[a_id]["request_count"] == 2
    assert by_agent[b_id]["request_count"] == 1


# ── /models/{id}/test ping ────────────────────────────────────────────


async def test_test_model_endpoint_pings_upstream(env) -> None:
    """The test endpoint hits the live gateway path end-to-end.

    We install a MockTransport so the call lands on our handler
    instead of a real litellm process; the assertions confirm the
    proxy path swaps the master key and hits the right path.
    """
    app = env["app"]
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> Response:
        captured["path"] = request.url.path
        captured["auth"] = request.headers.get("authorization")
        return Response(200, json={"ok": True, "usage": {"input_tokens": 1, "output_tokens": 1}})

    app.state.llm_gateway_client = httpx.AsyncClient(
        transport=MockTransport(handler),
        base_url="http://127.0.0.1:4001",
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        # Register a model first
        create = await c.post(
            "/api/v1/llm-gateway/models",
            headers=_auth(env["admin_jwt"]),
            json={
                "model_name": "test-model",
                "provider": "anthropic",
                "upstream_model": "anthropic/test-model",
                "api_key_ref": "ANTHROPIC_API_KEY",
            },
        )
        model_id = create.json()["id"]

        resp = await c.post(
            f"/api/v1/llm-gateway/models/{model_id}/test",
            headers=_auth(env["admin_jwt"]),
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["status_code"] == 200
    assert captured["path"] == "/v1/messages"
    assert captured["auth"] == "Bearer sk-fake-master"


# Avoid unused-import noise from the test scaffolding.
_ = (pytest, select, LLMGatewayModel)  # noqa: F841
