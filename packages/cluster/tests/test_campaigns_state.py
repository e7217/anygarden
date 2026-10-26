"""Campaign state system (#930 §3.3) and service skeleton."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from anygarden.campaigns.policy import STATUSES, TERMINAL_STATUSES, CampaignPolicyError
from anygarden.campaigns.service import (
    EVENTS,
    IllegalTransition,
    apply_transition,
    create_campaign,
    cycle_closed_at,
    extend_campaign,
    next_cycle_at,
    record_event,
    transition,
)
from anygarden.db.models import (
    Agent,
    Campaign,
    CampaignEvent,
    Participant,
    Project,
    Room,
    User,
)

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)

_ANY_OPEN = ("active", "paused", "blocked", "plateau", "awaiting_confirmation")

# The §3.3 table, written out independently of the implementation.
EXPECTED: dict[tuple[str, str], str] = {
    ("active", "pause"): "paused",
    ("active", "block"): "blocked",
    ("active", "plateau"): "plateau",
    ("active", "target_reached"): "awaiting_confirmation",
    ("active", "trusted_target_reached"): "achieved",
    ("paused", "resume"): "active",
    ("paused", "block"): "blocked",
    ("paused", "target_reached"): "awaiting_confirmation",
    ("paused", "trusted_target_reached"): "achieved",
    ("blocked", "resume"): "active",
    ("plateau", "resume"): "active",
    ("plateau", "trusted_target_reached"): "achieved",
    ("awaiting_confirmation", "confirm"): "achieved",
    ("awaiting_confirmation", "reject"): "active",
    ("awaiting_confirmation", "trusted_target_reached"): "achieved",
}
for _status in _ANY_OPEN:
    EXPECTED[(_status, "deadline")] = "deadline_reached"
    EXPECTED[(_status, "budget_exhausted")] = "budget_exhausted"
    EXPECTED[(_status, "stop")] = "stopped"
    EXPECTED[(_status, "edit")] = _status
    EXPECTED[(_status, "extend")] = _status
# An extension that clears the deadline/budget reopens those two.
EXPECTED[("deadline_reached", "extend")] = "active"
EXPECTED[("budget_exhausted", "extend")] = "active"


def test_status_vocabulary():
    assert set(STATUSES) == {
        "active", "paused", "blocked", "plateau", "awaiting_confirmation",
        "achieved", "deadline_reached", "budget_exhausted", "stopped",
    }
    assert TERMINAL_STATUSES == {"achieved", "deadline_reached", "budget_exhausted", "stopped"}


@pytest.mark.parametrize("status", sorted(STATUSES))
@pytest.mark.parametrize("event", sorted(EVENTS))
def test_full_transition_table(status, event):
    expected = EXPECTED.get((status, event))
    if expected is None:
        with pytest.raises(IllegalTransition):
            transition(status, event)
    else:
        assert transition(status, event) == expected


def test_terminal_statuses_accept_no_event_but_extend():
    for status in TERMINAL_STATUSES:
        for event in EVENTS:
            if event == "extend" and status in {"deadline_reached", "budget_exhausted"}:
                continue
            with pytest.raises(IllegalTransition):
                transition(status, event)


def test_unknown_status_or_event_is_illegal():
    with pytest.raises(IllegalTransition):
        transition("active", "explode")
    with pytest.raises(IllegalTransition):
        transition("running", "stop")


# ── close time ────────────────────────────────────────────────────


def test_close_time_never_precedes_opening():
    opened = NOW
    # Sweeper / Task API stamp the real clock, which may lag an injected one.
    assert cycle_closed_at(finished_at=opened - timedelta(hours=1), opened_at=opened) == opened
    later = opened + timedelta(minutes=7)
    assert cycle_closed_at(finished_at=later, opened_at=opened) == later


def test_close_time_without_finish_uses_the_clock():
    opened = NOW
    clock_now = opened + timedelta(minutes=3)
    assert cycle_closed_at(finished_at=None, opened_at=opened, clock=lambda: clock_now) == clock_now
    assert cycle_closed_at(
        finished_at=None, opened_at=opened, clock=lambda: opened - timedelta(days=1)
    ) == opened


def test_close_time_treats_naive_timestamps_as_utc():
    naive = datetime(2026, 10, 26, 13, 0)  # noqa: DTZ001 — naive on purpose
    assert cycle_closed_at(finished_at=naive, opened_at=NOW) == NOW + timedelta(hours=1)


def test_next_cycle_waits_the_minimum_gap_or_the_agents_wake():
    closed = NOW
    assert next_cycle_at(closed) == closed + timedelta(seconds=300)
    assert next_cycle_at(closed, wake_at=closed + timedelta(seconds=60)) == closed + timedelta(seconds=300)
    assert next_cycle_at(closed, wake_at=closed + timedelta(hours=2)) == closed + timedelta(hours=2)


# ── persistence ───────────────────────────────────────────────────


async def _project_room(db, *, representative: bool = True, member: bool = True):
    owner = User(email="owner@x", password_hash="x")
    agent = Agent(name="lead", engine="codex")
    db.add_all([owner, agent])
    await db.flush()
    project = Project(name="p", created_by=owner.id)
    db.add(project)
    await db.flush()
    room = Room(name="ops", project_id=project.id,
                representative_agent_id=agent.id if representative else None)
    db.add(room)
    await db.flush()
    if member:
        db.add(Participant(room_id=room.id, agent_id=agent.id, role="member"))
    await db.flush()
    return owner, agent, room


def _create_args(owner, room):
    return {
        "owner_id": owner.id,
        "room": room,
        "title": "Score",
        "purpose": "Win.",
        "mode": "target",
        "metric": {"name": "acc", "direction": "maximize", "target": 0.9},
        "evaluator": {"command": "python bench.py"},
        "budget": {"max_total_tokens": 100_000},
        "clock": lambda: NOW,
    }


async def test_create_campaign_starts_active_with_the_room_lead(db):
    owner, agent, room = await _project_room(db)
    campaign = await create_campaign(db, **_create_args(owner, room))
    await db.commit()
    assert campaign.status == "active"
    assert campaign.agent_id == agent.id
    assert campaign.next_cycle_at == NOW
    assert campaign.state_revision == 0
    assert campaign.budget["retro_reserve_ratio"] == 0.05
    events = (await db.scalars(select(CampaignEvent))).all()
    assert [e.type for e in events] == ["created"]


async def test_create_campaign_requires_a_project_room_lead(db):
    owner, _, room = await _project_room(db, representative=False)
    with pytest.raises(CampaignPolicyError) as exc:
        await create_campaign(db, **_create_args(owner, room))
    assert exc.value.code == "ROOM_LEAD_REQUIRED"


async def test_create_campaign_requires_the_lead_to_be_a_participant(db):
    owner, _, room = await _project_room(db, member=False)
    with pytest.raises(CampaignPolicyError) as exc:
        await create_campaign(db, **_create_args(owner, room))
    assert exc.value.code == "ROOM_LEAD_REQUIRED"


async def test_create_campaign_refuses_an_archived_room(db):
    owner, _, room = await _project_room(db)
    room.archived_at = NOW
    with pytest.raises(CampaignPolicyError) as exc:
        await create_campaign(db, **_create_args(owner, room))
    assert exc.value.code == "ROOM_ARCHIVED"


async def test_record_event_is_unique_per_key(db):
    owner, _, room = await _project_room(db)
    campaign = await create_campaign(db, **_create_args(owner, room))
    first = await record_event(db, campaign.id, key="k1", type="note", details={"a": 1})
    again = await record_event(db, campaign.id, key="k1", type="note", details={"a": 2})
    await db.commit()
    assert first is not None and again is None
    rows = (await db.scalars(select(CampaignEvent).where(CampaignEvent.event_key == "k1"))).all()
    assert len(rows) == 1 and rows[0].details == {"a": 1}


async def test_apply_transition_is_cas_guarded(db):
    owner, _, room = await _project_room(db)
    campaign = await create_campaign(db, **_create_args(owner, room))
    await db.commit()
    stale_revision = campaign.state_revision

    assert await apply_transition(db, campaign, "pause", actor="user:x", clock=lambda: NOW)
    await db.commit()
    await db.refresh(campaign)
    assert campaign.status == "paused"
    assert campaign.state_revision == stale_revision + 1

    # A writer that still holds the old revision loses.
    assert not await apply_transition(
        db, campaign, "stop", actor="user:x", clock=lambda: NOW, expected_revision=stale_revision
    )
    await db.rollback()
    await db.refresh(campaign)
    assert campaign.status == "paused"

    assert await apply_transition(
        db, campaign, "stop", actor="user:x", clock=lambda: NOW, stop_reason="user"
    )
    await db.commit()
    await db.refresh(campaign)
    assert campaign.status == "stopped" and campaign.stop_reason == "user"
    types = [e.type for e in (await db.scalars(
        select(CampaignEvent).where(CampaignEvent.campaign_id == campaign.id)
        .order_by(CampaignEvent.created_at)
    )).all()]
    assert types.count("status_changed") == 2


async def test_apply_transition_raises_on_an_illegal_event(db):
    owner, _, room = await _project_room(db)
    campaign = await create_campaign(db, **_create_args(owner, room))
    with pytest.raises(IllegalTransition):
        await apply_transition(db, campaign, "confirm", actor="user:x", clock=lambda: NOW)
    assert isinstance(campaign, Campaign) and campaign.status == "active"


# ── extend (coordinator decision: reopen deadline/budget ends) ──────


async def test_extend_keeps_an_open_status_and_records_the_change(db):
    owner, _, room = await _project_room(db)
    campaign = await create_campaign(db, **_create_args(owner, room))
    assert await extend_campaign(db, campaign, actor="user:x", max_total_tokens=200_000,
                                 clock=lambda: NOW)
    assert campaign.status == "active" and campaign.budget["max_total_tokens"] == 200_000
    event = await db.scalar(select(CampaignEvent).where(CampaignEvent.type == "status_changed"))
    assert event.details["event"] == "extend"
    assert event.details["max_total_tokens"] == {"from": 100_000, "to": 200_000}


@pytest.mark.req("CMP-10")
async def test_extend_reopens_a_budget_exhausted_campaign(db):
    owner, _, room = await _project_room(db)
    campaign = await create_campaign(db, **_create_args(owner, room))
    campaign.tokens_used = 96_000
    campaign.status = "budget_exhausted"
    campaign.stop_reason = "budget"
    await db.flush()
    with pytest.raises(CampaignPolicyError) as exc:
        # 101k × 0.95 is still below what was used.
        await extend_campaign(db, campaign, actor="user:x", max_total_tokens=101_000,
                              clock=lambda: NOW)
    assert exc.value.code == "EXTEND_DOES_NOT_REOPEN"
    assert await extend_campaign(db, campaign, actor="user:x", max_total_tokens=150_000,
                                 clock=lambda: NOW)
    assert (campaign.status, campaign.stop_reason) == ("active", None)
    assert campaign.next_cycle_at == NOW


@pytest.mark.req("CMP-09")
async def test_extend_reopens_a_deadline_reached_campaign_only_with_a_later_deadline(db):
    owner, _, room = await _project_room(db)
    campaign = await create_campaign(
        db, **{**_create_args(owner, room), "deadline_at": NOW + timedelta(hours=1)}
    )
    campaign.status = "deadline_reached"
    await db.flush()
    later = NOW + timedelta(days=1)
    with pytest.raises(CampaignPolicyError):
        await extend_campaign(db, campaign, actor="user:x", max_total_tokens=200_000,
                              clock=lambda: NOW + timedelta(hours=2))
    with pytest.raises(CampaignPolicyError):
        await extend_campaign(db, campaign, actor="user:x", deadline_at=NOW + timedelta(minutes=30),
                              clock=lambda: NOW)
    assert await extend_campaign(db, campaign, actor="user:x", deadline_at=later,
                                 clock=lambda: NOW + timedelta(hours=2))
    assert campaign.status == "active" and campaign.deadline_at == later


@pytest.mark.parametrize("status", ["achieved", "stopped"])
async def test_extend_refuses_final_statuses(db, status):
    owner, _, room = await _project_room(db)
    campaign = await create_campaign(db, **_create_args(owner, room))
    campaign.status = status
    with pytest.raises(IllegalTransition):
        await extend_campaign(db, campaign, actor="user:x", max_total_tokens=200_000,
                              clock=lambda: NOW)
