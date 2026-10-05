"""Durable completion ledger and replay-safe goal bookkeeping."""

from datetime import UTC, datetime

import pytest
from anygarden.db.engine import build_session_factory
from anygarden.db.models import Agent, Goal, Participant, Room, Task, User
from anygarden.goals.executor import apply_completion, trigger_goal
from anygarden.goals.policy import GOAL_FAILURE_PAUSE_THRESHOLD
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError


async def _goal(db, *, materialize="full", failures=0):
    user = User(email="completion@example.test", password_hash="x")
    agent = Agent(name="Completion bot", engine="echo")
    room = Room(name="Goal completion")
    db.add_all([user, agent, room])
    await db.flush()
    participant = Participant(room_id=room.id, agent_id=agent.id, role="member")
    db.add(participant)
    await db.flush()
    goal = Goal(
        assignee_agent_id=agent.id,
        owner_id=user.id,
        report_room_id=room.id,
        title="Daily verification",
        spec="Verify the provided fixture. Do not submit externally.",
        status="active",
        trigger_type="cron",
        trigger_config={"cron": "0 9 * * *", "timezone": "Asia/Seoul"},
        materialize=materialize,
        consecutive_failures=failures,
    )
    db.add(goal)
    await db.flush()
    return goal, participant


async def _task(db, goal, participant, *, status="failed"):
    task = Task(
        room_id=goal.report_room_id,
        title=goal.title,
        status=status,
        goal_id=goal.id,
        assignee_participant_id=participant.id,
    )
    db.add(task)
    await db.flush()
    return task


@pytest.mark.asyncio
async def test_silent_success_preserves_evidence_and_consumed_schedule_slot(engine):
    factory = build_session_factory(engine)
    due = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
    async with factory() as db:
        goal, _ = await _goal(db, materialize="interesting_only", failures=2)
        goal_id = goal.id
        slot_key = f"{goal_id}:{int(due.timestamp())}"
        task = await trigger_goal(
            db,
            goal,
            idempotency_key=slot_key,
            scheduled_for=due,
        )
        task.status = "done"
        task.result_markdown = "Verified fixture version A: all required checks passed."
        task.tokens_used = 321
        task.finished_at = due
        task_id, spec = task.id, task.spec
        context = dict(task.schedule_context)
        await db.flush()
        assert await apply_completion(db, task, final_status="done") is True
        await db.commit()

    async with factory() as db:
        task = await db.get(Task, task_id)
        assert task is not None
        assert task.is_silent is True
        assert task.goal_completion_applied is True
        assert task.status == "done"
        assert (
            task.result_markdown
            == "Verified fixture version A: all required checks passed."
        )
        assert task.spec == spec
        assert task.tokens_used == 321
        assert task.finished_at == due
        assert task.schedule_context == context
        assert task.idempotency_key == slot_key
        goal = await db.get(Goal, goal_id)
        assert goal.consecutive_failures == 0
        with pytest.raises(IntegrityError):
            await trigger_goal(db, goal, idempotency_key=slot_key, scheduled_for=due)
        await db.rollback()

    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(Task)) == 1
        assert (await db.get(Task, task_id)).result_markdown is not None


@pytest.mark.asyncio
async def test_failed_completion_replay_keeps_one_failure_and_finish_time(engine):
    factory = build_session_factory(engine)
    async with factory() as db:
        goal, participant = await _goal(db)
        task = await _task(db, goal, participant)
        task_id, goal_id = task.id, goal.id
        assert await apply_completion(db, task, final_status="failed") is False
        finished = task.finished_at
        await db.commit()

    # A fresh DB session represents a restarted server receiving the same event.
    for _ in range(2):
        async with factory() as db:
            task = await db.get(Task, task_id)
            assert await apply_completion(db, task, final_status="failed") is False
            await db.commit()
    async with factory() as db:
        assert (await db.get(Goal, goal_id)).consecutive_failures == 1
        task = await db.get(Task, task_id)
        assert task.finished_at == finished
        assert task.goal_completion_applied is True
        assert task.is_silent is False


@pytest.mark.asyncio
async def test_silent_completion_replay_does_not_clear_a_later_failure(engine):
    factory = build_session_factory(engine)
    async with factory() as db:
        goal, participant = await _goal(db, materialize="interesting_only")
        success = await _task(db, goal, participant, status="done")
        failure = await _task(db, goal, participant)
        success_id, goal_id = success.id, goal.id
        assert await apply_completion(db, success, final_status="done") is True
        assert await apply_completion(db, failure, final_status="failed") is False
        await db.commit()
    async with factory() as db:
        success = await db.get(Task, success_id)
        assert await apply_completion(db, success, final_status="done") is True
        await db.commit()
        assert (await db.get(Goal, goal_id)).consecutive_failures == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "event"), [("todo", "done"), ("done", "failed")])
async def test_stale_completion_hook_cannot_finalize_another_status(db, status, event):
    goal, participant = await _goal(db, failures=1)
    task = await _task(db, goal, participant, status=status)
    assert await apply_completion(db, task, final_status=event) is False
    await db.flush()
    assert task.goal_completion_applied is False
    assert task.finished_at is None
    assert goal.consecutive_failures == 1


@pytest.mark.asyncio
async def test_distinct_completions_with_stale_goal_snapshots_accumulate_failures(
    engine,
):
    factory = build_session_factory(engine)
    async with factory() as db:
        goal, participant = await _goal(db, failures=GOAL_FAILURE_PAUSE_THRESHOLD - 2)
        first = await _task(db, goal, participant)
        second = await _task(db, goal, participant)
        goal_id, first_id, second_id = goal.id, first.id, second.id
        await db.commit()
    async with factory() as first_db, factory() as second_db:
        first_snapshot = await first_db.get(Goal, goal_id)
        second_snapshot = await second_db.get(Goal, goal_id)
        assert (
            first_snapshot.consecutive_failures == second_snapshot.consecutive_failures
        )
        first = await first_db.get(Task, first_id)
        second = await second_db.get(Task, second_id)
        await apply_completion(first_db, first, final_status="failed")
        await first_db.commit()
        await apply_completion(second_db, second, final_status="failed")
        await second_db.commit()
    async with factory() as db:
        goal = await db.get(Goal, goal_id)
        assert goal.consecutive_failures == GOAL_FAILURE_PAUSE_THRESHOLD
        assert goal.status == "paused"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["paused", "completed", "abandoned"])
async def test_completion_preserves_concurrently_disabled_goal_status(engine, status):
    factory = build_session_factory(engine)
    async with factory() as stale_db:
        goal, participant = await _goal(
            stale_db, failures=GOAL_FAILURE_PAUSE_THRESHOLD - 1
        )
        task = await _task(stale_db, goal, participant)
        goal_id = goal.id
        await stale_db.commit()
        async with factory() as db:
            await db.execute(
                update(Goal).where(Goal.id == goal_id).values(status=status)
            )
            await db.commit()
        await apply_completion(stale_db, task, final_status="failed")
        await stale_db.commit()
    async with factory() as db:
        assert (await db.get(Goal, goal_id)).status == status
