"""Tests for /api/v1/admin/mcp-templates and /mcp-instances endpoints (#124).

Covers:
- Template CRUD with builtin immutability and name conflicts
- Instance attach with engine / env validation
- Detach / patch enabled
- Generation bump gating (only when the change affects the next spawn)
- Non-admin rejection
"""

from __future__ import annotations

import secrets as pysecrets

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, Base, User
from anygarden.mcp_templates.encryption import MCPSecrets
from anygarden.mcp_templates.service import MCPTemplateService
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus


@pytest_asyncio.fixture()
async def mcp_env():
    """App wired with a Fernet key, an admin user, and two agents."""
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=pysecrets.token_urlsafe(32),
        log_level="DEBUG",
        mcp_secrets_key=Fernet.generate_key().decode("ascii"),
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    mcp_secrets = MCPSecrets.from_config_key(config.mcp_secrets_key)
    service = MCPTemplateService(factory, secrets=mcp_secrets)
    lifecycle = AgentLifecycle(
        db_factory=factory, machine_bus=bus,
        mcp_template_service=service,
    )

    async with factory() as db:
        admin = User(email="admin@test.com", password_hash="x", is_admin=True)
        regular = User(email="reg@test.com", password_hash="x", is_admin=False)
        claude_agent = Agent(
            engine="claude-code", name="claude1",
            desired_state="idle", actual_state="idle",
        )
        codex_agent = Agent(
            engine="codex", name="codex1",
            desired_state="idle", actual_state="idle",
        )
        echo_agent = Agent(
            engine="echo", name="echo1",
            desired_state="idle", actual_state="idle",
        )
        db.add_all([admin, regular, claude_agent, codex_agent, echo_agent])
        await db.flush()
        await db.commit()

        admin_token = create_user_token(
            admin.id, admin.email, admin.is_admin, secret=config.jwt_secret,
        )
        regular_token = create_user_token(
            regular.id, regular.email, regular.is_admin, secret=config.jwt_secret,
        )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = factory
        app.state.machine_bus = bus
        app.state.mcp_template_service = service
        app.state.agent_lifecycle = lifecycle

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield {
                "client": client,
                "token": admin_token,
                "regular_token": regular_token,
                "factory": factory,
                "service": service,
                "claude_agent": claude_agent,
                "codex_agent": codex_agent,
                "echo_agent": echo_agent,
            }

    await engine.dispose()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create_template(client: AsyncClient, token: str, **overrides) -> dict:
    body = {
        "name": "my-custom",
        "display_name": "My Custom",
        "description": "test",
        "icon": None,
        "config_per_engine": {
            "claude-code": {
                "command": "npx",
                "args": ["-y", "custom-server"],
                "env": {"API_KEY": "${API_KEY}"},
            },
        },
        "required_env_vars": ["API_KEY"],
        "supported_engines": ["claude-code"],
    }
    body.update(overrides)
    resp = await client.post(
        "/api/v1/admin/mcp-templates", json=body, headers=_auth(token),
    )
    return {"status": resp.status_code, "body": resp.json()}


class TestTemplateCRUD:
    @pytest.mark.asyncio
    async def test_create_template_and_list(self, mcp_env):
        client, token = mcp_env["client"], mcp_env["token"]
        created = await _create_template(client, token)
        assert created["status"] == 201, created["body"]
        assert created["body"]["name"] == "my-custom"
        assert created["body"]["source"] == "custom"
        assert created["body"]["instance_count"] == 0

        resp = await client.get(
            "/api/v1/admin/mcp-templates", headers=_auth(token),
        )
        assert resp.status_code == 200
        names = {t["name"] for t in resp.json()}
        assert "my-custom" in names

    @pytest.mark.asyncio
    async def test_create_requires_admin(self, mcp_env):
        client, token = mcp_env["client"], mcp_env["regular_token"]
        resp = await client.post(
            "/api/v1/admin/mcp-templates",
            json={
                "name": "x", "display_name": "x",
                "config_per_engine": {"claude-code": {}},
                "required_env_vars": [],
                "supported_engines": ["claude-code"],
            },
            headers=_auth(token),
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_duplicate_name_rejected(self, mcp_env):
        client, token = mcp_env["client"], mcp_env["token"]
        await _create_template(client, token)
        dup = await _create_template(client, token)
        assert dup["status"] == 409

    @pytest.mark.asyncio
    async def test_config_engines_must_match_supported(self, mcp_env):
        client, token = mcp_env["client"], mcp_env["token"]
        res = await _create_template(
            client, token,
            config_per_engine={
                "claude-code": {"command": "x"},
                "codex": {"command": "y"},
            },
            supported_engines=["claude-code"],  # codex missing
        )
        assert res["status"] == 422

    @pytest.mark.asyncio
    async def test_builtin_templates_seeded_by_service(self, mcp_env):
        client, token = mcp_env["client"], mcp_env["token"]
        service = mcp_env["service"]
        await service.seed_builtins()

        resp = await client.get(
            "/api/v1/admin/mcp-templates?source=builtin",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        names = {t["name"] for t in resp.json()}
        # Plan §3 lists these five as Phase 1 builtins.
        assert {"github", "slack", "notion", "linear", "filesystem"} <= names

    @pytest.mark.asyncio
    async def test_builtin_cannot_be_updated_or_deleted(self, mcp_env):
        client, token = mcp_env["client"], mcp_env["token"]
        await mcp_env["service"].seed_builtins()

        rows = (await client.get(
            "/api/v1/admin/mcp-templates?source=builtin",
            headers=_auth(token),
        )).json()
        github = next(t for t in rows if t["name"] == "github")

        upd = await client.put(
            f"/api/v1/admin/mcp-templates/{github['id']}",
            json={"display_name": "Hacked"},
            headers=_auth(token),
        )
        assert upd.status_code == 403

        dl = await client.delete(
            f"/api/v1/admin/mcp-templates/{github['id']}",
            headers=_auth(token),
        )
        assert dl.status_code == 403

    @pytest.mark.asyncio
    async def test_custom_can_be_updated(self, mcp_env):
        client, token = mcp_env["client"], mcp_env["token"]
        created = await _create_template(client, token)
        tpl_id = created["body"]["id"]

        resp = await client.put(
            f"/api/v1/admin/mcp-templates/{tpl_id}",
            json={"display_name": "Renamed"},
            headers=_auth(token),
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "Renamed"

    @pytest.mark.asyncio
    async def test_custom_delete_blocked_when_in_use(self, mcp_env):
        client, token = mcp_env["client"], mcp_env["token"]
        agent = mcp_env["claude_agent"]

        created = await _create_template(client, token)
        tpl_id = created["body"]["id"]

        att = await client.post(
            f"/api/v1/admin/agents/{agent.id}/mcp-instances",
            json={
                "template_id": tpl_id,
                "env_values": {"API_KEY": "value"},
            },
            headers=_auth(token),
        )
        assert att.status_code == 201, att.text

        dl = await client.delete(
            f"/api/v1/admin/mcp-templates/{tpl_id}",
            headers=_auth(token),
        )
        assert dl.status_code == 409


class TestInstanceAttach:
    @pytest.mark.asyncio
    async def test_attach_validates_engine_compat(self, mcp_env):
        client, token = mcp_env["client"], mcp_env["token"]
        codex_agent = mcp_env["codex_agent"]

        created = await _create_template(
            client, token,
            supported_engines=["claude-code"],
            config_per_engine={"claude-code": {"command": "x", "args": [], "env": {}}},
        )
        tpl_id = created["body"]["id"]

        resp = await client.post(
            f"/api/v1/admin/agents/{codex_agent.id}/mcp-instances",
            json={"template_id": tpl_id, "env_values": {"API_KEY": "v"}},
            headers=_auth(token),
        )
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_attach_requires_all_required_env(self, mcp_env):
        client, token = mcp_env["client"], mcp_env["token"]
        agent = mcp_env["claude_agent"]
        created = await _create_template(client, token)
        tpl_id = created["body"]["id"]

        resp = await client.post(
            f"/api/v1/admin/agents/{agent.id}/mcp-instances",
            json={"template_id": tpl_id, "env_values": {}},
            headers=_auth(token),
        )
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_attach_creates_bump_detach_bump(self, mcp_env):
        client, token = mcp_env["client"], mcp_env["token"]
        agent = mcp_env["claude_agent"]
        factory = mcp_env["factory"]

        async def gen():
            async with factory() as db:
                a = (await db.execute(select(Agent).where(Agent.id == agent.id))).scalar_one()
                return a.generation

        created = await _create_template(client, token)
        tpl_id = created["body"]["id"]

        before = await gen()
        att = await client.post(
            f"/api/v1/admin/agents/{agent.id}/mcp-instances",
            json={"template_id": tpl_id, "env_values": {"API_KEY": "v"}},
            headers=_auth(token),
        )
        assert att.status_code == 201
        instance_id = att.json()["id"]
        assert await gen() == before + 1

        # Re-attach same credentials — no bump.
        same = await client.post(
            f"/api/v1/admin/agents/{agent.id}/mcp-instances",
            json={"template_id": tpl_id, "env_values": {"API_KEY": "v"}},
            headers=_auth(token),
        )
        assert same.status_code == 201
        assert await gen() == before + 1

        # Re-attach with different credentials — bump.
        new = await client.post(
            f"/api/v1/admin/agents/{agent.id}/mcp-instances",
            json={"template_id": tpl_id, "env_values": {"API_KEY": "v2"}},
            headers=_auth(token),
        )
        assert new.status_code == 201
        assert await gen() == before + 2

        # Detach — bump.
        det = await client.delete(
            f"/api/v1/admin/agents/{agent.id}/mcp-instances/{instance_id}",
            headers=_auth(token),
        )
        assert det.status_code == 204
        assert await gen() == before + 3

        # Detach noop — no bump.
        det2 = await client.delete(
            f"/api/v1/admin/agents/{agent.id}/mcp-instances/{instance_id}",
            headers=_auth(token),
        )
        assert det2.status_code == 204
        assert await gen() == before + 3

    @pytest.mark.asyncio
    async def test_patch_enabled_bumps_only_on_change(self, mcp_env):
        client, token = mcp_env["client"], mcp_env["token"]
        agent = mcp_env["claude_agent"]
        factory = mcp_env["factory"]

        created = await _create_template(client, token)
        tpl_id = created["body"]["id"]
        att = await client.post(
            f"/api/v1/admin/agents/{agent.id}/mcp-instances",
            json={"template_id": tpl_id, "env_values": {"API_KEY": "v"}},
            headers=_auth(token),
        )
        instance_id = att.json()["id"]

        async def gen():
            async with factory() as db:
                a = (await db.execute(select(Agent).where(Agent.id == agent.id))).scalar_one()
                return a.generation

        before = await gen()
        # Same value — no bump.
        same = await client.patch(
            f"/api/v1/admin/agents/{agent.id}/mcp-instances/{instance_id}",
            json={"enabled": True},
            headers=_auth(token),
        )
        assert same.status_code == 200
        assert await gen() == before

        # Disable — bump.
        off = await client.patch(
            f"/api/v1/admin/agents/{agent.id}/mcp-instances/{instance_id}",
            json={"enabled": False},
            headers=_auth(token),
        )
        assert off.status_code == 200
        assert await gen() == before + 1

    @pytest.mark.asyncio
    async def test_list_instances_hides_plaintext_credentials(self, mcp_env):
        """The decrypted values must never round-trip back out of the API —
        the whole point of encrypting them is that only the engine ever
        sees the plaintext at spawn time."""
        client, token = mcp_env["client"], mcp_env["token"]
        agent = mcp_env["claude_agent"]
        created = await _create_template(client, token)
        tpl_id = created["body"]["id"]
        await client.post(
            f"/api/v1/admin/agents/{agent.id}/mcp-instances",
            json={"template_id": tpl_id, "env_values": {"API_KEY": "sekret"}},
            headers=_auth(token),
        )

        resp = await client.get(
            f"/api/v1/admin/agents/{agent.id}/mcp-instances",
            headers=_auth(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        # Schema must not echo raw values back. Dump to JSON text and
        # assert the secret literal doesn't appear anywhere.
        import json as _json
        assert "sekret" not in _json.dumps(body)
        assert body[0]["has_credentials"] is True
