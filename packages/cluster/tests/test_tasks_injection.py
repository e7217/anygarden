"""Tests for the synthetic task-assignment message injection helper (#266).

These cover the contract used by ``api/v1/tasks.py`` to drop a single
mention-bearing message into the room whenever a task gets (re)assigned
to an agent participant. The agent-side ``decide_policy`` then wakes up
through its existing mention path — see plan §3.2 decision 1.
"""

from __future__ import annotations

import pytest

from anygarden.db.models import Agent, Participant, Room, Task, User
from anygarden.messages.service import inject_task_assignment_message


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
async def test_content_first_line_carries_mention_and_title(db):
    """Plan §3.1 — the first line of ``content`` is the canonical
    addressable form (``<@user:pid> [TASK] title``). Renderers that read
    only the first line — including the frontend ``stripTaskMentionPrefix``
    — must continue to find a clean title there."""
    room, creator, assignee = await _seed_room_with_assignee(db)
    task = Task(
        room_id=room.id,
        title="ship the homepage",
        status="todo",
        assignee_participant_id=assignee.id,
    )
    db.add(task)
    await db.flush()

    msg = await inject_task_assignment_message(
        db, room=room, task=task, sender_participant_id=creator.id
    )
    first = msg.content.split("\n", 1)[0]
    assert first.startswith(f"<@user:{assignee.id}>")
    assert "[TASK]" in first
    assert task.title in first


@pytest.mark.asyncio
async def test_content_includes_mark_task_status_self_instruction(db):
    """#275 — The synthetic message embeds a self-instruction telling the
    LLM to call ``mark_task_status`` so the assignee actually reports
    progress instead of just answering as a normal mention."""
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
        db, room=room, task=task, sender_participant_id=creator.id
    )

    # The instruction must reference the tool name and the concrete
    # task_id so the LLM can call it without re-deriving anything from
    # metadata.
    assert "mark_task_status" in msg.content
    assert task.id in msg.content
    # And the canonical status enum values the LLM is expected to use.
    assert "in_progress" in msg.content
    assert "done" in msg.content


@pytest.mark.asyncio
async def test_content_highlights_required_task_status_actions(db):
    """#338 — The task-assignment self-instruction must be structured as
    an action block. A trailing decorative aside was too easy for some
    engines to skip, leaving tasks in ``todo`` until pickup timeout."""
    room, creator, assignee = await _seed_room_with_assignee(db)
    task = Task(
        room_id=room.id,
        title="review stale task handling",
        status="todo",
        assignee_participant_id=assignee.id,
    )
    db.add(task)
    await db.flush()

    msg = await inject_task_assignment_message(
        db, room=room, task=task, sender_participant_id=creator.id
    )

    assert "REQUIRED ACTIONS" in msg.content
    assert "시작 직후" in msg.content
    assert "응답 완료 시" in msg.content


class _FakeManager:
    """Captures broadcast invocations for unit-testing without WS."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def broadcast(self, room_id, frame, **_kwargs):  # noqa: D401
        self.calls.append((room_id, frame))


@pytest.mark.asyncio
async def test_inject_broadcasts_message_frame_when_manager_supplied(db):
    """#314 — When a ``ConnectionManager`` is supplied the helper must
    fanout a ``MessageOut`` frame on the room channel. Without this
    fanout the persisted row sits silently in the DB and the agent's
    ``decide_policy`` mention path never fires (this is *the* bug
    that #314 fixes)."""
    from anygarden.ws.protocol import MessageOut

    room, _, assignee = await _seed_room_with_assignee(db)
    task = Task(
        room_id=room.id,
        title="ping",
        status="todo",
        assignee_participant_id=assignee.id,
    )
    db.add(task)
    await db.flush()

    fake = _FakeManager()
    msg = await inject_task_assignment_message(
        db,
        room=room,
        task=task,
        sender_participant_id=None,
        manager=fake,  # type: ignore[arg-type]
    )

    assert len(fake.calls) == 1
    room_id, frame = fake.calls[0]
    assert room_id == room.id
    assert isinstance(frame, MessageOut)
    assert frame.id == msg.id
    assert frame.room_id == room.id
    assert frame.seq == msg.seq
    assert frame.content == msg.content
    # Metadata is forwarded verbatim — agent SDK relies on
    # ``mentions[type=user, id=<assignee_pid>]`` to wake.
    assert frame.metadata == msg.extra_metadata


@pytest.mark.asyncio
async def test_inject_does_not_broadcast_when_manager_omitted(db):
    """Backwards-compatible default: legacy callers and unit tests pass
    no ``manager`` and the helper stays DB-only. Guards against a
    future regression that quietly couples broadcast to persistence."""
    room, _, assignee = await _seed_room_with_assignee(db)
    task = Task(
        room_id=room.id,
        title="ping",
        status="todo",
        assignee_participant_id=assignee.id,
    )
    db.add(task)
    await db.flush()

    # No manager kwarg — must not raise and must produce a row.
    msg = await inject_task_assignment_message(
        db,
        room=room,
        task=task,
        sender_participant_id=None,
    )
    assert msg.id is not None


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
