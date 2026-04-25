"""Tests for the synthetic task-assignment message injection helper (#266).

These cover the contract used by ``api/v1/tasks.py`` to drop a single
mention-bearing message into the room whenever a task gets (re)assigned
to an agent participant. The agent-side ``decide_policy`` then wakes up
through its existing mention path — see plan §3.2 decision 1.
"""

from __future__ import annotations

import pytest

from doorae.db.models import Agent, Participant, Room, Task, User
from doorae.messages.service import inject_task_assignment_message


async def _seed_room_with_assignee(db) -> tuple[Room, Participant, Participant]:
    """Build a room with one human participant (creator) and one agent
    participant (assignee). Returns the room, creator participant, and
    assignee participant."""
    user = User(email="creator@example.com", password_hash="x")
    db.add(user)
    agent = Agent(name="bot", engine="codex")
    db.add(agent)
    room = Room(name="r")
    db.add(room)
    await db.flush()

    creator = Participant(room_id=room.id, user_id=user.id, role="member")
    assignee = Participant(room_id=room.id, agent_id=agent.id, role="member")
    db.add_all([creator, assignee])
    await db.flush()
    return room, creator, assignee


@pytest.mark.asyncio
async def test_inject_creates_message_with_mention_and_metadata(db):
    room, creator, assignee = await _seed_room_with_assignee(db)
    task = Task(
        room_id=room.id,
        title="design review",
        status="todo",
        assignee_participant_id=assignee.id,
    )
    db.add(task)
    await db.flush()

    msg = await inject_task_assignment_message(
        db,
        room=room,
        task=task,
        sender_participant_id=creator.id,
    )

    # Persisted with the caller's participant as sender.
    assert msg.participant_id == creator.id
    assert msg.room_id == room.id
    # Content carries the mention token and a [TASK] marker so the
    # message is intelligible even without metadata interpretation.
    assert f"<@user:{assignee.id}>" in msg.content
    assert "[TASK]" in msg.content
    assert task.title in msg.content
    # Metadata: parsed mention list + task_assignment payload.
    assert msg.extra_metadata is not None
    assert msg.extra_metadata["mentions"] == [
        {"type": "user", "id": assignee.id}
    ]
    ta = msg.extra_metadata["task_assignment"]
    assert ta["task_id"] == task.id
    assert ta["assignee_pid"] == assignee.id
    assert ta["event"] == "assigned"
    # When a real participant carries the message there is no
    # ``system_origin`` marker — that is reserved for NULL-sender
    # injections (see next test).
    assert "system_origin" not in msg.extra_metadata


@pytest.mark.asyncio
async def test_inject_with_null_sender_marks_system_origin(db):
    room, _, assignee = await _seed_room_with_assignee(db)
    task = Task(
        room_id=room.id,
        title="weekly cleanup",
        status="todo",
        assignee_participant_id=assignee.id,
    )
    db.add(task)
    await db.flush()

    msg = await inject_task_assignment_message(
        db,
        room=room,
        task=task,
        sender_participant_id=None,
    )

    # NULL sender is allowed (Message.participant_id is nullable) and
    # the metadata flags the synthetic origin so renderers/auditors can
    # tell this row apart from a stray user-with-no-participant case.
    assert msg.participant_id is None
    assert msg.extra_metadata is not None
    assert msg.extra_metadata.get("system_origin") == "task_assignment"


@pytest.mark.asyncio
async def test_inject_reassigned_event_is_propagated(db):
    room, creator, assignee = await _seed_room_with_assignee(db)
    task = Task(
        room_id=room.id,
        title="rerun migration",
        status="todo",
        assignee_participant_id=assignee.id,
    )
    db.add(task)
    await db.flush()

    msg = await inject_task_assignment_message(
        db,
        room=room,
        task=task,
        sender_participant_id=creator.id,
        event="reassigned",
    )
    assert msg.extra_metadata is not None
    assert msg.extra_metadata["task_assignment"]["event"] == "reassigned"
