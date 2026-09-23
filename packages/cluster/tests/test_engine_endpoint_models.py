"""#685 — model discovery probe and direct endpoint at agent creation."""

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from anygarden.db.models import Agent, Machine
from anygarden.engines.endpoints import MODEL_PROBE_MAX_BYTES
from .test_agents_api import agents_env  # noqa: F401 — fixture
from .test_engine_endpoints import endpoint_env  # noqa: F401 — fixture

PROBE = "/api/v1/engine-endpoints/models"
BASE = "http://10.0.0.5:8000/v1"


def transport(handler, seen=None):
    def wrapped(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return handler(request)

    return httpx.MockTransport(wrapped)


def use(env, handler, seen=None):
    env["app"].state.engine_probe_transport = transport(handler, seen)


def ok(payload):
    return lambda request: httpx.Response(200, json=payload)


async def probe(env, body, headers=None):
    return await env["client"].post(
        PROBE, headers=headers or env["headers"], content=json.dumps(body)
    )


# ── Probe ───────────────────────────────────────────────────────────


async def test_probe_lists_model_ids_with_context_length(endpoint_env):
    seen = []
    use(
        endpoint_env,
        ok(
            {
                "object": "list",
                "data": [
                    {"id": "qwen3.8-27b-fp8", "object": "model", "max_model_len": 32768},
                    {"id": "llama-local", "object": "model"},
                    {"id": "qwen3.8-27b-fp8"},
                    {"id": 7},
                    {"object": "model"},
                ],
            }
        ),
        seen,
    )
    r = await probe(endpoint_env, {"base_url": BASE + "/"})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "models": [
            {"id": "qwen3.8-27b-fp8", "max_model_len": 32768},
            {"id": "llama-local", "max_model_len": None},
        ],
        "reachable_from": "server",
    }
    assert str(seen[0].url) == BASE + "/models"
    assert seen[0].method == "GET"
    assert "authorization" not in seen[0].headers


async def test_probe_sends_new_api_key_but_never_echoes_it(endpoint_env):
    seen = []
    use(endpoint_env, lambda request: httpx.Response(401, text="bad key probe-key-123"), seen)
    r = await probe(endpoint_env, {"base_url": BASE, "api_key": "probe-key-123"})
    assert r.status_code == 502
    assert seen[0].headers["authorization"] == "Bearer probe-key-123"
    assert "probe-key-123" not in r.text
    assert r.json()["detail"] == "The model server rejected the credentials (HTTP 401)"


async def test_probe_uses_stored_agent_credential(endpoint_env):
    e = endpoint_env
    ref = (
        await e["client"].post(
            e["path"] + "/credentials", headers=e["headers"], json={"value": "stored-key-9"}
        )
    ).json()["id"]
    seen = []
    use(e, ok({"data": [{"id": "m"}]}), seen)
    r = await probe(e, {"base_url": BASE, "agent_id": e["agent_id"], "credential_ref": ref})
    assert r.status_code == 200, r.text
    assert seen[0].headers["authorization"] == "Bearer stored-key-9"
    # A credential cannot be borrowed through another agent.
    r = await probe(e, {"base_url": BASE, "agent_id": e["other_id"], "credential_ref": ref})
    assert r.status_code == 422


@pytest.mark.parametrize(
    "url",
    [
        "ftp://10.0.0.5/v1",
        "http://user:secret-in-url@10.0.0.5/v1",
        "http://10.0.0.5/v1?token=secret-in-url",
        "http://10.0.0.5/v1#secret-in-url",
        "not a url",
        "",
    ],
)
async def test_probe_rejects_invalid_urls_without_echo(endpoint_env, url):
    use(endpoint_env, lambda request: pytest.fail("no request for invalid URL"))
    r = await probe(endpoint_env, {"base_url": url})
    assert r.status_code == 422
    assert "secret-in-url" not in r.text


async def test_probe_unreachable(endpoint_env):
    def refuse(request):
        raise httpx.ConnectError("connection refused to secret-host", request=request)

    use(endpoint_env, refuse)
    r = await probe(endpoint_env, {"base_url": BASE})
    assert r.status_code == 502
    assert r.json()["detail"] == "Could not reach the model server from the AnyGarden server"
    assert "secret-host" not in r.text


async def test_probe_timeout(endpoint_env):
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    use(endpoint_env, slow)
    r = await probe(endpoint_env, {"base_url": BASE})
    assert r.status_code == 504


async def test_probe_non_json_and_wrong_shape(endpoint_env):
    use(endpoint_env, lambda request: httpx.Response(200, text="<html>upstream-body</html>"))
    r = await probe(endpoint_env, {"base_url": BASE})
    assert r.status_code == 502
    assert "upstream-body" not in r.text
    use(endpoint_env, ok({"models": ["x"]}))
    assert (await probe(endpoint_env, {"base_url": BASE})).status_code == 502


async def test_probe_oversized_response(endpoint_env):
    big = b'{"data": [' + b" " * (MODEL_PROBE_MAX_BYTES + 10) + b"]}"
    use(endpoint_env, lambda request: httpx.Response(200, content=big))
    r = await probe(endpoint_env, {"base_url": BASE})
    assert r.status_code == 502
    assert "too large" in r.json()["detail"]


async def test_probe_does_not_follow_redirects(endpoint_env):
    seen = []
    use(
        endpoint_env,
        lambda request: httpx.Response(302, headers={"location": "http://169.254.169.254/"}),
        seen,
    )
    r = await probe(endpoint_env, {"base_url": BASE})
    assert r.status_code == 502
    assert len(seen) == 1


async def test_probe_is_admin_only(endpoint_env):
    use(endpoint_env, ok({"data": []}))
    r = await probe(
        endpoint_env,
        {"base_url": BASE},
        headers={"Authorization": f"Bearer {endpoint_env['regular_token']}"},
    )
    assert r.status_code == 403
    r = await endpoint_env["client"].post(PROBE, content=json.dumps({"base_url": BASE}))
    assert r.status_code in (401, 403)


async def test_probe_rejects_unknown_fields(endpoint_env):
    r = await probe(endpoint_env, {"base_url": BASE, "method": "POST"})
    assert r.status_code == 422


# ── Create with endpoint ────────────────────────────────────────────


def create_body(**overrides):
    body = {
        "name": "local-qwen",
        "engine": "pi-cli",
        "provider": "qwen-llm",
        "model": "qwen3.8-27b-fp8",
        "rooms": [],
        "endpoint": {"base_url": BASE, "api_protocol": "chat-completions"},
    }
    body.update(overrides)
    return body


@pytest.fixture
def startable(endpoint_env):
    endpoint_env["app"].state.agent_lifecycle.request_start = AsyncMock()
    return endpoint_env


async def create(env, body):
    return await env["client"].post(
        "/api/v1/agents", headers=env["headers"], json=body
    )


async def test_create_persists_endpoint_atomically(startable):
    r = await create(startable, create_body())
    assert r.status_code == 201, r.text
    async with startable["factory"]() as db:
        agent = await db.get(Agent, r.json()["id"])
        assert (agent.provider, agent.model) == ("qwen-llm", "qwen3.8-27b-fp8")
        assert agent.base_url == BASE
        assert agent.api_protocol == "chat-completions"
        assert agent.credential_ref is None
    startable["app"].state.agent_lifecycle.request_start.assert_awaited_once()


async def test_create_codex_endpoint_requires_responses(startable):
    r = await create(
        startable,
        create_body(engine="codex-cli", provider="local", model="gpt-local"),
    )
    assert r.status_code == 422
    r = await create(
        startable,
        create_body(
            engine="codex-cli",
            provider="local",
            model="gpt-local",
            endpoint={"base_url": BASE, "api_protocol": "responses"},
        ),
    )
    assert r.status_code == 201, r.text


@pytest.mark.parametrize(
    "overrides",
    [
        {"model": None},
        {"endpoint": {"base_url": "http://u:secret-in-url@h/v1", "api_protocol": "chat-completions"}},
        {"endpoint": {"base_url": BASE, "api_protocol": "grpc"}},
        {"endpoint": {"base_url": BASE}},
    ],
)
async def test_create_rejects_invalid_endpoint_without_rows(startable, overrides):
    r = await create(startable, create_body(**overrides))
    assert r.status_code == 422
    assert "secret-in-url" not in r.text
    async with startable["factory"]() as db:
        names = (await db.execute(select(Agent.name))).scalars().all()
        assert "local-qwen" not in names


async def test_create_endpoint_requires_capable_machine(startable):
    async with startable["factory"]() as db:
        machine = await db.get(Machine, startable["machine"].id)
        machine.control_capabilities = []
        await db.commit()
    r = await create(startable, create_body())
    assert r.status_code == 409
    async with startable["factory"]() as db:
        names = (await db.execute(select(Agent.name))).scalars().all()
        assert "local-qwen" not in names
