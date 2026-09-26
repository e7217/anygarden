"""Real scheduler tokens → Pi extension → local MCP HTTP, without an LLM."""

import asyncio
import json
import os
import secrets
import shutil
import socket
from pathlib import Path

import pytest
import pytest_asyncio
import uvicorn
from anygarden.app import create_app
from anygarden.auth.dependencies import get_identity
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentToken,
    Base,
    Machine,
    Participant,
    PiNativeCredential,
    Room,
    Task,
    User,
)
from anygarden.mcp_templates.encryption import MCPSecrets
from anygarden.mcp_templates.service import MCPTemplateService
from anygarden.scheduler.lifecycle import AgentLifecycle
from anygarden.scheduler.machine_bus import MachineBus
from anygarden.skills_library.service import SkillLibraryService
from anygarden_agent.runtime.execution.pi_self_tools import (
    CONFIG_ENV,
    EXTENSION_PATH,
    TOKEN_ENV,
    TOOL_NAMES,
    prepare_pi_self_tools,
)
from cryptography.fernet import Fernet
from sqlalchemy import select

HARNESS = (
    Path(__file__).parents[2]
    / "agent"
    / "tests"
    / "fixtures"
    / "pi_self_tools_harness.mjs"
)
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is required by Pi"
)


@pytest_asyncio.fixture
async def bridge(tmp_path):
    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://", jwt_secret=secrets.token_urlsafe(32)
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    secret_store = MCPSecrets(Fernet.generate_key())
    bus = MachineBus()
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.machine_bus = bus
    app.state.skill_library_service = SkillLibraryService(factory)
    service = MCPTemplateService(factory, secrets=secret_store)
    lifecycle = AgentLifecycle(
        db_factory=factory,
        machine_bus=bus,
        mcp_template_service=service,
        cluster_external_url="http://configured-cluster",
    )
    app.state.agent_lifecycle = lifecycle
    async with factory() as db:
        owner = User(email="pi-fixture@example.test", password_hash="fixture")
        db.add(owner)
        await db.flush()
        machine = Machine(
            name="fixture",
            hostname="fixture",
            owner_user_id=owner.id,
            control_capabilities=["pi_native_auth_v1"],
        )
        db.add(machine)
        await db.flush()
        agents = [
            Agent(
                name=name,
                engine="pi-cli",
                provider="zai",
                model="fixture-model",
                placed_on_machine_id=machine.id,
                permission_level="standard",
            )
            for name in ["coordinator", "worker"]
        ]
        db.add_all(agents)
        await db.flush()
        for agent in agents:
            db.add(
                PiNativeCredential(
                    agent_id=agent.id,
                    provider="zai",
                    encrypted_value=secret_store.encrypt_dict(
                        {"v": "not-a-real-provider-key"}
                    ),
                    revision=1,
                )
            )
        room = Room(
            name="Fixture room",
            speaker_strategy="orchestrator",
            orchestrator_agent_id=agents[0].id,
        )
        db.add(room)
        await db.flush()
        participants = [
            Participant(room_id=room.id, agent_id=agent.id, role="member")
            for agent in agents
        ]
        db.add_all(participants)
        await db.commit()
        ids = [agent.id for agent in agents]
        room_id, worker_pid = room.id, participants[1].id
        frames = []
        for agent in agents:
            frame = await lifecycle._build_sync_frame(db, agent, [room_id])
            await db.commit()
            frames.append(frame)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, lifespan="off", log_level="critical", access_log=False)
    )
    server_task = asyncio.create_task(server.serve(sockets=[listener]))
    for _ in range(100):
        if server.started:
            break
        await asyncio.sleep(0.01)
    assert server.started
    url = f"http://127.0.0.1:{port}"
    configs = []
    try:
        for index, frame in enumerate(frames):
            root = tmp_path / str(index)
            root.mkdir()
            configs.append(
                await prepare_pi_self_tools(
                    root, server_url=url, token=frame["anygarden_mcp_token"]
                )
            )
        yield {
            "factory": factory,
            "lifecycle": lifecycle,
            "frames": frames,
            "ids": ids,
            "room_id": room_id,
            "worker_pid": worker_pid,
            "configs": configs,
            "url": url,
            "jwt_secret": config.jwt_secret,
        }
    finally:
        server.should_exit = True
        await asyncio.wait_for(server_task, 5)
        listener.close()
        await engine.dispose()


async def execute(bridge, index, calls):
    token = bridge["frames"][index]["anygarden_mcp_token"]
    child = await asyncio.create_subprocess_exec(
        shutil.which("node"),
        str(HARNESS),
        str(EXTENSION_PATH),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={
            "PATH": os.environ.get("PATH", ""),
            TOKEN_ENV: token,
            CONFIG_ENV: str(bridge["configs"][index]),
        },
    )
    stdout, stderr = await asyncio.wait_for(
        child.communicate(json.dumps({"calls": calls}).encode()), 15
    )
    assert child.returncode == 0, stderr.decode()
    assert token.encode() not in stdout + stderr
    result = json.loads(stdout)
    assert set(result["names"]) == TOOL_NAMES
    return result["results"]


@pytest.mark.asyncio
async def test_all_nine_tools_work_with_actual_tokens_and_server_ownership(bridge):
    created = await execute(
        bridge,
        0,
        [
            {
                "name": "create_task",
                "args": {
                    "room_id": bridge["room_id"],
                    "title": title,
                    "assignee_pid": bridge["worker_pid"],
                },
            }
            for title in ["First task", "Dependent task"]
        ]
        + [
            {
                "name": "create_skill",
                "args": {
                    "name": "fixture-skill",
                    "description": "Fixture",
                    "body": "# Fixture\nLocal test.",
                },
            }
        ],
    )
    assert all(item["ok"] for item in created), created
    first, dependent = [item["value"]["details"]["task_id"] for item in created[:2]]
    skill = created[2]["value"]["details"]["id"]
    denied = await execute(
        bridge,
        1,
        [
            {
                "name": "create_task",
                "args": {"room_id": bridge["room_id"], "title": "Not coordinator"},
            },
            {"name": "update_skill", "args": {"id": skill, "body": "Not mine"}},
        ],
    )
    assert all(not item["ok"] for item in denied), denied
    wrong_owner = await execute(
        bridge,
        0,
        [{"name": "mark_task_status", "args": {"task_id": first, "status": "done"}}],
    )
    assert not wrong_owner[0]["ok"]
    finished = await execute(
        bridge,
        1,
        [
            {"name": "claim_task", "args": {"task_id": first}},
            {
                "name": "add_task_blocker",
                "args": {"task_id": dependent, "blocked_by_task_id": first},
            },
            {
                "name": "clear_task_blocker",
                "args": {"task_id": dependent, "blocked_by_task_id": first},
            },
            {"name": "mark_task_status", "args": {"task_id": first, "status": "done"}},
        ],
    )
    assert all(item["ok"] for item in finished), finished
    skills = await execute(
        bridge,
        0,
        [
            {"name": "update_skill", "args": {"id": skill, "body": "# Updated"}},
            {"name": "list_my_skills"},
            {"name": "delete_my_skill", "args": {"id": skill}},
        ],
    )
    assert all(item["ok"] for item in skills), skills
    async with bridge["factory"]() as db:
        assert (await db.get(Task, first)).status == "done"
        for agent_id, frame in zip(bridge["ids"], bridge["frames"]):
            identity = await get_identity(
                db,
                jwt_secret=bridge["jwt_secret"],
                authorization=f"Bearer {frame['anygarden_mcp_token']}",
            )
            assert identity.kind == "agent" and identity.id == agent_id
            assert not frame["files"]
            assert frame["anygarden_mcp_token"] not in json.dumps(
                frame["engine_secrets"]
            )


@pytest.mark.asyncio
async def test_pi_token_reuse_rollback_and_restricted_no_token(bridge):
    lifecycle = bridge["lifecycle"]
    async with bridge["factory"]() as db:
        agent = await db.get(Agent, bridge["ids"][0])
        frame = await lifecycle._build_sync_frame(db, agent, [bridge["room_id"]])
        assert (
            frame["anygarden_mcp_token"] == bridge["frames"][0]["anygarden_mcp_token"]
        )
        agent.permission_level = "restricted"
        restricted = await lifecycle._build_sync_frame(db, agent, [bridge["room_id"]])
        assert restricted["anygarden_mcp_token"] is None
        assert not restricted["files"]
        await db.rollback()
    async with bridge["factory"]() as db:
        rows = (await db.execute(select(AgentToken))).scalars().all()
        assert len(rows) == 2
    # Rebuilding after an uncommitted mint must not poison the durable cache.
    lifecycle._token_cache.clear()
    async with bridge["factory"]() as db:
        agent = await db.get(Agent, bridge["ids"][0])
        abandoned = await lifecycle._build_sync_frame(db, agent, [bridge["room_id"]])
        await db.rollback()
    async with bridge["factory"]() as db:
        agent = await db.get(Agent, bridge["ids"][0])
        fresh = await lifecycle._build_sync_frame(db, agent, [bridge["room_id"]])
        assert fresh["anygarden_mcp_token"] != abandoned["anygarden_mcp_token"]
        await db.commit()
        identity = await get_identity(
            db,
            jwt_secret=bridge["jwt_secret"],
            authorization=f"Bearer {fresh['anygarden_mcp_token']}",
        )
        assert identity.id == agent.id
