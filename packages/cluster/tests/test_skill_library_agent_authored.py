"""Tests for SkillLibraryService agent-authoring methods (#120).

Covers the service-layer surface the MCP tools rely on:

- ``create_from_agent`` / ``update_by_owner`` / ``list_by_owner`` /
  ``delete_by_owner`` — body + extra_files plumbing, ownership
  verification, name uniqueness within a single agent, content_hash
  reuse from the existing ``_canonical_tree_hash`` helper.
- ``promote_to_shared`` — admin action that clears
  ``created_by_agent_id`` and stamps ``approved_by``.
- ``resolve_for_agent`` — must continue to include a skill that's
  authored by the calling agent even when ``approved_by`` is NULL
  (the spawn-time gate tolerates "self-authored auto-approved" as
  an orthogonal axis).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, AgentSkill, Base, SkillLibraryEntry
from anygarden.skills_library.service import (
    SkillLibraryService,
    SkillOwnershipError,
    SkillNameConflictError,
    _canonical_tree_hash,
)


@pytest_asyncio.fixture()
async def session_factory():
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield factory
    await engine.dispose()


async def _seed_agent(factory, name: str = "a") -> Agent:
    async with factory() as db:
        agent = Agent(
            engine="echo", name=name, desired_state="idle", actual_state="idle"
        )
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        return agent


@pytest.mark.asyncio
async def test_create_from_agent_writes_row_with_ownership(session_factory):
    """A fresh agent-authored skill lands with the canonical content_hash,
    the author's agent id, and auto-attach to its author."""
    service = SkillLibraryService(session_factory)
    agent = await _seed_agent(session_factory)

    entry = await service.create_from_agent(
        agent_id=agent.id,
        name="mynotes",
        description="short desc",
        body="# My Notes\nHello",
        extra_files={"skills/mynotes/scripts/go.py": "print('go')"},
    )

    expected_tree = {
        "skills/mynotes/SKILL.md": "# My Notes\nHello",
        "skills/mynotes/scripts/go.py": "print('go')",
    }
    async with session_factory() as db:
        row = (
            await db.execute(
                select(SkillLibraryEntry).where(SkillLibraryEntry.id == entry.id)
            )
        ).scalar_one()
        assert row.created_by_agent_id == agent.id
        assert row.approved_by is None  # auto-approved for self, not shared
        assert row.name == "mynotes"
        assert row.skill_md == "# My Notes\nHello"
        assert row.extra_files == {"skills/mynotes/scripts/go.py": "print('go')"}
        assert row.content_hash == _canonical_tree_hash(expected_tree)

        # Auto-attach so the author sees the skill on their next spawn
        # without an explicit attach call.
        link = (
            await db.execute(
                select(AgentSkill).where(
                    AgentSkill.agent_id == agent.id,
                    AgentSkill.skill_library_id == row.id,
                )
            )
        ).scalar_one_or_none()
        assert link is not None


@pytest.mark.asyncio
async def test_create_from_agent_rejects_duplicate_name_within_author(
    session_factory,
):
    """Same agent can't have two skills with the same name — the UI would
    show identical rows and ``resolve_for_agent`` would pick
    non-deterministically between them."""
    service = SkillLibraryService(session_factory)
    agent = await _seed_agent(session_factory)

    await service.create_from_agent(
        agent_id=agent.id,
        name="duplicate",
        description="first",
        body="body1",
    )
    with pytest.raises(SkillNameConflictError):
        await service.create_from_agent(
            agent_id=agent.id,
            name="duplicate",
            description="second",
            body="body2",
        )


@pytest.mark.asyncio
async def test_create_from_agent_allows_same_name_across_authors(session_factory):
    """Two different agents may both have a skill called ``notes`` — the
    uniqueness key is ``(agent_id, name)``, not ``name`` alone."""
    service = SkillLibraryService(session_factory)
    a = await _seed_agent(session_factory, name="a")
    b = await _seed_agent(session_factory, name="b")

    await service.create_from_agent(
        agent_id=a.id, name="notes", description="a", body="A"
    )
    # Should not raise — different author scopes.
    await service.create_from_agent(
        agent_id=b.id, name="notes", description="b", body="B"
    )

    async with session_factory() as db:
        rows = (await db.execute(select(SkillLibraryEntry))).scalars().all()
    names = sorted(r.created_by_agent_id for r in rows if r.name == "notes")
    assert names == sorted([a.id, b.id])


@pytest.mark.asyncio
async def test_update_by_owner_rewrites_body_and_recomputes_hash(session_factory):
    service = SkillLibraryService(session_factory)
    agent = await _seed_agent(session_factory)

    entry = await service.create_from_agent(
        agent_id=agent.id, name="s", description="d", body="v1"
    )
    old_hash = entry.content_hash

    updated = await service.update_by_owner(
        agent_id=agent.id,
        skill_id=entry.id,
        body="v2",
        extra_files={"skills/s/helper.py": "pass"},
    )
    assert updated.skill_md == "v2"
    assert updated.extra_files == {"skills/s/helper.py": "pass"}
    assert updated.content_hash != old_hash


@pytest.mark.asyncio
async def test_update_by_owner_rejects_non_owner(session_factory):
    service = SkillLibraryService(session_factory)
    owner = await _seed_agent(session_factory, name="owner")
    intruder = await _seed_agent(session_factory, name="intruder")

    entry = await service.create_from_agent(
        agent_id=owner.id, name="priv", description="d", body="v1"
    )
    with pytest.raises(SkillOwnershipError):
        await service.update_by_owner(
            agent_id=intruder.id, skill_id=entry.id, body="hacked"
        )

    async with session_factory() as db:
        row = (
            await db.execute(
                select(SkillLibraryEntry).where(SkillLibraryEntry.id == entry.id)
            )
        ).scalar_one()
    assert row.skill_md == "v1"


@pytest.mark.asyncio
async def test_list_by_owner_filters_to_authoring_agent(session_factory):
    service = SkillLibraryService(session_factory)
    a = await _seed_agent(session_factory, name="a")
    b = await _seed_agent(session_factory, name="b")

    await service.create_from_agent(
        agent_id=a.id, name="s1", description="d", body="x"
    )
    await service.create_from_agent(
        agent_id=a.id, name="s2", description="d", body="y"
    )
    await service.create_from_agent(
        agent_id=b.id, name="s3", description="d", body="z"
    )

    a_skills = await service.list_by_owner(agent_id=a.id)
    b_skills = await service.list_by_owner(agent_id=b.id)
    assert sorted(r.name for r in a_skills) == ["s1", "s2"]
    assert [r.name for r in b_skills] == ["s3"]


@pytest.mark.asyncio
async def test_delete_by_owner_removes_row_and_link(session_factory):
    service = SkillLibraryService(session_factory)
    agent = await _seed_agent(session_factory)

    entry = await service.create_from_agent(
        agent_id=agent.id, name="trash", description="d", body="x"
    )
    deleted = await service.delete_by_owner(agent_id=agent.id, skill_id=entry.id)
    assert deleted is True

    async with session_factory() as db:
        rows = (await db.execute(select(SkillLibraryEntry))).scalars().all()
        links = (await db.execute(select(AgentSkill))).scalars().all()
    assert rows == []
    # AgentSkill rows cascade via FK ondelete when the library entry dies.
    assert links == []


@pytest.mark.asyncio
async def test_delete_by_owner_rejects_non_owner(session_factory):
    service = SkillLibraryService(session_factory)
    owner = await _seed_agent(session_factory, name="owner")
    intruder = await _seed_agent(session_factory, name="intruder")

    entry = await service.create_from_agent(
        agent_id=owner.id, name="priv", description="d", body="x"
    )
    with pytest.raises(SkillOwnershipError):
        await service.delete_by_owner(agent_id=intruder.id, skill_id=entry.id)

    async with session_factory() as db:
        rows = (await db.execute(select(SkillLibraryEntry))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_resolve_for_agent_includes_self_authored_even_without_approval(
    session_factory,
):
    """The spawn-time gate will reject rows with ``approved_by IS NULL``
    for non-authored skills once #125 lands, but a self-authored skill
    is always visible to its author regardless of approval state.
    """
    service = SkillLibraryService(session_factory)
    agent = await _seed_agent(session_factory)

    await service.create_from_agent(
        agent_id=agent.id,
        name="self",
        description="d",
        body="# Self",
        extra_files={"skills/self/x.py": "hi"},
    )

    async with session_factory() as db:
        resolved = await service.resolve_for_agent(db, agent.id)
    assert resolved["skills/self/SKILL.md"] == "# Self"
    assert resolved["skills/self/x.py"] == "hi"


@pytest.mark.asyncio
async def test_resolve_for_agent_hides_other_authors_skills(session_factory):
    """Agent B must not see a skill authored by agent A even if the row
    is in the library."""
    service = SkillLibraryService(session_factory)
    a = await _seed_agent(session_factory, name="a")
    b = await _seed_agent(session_factory, name="b")

    await service.create_from_agent(
        agent_id=a.id, name="secret", description="d", body="confidential"
    )

    async with session_factory() as db:
        b_resolved = await service.resolve_for_agent(db, b.id)
    assert b_resolved == {}


@pytest.mark.asyncio
async def test_promote_to_shared_clears_owner_and_stamps_admin(session_factory):
    service = SkillLibraryService(session_factory)
    agent = await _seed_agent(session_factory)

    entry = await service.create_from_agent(
        agent_id=agent.id, name="shared", description="d", body="v"
    )

    promoted = await service.promote_to_shared(
        skill_id=entry.id, admin_user_id="admin-42"
    )
    assert promoted.created_by_agent_id is None
    assert promoted.approved_by == "admin-42"

    # After promotion, a different agent can attach (via existing API)
    # and resolve — that's the whole point.
    other = await _seed_agent(session_factory, name="other")
    async with session_factory() as db:
        await service.attach(db, agent_id=other.id, skill_id=entry.id)
        await db.commit()
        resolved = await service.resolve_for_agent(db, other.id)
    assert "skills/shared/SKILL.md" in resolved
