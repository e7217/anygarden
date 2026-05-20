"""Tests for the orchestrator mention-missing server-side fallback.

Exercises ``_apply_orchestrator_fallback_nominate`` in isolation —
the helper that rotates to the next non-orchestrator participant
when the moderator LLM emits a non-terminal message without a valid
handoff or addressable mention.

Background: docs/research/2026-05-12-multi-agent-turn-taking-
mediator-failure.md documents the failure pattern this fallback
defends against (V1-V5 PoC observed orchestrator omit mention from
the second handoff onward in 5/5 trials).
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from doorae.config import DooraeSettings
from doorae.db.engine import build_engine, build_session_factory
from doorae.db.models import Agent, Base, Participant, Project, Room
from doorae.ws.handler import _apply_orchestrator_fallback_nominate


@pytest_asyncio.fixture()
async def fallback_env(config: DooraeSettings):
    """Room with orchestrator agent + 2 worker agents, all participants.

    Joined order: orchestrator → worker1 → worker2. The fallback
    helper rotates among workers only (excluding the orchestrator),
    so with current_speaker_index=0 it should nominate worker2
    (the second entry after rotation: (0+1)%2=1).
    """
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        project = Project(name="fallback-proj")
        db.add(project)
        await db.flush()

        orc_agent = Agent(name="orc-bot", engine="claude-code")
        worker1_agent = Agent(name="worker1-bot", engine="claude-code")
        worker2_agent = Agent(name="worker2-bot", engine="claude-code")
        db.add_all([orc_agent, worker1_agent, worker2_agent])
        await db.flush()

        room = Room(
            project_id=project.id,
            name="fallback-room",
            speaker_strategy="orchestrator",
            orchestrator_agent_id=orc_agent.id,
        )
        db.add(room)
        await db.flush()

        orc_part = Participant(
            room_id=room.id, agent_id=orc_agent.id, role="member"
        )
        db.add(orc_part)
        await db.flush()
        worker1_part = Participant(
            room_id=room.id, agent_id=worker1_agent.id, role="member"
        )
        db.add(worker1_part)
        await db.flush()
        worker2_part = Participant(
            room_id=room.id, agent_id=worker2_agent.id, role="member"
        )
        db.add(worker2_part)
        await db.flush()
        await db.commit()

        for obj in (
            room,
            orc_agent,
            worker1_agent,
            worker2_agent,
            orc_part,
            worker1_part,
            worker2_part,
        ):
            await db.refresh(obj)

    yield {
        "session_factory": session_factory,
        "room": room,
        "orc_agent": orc_agent,
        "worker1_agent": worker1_agent,
        "worker2_agent": worker2_agent,
        "orc_part": orc_part,
        "worker1_part": worker1_part,
        "worker2_part": worker2_part,
    }
    await engine.dispose()


@pytest.mark.asyncio
async def test_fallback_nominates_when_orchestrator_omits_mention(fallback_env):
    """Orchestrator emits a plain message (no [HANDOFF], no @mention).
    Fallback should rotate to the next non-orchestrator participant
    and stamp ``next_speaker_participant_id``."""
    sf = fallback_env["session_factory"]
    room = fallback_env["room"]
    orc_agent = fallback_env["orc_agent"]
    worker1_part = fallback_env["worker1_part"]

    metadata: dict = {"mentions": [], "_nonce": "n1"}
    async with sf() as db:
        result = await _apply_orchestrator_fallback_nominate(
            db,
            room_id=room.id,
            content="이제 Pragmatist 차례입니다.",
            metadata=metadata,
            orchestrator_agent_id=orc_agent.id,
            sender_agent_id=orc_agent.id,
            current_speaker_index=0,
        )
        # joined order: worker1, worker2 (orchestrator excluded).
        # (0 + 1) % 2 = 1 → worker2 nominated.
        worker2_part = fallback_env["worker2_part"]
        assert result is not None
        new_index, next_pid = result
        assert new_index == 1
        assert next_pid == worker2_part.id
        assert metadata["next_speaker_participant_id"] == worker2_part.id
        await db.commit()

        # Room.current_speaker_index + next_speaker_participant_id
        # persisted for replay safety.
        refreshed = (
            await db.execute(select(Room).where(Room.id == room.id))
        ).scalar_one()
        assert refreshed.current_speaker_index == 1
        assert refreshed.next_speaker_participant_id == worker2_part.id
        # worker1 not selected this rotation
        assert next_pid != worker1_part.id


@pytest.mark.asyncio
async def test_fallback_noop_when_mention_present(fallback_env):
    """Addressable mention in metadata → fallback declines so mention
    routing handles it normally (no override)."""
    sf = fallback_env["session_factory"]
    room = fallback_env["room"]
    orc_agent = fallback_env["orc_agent"]
    worker1_part = fallback_env["worker1_part"]

    metadata: dict = {
        "mentions": [{"type": "user", "id": worker1_part.id}],
        "_nonce": "n2",
    }
    async with sf() as db:
        result = await _apply_orchestrator_fallback_nominate(
            db,
            room_id=room.id,
            content=f"<@user:{worker1_part.id}> 발언 부탁드립니다.",
            metadata=metadata,
            orchestrator_agent_id=orc_agent.id,
            sender_agent_id=orc_agent.id,
            current_speaker_index=0,
        )
        assert result is None
        assert "next_speaker_participant_id" not in metadata


@pytest.mark.asyncio
async def test_fallback_noop_on_termination_marker(fallback_env):
    """Orchestrator emits ``[종료]`` → fallback respects explicit
    termination and does not nominate, so the room comes to rest."""
    sf = fallback_env["session_factory"]
    room = fallback_env["room"]
    orc_agent = fallback_env["orc_agent"]

    metadata: dict = {"mentions": [], "_nonce": "n3"}
    async with sf() as db:
        result = await _apply_orchestrator_fallback_nominate(
            db,
            room_id=room.id,
            content="[종료]\n- 합의: A → B\n- 다음 단계: PR 작성",
            metadata=metadata,
            orchestrator_agent_id=orc_agent.id,
            sender_agent_id=orc_agent.id,
            current_speaker_index=0,
        )
        assert result is None
        assert "next_speaker_participant_id" not in metadata


@pytest.mark.asyncio
async def test_fallback_noop_when_sender_is_not_orchestrator(fallback_env):
    """Worker agent sends a no-mention message → fallback only fires
    on orchestrator output; worker chatter is not the moderator's
    failure mode."""
    sf = fallback_env["session_factory"]
    room = fallback_env["room"]
    orc_agent = fallback_env["orc_agent"]
    worker1_agent = fallback_env["worker1_agent"]

    metadata: dict = {"mentions": [], "_nonce": "n4"}
    async with sf() as db:
        result = await _apply_orchestrator_fallback_nominate(
            db,
            room_id=room.id,
            content="제 입장은 A 후보입니다.",
            metadata=metadata,
            orchestrator_agent_id=orc_agent.id,
            sender_agent_id=worker1_agent.id,  # not the orchestrator
            current_speaker_index=0,
        )
        assert result is None
        assert "next_speaker_participant_id" not in metadata


@pytest.mark.asyncio
async def test_fallback_noop_when_next_speaker_already_stamped(fallback_env):
    """Upstream ``_apply_orchestrator_handoff`` already set
    ``next_speaker_participant_id`` → fallback must not override it.
    Simulates the case where the orchestrator did emit a valid
    [HANDOFF] and the stamp is already in metadata."""
    sf = fallback_env["session_factory"]
    room = fallback_env["room"]
    orc_agent = fallback_env["orc_agent"]
    worker1_part = fallback_env["worker1_part"]

    metadata: dict = {
        "mentions": [],
        "next_speaker_participant_id": worker1_part.id,
        "_nonce": "n5",
    }
    async with sf() as db:
        result = await _apply_orchestrator_fallback_nominate(
            db,
            room_id=room.id,
            content="Worker1, 진행 부탁합니다.",
            metadata=metadata,
            orchestrator_agent_id=orc_agent.id,
            sender_agent_id=orc_agent.id,
            current_speaker_index=0,
        )
        assert result is None
        # Original stamp preserved untouched.
        assert metadata["next_speaker_participant_id"] == worker1_part.id


@pytest.mark.asyncio
async def test_fallback_noop_when_only_orchestrator_in_room(config):
    """Degenerate room with only the orchestrator (no workers) →
    nothing to nominate; helper returns None without raising."""
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        async with session_factory() as db:
            project = Project(name="solo-proj")
            db.add(project)
            await db.flush()
            orc_agent = Agent(name="orc-only", engine="claude-code")
            db.add(orc_agent)
            await db.flush()
            room = Room(
                project_id=project.id,
                name="solo-room",
                speaker_strategy="orchestrator",
                orchestrator_agent_id=orc_agent.id,
            )
            db.add(room)
            await db.flush()
            db.add(
                Participant(
                    room_id=room.id, agent_id=orc_agent.id, role="member"
                )
            )
            await db.commit()
            await db.refresh(room)
            await db.refresh(orc_agent)

            metadata: dict = {"mentions": [], "_nonce": "solo"}
            result = await _apply_orchestrator_fallback_nominate(
                db,
                room_id=room.id,
                content="혼잣말입니다.",
                metadata=metadata,
                orchestrator_agent_id=orc_agent.id,
                sender_agent_id=orc_agent.id,
                current_speaker_index=0,
            )
            assert result is None
            assert "next_speaker_participant_id" not in metadata
    finally:
        await engine.dispose()
