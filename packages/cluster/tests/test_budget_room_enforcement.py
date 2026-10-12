"""Budget hard-stop wiring for normal room work (USE-05, USE-06).

USE-05 — a CLI engine's token frame becomes a ``UsageLedger`` row in the
WS handler (``_write_lifecycle_usage_row``). That write must feed the
post-cost evaluator so a real hard breach stops the agent and records an
incident, not only the unit tests of ``evaluate_cost_event``.

USE-06 — ``evaluate_invocation_block`` used to guard only federation
delegations. Room turns are dispatched by ``deliver_pending_outbox``; a
scope over its hard-stop ceiling must cancel the turn there (reason
``budget_hard_stop``) before any frame reaches the agent.

Both paths keep the default-OFF invariant: without an active
``hard_stop_enabled`` policy nothing changes.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from anygarden.budgets.ledger import clear_observed_cache
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    ActivityLog,
    Agent,
    AgentTurn,
    AgentTurnAttempt,
    AgentTurnOutbox,
    Base,
    Machine,
    Participant,
    Project,
    Room,
    TokenBudgetIncident,
    TokenBudgetPolicy,
    UsageLedger,
    User,
)
from anygarden.db.repository import append_message
from anygarden.turns.service import create_turn, deliver_pending_outbox
from anygarden.ws.handler import _write_lifecycle_usage_row
from anygarden.ws.protocol import LifecycleFrame


class _MockLifecycle:
    def __init__(self) -> None:
        self.stopped: list[str] = []

    async def request_stop(self, agent_id: str) -> None:
        self.stopped.append(agent_id)


class _FakeManager:
    def __init__(self, participant_id: str, generation: int) -> None:
        self.participant_id = participant_id
        self.generation = generation
        self.frames: list = []

    async def is_connected(self, participant_id: str) -> bool:
        return participant_id == self.participant_id

    async def participant_generation(self, participant_id: str) -> int | None:
        return self.generation

    async def send_to(self, participant_id, frame, *, expected_generation=None):
        if participant_id != self.participant_id:
            return False
        self.frames.append(frame)
        return True

    async def broadcast(self, room_id, frame) -> None:
        return None


@pytest_asyncio.fixture()
async def env():
    engine = build_engine("sqlite+aiosqlite://")
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        user = User(email="budget@test.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="budget-project")
        db.add(project)
        await db.flush()
        room = Room(project_id=project.id, name="budget-room")
        db.add(room)
        machine = Machine(
            name="m", hostname="h", owner_user_id=user.id, status="online"
        )
        db.add(machine)
        await db.flush()
        agent = Agent(
            name="budget-agent",
            engine="echo",
            desired_state="running",
            actual_state="running",
            placed_on_machine_id=machine.id,
            generation=1,
        )
        db.add(agent)
        await db.flush()
        user_p = Participant(room_id=room.id, user_id=user.id, role="member")
        agent_p = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add_all([user_p, agent_p])
        await db.commit()
        ids = {
            "room": room.id,
            "agent": agent.id,
            "user_participant": user_p.id,
            "agent_participant": agent_p.id,
        }
    clear_observed_cache()
    yield {"factory": factory, **ids}
    clear_observed_cache()
    await engine.dispose()


async def _add_policy(
    factory,
    *,
    scope_type: str,
    scope_id: str | None,
    ceiling: int,
    hard_stop: bool = True,
) -> None:
    async with factory() as db:
        db.add(
            TokenBudgetPolicy(
                scope_type=scope_type,
                scope_id=scope_id,
                token_ceiling=ceiling,
                warn_percent=80,
                hard_stop_enabled=hard_stop,
                is_active=True,
            )
        )
        await db.commit()


async def _add_usage(factory, *, agent_id: str, room_id: str, tokens: int) -> None:
    async with factory() as db:
        db.add(
            UsageLedger(
                identity_kind="agent",
                identity_id=agent_id,
                agent_id=agent_id,
                room_id=room_id,
                model_name="m",
                prompt_tokens=tokens,
                completion_tokens=0,
                status_code=200,
                timestamp=datetime.now(UTC),
            )
        )
        await db.commit()


def _usage_frame(room_id: str, tokens: int) -> LifecycleFrame:
    return LifecycleFrame(
        request_id="req-usage",
        room_id=room_id,
        event="engine_call_finished",
        engine="claude-code",
        outcome="ok",
        duration_ms=5,
        model="claude-sonnet-4-5",
        input_tokens=tokens,
        output_tokens=0,
    )


async def _incidents(factory) -> list[TokenBudgetIncident]:
    async with factory() as db:
        return list((await db.execute(select(TokenBudgetIncident))).scalars().all())


# ── USE-05: the real usage write feeds the post-cost evaluator ──────────


@pytest.mark.asyncio
@pytest.mark.req("USE-05")
async def test_usage_frame_over_agent_hard_stop_stops_agent_and_records_incident(
    env,
) -> None:
    factory = env["factory"]
    await _add_policy(factory, scope_type="agent", scope_id=env["agent"], ceiling=100)
    lifecycle = _MockLifecycle()

    await _write_lifecycle_usage_row(
        factory,
        agent_id=env["agent"],
        frame=_usage_frame(env["room"], 150),
        lifecycle=lifecycle,
    )

    assert lifecycle.stopped == [env["agent"]]
    incidents = await _incidents(factory)
    assert [(i.threshold_type, i.status, i.observed_tokens) for i in incidents] == [
        ("hard", "open", 150)
    ]
    async with factory() as db:
        agent = await db.get(Agent, env["agent"])
        assert agent.pause_reason == "budget"


@pytest.mark.asyncio
@pytest.mark.req("USE-05")
async def test_usage_frame_without_hard_stop_policy_changes_nothing(env) -> None:
    factory = env["factory"]
    # A disabled (warn-only) policy and a huge usage: still no incident/stop.
    await _add_policy(
        factory, scope_type="agent", scope_id=env["agent"], ceiling=10, hard_stop=False
    )
    lifecycle = _MockLifecycle()

    await _write_lifecycle_usage_row(
        factory,
        agent_id=env["agent"],
        frame=_usage_frame(env["room"], 10_000),
        lifecycle=lifecycle,
    )

    assert lifecycle.stopped == []
    assert await _incidents(factory) == []
    async with factory() as db:
        rows = (await db.execute(select(UsageLedger))).scalars().all()
        assert len(rows) == 1  # the usage row itself is still written
        agent = await db.get(Agent, env["agent"])
        assert agent.pause_reason is None


# ── USE-06: room dispatch honours the hard stop ─────────────────────────


async def _create_turn(env) -> str:
    async with env["factory"]() as db:
        trigger = await append_message(
            db, env["room"], env["user_participant"], "@budget-agent hi"
        )
        turn = await create_turn(
            db,
            room_id=env["room"],
            participant_id=env["agent_participant"],
            agent_id=env["agent"],
            trigger_message_id=trigger.id,
        )
        rid = turn.request_id
        await db.commit()
    return rid


async def _assert_budget_cancelled(env, rid: str) -> None:
    async with env["factory"]() as db:
        turn = await db.get(AgentTurn, rid)
        attempt = await db.scalar(
            select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == rid)
        )
        outbox = await db.scalar(
            select(AgentTurnOutbox).where(AgentTurnOutbox.turn_id == rid)
        )
        log = await db.scalar(
            select(ActivityLog).where(
                ActivityLog.request_id == rid,
                ActivityLog.event_type == "turn_cancelled",
            )
        )
    assert turn.state == "cancelled"
    assert turn.terminal_reason == "budget_hard_stop"
    assert attempt.state == "cancelled"
    assert attempt.reason == "budget_hard_stop"
    assert outbox.state == "cancelled"
    assert outbox.last_error == "budget_hard_stop"
    assert log is not None
    assert log.details["reason"] == "budget_hard_stop"
    assert log.details["budget_scope_type"] in {"agent", "room", "global"}
    assert "token budget exceeded" in log.details["detail"]


@pytest.mark.asyncio
@pytest.mark.req("USE-06")
async def test_agent_hard_stop_blocks_room_turn_dispatch(env) -> None:
    factory = env["factory"]
    await _add_policy(factory, scope_type="agent", scope_id=env["agent"], ceiling=100)
    await _add_usage(factory, agent_id=env["agent"], room_id=env["room"], tokens=200)
    rid = await _create_turn(env)

    manager = _FakeManager(env["agent_participant"], generation=1)
    assert await deliver_pending_outbox(factory, manager) == 0
    assert manager.frames == []
    await _assert_budget_cancelled(env, rid)


@pytest.mark.asyncio
@pytest.mark.req("USE-06")
async def test_room_hard_stop_blocks_room_turn_dispatch(env) -> None:
    factory = env["factory"]
    await _add_policy(factory, scope_type="room", scope_id=env["room"], ceiling=100)
    await _add_usage(factory, agent_id=env["agent"], room_id=env["room"], tokens=200)
    rid = await _create_turn(env)

    manager = _FakeManager(env["agent_participant"], generation=1)
    assert await deliver_pending_outbox(factory, manager) == 0
    assert manager.frames == []
    await _assert_budget_cancelled(env, rid)


@pytest.mark.asyncio
@pytest.mark.req("USE-06")
async def test_room_turn_dispatch_unchanged_without_hard_stop_policy(env) -> None:
    factory = env["factory"]
    # Over a ceiling, but the policy is not hard-stop → dispatch as before.
    await _add_policy(
        factory, scope_type="agent", scope_id=env["agent"], ceiling=100, hard_stop=False
    )
    await _add_usage(factory, agent_id=env["agent"], room_id=env["room"], tokens=200)
    rid = await _create_turn(env)

    manager = _FakeManager(env["agent_participant"], generation=1)
    assert await deliver_pending_outbox(factory, manager) == 1
    assert len(manager.frames) == 1
    async with factory() as db:
        turn = await db.get(AgentTurn, rid)
        assert turn.state == "leased"
        cancelled = (
            await db.execute(
                select(ActivityLog).where(
                    ActivityLog.request_id == rid,
                    ActivityLog.event_type == "turn_cancelled",
                )
            )
        ).scalars().all()
        assert cancelled == []


@pytest.mark.req("USE-06")
def test_budget_hard_stop_has_a_public_reason_code() -> None:
    # A task projection must not surface the budget refusal as UNKNOWN_FAILURE.
    from anygarden.project_executions.recovery import public_reason_code

    assert public_reason_code("budget_hard_stop") == "QUOTA_EXHAUSTED"
