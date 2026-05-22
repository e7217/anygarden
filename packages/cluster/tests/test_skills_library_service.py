"""Tests for SkillLibraryService — skill_library (#119 / #127 / #125)."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentSkill,
    Base,
    SkillLibraryAudit,
    SkillLibraryEntry,
    User,
)
from anygarden.skills_library.github_fetcher import SkillFetchResult
from anygarden.skills_library.service import (
    ACTION_APPROVE,
    ACTION_ATTACH,
    ACTION_DETACH,
    ACTION_REGISTER,
    ACTION_UPDATE,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    SkillLibraryService,
    _canonical_tree_hash,
)


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


async def _seed_admin(factory, email: str = "admin@test.com") -> User:
    """Create an admin user (for ``actor_user_id`` in approve calls)."""
    async with factory() as db:
        admin = User(email=email, password_hash="x", is_admin=True)
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        return admin


async def _auto_approve(factory, skill_id: str, actor_id: str) -> None:
    """Convenience: call service.approve on a seeded admin.

    Phase 1 tests treat skills as spawnable on register; Phase 2 adds
    the gate so pre-existing tests now wrap their register call with
    this helper to keep asserting the same behaviour under the new
    default-pending state.
    """
    service = SkillLibraryService(factory)
    # Fetcher isn't used for approve.
    await service.approve(skill_id=skill_id, actor_user_id=actor_id)


@pytest.mark.asyncio
async def test_register_creates_row_with_extra_files_and_canonical_hash(session_factory):
    """Phase 3: register stores SKILL.md + extra_files, and content_hash
    is computed over the canonical tree (so later drift in any file is
    detectable)."""
    extra = {"skills/hello/scripts/x.py": "print('x')"}
    fetcher = FakeFetcher(
        SkillFetchResult(
            commit_sha="c0ffee",
            skill_md="# Hello\nbody",
            scripts_detected=["skills/hello/scripts/x.py"],
            extra_files=extra,
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
    assert row.extra_files == extra
    # content_hash is the canonical tree hash over SKILL.md + extras.
    expected_tree = {"skills/hello/SKILL.md": "# Hello\nbody", **extra}
    assert row.content_hash == _canonical_tree_hash(expected_tree)


@pytest.mark.asyncio
async def test_canonical_tree_hash_is_path_order_independent(session_factory):
    """Different Python dict orderings must yield the same hash —
    the hash is what gates bump decisions, and we don't want spurious
    bumps just because dict insertion order happened to flip."""
    a = {"skills/x/SKILL.md": "md", "skills/x/scripts/a.py": "a", "skills/x/scripts/b.py": "b"}
    b = {"skills/x/scripts/b.py": "b", "skills/x/SKILL.md": "md", "skills/x/scripts/a.py": "a"}
    assert _canonical_tree_hash(a) == _canonical_tree_hash(b)


@pytest.mark.asyncio
async def test_canonical_tree_hash_changes_when_any_body_changes(session_factory):
    base = {"skills/x/SKILL.md": "md", "skills/x/scripts/a.py": "print(1)"}
    mutated = {"skills/x/SKILL.md": "md", "skills/x/scripts/a.py": "print(2)"}
    assert _canonical_tree_hash(base) != _canonical_tree_hash(mutated)


@pytest.mark.asyncio
async def test_register_body_changed_when_only_extra_file_changes(session_factory):
    """Canonical hash must detect drift in extra_files even when
    SKILL.md itself is unchanged — otherwise attached agents never get
    re-materialized after a script update."""
    fetcher = FakeFetcher(
        SkillFetchResult(
            commit_sha="same",
            skill_md="same md",
            scripts_detected=["skills/x/scripts/a.py"],
            extra_files={"skills/x/scripts/a.py": "v1"},
        )
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    await service.register(source="owner/repo", name="x")

    # Only the extra file body flips; SKILL.md is byte-identical.
    fetcher.result = SkillFetchResult(
        commit_sha="same",
        skill_md="same md",
        scripts_detected=["skills/x/scripts/a.py"],
        extra_files={"skills/x/scripts/a.py": "v2"},
    )
    result = await service.register(source="owner/repo", name="x")
    assert result.body_changed is True


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
    admin = await _seed_admin(session_factory)
    await _auto_approve(session_factory, result.entry.id, admin.id)

    async with session_factory() as db:
        await service.attach(db, agent_id=agent.id, skill_id=result.entry.id)
        await db.commit()
        resolved = await service.resolve_for_agent(db, agent.id)
    assert resolved == {"skills/hello/SKILL.md": "# Body"}


@pytest.mark.asyncio
async def test_resolve_for_agent_includes_extra_files(session_factory):
    """Phase 3: resolve must return SKILL.md *and* every extra_file so
    the lifecycle frame materializes the whole directory."""
    extra = {
        "skills/hello/scripts/x.py": "print('x')",
        "skills/hello/references/guide.md": "# Guide",
    }
    fetcher = FakeFetcher(
        SkillFetchResult(
            commit_sha="sha",
            skill_md="# Body",
            scripts_detected=list(extra.keys()),
            extra_files=extra,
        )
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)

    result = await service.register(source="owner/repo", name="hello")
    agent = await _seed_agent(session_factory)
    admin = await _seed_admin(session_factory)
    await _auto_approve(session_factory, result.entry.id, admin.id)

    async with session_factory() as db:
        await service.attach(db, agent_id=agent.id, skill_id=result.entry.id)
        await db.commit()
        resolved = await service.resolve_for_agent(db, agent.id)
    assert resolved == {"skills/hello/SKILL.md": "# Body", **extra}


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


# ── Phase 2: approve / reject / audit / gate (#125) ───────────────


@pytest.mark.asyncio
async def test_register_records_audit_entry(session_factory):
    """Register must land one ``register`` audit row carrying the
    after_hash and identifying metadata."""
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="# Body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    admin = await _seed_admin(session_factory)
    result = await service.register(
        source="owner/repo", name="hello", actor_user_id=admin.id,
    )

    async with session_factory() as db:
        rows = (
            await db.execute(
                select(SkillLibraryAudit).where(
                    SkillLibraryAudit.skill_library_id == result.entry.id
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].action == ACTION_REGISTER
    assert rows[0].actor_user_id == admin.id
    assert rows[0].detail["after_hash"] == result.entry.content_hash
    assert rows[0].detail["source"] == "owner/repo"


@pytest.mark.asyncio
async def test_register_idempotent_does_not_duplicate_audit(session_factory):
    """A no-op re-register (same body) should not write an ``update``
    audit row — audits track state changes, not unchanged polls."""
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="# Body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    admin = await _seed_admin(session_factory)
    result = await service.register(
        source="owner/repo", name="hello", actor_user_id=admin.id,
    )
    # Re-register with identical body.
    await service.register(
        source="owner/repo", name="hello", actor_user_id=admin.id,
    )
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(SkillLibraryAudit).where(
                    SkillLibraryAudit.skill_library_id == result.entry.id
                )
            )
        ).scalars().all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_register_update_writes_update_audit(session_factory):
    """Same (source,name,rev) with a different body writes an
    ``update`` audit carrying before_hash/after_hash."""
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="v1", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    admin = await _seed_admin(session_factory)
    first = await service.register(
        source="owner/repo", name="x", actor_user_id=admin.id,
    )

    fetcher.result = SkillFetchResult(
        commit_sha="sha", skill_md="v2", scripts_detected=[]
    )
    second = await service.register(
        source="owner/repo", name="x", actor_user_id=admin.id,
    )

    async with session_factory() as db:
        rows = (
            await db.execute(
                select(SkillLibraryAudit)
                .where(SkillLibraryAudit.skill_library_id == first.entry.id)
                .order_by(SkillLibraryAudit.at.asc())
            )
        ).scalars().all()
    actions = [r.action for r in rows]
    assert actions == [ACTION_REGISTER, ACTION_UPDATE]
    assert rows[1].detail["before_hash"] != rows[1].detail["after_hash"]
    assert rows[1].detail["after_hash"] == second.entry.content_hash


@pytest.mark.asyncio
async def test_approve_stamps_approved_fields_and_audits(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    admin = await _seed_admin(session_factory)
    reg = await service.register(
        source="owner/repo", name="x", actor_user_id=admin.id,
    )
    assert reg.entry.approved_by is None  # still pending right after register

    result = await service.approve(skill_id=reg.entry.id, actor_user_id=admin.id)
    assert result is not None
    assert result.entry.approved_by == admin.id
    assert result.entry.approved_at is not None

    async with session_factory() as db:
        audits = (
            await db.execute(
                select(SkillLibraryAudit)
                .where(SkillLibraryAudit.skill_library_id == reg.entry.id)
                .order_by(SkillLibraryAudit.at.asc())
            )
        ).scalars().all()
    assert [a.action for a in audits] == [ACTION_REGISTER, ACTION_APPROVE]


@pytest.mark.asyncio
async def test_approve_nonexistent_returns_none(session_factory):
    admin = await _seed_admin(session_factory)
    service = SkillLibraryService(session_factory)
    result = await service.approve(skill_id="missing", actor_user_id=admin.id)
    assert result is None


@pytest.mark.asyncio
async def test_reject_clears_approval_and_audits(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    admin = await _seed_admin(session_factory)
    reg = await service.register(
        source="owner/repo", name="x", actor_user_id=admin.id,
    )
    await service.approve(skill_id=reg.entry.id, actor_user_id=admin.id)

    result = await service.reject(skill_id=reg.entry.id, actor_user_id=admin.id)
    assert result is not None
    assert result.entry.approved_by is None
    assert result.entry.approved_at is None

    # Status should now report rejected (latest audit is reject, and
    # approved_by was cleared).
    async with session_factory() as db:
        status = await service.get_status(db, reg.entry.id)
    assert status == STATUS_REJECTED


@pytest.mark.asyncio
async def test_get_status_transitions(session_factory):
    """pending → approved → rejected → approved round-trip."""
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    admin = await _seed_admin(session_factory)
    reg = await service.register(
        source="owner/repo", name="x", actor_user_id=admin.id,
    )

    async with session_factory() as db:
        assert await service.get_status(db, reg.entry.id) == STATUS_PENDING

    await service.approve(skill_id=reg.entry.id, actor_user_id=admin.id)
    async with session_factory() as db:
        assert await service.get_status(db, reg.entry.id) == STATUS_APPROVED

    await service.reject(skill_id=reg.entry.id, actor_user_id=admin.id)
    async with session_factory() as db:
        assert await service.get_status(db, reg.entry.id) == STATUS_REJECTED

    # Re-approve resurrects approved_by / approved_at.
    await service.approve(skill_id=reg.entry.id, actor_user_id=admin.id)
    async with session_factory() as db:
        assert await service.get_status(db, reg.entry.id) == STATUS_APPROVED


@pytest.mark.asyncio
async def test_resolve_filters_unapproved_skills(session_factory):
    """An attached-but-unapproved skill must not land in resolve output."""
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    reg = await service.register(source="owner/repo", name="x")
    agent = await _seed_agent(session_factory)

    # Manually bypass the attach gate to force an unapproved attach —
    # the service.attach method itself does not reject (API layer does),
    # so directly inserting the row simulates either a race or a manual
    # DB edit.
    async with session_factory() as db:
        await service.attach(db, agent_id=agent.id, skill_id=reg.entry.id)
        await db.commit()
        resolved = await service.resolve_for_agent(db, agent.id)
    assert resolved == {}


@pytest.mark.asyncio
async def test_list_with_status_filters(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    admin = await _seed_admin(session_factory)

    # Skill A — approved.
    fetcher.result = SkillFetchResult(
        commit_sha="sha1", skill_md="a", scripts_detected=[]
    )
    a = await service.register(
        source="owner/repo", name="a", actor_user_id=admin.id,
    )
    await service.approve(skill_id=a.entry.id, actor_user_id=admin.id)

    # Skill B — rejected.
    fetcher.result = SkillFetchResult(
        commit_sha="sha2", skill_md="b", scripts_detected=[]
    )
    b = await service.register(
        source="owner/repo", name="b", actor_user_id=admin.id,
    )
    await service.reject(skill_id=b.entry.id, actor_user_id=admin.id)

    # Skill C — pending (never touched after register).
    fetcher.result = SkillFetchResult(
        commit_sha="sha3", skill_md="c", scripts_detected=[]
    )
    c = await service.register(
        source="owner/repo", name="c", actor_user_id=admin.id,
    )

    async with session_factory() as db:
        approved = await service.list_with_status(db, status=STATUS_APPROVED)
        rejected = await service.list_with_status(db, status=STATUS_REJECTED)
        pending = await service.list_with_status(db, status=STATUS_PENDING)
        allp = await service.list_with_status(db)

    assert [e.id for e, _ in approved] == [a.entry.id]
    assert [e.id for e, _ in rejected] == [b.entry.id]
    assert [e.id for e, _ in pending] == [c.entry.id]
    assert len(allp) == 3


@pytest.mark.asyncio
async def test_attach_and_detach_record_audit(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    admin = await _seed_admin(session_factory)
    reg = await service.register(
        source="owner/repo", name="x", actor_user_id=admin.id,
    )
    agent = await _seed_agent(session_factory)

    async with session_factory() as db:
        await service.attach(
            db,
            agent_id=agent.id,
            skill_id=reg.entry.id,
            actor_user_id=admin.id,
        )
        await db.commit()
        await service.detach(
            db,
            agent_id=agent.id,
            skill_id=reg.entry.id,
            actor_user_id=admin.id,
        )
        await db.commit()

        rows = (
            await db.execute(
                select(SkillLibraryAudit)
                .where(SkillLibraryAudit.skill_library_id == reg.entry.id)
                .order_by(SkillLibraryAudit.at.asc())
            )
        ).scalars().all()
    actions = [r.action for r in rows]
    # register + attach + detach — no approve since this test exercises
    # the audit trail shape, not the gate.
    assert actions == [ACTION_REGISTER, ACTION_ATTACH, ACTION_DETACH]
    assert rows[1].detail == {"agent_id": agent.id}
    assert rows[2].detail == {"agent_id": agent.id}


@pytest.mark.asyncio
async def test_delete_records_audit_and_returns_affected_agents(session_factory):
    fetcher = FakeFetcher(
        SkillFetchResult(commit_sha="sha", skill_md="body", scripts_detected=[])
    )
    service = SkillLibraryService(session_factory, fetcher=fetcher)
    admin = await _seed_admin(session_factory)
    reg = await service.register(
        source="owner/repo", name="x", actor_user_id=admin.id,
    )
    agent = await _seed_agent(session_factory)
    async with session_factory() as db:
        await service.attach(
            db,
            agent_id=agent.id,
            skill_id=reg.entry.id,
            actor_user_id=admin.id,
        )
        await db.commit()

    agent_ids, existed = await service.delete(
        skill_id=reg.entry.id, actor_user_id=admin.id,
    )
    assert existed is True
    assert agent_ids == [agent.id]

    # After delete, the audit row persists but its FK is SET NULL.
    async with session_factory() as db:
        rows = (
            await db.execute(
                select(SkillLibraryAudit).where(
                    SkillLibraryAudit.action == "delete"
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].skill_library_id is None
    assert rows[0].detail["name"] == "x"


@pytest.mark.asyncio
async def test_delete_missing_returns_existed_false(session_factory):
    admin = await _seed_admin(session_factory)
    service = SkillLibraryService(session_factory)
    agent_ids, existed = await service.delete(
        skill_id="missing", actor_user_id=admin.id,
    )
    assert existed is False
    assert agent_ids == []
