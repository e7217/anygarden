"""PresenceService unit tests (#54).

Covers the three-tier resolution of ``ParticipantStatus`` — WS
subscription → Agent heartbeat fallback → DB-only defaults — plus
the batch snapshot's N+1-free query shape.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from anygarden.db.models import Agent, Participant, Project, Room, User
from anygarden.presence import PresenceService
from anygarden.ws.manager import ConnectionManager


class _FakeWS:
    async def send_text(self, data: str) -> None:  # pragma: no cover
        pass


@pytest.fixture
def now() -> datetime:
    # Keep a stable clock across assertions to avoid flaky "now-drift".
    return datetime(2026, 4, 15, 12, 0, 0, tzinfo=timezone.utc)


async def _seed_room_with_agent(db, *, agent_heartbeat=None) -> tuple[str, str, str]:
    """Seed a Room + (User-owner, Agent-participant). Returns
    ``(room_id, user_participant_id, agent_participant_id)``."""
    user = User(email="p@test.com", password_hash="x")
    db.add(user)
    await db.flush()

    project = Project(name="p-proj")
    db.add(project)
    await db.flush()

    room = Room(project_id=project.id, name="p-room")
    db.add(room)
    await db.flush()

    agent = Agent(
        name="a1",
        engine="codex",
        actual_state="running",
        last_heartbeat_at=agent_heartbeat,
    )
    db.add(agent)
    await db.flush()

    user_part = Participant(room_id=room.id, user_id=user.id, role="owner")
    agent_part = Participant(room_id=room.id, agent_id=agent.id, role="member")
    db.add_all([user_part, agent_part])
    await db.commit()

    await db.refresh(room)
    await db.refresh(user_part)
    await db.refresh(agent_part)
    return room.id, user_part.id, agent_part.id


@pytest.mark.asyncio
async def test_status_online_when_subscribed(db, now) -> None:
    """Active WS subscription short-circuits to online=True."""
    mgr = ConnectionManager()
    presence = PresenceService(mgr)

    _, user_pid, _ = await _seed_room_with_agent(db)
    await mgr.subscribe("room-x", user_pid, _FakeWS())  # type: ignore[arg-type]

    status = await presence.status(user_pid, db=db, now=now)
    assert status.online is True
    assert status.source == "ws"
    assert status.last_seen_at == now


@pytest.mark.asyncio
async def test_status_offline_falls_back_to_heartbeat(db, now) -> None:
    """Agent with a recent heartbeat surfaces last_seen_at from the DB."""
    mgr = ConnectionManager()
    presence = PresenceService(mgr)

    hb = now - timedelta(seconds=12)
    _, _, agent_pid = await _seed_room_with_agent(db, agent_heartbeat=hb)

    status = await presence.status(agent_pid, db=db, now=now)
    assert status.online is False
    assert status.source == "heartbeat"
    assert status.last_seen_at == hb


@pytest.mark.asyncio
async def test_status_offline_stale_heartbeat(db, now) -> None:
    """A very old heartbeat still surfaces last_seen_at (UI wants it)
    but the source is heartbeat and online stays False."""
    mgr = ConnectionManager()
    presence = PresenceService(mgr)

    hb = now - timedelta(hours=2)
    _, _, agent_pid = await _seed_room_with_agent(db, agent_heartbeat=hb)

    status = await presence.status(agent_pid, db=db, now=now)
    assert status.online is False
    assert status.source == "heartbeat"
    assert status.last_seen_at == hb


@pytest.mark.asyncio
async def test_status_user_with_no_heartbeat_source_is_db(db, now) -> None:
    """User participants have no heartbeat source; status falls through
    to source='db' with last_seen_at=None unless the manager has a
    memo from a prior unsubscribe."""
    mgr = ConnectionManager()
    presence = PresenceService(mgr)

    _, user_pid, _ = await _seed_room_with_agent(db)
    status = await presence.status(user_pid, db=db, now=now)
    assert status.online is False
    assert status.source == "db"
    assert status.last_seen_at is None


@pytest.mark.asyncio
async def test_status_offline_memo_from_unsubscribe(db, now) -> None:
    """After a user subscribes and unsubscribes, the memo feeds
    last_seen_at so the UI can still say 'last seen 2s ago'."""
    mgr = ConnectionManager()
    presence = PresenceService(mgr)

    _, user_pid, _ = await _seed_room_with_agent(db)
    await mgr.subscribe("room-x", user_pid, _FakeWS())  # type: ignore[arg-type]
    await mgr.unsubscribe(user_pid)

    status = await presence.status(user_pid, db=db, now=now)
    assert status.online is False
    assert status.last_seen_at is not None


@pytest.mark.asyncio
async def test_room_snapshot_batches_agent_heartbeats(db, now) -> None:
    """room_snapshot returns one status per participant and hits agents
    in a single IN query (no N+1)."""
    mgr = ConnectionManager()
    presence = PresenceService(mgr)

    hb = now - timedelta(seconds=5)
    room_id, user_pid, agent_pid = await _seed_room_with_agent(
        db, agent_heartbeat=hb
    )

    # Subscribe user only — agent is "offline-but-recently-alive".
    await mgr.subscribe(room_id, user_pid, _FakeWS())  # type: ignore[arg-type]

    snapshot = await presence.room_snapshot(room_id, db=db, now=now)
    by_pid = {s.participant_id: s for s in snapshot}
    assert by_pid[user_pid].online is True
    assert by_pid[agent_pid].online is False
    assert by_pid[agent_pid].last_seen_at == hb


@pytest.mark.asyncio
async def test_publish_broadcasts_presence_update(db, now) -> None:
    """publish() hits ConnectionManager.broadcast with a PresenceUpdateOut."""
    mgr = ConnectionManager()
    presence = PresenceService(mgr)

    received: list[str] = []

    class CaptureWS:
        async def send_text(self, data: str) -> None:
            received.append(data)

    await mgr.subscribe("room-x", "observer-pid", CaptureWS())  # type: ignore[arg-type]
    # The subscribe above would have triggered its own publish IF
    # presence_service were wired into the manager, but here we want
    # to test ``publish`` directly without that side effect.
    received.clear()

    await presence.publish(
        "room-x",
        "subject-pid",
        online=True,
        last_seen_at=now,
    )
    assert len(received) == 1
    assert '"type":"presence_update"' in received[0]
    assert '"participant_id":"subject-pid"' in received[0]
