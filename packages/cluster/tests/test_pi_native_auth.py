"""Per-agent native Pi credentials never use the service user's login."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

from cryptography.fernet import Fernet
from sqlalchemy import select

from anygarden.db.models import Agent, Machine, PiNativeCredential
from anygarden.engines.pi_auth import build_pi_native_engine_secrets
from anygarden.mcp_templates.encryption import MCPSecrets
from .test_agents_api import agents_env


async def test_native_key_is_private_scoped_and_rotatable(agents_env):
    e = agents_env
    secrets = MCPSecrets(Fernet.generate_key())
    service = SimpleNamespace(_secrets=secrets)
    e["app"].state.mcp_template_service = service
    e["app"].state.agent_lifecycle = SimpleNamespace(bump_generation=AsyncMock())
    async with e["factory"]() as db:
        agent = Agent(
            name="pi-zai", engine="pi-cli", provider="zai", model="glm-5.3-flash"
        )
        other = Agent(name="pi-other", engine="pi-cli", provider="zai")
        db.add_all([agent, other])
        await db.commit()
        aid, other_id = agent.id, other.id
    path = f"/api/v1/agents/{aid}/pi-auth"
    headers = {"Authorization": f"Bearer {e['token']}"}
    missing = await e["client"].get(path, headers=headers)
    assert missing.json()["configured"] is False
    refused = await e["client"].post(f"/api/v1/agents/{aid}/start", headers=headers)
    assert refused.status_code == 422 and "Pi provider credential" in refused.text
    created = await e["client"].put(path, headers=headers, json={"value": "secret-one"})
    assert created.status_code == 200
    assert created.json() == {"configured": True, "provider": "zai", "revision": 1}
    assert "secret-one" not in created.text
    async with e["factory"]() as db:
        row = (
            await db.execute(
                select(PiNativeCredential).where(PiNativeCredential.agent_id == aid)
            )
        ).scalar_one()
        assert b"secret-one" not in row.encrypted_value
        agent = await db.get(Agent, aid)
        payload = await build_pi_native_engine_secrets(db, agent, secrets)
        assert payload["AG_PI_NATIVE_AUTH_KEY"] == "secret-one"
        assert "secret-one" not in payload["AG_PI_NATIVE_AUTH_CONFIG"]
        machine = await db.get(Machine, e["machine"].id)
        machine.control_capabilities = ["pi_native_auth_v1"]
        agent.placed_on_machine_id = machine.id
        e["lifecycle"]._mcp_template_service = service
        frame = await e["lifecycle"]._build_sync_frame(db, agent, [])
        assert frame["pi_auth_configured"] is True
        assert frame["engine_secrets"]["AG_PI_NATIVE_AUTH_KEY"] == "secret-one"
        other = await db.get(Agent, other_id)
        try:
            await build_pi_native_engine_secrets(db, other, secrets)
        except ValueError:
            pass
        else:
            raise AssertionError("another agent inherited Pi auth")
    rotated = await e["client"].put(path, headers=headers, json={"value": "secret-two"})
    assert rotated.json()["revision"] == 2
    assert "secret-two" not in rotated.text
    async with e["factory"]() as db:
        agent = await db.get(Agent, aid)
        try:
            await build_pi_native_engine_secrets(
                db, agent, MCPSecrets(Fernet.generate_key())
            )
        except ValueError:
            pass
        else:
            raise AssertionError("wrong encryption key accepted")
        agent.provider = "other-provider"
        await db.commit()
    stale = await e["client"].get(path, headers=headers)
    assert stale.json() == {"configured": False, "provider": "zai", "revision": 2}
    reselected = await e["client"].put(
        path, headers=headers, json={"value": "secret-three"}
    )
    assert reselected.json() == {
        "configured": True,
        "provider": "other-provider",
        "revision": 3,
    }
    assert e["app"].state.agent_lifecycle.bump_generation.await_count == 3
    deleted = await e["client"].delete(path, headers=headers)
    assert deleted.status_code == 204
    assert (await e["client"].get(path, headers=headers)).json()["configured"] is False


async def test_native_auth_is_admin_only(agents_env):
    e = agents_env
    async with e["factory"]() as db:
        agent = Agent(name="pi-zai", engine="pi-cli", provider="zai")
        db.add(agent)
        await db.commit()
        aid = agent.id
    path = f"/api/v1/agents/{aid}/pi-auth"
    headers = {"Authorization": f"Bearer {e['regular_token']}"}
    assert (await e["client"].get(path, headers=headers)).status_code == 403
    assert (
        await e["client"].put(path, headers=headers, json={"value": "key"})
    ).status_code == 403
