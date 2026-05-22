"""Tests for the server-side ``[HANDOFF]`` wiring (#159 Phase C).

Exercises ``_apply_orchestrator_handoff`` in isolation — the helper
that parses a ``[HANDOFF]`` prefixed message from the room's
orchestrator, updates ``Room.next_speaker_participant_id``, and
stamps ``metadata.next_speaker_participant_id`` so the broadcast
triggers the target agent's ``decide_policy`` O2 rule.

The helper is deliberately decoupled from the full WS handler so
these tests stay fast and don't depend on WS plumbing. End-to-end
coverage happens at the feature-test layer.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select

from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Agent, Base, Participant, Project, Room
from anygarden.ws.handler import _apply_orchestrator_handoff


@pytest_asyncio.fixture()
async def handoff_env(config: AnygardenSettings):
    """Room with orchestrator agent + worker agent, both participants."""
    engine = build_engine(config.db_url)
    session_factory = build_session_factory(engine)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with session_factory() as db:
        project = Project(name="handoff-proj")
        db.add(project)
        await db.flush()

        orc_agent = Agent(name="orc-bot", engine="claude-code")
        worker_agent = Agent(name="worker-bot", engine="claude-code")
        db.add_all([orc_agent, worker_agent])
        await db.flush()

        room = Room(
            project_id=project.id,
            name="handoff-room",
            speaker_strategy="orchestrator",
            orchestrator_agent_id=orc_agent.id,
        )
        db.add(room)
        await db.flush()

        orc_part = Participant(room_id=room.id, agent_id=orc_agent.id, role="member")
        worker_part = Participant(
            room_id=room.id, agent_id=worker_agent.id, role="member"
        )
        db.add_all([orc_part, worker_part])
        await db.flush()
        await db.commit()

        for obj in (room, orc_agent, worker_agent, orc_part, worker_part):
            await db.refresh(obj)

    yield {
        "session_factory": session_factory,
        "room": room,
        "orc_agent": orc_agent,
        "worker_agent": worker_agent,
        "orc_part": orc_part,
        "worker_part": worker_part,
    }
    await engine.dispose()


@pytest.mark.asyncio
async def test_orchestrator_handoff_updates_next_speaker(handoff_env):
    """Orchestrator sends ``[HANDOFF] <@user:worker-pid> …`` →
    ``Room.next_speaker_participant_id`` flips to the worker."""
    sf = handoff_env["session_factory"]
    room = handoff_env["room"]
    orc_agent = handoff_env["orc_agent"]
    worker_part = handoff_env["worker_part"]

    metadata = {
        "mentions": [{"type": "user", "id": worker_part.id}],
        "_nonce": "n1",
    }
    async with sf() as db:
        applied = await _apply_orchestrator_handoff(
            db,
            room_id=room.id,
            content=f"[HANDOFF] <@user:{worker_part.id}> take it",
            metadata=metadata,
            orchestrator_agent_id=orc_agent.id,
            sender_agent_id=orc_agent.id,
        )
        assert applied == worker_part.id
        # ``metadata`` is mutated in place so the broadcast carries
        # the stamp — the agent's O2 rule reads it from here.
        assert metadata.get("next_speaker_participant_id") == worker_part.id
        await db.commit()

    async with sf() as db:
        reloaded = await db.scalar(select(Room).where(Room.id == room.id))
        assert reloaded is not None
        assert reloaded.next_speaker_participant_id == worker_part.id


@pytest.mark.asyncio
async def test_non_orchestrator_handoff_is_ignored(handoff_env):
    """A worker sending ``[HANDOFF]`` does NOT get to flip the pointer.

    Defends against a misbehaving or compromised worker that tries to
    hijack turn order. The message itself still carries the prefix
    so the target agent can still be addressed via normal mention
    rules — we just refuse to update Room state from it."""
    sf = handoff_env["session_factory"]
    room = handoff_env["room"]
    orc_agent = handoff_env["orc_agent"]
    worker_agent = handoff_env["worker_agent"]
    orc_part = handoff_env["orc_part"]

    metadata = {
        "mentions": [{"type": "user", "id": orc_part.id}],
        "_nonce": "n1",
    }
    async with sf() as db:
        applied = await _apply_orchestrator_handoff(
            db,
            room_id=room.id,
            content=f"[HANDOFF] <@user:{orc_part.id}> over to you",
            metadata=metadata,
            orchestrator_agent_id=orc_agent.id,
            sender_agent_id=worker_agent.id,  # NOT the orchestrator
        )
        assert applied is None
        assert "next_speaker_participant_id" not in metadata


@pytest.mark.asyncio
async def test_handoff_without_target_mention_ignored(handoff_env):
    """``[HANDOFF]`` prefix without a parsable user mention is treated
    as ordinary text — no Room update, no metadata stamp."""
    sf = handoff_env["session_factory"]
    room = handoff_env["room"]
    orc_agent = handoff_env["orc_agent"]

    metadata: dict = {"_nonce": "n1"}
    async with sf() as db:
        applied = await _apply_orchestrator_handoff(
            db,
            room_id=room.id,
            content="[HANDOFF] someone please take over",
            metadata=metadata,
            orchestrator_agent_id=orc_agent.id,
            sender_agent_id=orc_agent.id,
        )
        assert applied is None
        assert "next_speaker_participant_id" not in metadata


@pytest.mark.asyncio
async def test_handoff_target_not_in_room_ignored(handoff_env):
    """Target participant id isn't a member of this room → refuse
    the update. LLMs hallucinate ids; the server is the trust root."""
    sf = handoff_env["session_factory"]
    room = handoff_env["room"]
    orc_agent = handoff_env["orc_agent"]

    metadata = {
        "mentions": [{"type": "user", "id": "ghost-participant-id"}],
        "_nonce": "n1",
    }
    async with sf() as db:
        applied = await _apply_orchestrator_handoff(
            db,
            room_id=room.id,
            content="[HANDOFF] <@user:ghost-participant-id> ...",
            metadata=metadata,
            orchestrator_agent_id=orc_agent.id,
            sender_agent_id=orc_agent.id,
        )
        assert applied is None


@pytest.mark.asyncio
async def test_non_handoff_content_short_circuits(handoff_env):
    """Plain messages never enter the handoff path, even from the
    orchestrator — only the explicit ``[HANDOFF]`` prefix triggers
    the pointer update."""
    sf = handoff_env["session_factory"]
    room = handoff_env["room"]
    orc_agent = handoff_env["orc_agent"]
    worker_part = handoff_env["worker_part"]

    metadata = {
        "mentions": [{"type": "user", "id": worker_part.id}],
        "_nonce": "n1",
    }
    async with sf() as db:
        applied = await _apply_orchestrator_handoff(
            db,
            room_id=room.id,
            content=f"<@user:{worker_part.id}> ordinary mention",
            metadata=metadata,
            orchestrator_agent_id=orc_agent.id,
            sender_agent_id=orc_agent.id,
        )
        assert applied is None
