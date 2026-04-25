"""Tests for the agent-facing MCP server (#120).

Covers the HTTP JSON-RPC surface that backs the MCP
``create_skill / update_skill / list_my_skills / delete_my_skill``
tools.  The server is mounted on ``POST /mcp/rpc`` inside the
cluster FastAPI app; callers authenticate with ``Authorization:
Bearer <agent-token>`` so the server can derive the author's
``agent_id`` the same way HTTP API endpoints do.

The cluster does not depend on the official ``mcp`` SDK — these
tests exercise the minimum JSON-RPC 2.0 subset the spec mandates
for a tools-only server (``initialize``, ``tools/list``,
``tools/call``) so the implementation stays dependency-light
(decision in plan §2.4 / §2.5).
"""

from __future__ import annotations

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from doorae.app import create_app
from doorae.auth.token import generate_token, hash_agent_token
from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Agent, AgentSkill, AgentToken, Base, SkillLibraryEntry
from doorae.scheduler.lifecycle import AgentLifecycle
from doorae.scheduler.machine_bus import MachineBus
from doorae.skills_library.service import SkillLibraryService


async def _seed_agent_with_token(
    factory, name: str = "a"
) -> tuple[Agent, str]:
    """Create an agent row + a working agent token.  Returns
    ``(agent, plaintext_token)``."""
    async with factory() as db:
        agent = Agent(
            engine="echo", name=name, desired_state="idle", actual_state="idle"
        )
        db.add(agent)
        await db.flush()
        plain = generate_token()
        token_hash, hint = hash_agent_token(plain)
        db.add(
            AgentToken(
                agent_id=agent.id, token_hash=token_hash, lookup_hint=hint
            )
        )
        await db.commit()
        await db.refresh(agent)
    return agent, plain


@pytest_asyncio.fixture()
async def mcp_env():
    config = DooraeSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.machine_bus = bus
    app.state.agent_lifecycle = lifecycle
    app.state.skill_library_service = SkillLibraryService(factory)

    agent_a, token_a = await _seed_agent_with_token(factory, "a")
    agent_b, token_b = await _seed_agent_with_token(factory, "b")

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "factory": factory,
            "agent_a": agent_a,
            "token_a": token_a,
            "agent_b": agent_b,
            "token_b": token_b,
        }

    await engine.dispose()


async def _rpc_call(
    client: AsyncClient,
    token: str,
    method: str,
    params: dict | None = None,
    *,
    req_id: int = 1,
) -> dict:
    """Send a JSON-RPC 2.0 request and return the parsed response body."""
    body: dict = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": method,
    }
    if params is not None:
        body["params"] = params
    resp = await client.post(
        "/mcp/rpc",
        json=body,
        headers={"Authorization": f"Bearer {token}"},
    )
    return resp.json() | {"_status": resp.status_code}


async def _tool_call(
    client: AsyncClient,
    token: str,
    tool_name: str,
    arguments: dict,
    *,
    req_id: int = 1,
) -> dict:
    return await _rpc_call(
        client,
        token,
        "tools/call",
        {"name": tool_name, "arguments": arguments},
        req_id=req_id,
    )


# ── Auth / protocol handshake ────────────────────────────────


@pytest.mark.asyncio
async def test_rpc_without_auth_rejected(mcp_env):
    resp = await mcp_env["client"].post(
        "/mcp/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_rpc_user_token_rejected(mcp_env):
    """Only agent tokens may access the MCP channel — a user/admin JWT
    is a different auth axis and shouldn't be able to write skills
    that would attach to an arbitrary agent."""
    from doorae.auth.jwt import create_user_token
    config = DooraeSettings(jwt_secret="x")
    # Use the real app's jwt_secret so decoding succeeds.
    secret = mcp_env["client"]._transport.app.state.config.jwt_secret
    jwt_token = create_user_token(
        "user-1", "x@y", is_admin=True, secret=secret
    )
    resp = await mcp_env["client"].post(
        "/mcp/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {jwt_token}"},
    )
    assert resp.status_code in (401, 403)
    # Silence unused-import lints for test-only imports.
    _ = config


@pytest.mark.asyncio
async def test_tools_list_returns_expected_tools(mcp_env):
    client, token = mcp_env["client"], mcp_env["token_a"]
    data = await _rpc_call(client, token, "tools/list")
    assert data["_status"] == 200
    assert data["jsonrpc"] == "2.0"
    assert data["id"] == 1
    assert "result" in data
    names = {t["name"] for t in data["result"]["tools"]}
    # #266 — ``mark_task_status`` joins the original skill-authoring
    # quartet. Tests for the new tool live in test_mark_task_status.py.
    assert names == {
        "create_skill",
        "update_skill",
        "list_my_skills",
        "delete_my_skill",
        "mark_task_status",
    }


# ── create_skill ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_skill_persists_row_and_auto_attaches(mcp_env):
    client = mcp_env["client"]
    agent = mcp_env["agent_a"]
    token = mcp_env["token_a"]
    factory = mcp_env["factory"]

    resp = await _tool_call(
        client,
        token,
        "create_skill",
        {
            "name": "notes",
            "description": "Quick notes skill",
            "body": "# Notes\nuseful",
            "extra_files": {"skills/notes/scripts/run.py": "print('hi')"},
        },
    )
    assert resp["_status"] == 200, resp
    assert "result" in resp, resp
    tool_result = resp["result"]
    assert tool_result["isError"] is False
    # The structured tool result echoes the skill id so the LLM can
    # reference it in later update_skill / delete_my_skill calls.
    payload = tool_result["structuredContent"]
    skill_id = payload["id"]
    assert skill_id

    async with factory() as db:
        row = (
            await db.execute(
                select(SkillLibraryEntry).where(SkillLibraryEntry.id == skill_id)
            )
        ).scalar_one()
        assert row.created_by_agent_id == agent.id
        assert row.skill_md == "# Notes\nuseful"
        assert row.extra_files == {"skills/notes/scripts/run.py": "print('hi')"}
        link = (
            await db.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == agent.id,
                    AgentSkill.skill_library_id == skill_id,
                )
            )
        ).scalar_one_or_none()
        assert link is not None


@pytest.mark.asyncio
async def test_create_skill_duplicate_name_returns_tool_error(mcp_env):
    client, token = mcp_env["client"], mcp_env["token_a"]
    await _tool_call(
        client, token, "create_skill",
        {"name": "dup", "description": "d", "body": "x"},
    )
    resp = await _tool_call(
        client, token, "create_skill",
        {"name": "dup", "description": "d", "body": "y"},
        req_id=2,
    )
    # "Tool errors" in MCP are reported as ``isError=True`` inside the
    # result rather than a JSON-RPC error — the LLM can read the
    # message and decide to rename.
    assert resp["result"]["isError"] is True
    assert "exists" in resp["result"]["content"][0]["text"].lower() or \
        "conflict" in resp["result"]["content"][0]["text"].lower() or \
        "duplicate" in resp["result"]["content"][0]["text"].lower()


# ── update_skill ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_skill_rewrites_body(mcp_env):
    client, token = mcp_env["client"], mcp_env["token_a"]
    factory = mcp_env["factory"]

    create = await _tool_call(
        client, token, "create_skill",
        {"name": "u", "description": "d", "body": "v1"},
    )
    skill_id = create["result"]["structuredContent"]["id"]

    resp = await _tool_call(
        client, token, "update_skill",
        {"id": skill_id, "body": "v2"},
        req_id=2,
    )
    assert resp["result"]["isError"] is False

    async with factory() as db:
        row = (
            await db.execute(
                select(SkillLibraryEntry).where(SkillLibraryEntry.id == skill_id)
            )
        ).scalar_one()
    assert row.skill_md == "v2"


@pytest.mark.asyncio
async def test_update_skill_rejects_other_agents_skill(mcp_env):
    """Agent B must not be able to update a skill authored by agent A."""
    client = mcp_env["client"]
    token_a = mcp_env["token_a"]
    token_b = mcp_env["token_b"]
    factory = mcp_env["factory"]

    create = await _tool_call(
        client, token_a, "create_skill",
        {"name": "priv", "description": "d", "body": "v1"},
    )
    skill_id = create["result"]["structuredContent"]["id"]

    resp = await _tool_call(
        client, token_b, "update_skill",
        {"id": skill_id, "body": "hacked"},
        req_id=2,
    )
    assert resp["result"]["isError"] is True
    async with factory() as db:
        row = (
            await db.execute(
                select(SkillLibraryEntry).where(SkillLibraryEntry.id == skill_id)
            )
        ).scalar_one()
    assert row.skill_md == "v1"


# ── list_my_skills ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_my_skills_returns_only_callers_skills(mcp_env):
    client = mcp_env["client"]
    token_a = mcp_env["token_a"]
    token_b = mcp_env["token_b"]

    for n in ("s1", "s2"):
        await _tool_call(
            client, token_a, "create_skill",
            {"name": n, "description": "d", "body": "x"},
        )
    await _tool_call(
        client, token_b, "create_skill",
        {"name": "s3", "description": "d", "body": "y"},
    )

    resp = await _tool_call(client, token_a, "list_my_skills", {}, req_id=10)
    names = sorted(
        s["name"] for s in resp["result"]["structuredContent"]["skills"]
    )
    assert names == ["s1", "s2"]


# ── delete_my_skill ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_my_skill_removes_row(mcp_env):
    client, token = mcp_env["client"], mcp_env["token_a"]
    factory = mcp_env["factory"]

    create = await _tool_call(
        client, token, "create_skill",
        {"name": "todelete", "description": "d", "body": "x"},
    )
    skill_id = create["result"]["structuredContent"]["id"]

    resp = await _tool_call(
        client, token, "delete_my_skill",
        {"id": skill_id},
        req_id=2,
    )
    assert resp["result"]["isError"] is False
    assert resp["result"]["structuredContent"]["deleted"] is True

    async with factory() as db:
        row = (
            await db.execute(
                select(SkillLibraryEntry).where(SkillLibraryEntry.id == skill_id)
            )
        ).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_delete_my_skill_rejects_non_owner(mcp_env):
    client = mcp_env["client"]
    token_a = mcp_env["token_a"]
    token_b = mcp_env["token_b"]
    factory = mcp_env["factory"]

    create = await _tool_call(
        client, token_a, "create_skill",
        {"name": "priv", "description": "d", "body": "x"},
    )
    skill_id = create["result"]["structuredContent"]["id"]

    resp = await _tool_call(
        client, token_b, "delete_my_skill",
        {"id": skill_id},
        req_id=2,
    )
    assert resp["result"]["isError"] is True
    async with factory() as db:
        row = (
            await db.execute(
                select(SkillLibraryEntry).where(SkillLibraryEntry.id == skill_id)
            )
        ).scalar_one_or_none()
    assert row is not None


# ── Admin promote endpoint (#120) ────────────────────────────


@pytest.mark.asyncio
async def test_admin_promote_agent_authored_skill(mcp_env):
    """An admin can promote an agent-authored skill into the shared
    library — clears the ``created_by_agent_id`` and stamps
    ``approved_by`` so later attachers can see it."""
    client = mcp_env["client"]
    token_a = mcp_env["token_a"]
    factory = mcp_env["factory"]

    create = await _tool_call(
        client, token_a, "create_skill",
        {"name": "toshare", "description": "d", "body": "x"},
    )
    skill_id = create["result"]["structuredContent"]["id"]

    # Seed an admin user and create a matching JWT.
    from doorae.auth.jwt import create_user_token
    from doorae.db.models import User
    secret = client._transport.app.state.config.jwt_secret
    async with factory() as db:
        admin = User(email="admin@x.com", password_hash="h", is_admin=True)
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        admin_token = create_user_token(
            admin.id, admin.email, admin.is_admin, secret=secret
        )

    resp = await client.post(
        f"/api/v1/admin/skills/{skill_id}/promote",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["approved_by"] == admin.id
    assert body["created_by_agent_id"] is None


@pytest.mark.asyncio
async def test_admin_list_skills_filter_agent_authored(mcp_env):
    """Admin UI filter — ``?filter=agent_authored`` returns only skills
    with a non-NULL ``created_by_agent_id``."""
    client = mcp_env["client"]
    token_a = mcp_env["token_a"]
    factory = mcp_env["factory"]

    # One agent-authored row.
    await _tool_call(
        client, token_a, "create_skill",
        {"name": "authored", "description": "d", "body": "x"},
    )

    # One admin-registered-looking row (created directly via service,
    # since the real admin register uses GitHub fetch which we don't
    # want here).
    from doorae.db.models import SkillLibraryEntry
    async with factory() as db:
        db.add(
            SkillLibraryEntry(
                source="owner/repo",
                name="shared",
                pinned_rev="rev",
                skill_md="md",
                extra_files={},
                scripts_detected=[],
                content_hash="h",
            )
        )
        await db.commit()

    # Seed admin.
    from doorae.auth.jwt import create_user_token
    from doorae.db.models import User
    secret = client._transport.app.state.config.jwt_secret
    async with factory() as db:
        admin = User(email="admin@x.com", password_hash="h", is_admin=True)
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
    admin_token = create_user_token(
        admin.id, admin.email, admin.is_admin, secret=secret
    )

    resp = await client.get(
        "/api/v1/admin/skills?filter=agent_authored",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200, resp.text
    names = sorted(s["name"] for s in resp.json())
    assert names == ["authored"]

    # No filter → both rows.
    resp = await client.get(
        "/api/v1/admin/skills",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    names = sorted(s["name"] for s in resp.json())
    assert names == ["authored", "shared"]
