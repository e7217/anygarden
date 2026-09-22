"""Direct endpoint policy, encryption and local dispatch regressions."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet

from anygarden.db.models import Agent, EngineCredential, Machine, MachineEngine
from anygarden.engines.endpoints import build_direct_engine_secrets
from anygarden.mcp_templates.encryption import MCPSecrets
from .test_agents_api import agents_env


@pytest_asyncio.fixture
async def endpoint_env(agents_env):
    env = agents_env
    secret_service = MCPSecrets(Fernet.generate_key())
    service = SimpleNamespace(
        _secrets=secret_service, list_instances_for_agent=AsyncMock(return_value=[])
    )
    env["app"].state.mcp_template_service = service
    env["lifecycle"]._mcp_template_service = service
    env["app"].state.agent_lifecycle = SimpleNamespace(bump_generation=AsyncMock())
    async with env["factory"]() as db:
        machine = await db.get(Machine, env["machine"].id)
        machine.control_capabilities = ["direct_endpoint_v1"]
        db.add_all(
            [
                MachineEngine(machine_id=machine.id, engine="pi-cli"),
                MachineEngine(machine_id=machine.id, engine="codex-cli"),
            ]
        )
        agent = Agent(
            name="local-model",
            engine="pi-cli",
            provider="local",
            model="model-a",
            placed_on_machine_id=machine.id,
            desired_state="running",
        )
        other = Agent(name="other", engine="pi-cli", provider="local")
        db.add_all([agent, other])
        await db.commit()
        env.update(agent_id=agent.id, other_id=other.id, secret_service=secret_service)
    env["headers"] = {"Authorization": f"Bearer {env['token']}"}
    env["path"] = f"/api/v1/agents/{env['agent_id']}/endpoint"
    yield env


def config(**kwargs):
    return dict(
        provider="local",
        model="model-a",
        base_url="http://127.0.0.1:8080/v1",
        api_protocol="chat-completions",
        **kwargs,
    )


async def test_credential_lifecycle_and_dispatch(endpoint_env):
    e = endpoint_env
    c = e["client"]
    p = e["path"]
    h = e["headers"]
    created = await c.post(
        p + "/credentials",
        headers=h,
        json={"value": "test-secret-123", "label": "Local"},
    )
    assert created.status_code == 201, created.text
    ref = created.json()["id"]
    assert "test-secret" not in created.text
    async with e["factory"]() as db:
        row = await db.get(EngineCredential, ref)
        assert b"test-secret" not in row.encrypted_value
        assert (
            e["secret_service"].decrypt_dict(row.encrypted_value)["v"]
            == "test-secret-123"
        )
    saved = await c.put(p, headers=h, json=config(credential_ref=ref))
    assert saved.status_code == 200, saved.text
    e["app"].state.agent_lifecycle.bump_generation.assert_awaited_once_with(
        e["agent_id"]
    )
    async with e["factory"]() as db:
        a = await db.get(Agent, e["agent_id"])
        payload = await build_direct_engine_secrets(db, a, e["secret_service"])
        assert (
            json.loads(payload["AG_ENGINE_ENDPOINT_CONFIG"])["credential_revision"] == 1
        )
        assert payload["AG_ENGINE_ENDPOINT_KEY"] == "test-secret-123"
        authenticated_frame = await e["lifecycle"]._build_sync_frame(db, a, [])
        assert (
            authenticated_frame["engine_secrets"]["AG_ENGINE_ENDPOINT_KEY"]
            == "test-secret-123"
        )
        assert "test-secret-123" not in json.dumps(
            {k: v for k, v in authenticated_frame.items() if k != "engine_secrets"}
        )
        # Check actual frame generation (no MCP overlay in this isolated fixture).
        e["lifecycle"]._mcp_template_service = None
        a.credential_ref = None
        frame = await e["lifecycle"]._build_sync_frame(db, a, [])
        assert frame["endpoint_configured"] is True
        assert (
            json.loads(frame["engine_secrets"]["AG_ENGINE_ENDPOINT_CONFIG"])["base_url"]
            == config()["base_url"]
        )
        await db.rollback()
    assert (await c.delete(p + "/credentials/" + ref, headers=h)).status_code == 409
    rotated = await c.put(
        p + "/credentials/" + ref, headers=h, json={"value": "rotated-test-secret"}
    )
    assert rotated.status_code == 200 and rotated.json()["revision"] == 2
    assert (
        "rotated-test-secret" not in (await c.get(p + "/credentials", headers=h)).text
    )
    assert (await c.put(p, headers=h, json={"base_url": None})).status_code == 200
    assert (await c.delete(p + "/credentials/" + ref, headers=h)).status_code == 204


async def test_cross_agent_scope_and_admin_only(endpoint_env):
    e = endpoint_env
    c = e["client"]
    p = e["path"]
    h = e["headers"]
    ref = (
        await c.post(p + "/credentials", headers=h, json={"value": "scope-test-secret"})
    ).json()["id"]
    other = f"/api/v1/agents/{e['other_id']}/endpoint"
    assert (
        await c.put(other, headers=h, json=config(credential_ref=ref))
    ).status_code == 422
    assert (
        await c.put(other + "/credentials/" + ref, headers=h, json={"value": "x"})
    ).status_code == 404
    assert (await c.delete(other + "/credentials/" + ref, headers=h)).status_code == 404
    rh = {"Authorization": f"Bearer {e['regular_token']}"}
    for method, path, payload in [
        ("get", p, None),
        ("get", p + "/credentials", None),
        ("put", p, config()),
        ("post", p + "/credentials", {"value": "x"}),
        ("delete", p + "/credentials/" + ref, None),
    ]:
        r = await c.request(
            method, path, headers=rh, **({"json": payload} if payload else {})
        )
        assert r.status_code == 403
    async with e["factory"]() as db:
        a = await db.get(Agent, e["agent_id"])
        a.engine = "codex-cli"
        await db.commit()
    cfg = config(credential_ref=ref)
    cfg["api_protocol"] = "responses"
    assert (await c.put(p, headers=h, json=cfg)).status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "http://user:secret-token@localhost/v1",
        "http://localhost/v1?api_key=secret-token",
        "http://localhost/v1#secret-token",
        "http://localhost:99999/v1",
    ],
)
async def test_url_errors_do_not_echo_values(endpoint_env, url):
    e = endpoint_env
    body = config()
    body["base_url"] = url
    r = await e["client"].put(e["path"], headers=e["headers"], json=body)
    assert r.status_code == 422
    assert "secret-token" not in r.text and url not in r.text


async def test_old_machine_rejected_before_config_save_and_reconnect(endpoint_env):
    e = endpoint_env
    c = e["client"]
    p = e["path"]
    h = e["headers"]
    async with e["factory"]() as db:
        m = await db.get(Machine, e["machine"].id)
        m.control_capabilities = []
        await db.commit()
    r = await c.put(p, headers=h, json=config())
    assert r.status_code == 409 and "direct_endpoint_v1" in r.text
    assert (await c.get(p, headers=h)).json()["base_url"] is None
    async with e["factory"]() as db:
        a = await db.get(Agent, e["agent_id"])
        for k, v in config().items():
            setattr(a, k, v)
        frame = await e["lifecycle"]._build_sync_frame(db, a, [])
        assert frame["desired_state"] == "stopped" and "engine_secrets" not in frame


async def test_missing_or_wrong_encryption_key_never_falls_back(endpoint_env):
    e = endpoint_env
    c = e["client"]
    p = e["path"]
    h = e["headers"]
    ref = (
        await c.post(p + "/credentials", headers=h, json={"value": "test-key"})
    ).json()["id"]
    assert (
        await c.put(p, headers=h, json=config(credential_ref=ref))
    ).status_code == 200
    async with e["factory"]() as db:
        a = await db.get(Agent, e["agent_id"])
        with pytest.raises(ValueError, match="cannot be loaded"):
            await build_direct_engine_secrets(db, a, MCPSecrets(Fernet.generate_key()))
        e["lifecycle"]._mcp_template_service = None
        frame = await e["lifecycle"]._build_sync_frame(db, a, [])
        assert frame["desired_state"] == "stopped" and "engine_secrets" not in frame


async def test_invalid_direct_start_never_stops_running_agent(endpoint_env):
    e = endpoint_env
    async with e["factory"]() as db:
        a = await db.get(Agent, e["agent_id"])
        for key, value in config(credential_ref="missing").items():
            setattr(a, key, value)
        a.actual_state = "running"
        await db.commit()
    e["app"].state.agent_lifecycle = SimpleNamespace(
        request_start=AsyncMock(), request_stop=AsyncMock()
    )
    r = await e["client"].post(
        f"/api/v1/agents/{e['agent_id']}/start", headers=e["headers"]
    )
    assert r.status_code == 422
    e["app"].state.agent_lifecycle.request_stop.assert_not_awaited()
    e["app"].state.agent_lifecycle.request_start.assert_not_awaited()
