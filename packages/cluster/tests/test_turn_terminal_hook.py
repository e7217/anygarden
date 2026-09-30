"""#762 — every terminal AgentTurn transition goes through ``mark_turn_terminal``.

The async peer fan-in (PR2) hangs off ``_on_turn_terminal``. It is only
reliable if every path that closes a turn (completed / cancelled / failed)
fires it exactly once, and nothing that leaves the turn open (lease, retry,
lease expiry) fires it at all. Each test drives one real path and counts the
hook invocations with a spy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from anygarden.db.models import (
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    Message,
    Participant,
    Room,
    Task,
    WorkspaceAttachment,
)
from anygarden.db.repository import append_message
from anygarden.turns import service as turn_service
from anygarden.turns.service import (
    begin_completion,
    cancel_invalid_turns,
    create_turn,
    deliver_pending_outbox,
    finish_completion,
    mark_turn_terminal,
    record_lifecycle,
    recover_stalled_turns,
)
from anygarden.workspaces.service import cancel_attachment_turns
from tests import test_durable_turns as durable
from tests import test_workspace_attachments as workspace_tests

# Shared fixtures, re-exported so pytest discovers them in this module.
turn_env = durable.turn_env
workspace_env = workspace_tests.workspace_env
FakeManager = durable.FakeManager
WorkspaceFakeManager = workspace_tests.FakeManager
_create_and_deliver = durable._create_and_deliver
_skipped_frame = durable._skipped_frame


@pytest.fixture()
def hook_calls(monkeypatch) -> list[tuple[str, str, str | None]]:
    """Replace the no-op extension point with a recording spy."""

    calls: list[tuple[str, str, str | None]] = []

    async def spy(db, turn) -> None:
        # The hook must observe the already-applied terminal fields.
        assert turn.state in {"completed", "cancelled", "failed"}
        calls.append((turn.request_id, turn.state, turn.terminal_reason))

    monkeypatch.setattr(turn_service, "_on_turn_terminal", spy)
    return calls


def _lifecycle_frame(env, request_id: str, metadata: dict, **fields):
    base = {
        "request_id": request_id,
        "room_id": env["room"],
        "event": "handler_finished",
        "outcome": "ok",
        "turn_attempt": metadata["turn_attempt"],
        "turn_generation": metadata["turn_generation"],
        "turn_lease": metadata["turn_lease"],
    }
    base.update(fields)
    return SimpleNamespace(**base)


async def _expire_active_attempt(env, request_id: str, now: datetime) -> None:
    async with env["factory"]() as db:
        turn = await db.get(AgentTurn, request_id)
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(
                AgentTurnAttempt.turn_id == request_id,
                AgentTurnAttempt.attempt_number == turn.active_attempt,
            )
        )
        attempt.lease_expires_at = now - timedelta(seconds=1)
        await db.commit()


# ---------------------------------------------------------------- helper


@pytest.mark.asyncio
async def test_helper_rejects_open_state(turn_env, hook_calls) -> None:
    async with turn_env["factory"]() as db:
        turn = AgentTurn(request_id="r", room_id=turn_env["room"], state="pending")
        with pytest.raises(ValueError):
            await mark_turn_terminal(db, turn, state="retrying", reason=None)
    assert hook_calls == []


# ---------------------------------------------------------------- create_turn


@pytest.mark.asyncio
async def test_create_turn_open_does_not_fire(turn_env, hook_calls) -> None:
    await _create_and_deliver(turn_env)
    assert hook_calls == []


@pytest.mark.asyncio
async def test_create_turn_agent_not_running_fires_once(turn_env, hook_calls) -> None:
    async with turn_env["factory"]() as db:
        agent = await db.get(Agent, turn_env["agent"])
        agent.desired_state = "stopped"
        trigger = await append_message(
            db, turn_env["room"], turn_env["user_participant"], "stopped agent"
        )
        turn = await create_turn(
            db,
            room_id=turn_env["room"],
            participant_id=turn_env["agent_participant"],
            agent_id=turn_env["agent"],
            trigger_message_id=trigger.id,
        )
        await db.commit()
        # Idempotent replay returns the existing row without a new transition.
        again = await create_turn(
            db,
            room_id=turn_env["room"],
            participant_id=turn_env["agent_participant"],
            agent_id=turn_env["agent"],
            trigger_message_id=trigger.id,
        )
        assert again.request_id == turn.request_id

    assert hook_calls == [(turn.request_id, "cancelled", "agent_not_running")]
    async with turn_env["factory"]() as db:
        stored = await db.get(AgentTurn, turn.request_id)
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == turn.request_id)
        )
        assert stored.completed_at is None
        assert attempt.state == "cancelled"
        assert attempt.reason == "agent_not_running"


@pytest.mark.asyncio
async def test_create_turn_workspace_denied_fires_once(
    workspace_env, hook_calls
) -> None:
    async with workspace_env["factory"]() as db:
        message = await append_message(
            db,
            workspace_env["room"],
            workspace_env["user_participant"],
            "change the workspace",
        )
        denied = await create_turn(
            db,
            room_id=workspace_env["room"],
            participant_id=workspace_env["agent_participant"],
            agent_id=workspace_env["agent"],
            trigger_message_id=message.id,
        )
        await db.commit()
    assert hook_calls == [
        (denied.request_id, "cancelled", "workspace_write_requires_task")
    ]


# ------------------------------------------------------ deliver_pending_outbox


@pytest.mark.asyncio
async def test_delivery_workspace_revoked_fires_once(workspace_env, hook_calls) -> None:
    async with workspace_env["factory"]() as db:
        message = await append_message(
            db,
            workspace_env["room"],
            workspace_env["user_participant"],
            "epoch-fenced change",
        )
        task = Task(
            room_id=workspace_env["room"],
            source_message_id=message.id,
            title="epoch-fenced",
            status="in_progress",
            assignee_participant_id=workspace_env["agent_participant"],
            created_by=workspace_env["user"],
        )
        db.add(task)
        await db.flush()
        turn = await create_turn(
            db,
            room_id=workspace_env["room"],
            participant_id=workspace_env["agent_participant"],
            agent_id=workspace_env["agent"],
            trigger_message_id=message.id,
            task_id=task.id,
        )
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        attachment.epoch += 1
        await db.commit()
    assert hook_calls == []

    manager = WorkspaceFakeManager(workspace_env["agent_participant"])
    assert await deliver_pending_outbox(workspace_env["factory"], manager) == 0
    assert hook_calls == [(turn.request_id, "cancelled", "workspace_epoch_mismatch")]


@pytest.mark.asyncio
async def test_delivery_authorization_revoked_fires_once(turn_env, hook_calls) -> None:
    async with turn_env["factory"]() as db:
        participant = await db.get(Participant, turn_env["agent_participant"])
        participant.role = "observer"
        trigger = await append_message(
            db, turn_env["room"], turn_env["user_participant"], "observer target"
        )
        turn = await create_turn(
            db,
            room_id=turn_env["room"],
            participant_id=turn_env["agent_participant"],
            agent_id=turn_env["agent"],
            trigger_message_id=trigger.id,
        )
        await db.commit()

    manager = FakeManager(turn_env["agent_participant"], generation=3)
    assert await deliver_pending_outbox(turn_env["factory"], manager) == 0
    assert await deliver_pending_outbox(turn_env["factory"], manager) == 0
    assert hook_calls == [(turn.request_id, "cancelled", "authorization_revoked")]


@pytest.mark.asyncio
# The delivery path looks up the SET-NULL'd trigger id; SQLAlchemy warns.
@pytest.mark.filterwarnings("ignore:fully NULL primary key")
async def test_delivery_trigger_deleted_fires_once(turn_env, hook_calls) -> None:
    async with turn_env["factory"]() as db:
        trigger = await append_message(
            db, turn_env["room"], turn_env["user_participant"], "soon deleted"
        )
        turn = await create_turn(
            db,
            room_id=turn_env["room"],
            participant_id=turn_env["agent_participant"],
            agent_id=turn_env["agent"],
            trigger_message_id=trigger.id,
        )
        await db.commit()
    async with turn_env["factory"]() as db:
        await db.delete(await db.get(Message, trigger.id))
        await db.commit()

    manager = FakeManager(turn_env["agent_participant"], generation=3)
    assert await deliver_pending_outbox(turn_env["factory"], manager) == 0
    assert hook_calls == [(turn.request_id, "cancelled", "trigger_message_deleted")]
    async with turn_env["factory"]() as db:
        stored = await db.get(AgentTurn, turn.request_id)
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == turn.request_id)
        )
        # Delivery-time cancels never stamped completion/ended timestamps.
        assert stored.completed_at is None
        assert attempt.ended_at is None
        assert attempt.reason == "trigger_message_deleted"


# ---------------------------------------------------------- completion path


@pytest.mark.asyncio
async def test_finish_completion_fires_once(turn_env, hook_calls) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    metadata = manager.frames[0].metadata
    async with turn_env["factory"]() as db:
        decision = await begin_completion(
            db,
            request_id=request_id,
            room_id=turn_env["room"],
            participant_id=turn_env["agent_participant"],
            agent_id=turn_env["agent"],
            attempt_number=metadata["turn_attempt"],
            generation=metadata["turn_generation"],
            lease_token=metadata["turn_lease"],
        )
        assert decision.outcome == "accept"
        # Reserving the completion ("completing") is not terminal.
        assert hook_calls == []
        reply = await append_message(
            db, turn_env["room"], turn_env["agent_participant"], "answer"
        )
        await finish_completion(
            db, turn=decision.turn, attempt=decision.attempt, message_id=reply.id
        )
        await db.commit()
    assert hook_calls == [(request_id, "completed", None)]

    # The trailing handler_finished on an already-completed turn only closes
    # the attempt bookkeeping; it is not a second transition.
    async with turn_env["factory"]() as db:
        assert await record_lifecycle(
            db,
            agent_id=turn_env["agent"],
            frame=_lifecycle_frame(turn_env, request_id, metadata),
        )
        await db.commit()
    assert hook_calls == [(request_id, "completed", None)]
    async with turn_env["factory"]() as db:
        stored = await db.get(AgentTurn, request_id)
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == request_id)
        )
        assert stored.accepted_message_id == reply.id
        assert stored.completed_at is not None
        assert attempt.state == "completed"
        assert attempt.ended_at is not None


# ---------------------------------------------------------- record_lifecycle


@pytest.mark.asyncio
async def test_lifecycle_started_does_not_fire(turn_env, hook_calls) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    metadata = manager.frames[0].metadata
    async with turn_env["factory"]() as db:
        assert await record_lifecycle(
            db,
            agent_id=turn_env["agent"],
            frame=_lifecycle_frame(
                turn_env, request_id, metadata, event="handler_started"
            ),
        )
        await db.commit()
    assert hook_calls == []


@pytest.mark.asyncio
async def test_lifecycle_cancelled_fires_once(turn_env, hook_calls) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    metadata = manager.frames[0].metadata
    async with turn_env["factory"]() as db:
        assert await record_lifecycle(
            db,
            agent_id=turn_env["agent"],
            frame=_lifecycle_frame(turn_env, request_id, metadata, outcome="cancelled"),
        )
        await db.commit()
    assert hook_calls == [(request_id, "cancelled", "agent_cancelled")]
    async with turn_env["factory"]() as db:
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == request_id)
        )
        assert attempt.state == "cancelled"
        assert attempt.outcome == "cancelled"
        assert attempt.reason is None


@pytest.mark.asyncio
async def test_lifecycle_skipped_fires_once(turn_env, hook_calls) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    metadata = manager.frames[0].metadata
    async with turn_env["factory"]() as db:
        assert await record_lifecycle(
            db,
            agent_id=turn_env["agent"],
            frame=_skipped_frame(turn_env, request_id, metadata),
        )
        await db.commit()
    assert hook_calls == [(request_id, "completed", "agent_skipped")]


@pytest.mark.asyncio
async def test_lifecycle_failed_without_completion_does_not_fire(
    turn_env, hook_calls
) -> None:
    # A terminal frame without a reply only expires the lease; the turn stays
    # open for bounded recovery.
    request_id, _, manager = await _create_and_deliver(turn_env)
    metadata = manager.frames[0].metadata
    async with turn_env["factory"]() as db:
        assert await record_lifecycle(
            db,
            agent_id=turn_env["agent"],
            frame=_lifecycle_frame(turn_env, request_id, metadata, outcome="failed"),
        )
        await db.commit()
    assert hook_calls == []


# ------------------------------------------------------ recover_stalled_turns


@pytest.mark.asyncio
async def test_recovery_retry_then_exhaustion_fires_once(turn_env, hook_calls) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    now = datetime.now(UTC)
    await _expire_active_attempt(turn_env, request_id, now)
    first = await recover_stalled_turns(turn_env["factory"], manager, now=now)
    assert first.redispatched == 1
    # Lease expiry -> retrying keeps the turn open.
    assert hook_calls == []

    assert await deliver_pending_outbox(turn_env["factory"], manager) == 1
    await _expire_active_attempt(turn_env, request_id, now)
    second = await recover_stalled_turns(turn_env["factory"], manager, now=now)
    assert second.failed == 1
    again = await recover_stalled_turns(turn_env["factory"], manager, now=now)
    assert again.failed == 0
    assert hook_calls == [(request_id, "failed", "retry_exhausted")]


@pytest.mark.asyncio
async def test_recovery_legacy_interrupted_fires_once(turn_env, hook_calls) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env, generation=None)
    now = datetime.now(UTC)
    await _expire_active_attempt(turn_env, request_id, now)
    result = await recover_stalled_turns(turn_env["factory"], manager, now=now)
    assert result.failed == 1
    assert hook_calls == [(request_id, "failed", "legacy_interrupted")]


@pytest.mark.asyncio
async def test_recovery_gate_revoked_cancels_once(turn_env, hook_calls) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    now = datetime.now(UTC)
    await _expire_active_attempt(turn_env, request_id, now)
    async with turn_env["factory"]() as db:
        room = await db.get(Room, turn_env["room"])
        room.archived_at = now
        await db.commit()
    result = await recover_stalled_turns(turn_env["factory"], manager, now=now)
    assert result.cancelled == 1
    assert hook_calls == [(request_id, "cancelled", "authorization_revoked")]
    async with turn_env["factory"]() as db:
        stored = await db.get(AgentTurn, request_id)
        assert stored.completed_at is not None


# ------------------------------------------------------- cancel_invalid_turns


@pytest.mark.asyncio
async def test_cancel_invalid_turns_fires_once(turn_env, hook_calls) -> None:
    request_id, _, _ = await _create_and_deliver(turn_env)
    async with turn_env["factory"]() as db:
        room = await db.get(Room, turn_env["room"])
        room.archived_at = datetime.now(UTC)
        await db.commit()
    assert await cancel_invalid_turns(turn_env["factory"]) == 1
    assert await cancel_invalid_turns(turn_env["factory"]) == 0
    assert hook_calls == [(request_id, "cancelled", "authorization_revoked")]
    async with turn_env["factory"]() as db:
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == request_id)
        )
        assert attempt.state == "cancelled"
        assert attempt.reason == "authorization_revoked"
        assert attempt.ended_at is not None


# ---------------------------------------------------- cancel_attachment_turns


@pytest.mark.asyncio
async def test_cancel_attachment_turns_fires_once(workspace_env, hook_calls) -> None:
    async with workspace_env["factory"]() as db:
        message = await append_message(
            db,
            workspace_env["room"],
            workspace_env["user_participant"],
            "workspace change",
        )
        task = Task(
            room_id=workspace_env["room"],
            source_message_id=message.id,
            title="workspace change",
            status="in_progress",
            assignee_participant_id=workspace_env["agent_participant"],
            created_by=workspace_env["user"],
        )
        db.add(task)
        await db.flush()
        turn = await create_turn(
            db,
            room_id=workspace_env["room"],
            participant_id=workspace_env["agent_participant"],
            agent_id=workspace_env["agent"],
            trigger_message_id=message.id,
            task_id=task.id,
        )
        await db.commit()
    assert hook_calls == []

    async with workspace_env["factory"]() as db:
        attachment = await db.get(WorkspaceAttachment, workspace_env["attachment"])
        assert (
            await cancel_attachment_turns(
                db, attachment=attachment, reason="workspace_revoked"
            )
            == 1
        )
        await db.commit()
    assert hook_calls == [(turn.request_id, "cancelled", "workspace_revoked")]
    async with workspace_env["factory"]() as db:
        stored = await db.get(AgentTurn, turn.request_id)
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == turn.request_id)
        )
        assert stored.completed_at is not None
        assert attempt.state == "cancelled"
        assert attempt.reason == "workspace_revoked"
