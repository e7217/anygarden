"""A campaign cycle Task begins a project execution with clamped limits
(#930 §3.2, plan CMP-03).

Reuses the project-execution harness: the test plays the lead agent,
receives the cycle assignment turn and calls ``begin_project_execution``
with that turn's proof.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from anygarden.campaigns.scheduler import CampaignScheduler
from anygarden.campaigns.service import create_campaign
from anygarden.db.models import Campaign, CampaignAttempt, ProjectExecution, Room

from . import test_project_execution_flow as _flow
from .test_project_execution_flow import Harness

# Reuse the project-execution harness fixture under this module's name.
flow_harness = _flow.harness


async def _open_cycle(h: Harness, *, deadline: datetime | None, tokens_used: int = 20_000) -> dict:
    async with h.sessions() as db:
        room = await db.get(Room, h.ids["lead_room"])
        campaign = await create_campaign(
            db,
            owner_id=h.ids["owner"],
            room=room,
            title="Raise accuracy",
            purpose="Win the contest.",
            mode="target",
            metric={"name": "acc", "direction": "maximize", "target": 0.9},
            evaluator={"command": "python bench.py"},
            budget={"max_total_tokens": 100_000, "retro_reserve_ratio": 0.05},
            deadline_at=deadline,
        )
        campaign.tokens_used = tokens_used
        await db.commit()
        campaign_id = campaign.id
    outcome = await CampaignScheduler(h.sessions).advance(campaign_id)
    assert outcome.action == "opened"
    turn = await h.deliver("lead")
    assert turn.frame["metadata"]["task_assignment"]["task_id"] == outcome.task_id
    return {"campaign_id": campaign_id, "task_id": outcome.task_id, "turn": turn}


def _iso(value: datetime) -> str:
    return value.isoformat()


async def test_cycle_execution_is_clamped_to_the_campaign_budget_and_deadline(flow_harness):
    deadline = datetime.now(UTC) + timedelta(hours=2)
    cycle = await _open_cycle(flow_harness, deadline=deadline)

    result = await flow_harness.tool(
        cycle["turn"], "begin_project_execution", objective="Improve accuracy",
        limits={"max_total_tokens": 500_000,
                "deadline_at": _iso(datetime.now(UTC) + timedelta(days=1))},
    )
    assert result["root_task_id"] == cycle["task_id"]
    async with flow_harness.sessions() as db:
        execution = await db.get(ProjectExecution, result["execution_id"])
        # 100k budget − 5% retro reserve − 20k used = 75k left for cycles.
        assert execution.limits["max_total_tokens"] == 75_000
        assert execution.deadline_at == deadline
        assert datetime.fromisoformat(execution.limits["deadline_at"]) == deadline
        assert execution.owner_user_id == flow_harness.ids["owner"]
        attempt = await db.scalar(select(CampaignAttempt).where(
            CampaignAttempt.campaign_id == cycle["campaign_id"]))
        assert attempt.execution_id == execution.id


async def test_smaller_requested_limits_are_kept(flow_harness):
    cycle = await _open_cycle(flow_harness, deadline=datetime.now(UTC) + timedelta(days=2))
    soon = datetime.now(UTC) + timedelta(hours=1)
    result = await flow_harness.tool(
        cycle["turn"], "begin_project_execution", objective="Improve accuracy",
        limits={"max_total_tokens": 1000, "deadline_at": _iso(soon)},
    )
    async with flow_harness.sessions() as db:
        execution = await db.get(ProjectExecution, result["execution_id"])
        assert execution.limits["max_total_tokens"] == 1000
        assert execution.deadline_at == soon


async def test_without_requested_limits_the_campaign_limits_apply(flow_harness):
    deadline = datetime.now(UTC) + timedelta(hours=3)
    cycle = await _open_cycle(flow_harness, deadline=deadline, tokens_used=0)
    result = await flow_harness.tool(cycle["turn"], "begin_project_execution", objective="Improve")
    async with flow_harness.sessions() as db:
        execution = await db.get(ProjectExecution, result["execution_id"])
        assert execution.limits["max_total_tokens"] == 95_000
        assert execution.deadline_at == deadline


async def test_no_deadline_campaign_sets_no_deadline(flow_harness):
    cycle = await _open_cycle(flow_harness, deadline=None)
    result = await flow_harness.tool(cycle["turn"], "begin_project_execution", objective="Improve")
    async with flow_harness.sessions() as db:
        execution = await db.get(ProjectExecution, result["execution_id"])
        assert execution.deadline_at is None
        assert "deadline_at" not in execution.limits


async def test_cycle_cannot_begin_once_the_budget_is_spent(flow_harness):
    cycle = await _open_cycle(flow_harness, deadline=None)
    async with flow_harness.sessions() as db:
        campaign = await db.get(Campaign, cycle["campaign_id"])
        campaign.tokens_used = 95_000
        await db.commit()
    error = await flow_harness.tool_error(cycle["turn"], "begin_project_execution", objective="Improve")
    assert "budget is used up" in error


async def test_cycle_of_a_stopped_campaign_cannot_begin(flow_harness):
    cycle = await _open_cycle(flow_harness, deadline=None)
    async with flow_harness.sessions() as db:
        campaign = await db.get(Campaign, cycle["campaign_id"])
        campaign.status = "stopped"
        await db.commit()
    error = await flow_harness.tool_error(cycle["turn"], "begin_project_execution", objective="Improve")
    assert "campaign has ended" in error


async def test_closed_cycle_cannot_begin(flow_harness):
    cycle = await _open_cycle(flow_harness, deadline=None)
    async with flow_harness.sessions() as db:
        attempt = await db.scalar(select(CampaignAttempt).where(
            CampaignAttempt.campaign_id == cycle["campaign_id"]))
        attempt.closed_at = datetime.now(UTC)
        attempt.outcome = "unknown"
        await db.commit()
    error = await flow_harness.tool_error(cycle["turn"], "begin_project_execution", objective="Improve")
    assert "not the campaign's open cycle" in error
