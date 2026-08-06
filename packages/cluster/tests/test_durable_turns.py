"""Regression coverage for the durable agent-turn recovery state machine."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    ActivityLog,
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    AgentTurnOutbox,
    Base,
    Machine,
    Message,
    Participant,
    Project,
    Room,
    User,
)
from anygarden.db.repository import append_message
from anygarden.scheduler.lifecycle import AgentLifecycle, _manifest_hash
from anygarden.turns.service import (
    begin_completion,
    cancel_invalid_turns,
    create_turn,
    deliver_pending_outbox,
    finish_completion,
    record_lifecycle,
    recover_stalled_turns,
)


class FakeManager:
    def __init__(self, participant_id: str, generation: int | None) -> None:
        self.participant_id = participant_id
        self.generation = generation
        self.frames = []
        self.broadcasts = []

    async def is_connected(self, participant_id: str) -> bool:
        return participant_id == self.participant_id

    async def participant_generation(self, participant_id: str) -> int | None:
        assert participant_id == self.participant_id
        return self.generation

    async def send_to(self, participant_id, frame, *, expected_generation=None):
        if participant_id != self.participant_id:
            return False
        if expected_generation is not None and expected_generation != self.generation:
            return False
        self.frames.append(frame)
        return True

    async def broadcast(self, room_id, frame) -> None:
        self.broadcasts.append((room_id, frame))


class FailFirstSendManager(FakeManager):
    def __init__(self, participant_id: str, generation: int | None) -> None:
        super().__init__(participant_id, generation)
        self.attempted_frames = []

    async def send_to(self, participant_id, frame, *, expected_generation=None):
        self.attempted_frames.append(frame)
        if len(self.attempted_frames) == 1:
            return False
        return await super().send_to(
            participant_id,
            frame,
            expected_generation=expected_generation,
        )


class FakeBus:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    async def send(self, machine_id: str, frame: dict) -> bool:
        self.frames.append((machine_id, frame))
        return True


@pytest_asyncio.fixture()
async def turn_env():
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with factory() as db:
        user = User(email="turns@test.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="turn-project")
        db.add(project)
        await db.flush()
        room = Room(project_id=project.id, name="turn-room")
        db.add(room)
        machine = Machine(
            name="turn-machine",
            hostname="turn-host",
            owner_user_id=user.id,
            status="online",
        )
        db.add(machine)
        await db.flush()
        agent = Agent(
            name="turn-agent",
            engine="echo",
            desired_state="running",
            actual_state="running",
            placed_on_machine_id=machine.id,
            generation=3,
            manifest_hash="previous-config",
        )
        db.add(agent)
        await db.flush()
        user_participant = Participant(room_id=room.id, user_id=user.id, role="member")
        agent_participant = Participant(
            room_id=room.id, agent_id=agent.id, role="member"
        )
        db.add_all([user_participant, agent_participant])
        await db.commit()
        ids = {
            "room": room.id,
            "agent": agent.id,
            "machine": machine.id,
            "user_participant": user_participant.id,
            "agent_participant": agent_participant.id,
        }

    yield {"engine": engine, "factory": factory, **ids}
    await engine.dispose()


async def _create_and_deliver(env, *, generation: int | None = 3):
    factory = env["factory"]
    async with factory() as db:
        trigger = await append_message(
            db,
            env["room"],
            env["user_participant"],
            "please recover this turn",
        )
        turn = await create_turn(
            db,
            room_id=env["room"],
            participant_id=env["agent_participant"],
            agent_id=env["agent"],
            trigger_message_id=trigger.id,
        )
        request_id = turn.request_id
        trigger_id = trigger.id
        await db.commit()

    manager = FakeManager(env["agent_participant"], generation)
    assert await deliver_pending_outbox(factory, manager) == 1
    return request_id, trigger_id, manager


@pytest.mark.asyncio
async def test_turn_attempt_outbox_are_atomic_and_delivery_leases(turn_env) -> None:
    request_id, trigger_id, manager = await _create_and_deliver(turn_env)
    assert len(manager.frames) == 1
    metadata = manager.frames[0].metadata
    assert metadata["request_id"] == request_id
    assert metadata["turn_attempt"] == 1
    assert metadata["turn_generation"] == 3
    assert metadata["turn_protocol"] == 1
    assert metadata["turn_lease"]

    async with turn_env["factory"]() as db:
        turn = await db.get(AgentTurn, request_id)
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == request_id)
        )
        outbox = await db.scalar(
            select(AgentTurnOutbox).where(AgentTurnOutbox.turn_id == request_id)
        )
        assert turn is not None and turn.trigger_message_id == trigger_id
        assert turn.state == "leased"
        assert attempt is not None and attempt.state == "leased"
        assert attempt.lease_expires_at is not None
        assert outbox is not None and outbox.state == "delivered"


@pytest.mark.asyncio
async def test_failed_older_outbox_fences_later_room_turn(turn_env) -> None:
    async with turn_env["factory"]() as db:
        first_message = await append_message(
            db,
            turn_env["room"],
            turn_env["user_participant"],
            "first user intent",
        )
        first_turn = await create_turn(
            db,
            room_id=turn_env["room"],
            participant_id=turn_env["agent_participant"],
            agent_id=turn_env["agent"],
            trigger_message_id=first_message.id,
        )
        first_request_id = first_turn.request_id
        await db.commit()

    async with turn_env["factory"]() as db:
        second_message = await append_message(
            db,
            turn_env["room"],
            turn_env["user_participant"],
            "second user intent",
        )
        second_turn = await create_turn(
            db,
            room_id=turn_env["room"],
            participant_id=turn_env["agent_participant"],
            agent_id=turn_env["agent"],
            trigger_message_id=second_message.id,
        )
        second_request_id = second_turn.request_id
        await db.commit()

    manager = FailFirstSendManager(turn_env["agent_participant"], generation=3)
    assert await deliver_pending_outbox(turn_env["factory"], manager) == 0
    assert [frame.metadata["request_id"] for frame in manager.attempted_frames] == [
        first_request_id
    ]
    assert manager.frames == []

    async with turn_env["factory"]() as db:
        first_attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == first_request_id)
        )
        second_attempt = await db.scalar(
            select(AgentTurnAttempt).where(
                AgentTurnAttempt.turn_id == second_request_id
            )
        )
        first_outbox = await db.scalar(
            select(AgentTurnOutbox).where(AgentTurnOutbox.turn_id == first_request_id)
        )
        second_outbox = await db.scalar(
            select(AgentTurnOutbox).where(AgentTurnOutbox.turn_id == second_request_id)
        )
        assert first_attempt is not None and first_attempt.state == "leased"
        assert second_attempt is not None and second_attempt.state == "pending"
        assert first_outbox is not None and first_outbox.delivery_count == 1
        assert second_outbox is not None and second_outbox.delivery_count == 0


@pytest.mark.asyncio
async def test_turn_operator_api_is_admin_only_and_reports_state(turn_env) -> None:
    request_id, _, _ = await _create_and_deliver(turn_env)
    jwt_secret = secrets.token_urlsafe(32)
    async with turn_env["factory"]() as db:
        admin = User(email="turn-admin@test.com", password_hash="x", is_admin=True)
        regular = User(email="turn-regular@test.com", password_hash="x", is_admin=False)
        db.add_all([admin, regular])
        await db.commit()
        await db.refresh(admin)
        await db.refresh(regular)

    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=jwt_secret,
        log_level="WARNING",
    )
    app = create_app(config)
    app.state.engine = turn_env["engine"]
    app.state.session_factory = turn_env["factory"]
    admin_token = create_user_token(
        admin.id, admin.email, admin.is_admin, secret=jwt_secret
    )
    regular_token = create_user_token(
        regular.id, regular.email, regular.is_admin, secret=jwt_secret
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        forbidden = await client.get(
            "/api/v1/turns",
            headers={"Authorization": f"Bearer {regular_token}"},
        )
        assert forbidden.status_code == 403
        listed = await client.get(
            "/api/v1/turns",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert listed.status_code == 200
        assert listed.json()[0]["request_id"] == request_id
        assert listed.json()[0]["state"] == "leased"
        summary = await client.get(
            "/api/v1/turns/summary",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert summary.status_code == 200
        assert summary.json()["counts"]["leased"] == 1


@pytest.mark.asyncio
async def test_completion_is_visible_once_and_duplicate_is_idempotent(turn_env) -> None:
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
        reply = await append_message(
            db,
            turn_env["room"],
            turn_env["agent_participant"],
            "durable answer",
        )
        await finish_completion(
            db,
            turn=decision.turn,
            attempt=decision.attempt,
            message_id=reply.id,
        )
        await db.commit()

    async with turn_env["factory"]() as db:
        duplicate = await begin_completion(
            db,
            request_id=request_id,
            room_id=turn_env["room"],
            participant_id=turn_env["agent_participant"],
            agent_id=turn_env["agent"],
            attempt_number=metadata["turn_attempt"],
            generation=metadata["turn_generation"],
            lease_token=metadata["turn_lease"],
        )
        assert duplicate.outcome == "idempotent"
        assert duplicate.existing_message_id == reply.id
        assert (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.content == "durable answer")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_completion_proof_cannot_be_replayed_by_another_agent(turn_env) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    metadata = manager.frames[0].metadata
    async with turn_env["factory"]() as db:
        attacker = Agent(
            name="other-agent",
            engine="echo",
            desired_state="running",
            actual_state="running",
            generation=3,
        )
        db.add(attacker)
        await db.flush()
        attacker_participant = Participant(
            room_id=turn_env["room"],
            agent_id=attacker.id,
            role="member",
        )
        db.add(attacker_participant)
        await db.flush()

        decision = await begin_completion(
            db,
            request_id=request_id,
            room_id=turn_env["room"],
            participant_id=attacker_participant.id,
            agent_id=attacker.id,
            attempt_number=metadata["turn_attempt"],
            generation=metadata["turn_generation"],
            lease_token=metadata["turn_lease"],
        )
        assert decision.outcome == "stale"
        assert decision.reason == "authorization_revoked"
        await db.commit()


@pytest.mark.asyncio
async def test_expired_lease_retries_once_then_fails_with_one_notice(turn_env) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    now = datetime.now(timezone.utc)
    async with turn_env["factory"]() as db:
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == request_id)
        )
        attempt.lease_expires_at = now - timedelta(seconds=1)
        await db.commit()

    first = await recover_stalled_turns(turn_env["factory"], manager, now=now)
    assert first.redispatched == 1
    assert await deliver_pending_outbox(turn_env["factory"], manager) == 1

    async with turn_env["factory"]() as db:
        turn = await db.get(AgentTurn, request_id)
        second_attempt = await db.scalar(
            select(AgentTurnAttempt).where(
                AgentTurnAttempt.turn_id == request_id,
                AgentTurnAttempt.attempt_number == 2,
            )
        )
        assert turn is not None and turn.retry_count == 1
        second_attempt.lease_expires_at = now - timedelta(seconds=1)
        await db.commit()

    second = await recover_stalled_turns(turn_env["factory"], manager, now=now)
    assert second.failed == 1
    assert len(manager.broadcasts) == 1
    again = await recover_stalled_turns(turn_env["factory"], manager, now=now)
    assert again.failed == 0
    assert len(manager.broadcasts) == 1

    async with turn_env["factory"]() as db:
        turn = await db.get(AgentTurn, request_id)
        assert turn is not None and turn.state == "failed"
        assert turn.terminal_reason == "retry_exhausted"


@pytest.mark.asyncio
async def test_old_attempt_completion_is_fenced_after_redispatch(turn_env) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    old = dict(manager.frames[0].metadata)
    now = datetime.now(timezone.utc)
    async with turn_env["factory"]() as db:
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == request_id)
        )
        attempt.lease_expires_at = now - timedelta(seconds=1)
        await db.commit()
    assert (
        await recover_stalled_turns(turn_env["factory"], manager, now=now)
    ).redispatched == 1

    async with turn_env["factory"]() as db:
        stale = await begin_completion(
            db,
            request_id=request_id,
            room_id=turn_env["room"],
            participant_id=turn_env["agent_participant"],
            agent_id=turn_env["agent"],
            attempt_number=old["turn_attempt"],
            generation=old["turn_generation"],
            lease_token=old["turn_lease"],
        )
        assert stale.outcome == "stale"
        await db.commit()
        assert (
            await db.scalar(
                select(func.count())
                .select_from(ActivityLog)
                .where(
                    ActivityLog.request_id == request_id,
                    ActivityLog.event_type == "stale_completion",
                )
            )
            == 1
        )


@pytest.mark.asyncio
async def test_terminal_lifecycle_without_completion_enters_bounded_recovery(
    turn_env,
) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    metadata = manager.frames[0].metadata
    async with turn_env["factory"]() as db:
        accepted = await record_lifecycle(
            db,
            agent_id=turn_env["agent"],
            frame=SimpleNamespace(
                request_id=request_id,
                room_id=turn_env["room"],
                event="handler_finished",
                outcome="failed",
                turn_attempt=metadata["turn_attempt"],
                turn_generation=metadata["turn_generation"],
                turn_lease=metadata["turn_lease"],
            ),
        )
        assert accepted
        await db.commit()

    result = await recover_stalled_turns(
        turn_env["factory"],
        manager,
        now=datetime.now(timezone.utc) + timedelta(seconds=1),
    )
    assert result.redispatched == 1
    async with turn_env["factory"]() as db:
        first_attempt = await db.scalar(
            select(AgentTurnAttempt).where(
                AgentTurnAttempt.turn_id == request_id,
                AgentTurnAttempt.attempt_number == 1,
            )
        )
        assert first_attempt is not None
        assert first_attempt.state == "interrupted"
        assert first_attempt.reason == "agent_failed_without_completion"


@pytest.mark.asyncio
async def test_legacy_interruption_closes_without_retry(turn_env) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env, generation=None)
    now = datetime.now(timezone.utc)
    async with turn_env["factory"]() as db:
        turn = await db.get(AgentTurn, request_id)
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == request_id)
        )
        assert turn is not None and turn.protocol_version == 0
        attempt.lease_expires_at = now - timedelta(seconds=1)
        await db.commit()

    result = await recover_stalled_turns(turn_env["factory"], manager, now=now)
    assert result.redispatched == 0
    assert result.failed == 1
    async with turn_env["factory"]() as db:
        turn = await db.get(AgentTurn, request_id)
        attempts = await db.scalar(
            select(func.count())
            .select_from(AgentTurnAttempt)
            .where(AgentTurnAttempt.turn_id == request_id)
        )
        assert turn is not None and turn.terminal_reason == "legacy_interrupted"
        assert attempts == 1


@pytest.mark.asyncio
async def test_archive_cancels_and_fences_late_completion(turn_env) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    metadata = manager.frames[0].metadata
    async with turn_env["factory"]() as db:
        room = await db.get(Room, turn_env["room"])
        room.archived_at = datetime.now(timezone.utc)
        await db.commit()
    assert await cancel_invalid_turns(turn_env["factory"]) == 1

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
        assert decision.outcome == "stale"


@pytest.mark.asyncio
async def test_observer_target_is_cancelled_before_lease_or_delivery(turn_env) -> None:
    """Recovery must re-check the target's agent-write capability.

    An observer is still a room participant and may have an open read socket,
    but it cannot own a lifecycle/response.  Leasing or sending durable work
    to it would execute an engine call that the final WS frame must reject.
    """
    async with turn_env["factory"]() as db:
        participant = await db.get(Participant, turn_env["agent_participant"])
        assert participant is not None
        participant.role = "observer"
        trigger = await append_message(
            db,
            turn_env["room"],
            turn_env["user_participant"],
            "do not dispatch to an observer",
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
    assert manager.frames == []

    async with turn_env["factory"]() as db:
        stored_turn = await db.get(AgentTurn, turn.request_id)
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == turn.request_id)
        )
        outbox = await db.scalar(
            select(AgentTurnOutbox).where(AgentTurnOutbox.turn_id == turn.request_id)
        )
        assert stored_turn is not None and stored_turn.state == "cancelled"
        assert stored_turn.terminal_reason == "authorization_revoked"
        assert attempt is not None and attempt.state == "cancelled"
        assert outbox is not None and outbox.state == "cancelled"


@pytest.mark.asyncio
async def test_observer_revocation_fences_lifecycle_completion_and_retry(
    turn_env,
) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    metadata = manager.frames[0].metadata
    now = datetime.now(timezone.utc)
    async with turn_env["factory"]() as db:
        participant = await db.get(Participant, turn_env["agent_participant"])
        assert participant is not None
        participant.role = "observer"
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == request_id)
        )
        assert attempt is not None
        attempt.lease_expires_at = now - timedelta(seconds=1)

        lifecycle_ok = await record_lifecycle(
            db,
            agent_id=turn_env["agent"],
            frame=SimpleNamespace(
                request_id=request_id,
                room_id=turn_env["room"],
                event="handler_started",
                outcome=None,
                turn_attempt=metadata["turn_attempt"],
                turn_generation=metadata["turn_generation"],
                turn_lease=metadata["turn_lease"],
            ),
        )
        assert not lifecycle_ok
        completion = await begin_completion(
            db,
            request_id=request_id,
            room_id=turn_env["room"],
            participant_id=turn_env["agent_participant"],
            agent_id=turn_env["agent"],
            attempt_number=metadata["turn_attempt"],
            generation=metadata["turn_generation"],
            lease_token=metadata["turn_lease"],
        )
        assert completion.outcome == "stale"
        assert completion.reason == "authorization_revoked"
        await db.commit()

    recovered = await recover_stalled_turns(turn_env["factory"], manager, now=now)
    assert recovered.cancelled == 1
    assert recovered.redispatched == 0

    async with turn_env["factory"]() as db:
        turn = await db.get(AgentTurn, request_id)
        attempt_count = await db.scalar(
            select(func.count())
            .select_from(AgentTurnAttempt)
            .where(AgentTurnAttempt.turn_id == request_id)
        )
        assert turn is not None and turn.state == "cancelled"
        assert turn.terminal_reason == "authorization_revoked"
        assert attempt_count == 1


@pytest.mark.asyncio
async def test_unchanged_effective_manifest_does_not_drain_active_turn(
    turn_env,
) -> None:
    request_id, _, _ = await _create_and_deliver(turn_env)
    bus = FakeBus()
    lifecycle = AgentLifecycle(turn_env["factory"], bus)
    async with turn_env["factory"]() as db:
        agent = await db.get(Agent, turn_env["agent"])
        assert agent is not None
        frame = await lifecycle._build_sync_frame(db, agent, [turn_env["room"]])
        agent.manifest_hash = _manifest_hash(frame, agent=agent)
        await db.commit()

    await lifecycle.bump_generation(turn_env["agent"])

    async with turn_env["factory"]() as db:
        agent = await db.get(Agent, turn_env["agent"])
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == request_id)
        )
        assert agent is not None and agent.generation == 3
        assert agent.pending_generation is None
        assert agent.restart_deadline_at is None
        assert attempt is not None and attempt.state == "leased"
    assert bus.frames == []


@pytest.mark.asyncio
async def test_generation_change_drains_then_retries_on_new_generation(
    turn_env,
) -> None:
    request_id, _, manager = await _create_and_deliver(turn_env)
    bus = FakeBus()
    lifecycle = AgentLifecycle(turn_env["factory"], bus)
    await lifecycle.bump_generation(turn_env["agent"])

    async with turn_env["factory"]() as db:
        agent = await db.get(Agent, turn_env["agent"])
        assert agent.generation == 3
        assert agent.pending_generation == 4
        deadline = agent.restart_deadline_at
        assert deadline is not None
    assert bus.frames == []

    recovered = await recover_stalled_turns(
        turn_env["factory"], manager, now=deadline + timedelta(seconds=1)
    )
    assert recovered.redispatched == 1
    assert recovered.drain_agents == {turn_env["agent"]}
    assert await lifecycle.release_generation_drain(turn_env["agent"])

    async with turn_env["factory"]() as db:
        agent = await db.get(Agent, turn_env["agent"])
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(
                AgentTurnAttempt.turn_id == request_id,
                AgentTurnAttempt.attempt_number == 2,
            )
        )
        assert agent.generation == 4
        assert agent.pending_generation is None
        assert attempt is not None and attempt.generation == 4
    assert bus.frames[-1][1]["generation"] == 4
