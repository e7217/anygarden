"""End-to-end smoke test for the per-agent directory pipeline.

Covers the server → frame → machine path at the Python level, without
spinning up actual subprocesses:

1. Server DB holds an agent with AGENTS.md body and two skill rows
2. Server AgentLifecycle.request_start packs them into a spawn frame
3. Machine Spawner._materialize_agent_dir reconciles the on-disk
   tree against that frame
4. Assert the tree matches
5. Delete one skill from the DB, re-spawn, assert the disk skill is
   preserved because skills are runtime-owned after initial seed
6. Drop a runtime file under the agent root between spawns, assert it survives

The full subprocess E2E lives in scripts/e2e_multiprocess.py; this
test deliberately stays at the Python library level so the
reconciliation contract can be checked in under a second.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from unittest.mock import AsyncMock

from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import (
    Agent,
    AgentFile,
    Base,
    Machine,
    MachineEngine,
    Participant,
    Project,
    Room,
    User,
)
from doorae.scheduler.lifecycle import AgentLifecycle
from doorae.scheduler.machine_bus import MachineBus

from doorae_machine.spawner import SpawnManifest, Spawner


class FakeWS:
    """Captures JSON frames the server sends to the fake daemon."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    def last_spawn_frame(self) -> SpawnManifest:
        """Return the last sent frame as a SpawnManifest.

        The server now sends ``sync_desired_state`` frames rather than the
        old ``spawn_agent`` frame.  We parse the raw dict and build a
        SpawnManifest so the machine-side materializer can be called
        directly without any protocol coupling.
        """
        assert self.sent, "no frame sent"
        data = json.loads(self.sent[-1])
        return SpawnManifest(
            agent_id=data["agent_id"],
            engine=data.get("engine", ""),
            agent_token="",  # not issued at this layer in unit tests
            profile_yaml=data.get("profile_yaml", ""),
            rooms=data.get("rooms", []),
            name=data.get("name", ""),
            agents_md=data.get("agents_md"),
            files=data.get("files", {}),
            engine_secrets=data.get("engine_secrets", {}),
            reasoning_effort=data.get("reasoning_effort"),
            sub_rooms=data.get("sub_rooms", []),
        )


@pytest_asyncio.fixture()
async def pipeline(tmp_path: Path):
    """Wire up an in-memory server + an isolated machine spawner."""
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)

    fake_ws = FakeWS()

    async with factory() as db:
        user = User(email="e2e@test.com", password_hash="x")
        db.add(user)
        await db.flush()

        machine = Machine(
            name="e2e-machine",
            hostname="e2e-host",
            owner_user_id=user.id,
            status="online",
            max_agents=5,
        )
        db.add(machine)
        await db.flush()
        db.add(MachineEngine(machine_id=machine.id, engine="echo"))

        project = Project(name="e2e-project")
        db.add(project)
        await db.flush()
        room = Room(project_id=project.id, name="e2e-room")
        db.add(room)
        await db.commit()

        room_id = room.id

    await bus.register(machine.id, fake_ws)

    # Machine-side spawner with an isolated materialize root so we
    # don't touch the developer's real ~/.doorae/agents/.
    spawner = Spawner(
        on_stopped=AsyncMock(),
        on_crashed=AsyncMock(),
        agent_dirs_root=tmp_path / "doorae" / "agents",
    )

    async def make_agent(
        name: str,
        agents_md: str,
        files: dict[str, str],
    ) -> str:
        async with factory() as db:
            agent = Agent(
                name=name,
                engine="echo",
                desired_state="running",
                actual_state="pending",
                agents_md=agents_md,
            )
            db.add(agent)
            await db.flush()
            for path, content in files.items():
                db.add(
                    AgentFile(
                        agent_id=agent.id, path=path, content=content
                    )
                )
            # Attach to the room so request_start's rooms=[] guard passes.
            db.add(
                Participant(
                    room_id=room_id, agent_id=agent.id, role="member"
                )
            )
            await db.commit()
            return agent.id

    async def delete_file(agent_id: str, path: str) -> None:
        async with factory() as db:
            await db.execute(
                delete(AgentFile).where(
                    AgentFile.agent_id == agent_id,
                    AgentFile.path == path,
                )
            )
            await db.commit()

    yield {
        "factory": factory,
        "lifecycle": lifecycle,
        "spawner": spawner,
        "fake_ws": fake_ws,
        "make_agent": make_agent,
        "delete_file": delete_file,
    }

    await engine.dispose()


@pytest.mark.asyncio
async def test_server_frame_materializes_to_disk(pipeline) -> None:
    agent_id = await pipeline["make_agent"](
        name="e2e-a",
        agents_md="# e2e agent\nBe helpful.",
        files={
            "skills/coder/SKILL.md": "---\nname: coder\ndescription: writes code\n---\ncoder body",
            "skills/reviewer/SKILL.md": "---\nname: reviewer\ndescription: reviews code\n---\nreviewer body",
            ".codex/config.toml": "[mcp_servers.docs]\ncommand = \"docs-mcp\"\n",
        },
    )

    await pipeline["lifecycle"].request_start(agent_id)

    frame = pipeline["fake_ws"].last_spawn_frame()
    agent_root = pipeline["spawner"]._materialize_agent_dir(frame)

    # AGENTS.md materialized from the DB field. Phase 1.5 appends
    # an auto-generated "## Available skills" section to AGENTS.md
    # whenever the manifest ships ``skills/<name>/SKILL.md`` files,
    # so the on-disk AGENTS.md is the base body PLUS the inlined
    # skill bodies — this lets engines that don't natively discover
    # project skills (codex) still honor the rules.
    rendered = (agent_root / "AGENTS.md").read_text()
    assert rendered.startswith("# e2e agent\nBe helpful.")
    assert "## Available skills" in rendered
    assert "coder body" in rendered
    assert "reviewer body" in rendered
    # Skills materialized from the agent_files rows
    assert (
        agent_root / "skills" / "coder" / "SKILL.md"
    ).read_text().startswith("---\nname: coder")
    assert (
        agent_root / "skills" / "reviewer" / "SKILL.md"
    ).read_text().startswith("---\nname: reviewer")
    # Engine config materialized too
    assert (agent_root / ".codex" / "config.toml").read_text().startswith(
        "[mcp_servers.docs]"
    )


@pytest.mark.asyncio
async def test_deleted_manifest_skill_is_preserved_on_respawn(pipeline) -> None:
    agent_id = await pipeline["make_agent"](
        name="e2e-b",
        agents_md="# agent",
        files={
            "skills/coder/SKILL.md": "coder",
            "skills/reviewer/SKILL.md": "reviewer",
        },
    )

    # First spawn: both skills present.
    await pipeline["lifecycle"].request_start(agent_id)
    frame = pipeline["fake_ws"].last_spawn_frame()
    agent_root = pipeline["spawner"]._materialize_agent_dir(frame)
    assert (agent_root / "skills" / "reviewer" / "SKILL.md").exists()

    # Admin deletes the reviewer skill from the DB.
    await pipeline["delete_file"](agent_id, "skills/reviewer/SKILL.md")

    # Second spawn: the new frame should not mention reviewer.
    # Reset agent state so request_start can dispatch again.
    async with pipeline["factory"]() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one()
        agent.actual_state = "pending"
        agent.placed_on_machine_id = None
        agent.pid = None
        await db.commit()

    await pipeline["lifecycle"].request_start(agent_id)
    frame2 = pipeline["fake_ws"].last_spawn_frame()
    assert "skills/reviewer/SKILL.md" not in frame2.files

    pipeline["spawner"]._materialize_agent_dir(frame2)

    # Skills are runtime-owned after seeding. A normal respawn no
    # longer deletes a disk skill just because the DB manifest stopped
    # advertising it; forced reset/sync is a separate control-plane
    # operation.
    assert (agent_root / "skills" / "coder" / "SKILL.md").exists()
    assert (agent_root / "skills" / "reviewer" / "SKILL.md").exists()
    assert (agent_root / "skills" / "reviewer").is_dir()


@pytest.mark.asyncio
async def test_runtime_file_survives_respawn(pipeline) -> None:
    agent_id = await pipeline["make_agent"](
        name="e2e-c",
        agents_md="# agent",
        files={"skills/coder/SKILL.md": "body"},
    )

    await pipeline["lifecycle"].request_start(agent_id)
    frame = pipeline["fake_ws"].last_spawn_frame()
    agent_root = pipeline["spawner"]._materialize_agent_dir(frame)

    # Agent writes a runtime file during its first run.
    scratch = agent_root / "in-progress.md"
    scratch.write_text("session state")

    # Reset state and re-spawn.
    async with pipeline["factory"]() as db:
        agent = (
            await db.execute(select(Agent).where(Agent.id == agent_id))
        ).scalar_one()
        agent.actual_state = "pending"
        agent.placed_on_machine_id = None
        agent.pid = None
        await db.commit()

    await pipeline["lifecycle"].request_start(agent_id)
    frame2 = pipeline["fake_ws"].last_spawn_frame()
    pipeline["spawner"]._materialize_agent_dir(frame2)

    # Prune wiped the managed tree and re-materialized it, but
    # agent-created runtime output was left alone.
    assert scratch.read_text() == "session state"
