"""Tests for SkillLibraryService — skill_library (#119 Phase 1)."""

from __future__ import annotations

import hashlib

import pytest
import pytest_asyncio
from sqlalchemy import select

from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Agent, AgentSkill, Base, SkillLibraryEntry
from doorae.skills_library.github_fetcher import SkillFetchResult
from doorae.skills_library.service import SkillLibraryService


class FakeFetcher:
    """Test double that returns a canned SkillFetchResult.

    Service-layer tests should not touch the real GitHubFetcher — that
    would need network + respx. Fetcher logic is covered by its own
    unit suite (``test_skills_library_github.py``).
    """

    def __init__(self, result: SkillFetchResult) -> None:
        self.result = result
        self.calls: list[tuple[str, str, str]] = []

    async def fetch_skill(self, source: str, name: str, rev: str = "HEAD") -> SkillFetchResult:
        self.calls.append((source, name, rev))
        return self.result


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
        agent = Agent(engine="echo", name=name, desired_state="idle", actual_state="idle")
        db.add(agent)
        await db.commit()
        await db.refresh(agent)
        return agent


@pytest.mark.asyncio
async def test_register_creates_row_and_hashes_skill_md(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(
            commit_sha="c0ffee",
            skill_md="# Hello\nbody",
            scripts_detected=["skills/hello/scripts/x.py"],
        )
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)

    result = await service.register(source="owner/repo", name="hello")
    assert fetcher.calls == [("owner/repo", "hello", "HEAD")]
    assert result.body_changed is True  # new row

    async with session_factory() as db:
        rows = (await db.execute(select(SkillLibraryEntry))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.id == result.entry.id
    assert row.source == "owner/repo"
    assert row.name == "hello"
    assert row.pinned_rev == "c0ffee"
    assert row.skill_md == "# Hello\nbody"
    assert row.scripts_detected == ["skills/hello/scripts/x.py"]
    assert row.extra_files == {}
    expected_hash = hashlib.sha256("# Hello\nbody".encode("utf-8")).hexdigest()
    assert row.content_hash == expected_hash


@pytest.mark.asyncio
async def test_register_same_source_name_rev_is_idempotent(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="same", skill_md="body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)

    first = await service.register(source="owner/repo", name="x")
    second = await service.register(source="owner/repo", name="x")
    # Upsert behaviour — same triple reuses the row id, no duplicate
    # row in the DB.
    assert first.entry.id == second.entry.id
    # Identical body on re-register → body_changed False (no bump).
    assert second.body_changed is False
    async with session_factory() as db:
        count = len((await db.execute(select(SkillLibraryEntry))).scalars().all())
    assert count == 1


@pytest.mark.asyncio
async def test_register_upsert_with_changed_body_reports_changed(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="same", skill_md="v1", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    await service.register(source="owner/repo", name="x")

    # Same commit_sha, different body — real-world case when a force-push
    # or branch retag moves the SHA but the pointed-at content diverges,
    # and the admin re-registers.
    fetcher.result = SkillFetchResult(
        commit_sha="same", skill_md="v2", scripts_detected=[]
    )
    result = await service.register(source="owner/repo", name="x")
    assert result.body_changed is True


@pytest.mark.asyncio
async def test_register_different_pinned_rev_creates_new_row(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="rev1", skill_md="v1", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    await service.register(source="owner/repo", name="x")

    # Simulate upstream update — new SHA after re-resolve.
    fetcher.result = SkillFetchResult(
        commit_sha="rev2", skill_md="v2", scripts_detected=[]
    )
    await service.register(source="owner/repo", name="x", rev="main")

    async with session_factory() as db:
        rows = (await db.execute(select(SkillLibraryEntry))).scalars().all()
    revs = sorted(r.pinned_rev for r in rows)
    assert revs == ["rev1", "rev2"]


@pytest.mark.asyncio
async def test_resolve_for_agent_returns_empty_when_no_skills(session_factory):
    service = SkillLibraryService(session_factory, fetcher=FakeFetcher(
        SkillFetchResult(commit_sha="x", skill_md="y", scripts_detected=[])
    ))
    agent = await _seed_agent(session_factory)
    async with session_factory() as db:
        resolved = await service.resolve_for_agent(db, agent.id)
    assert resolved == {}


@pytest.mark.asyncio
async def test_resolve_for_agent_returns_skill_md_under_skills_path(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="# Body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)

    result = await service.register(source="owner/repo", name="hello")
    agent = await _seed_agent(session_factory)

    async with session_factory() as db:
        await service.attach(db, agent_id=agent.id, skill_id=result.entry.id)
        await db.commit()
        resolved = await service.resolve_for_agent(db, agent.id)
    assert resolved == {"skills/hello/SKILL.md": "# Body"}


@pytest.mark.asyncio
async def test_attach_is_idempotent(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="# Body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    result = await service.register(source="owner/repo", name="hello")
    agent = await _seed_agent(session_factory)

    async with session_factory() as db:
        first = await service.attach(db, agent_id=agent.id, skill_id=result.entry.id)
        second = await service.attach(db, agent_id=agent.id, skill_id=result.entry.id)
        await db.commit()

    assert first is True
    assert second is False  # no-op on re-attach — signals "no bump"

    async with session_factory() as db:
        rows = (await db.execute(select(AgentSkill))).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_detach_removes_link_without_touching_entry(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="# Body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    result = await service.register(source="owner/repo", name="hello")
    agent = await _seed_agent(session_factory)

    async with session_factory() as db:
        await service.attach(db, agent_id=agent.id, skill_id=result.entry.id)
        await db.commit()
        did_detach = await service.detach(db, agent_id=agent.id, skill_id=result.entry.id)
        await db.commit()

    assert did_detach is True

    async with session_factory() as db:
        rows = (await db.execute(select(AgentSkill))).scalars().all()
        entries = (await db.execute(select(SkillLibraryEntry))).scalars().all()
    assert rows == []
    assert len(entries) == 1  # library entry itself stays


@pytest.mark.asyncio
async def test_detach_noop_returns_false(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="# Body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    result = await service.register(source="owner/repo", name="hello")
    agent = await _seed_agent(session_factory)

    async with session_factory() as db:
        # Never attached — detach should be a no-op and report False
        # so the API handler skips the unnecessary bump.
        did_detach = await service.detach(
            db, agent_id=agent.id, skill_id=result.entry.id,
        )
    assert did_detach is False
