"""Campaign scheduler: opening cycles exactly once (#930 §3.2, plan CMP-02/09/10).

Runs on a file-backed SQLite DB (in-memory StaticPool shares one
connection, which hides — and sometimes breaks — concurrent sessions).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select

from anygarden.campaigns.scheduler import CampaignScheduler, cycle_idempotency_key
from anygarden.campaigns.service import create_campaign
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentTurn,
    Base,
    Campaign,
    CampaignAttempt,
    CampaignEvent,
    Message,
    Participant,
    Project,
    Room,
    Task,
    User,
)
from anygarden.task_materialize import materialize_assigned_task

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


async def _make_sessions(url: str):
    engine = build_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    return engine, build_session_factory(engine)


@pytest_asyncio.fixture()
async def sessions(tmp_path):
    engine, factory = await _make_sessions(f"sqlite+aiosqlite:///{tmp_path / 'campaigns.db'}")
    yield factory
    await engine.dispose()


async def _seed(sessions, **campaign_overrides) -> dict[str, str]:
    async with sessions() as db:
        owner = User(email="owner@x", password_hash="x")
        agent = Agent(name="lead", engine="codex")
        db.add_all([owner, agent])
        await db.flush()
        project = Project(name="p", created_by=owner.id)
        db.add(project)
        await db.flush()
        room = Room(name="ops", project_id=project.id, representative_agent_id=agent.id)
        db.add(room)
        await db.flush()
        participant = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add(participant)
        await db.flush()
        args = {
            "owner_id": owner.id,
            "room": room,
            "title": "Raise accuracy",
            "purpose": "Win the contest.",
            "mode": "target",
            "metric": {"name": "acc", "direction": "maximize", "target": 0.9},
            "evaluator": {"command": "python bench.py"},
            "budget": {"max_total_tokens": 100_000},
            "clock": lambda: NOW - timedelta(minutes=1),
        }
        campaign = await create_campaign(db, **args)
        for key, value in campaign_overrides.items():
            setattr(campaign, key, value)
        await db.commit()
        return {"campaign": campaign.id, "room": room.id, "agent": agent.id,
                "participant": participant.id, "owner": owner.id}


def _scheduler(sessions, now=NOW) -> CampaignScheduler:
    return CampaignScheduler(sessions, clock=lambda: now)


async def _counts(sessions, campaign_id: str) -> tuple[int, int]:
    async with sessions() as db:
        tasks = await db.scalar(select(func.count()).select_from(Task).where(Task.campaign_id == campaign_id))
        attempts = await db.scalar(
            select(func.count()).select_from(CampaignAttempt).where(CampaignAttempt.campaign_id == campaign_id)
        )
        return tasks, attempts


async def test_tick_opens_one_cycle_task_assigned_to_the_lead(sessions):
    ids = await _seed(sessions)
    outcomes = await _scheduler(sessions)._tick()
    assert [o.action for o in outcomes] == ["opened"]

    async with sessions() as db:
        task = await db.scalar(select(Task).where(Task.campaign_id == ids["campaign"]))
        assert task.status == "todo"
        assert task.assignee_participant_id == ids["participant"]
        assert task.triggered_by == "campaign"
        assert task.idempotency_key == cycle_idempotency_key(ids["campaign"], 1)
        assert task.spec.startswith("# Campaign cycle 1")
        assert "Win the contest." in task.spec
        attempt = await db.scalar(select(CampaignAttempt).where(CampaignAttempt.campaign_id == ids["campaign"]))
        assert (attempt.cycle_no, attempt.kind, attempt.task_id) == (1, "cycle", task.id)
        assert attempt.opened_at == NOW and attempt.closed_at is None
        campaign = await db.get(Campaign, ids["campaign"])
        assert campaign.cycles_run == 1 and campaign.state_revision == 1
        # The assignment woke the lead through the ordinary turn path.
        turn = await db.scalar(select(AgentTurn).where(AgentTurn.task_id == task.id))
        assert turn is not None and turn.agent_id == ids["agent"]
        wake = await db.get(Message, turn.trigger_message_id)
        assert "begin_project_execution" in wake.content
        assert "mark_task_status" not in wake.content.split("**TASK INPUT")[0]
        types = [e.type for e in await db.scalars(select(CampaignEvent).where(
            CampaignEvent.campaign_id == ids["campaign"]))]
        assert "cycle_opened" in types


async def test_open_cycle_blocks_the_next_one_across_restarts(sessions):
    ids = await _seed(sessions)
    await _scheduler(sessions)._tick()
    # Another tick and a fresh scheduler (a restart) much later.
    await _scheduler(sessions)._tick()
    assert await _scheduler(sessions, NOW + timedelta(hours=5))._tick() == []
    assert await _counts(sessions, ids["campaign"]) == (1, 1)


async def test_next_cycle_opens_after_the_previous_closes(sessions):
    ids = await _seed(sessions)
    await _scheduler(sessions)._tick()
    async with sessions() as db:
        attempt = await db.scalar(select(CampaignAttempt))
        attempt.closed_at = NOW + timedelta(minutes=10)
        campaign = await db.get(Campaign, ids["campaign"])
        campaign.next_cycle_at = NOW + timedelta(minutes=15)
        await db.commit()
    assert await _scheduler(sessions, NOW + timedelta(minutes=14))._tick() == []
    outcomes = await _scheduler(sessions, NOW + timedelta(minutes=15))._tick()
    assert [(o.action, o.cycle_no) for o in outcomes] == [("opened", 2)]
    async with sessions() as db:
        keys = set(await db.scalars(select(Task.idempotency_key).where(Task.campaign_id == ids["campaign"])))
    assert keys == {cycle_idempotency_key(ids["campaign"], 1), cycle_idempotency_key(ids["campaign"], 2)}


@pytest.mark.parametrize("round_", range(5))
async def test_concurrent_ticks_open_exactly_one_cycle(sessions, round_):
    ids = await _seed(sessions)
    schedulers = [_scheduler(sessions) for _ in range(4)]
    results = await asyncio.gather(*(s._tick() for s in schedulers))
    opened = [o for outcomes in results for o in outcomes if o.action == "opened"]
    assert len(opened) == 1
    assert await _counts(sessions, ids["campaign"]) == (1, 1)


@pytest.mark.parametrize("round_", range(5))
async def test_concurrent_advances_serialise_without_errors(sessions, round_):
    """Every racer gets past its due-check; one opens, the rest skip — no
    lock or integrity error escapes."""
    ids = await _seed(sessions)
    schedulers = [_scheduler(sessions) for _ in range(4)]
    results = await asyncio.gather(
        *(s.advance(ids["campaign"], now=NOW) for s in schedulers), return_exceptions=True
    )
    assert not [r for r in results if isinstance(r, BaseException)], results
    assert sorted(r.action for r in results) == ["opened", "skipped", "skipped", "skipped"]
    assert await _counts(sessions, ids["campaign"]) == (1, 1)


async def test_duplicate_cycle_key_is_refused_by_the_task_index(sessions):
    from sqlalchemy.exc import IntegrityError

    ids = await _seed(sessions)
    await _scheduler(sessions)._tick()
    async with sessions() as db:
        room = await db.get(Room, ids["room"])
        participant = await db.get(Participant, ids["participant"])
        with pytest.raises(IntegrityError):
            await materialize_assigned_task(
                db, room=room, participant=participant, title="dup", spec=None,
                created_by=None, triggered_by="campaign",
                idempotency_key=cycle_idempotency_key(ids["campaign"], 1),
                campaign_id=ids["campaign"],
            )


async def test_future_next_cycle_or_paused_campaign_opens_nothing(sessions):
    ids = await _seed(sessions, next_cycle_at=NOW + timedelta(minutes=5))
    assert await _scheduler(sessions)._tick() == []
    async with sessions() as db:
        campaign = await db.get(Campaign, ids["campaign"])
        campaign.next_cycle_at = NOW - timedelta(minutes=5)
        campaign.status = "paused"
        await db.commit()
    assert await _scheduler(sessions)._tick() == []
    assert await _counts(sessions, ids["campaign"]) == (0, 0)


async def test_exhausted_budget_ends_the_campaign_without_a_cycle(sessions):
    # 100k budget with the 5% retro reserve leaves 95k for cycles.
    ids = await _seed(sessions, tokens_used=95_000)
    outcomes = await _scheduler(sessions)._tick()
    assert [o.action for o in outcomes] == ["budget_exhausted"]
    async with sessions() as db:
        campaign = await db.get(Campaign, ids["campaign"])
        assert (campaign.status, campaign.stop_reason) == ("budget_exhausted", "budget")
    assert await _counts(sessions, ids["campaign"]) == (0, 0)


async def test_passed_deadline_ends_the_campaign_without_a_cycle(sessions):
    ids = await _seed(sessions)
    async with sessions() as db:
        campaign = await db.get(Campaign, ids["campaign"])
        campaign.deadline_at = NOW - timedelta(seconds=1)
        # Even with the next cycle far in the future, the deadline is acted on.
        campaign.next_cycle_at = NOW + timedelta(days=1)
        await db.commit()
    outcomes = await _scheduler(sessions)._tick()
    assert [o.action for o in outcomes] == ["deadline_reached"]
    async with sessions() as db:
        assert (await db.get(Campaign, ids["campaign"])).status == "deadline_reached"
    assert await _counts(sessions, ids["campaign"]) == (0, 0)


async def test_archived_room_stops_the_campaign(sessions):
    ids = await _seed(sessions)
    async with sessions() as db:
        (await db.get(Room, ids["room"])).archived_at = NOW - timedelta(minutes=1)
        await db.commit()
    outcomes = await _scheduler(sessions)._tick()
    assert [o.action for o in outcomes] == ["stopped"]
    async with sessions() as db:
        campaign = await db.get(Campaign, ids["campaign"])
        assert (campaign.status, campaign.stop_reason) == ("stopped", "room_archived")
    assert await _counts(sessions, ids["campaign"]) == (0, 0)


async def test_agent_leaving_the_room_blocks_the_campaign(sessions):
    ids = await _seed(sessions)
    async with sessions() as db:
        await db.delete(await db.get(Participant, ids["participant"]))
        await db.commit()
    outcomes = await _scheduler(sessions)._tick()
    assert [o.action for o in outcomes] == ["blocked"]
    async with sessions() as db:
        campaign = await db.get(Campaign, ids["campaign"])
        assert campaign.status == "blocked" and campaign.status_detail
    assert await _counts(sessions, ids["campaign"]) == (0, 0)
