"""Tests that lifecycle._build_sync_frame merges attached skills (#119 / #123)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Agent, AgentFile, AgentSkill, Base, SkillLibraryEntry
from doorae.scheduler.lifecycle import AgentLifecycle
from doorae.scheduler.machine_bus import MachineBus


@pytest_asyncio.fixture()
async def env():
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


async def _build_frame(factory, agent_id: str) -> dict:
    bus = MachineBus()
    lifecycle = AgentLifecycle(db_factory=factory, machine_bus=bus)
    async with factory() as db:
        agent = (await db.execute(select(Agent).where(Agent.id == agent_id))).scalar_one()
        return await lifecycle._build_sync_frame(db, agent, rooms=[])


@pytest.mark.asyncio
async def test_sync_frame_without_skills_has_only_agent_files(env):
    async with env() as db:
        agent = Agent(engine="echo", name="a", desired_state="idle", actual_state="idle")
        db.add(agent)
        await db.flush()
        db.add(AgentFile(agent_id=agent.id, path="AGENTS.md", content="direct"))
        await db.commit()
        agent_id = agent.id

    frame = await _build_frame(env, agent_id)
    assert frame["files"] == {"AGENTS.md": "direct"}


@pytest.mark.asyncio
async def test_sync_frame_merges_attached_skill_md(env):
    async with env() as db:
        agent = Agent(engine="echo", name="a", desired_state="idle", actual_state="idle")
        skill = SkillLibraryEntry(
            source="owner/repo",
            name="web-design",
            pinned_rev="sha",
            skill_md="# Web design body",
            extra_files={},
            scripts_detected=[],
            content_hash="h",
        )
        db.add_all([agent, skill])
        await db.flush()
        db.add(AgentSkill(agent_id=agent.id, skill_library_id=skill.id))
        await db.commit()
        agent_id = agent.id

    frame = await _build_frame(env, agent_id)
    assert frame["files"] == {
        "skills/web-design/SKILL.md": "# Web design body",
    }


@pytest.mark.asyncio
async def test_agent_file_wins_over_skill_file_at_same_path(env):
    """If the admin manually uploaded a file that collides with a skill,
    the admin override wins — matches the precedence documented in the
    plan §3.1 (agent_files are the explicit override surface)."""
    async with env() as db:
        agent = Agent(engine="echo", name="a", desired_state="idle", actual_state="idle")
        skill = SkillLibraryEntry(
            source="owner/repo",
            name="web-design",
            pinned_rev="sha",
            skill_md="# from library",
            extra_files={},
            scripts_detected=[],
            content_hash="h",
        )
        db.add_all([agent, skill])
        await db.flush()
        db.add(AgentSkill(agent_id=agent.id, skill_library_id=skill.id))
        db.add(AgentFile(
            agent_id=agent.id,
            path="skills/web-design/SKILL.md",
            content="# admin override",
        ))
        await db.commit()
        agent_id = agent.id

    frame = await _build_frame(env, agent_id)
    assert frame["files"] == {
        "skills/web-design/SKILL.md": "# admin override",
    }


@pytest.mark.asyncio
async def test_sync_frame_merges_skill_extra_files(env):
    """Phase 3: every entry in ``SkillLibraryEntry.extra_files`` should
    land in the sync frame alongside the SKILL.md body, so the machine
    materializes the whole skill directory."""
    async with env() as db:
        agent = Agent(engine="echo", name="a", desired_state="idle", actual_state="idle")
        skill = SkillLibraryEntry(
            source="owner/repo",
            name="pdf",
            pinned_rev="sha",
            skill_md="# PDF skill",
            extra_files={
                "skills/pdf/scripts/extract.py": "print('extract')",
                "skills/pdf/references/notes.md": "# Notes",
            },
            scripts_detected=[
                "skills/pdf/scripts/extract.py",
                "skills/pdf/references/notes.md",
            ],
            content_hash="h",
        )
        db.add_all([agent, skill])
        await db.flush()
        db.add(AgentSkill(agent_id=agent.id, skill_library_id=skill.id))
        await db.commit()
        agent_id = agent.id

    frame = await _build_frame(env, agent_id)
    assert frame["files"] == {
        "skills/pdf/SKILL.md": "# PDF skill",
        "skills/pdf/scripts/extract.py": "print('extract')",
        "skills/pdf/references/notes.md": "# Notes",
    }


@pytest.mark.asyncio
async def test_agent_file_wins_over_skill_extra_file_at_same_path(env):
    """AgentFile precedence must extend to extra_files too, not just
    SKILL.md — otherwise admins couldn't override a script from a
    library skill."""
    async with env() as db:
        agent = Agent(engine="echo", name="a", desired_state="idle", actual_state="idle")
        skill = SkillLibraryEntry(
            source="owner/repo",
            name="pdf",
            pinned_rev="sha",
            skill_md="# PDF",
            extra_files={"skills/pdf/scripts/extract.py": "library version"},
            scripts_detected=["skills/pdf/scripts/extract.py"],
            content_hash="h",
        )
        db.add_all([agent, skill])
        await db.flush()
        db.add(AgentSkill(agent_id=agent.id, skill_library_id=skill.id))
        db.add(AgentFile(
            agent_id=agent.id,
            path="skills/pdf/scripts/extract.py",
            content="admin override",
        ))
        await db.commit()
        agent_id = agent.id

    frame = await _build_frame(env, agent_id)
    assert frame["files"]["skills/pdf/scripts/extract.py"] == "admin override"
    assert frame["files"]["skills/pdf/SKILL.md"] == "# PDF"
