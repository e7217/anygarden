"""A campaign cycle Task begins a project execution with clamped limits
(#930 §3.2, plan CMP-03).

Reuses the project-execution harness: the test plays the lead agent,
receives the cycle assignment turn and calls ``begin_project_execution``
with that turn's proof.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
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


@pytest.mark.req("CMP-03")
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


@pytest.mark.req("CMP-03")
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


@pytest.mark.req("CMP-03")
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


@pytest.mark.req("CMP-03")
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


@pytest.mark.req("CMP-04")
async def test_completed_cycle_is_evaluated_through_the_machine_handler(flow_harness):
    """Real path: cycle turn → execution → completion hook → evaluation
    request → machine result frame → judged (CMP-04)."""
    from anygarden.db.models import Agent, Machine, Task
    from anygarden.ws.machine_handler import handle_machine_frame

    h = flow_harness
    cycle = await _open_cycle(h, deadline=None, tokens_used=0)
    turn = cycle["turn"]
    execution = await h.tool(turn, "begin_project_execution", objective="Improve accuracy")
    execution_id = execution["execution_id"]

    # The agent cannot end its cycle Task directly (decision: cycles end
    # through the execution so the system evaluates them).
    error = await h.tool_error(turn, "mark_task_status", task_id=cycle["task_id"], status="failed")
    assert "CAMPAIGN_CYCLE_TASK_MANAGED" in error

    delegated = await h.tool(
        turn, "delegate_project_task", execution_id=execution_id,
        parent_task_id=execution["root_task_id"], target_room_id=h.ids["worker_room"],
        assignee_participant_id=h.ids["worker_pid"], delegation_key="try-1",
        title="Try a feature", spec="Add a feature and report.",
    )
    await h.tool(turn, "seal_project_plan", execution_id=execution_id,
                 required_task_ids=[delegated["task_id"]])
    worker_turn = await h.deliver("worker")
    await h.tool(worker_turn, "mark_task_status", task_id=delegated["task_id"], status="done",
                 result_markdown="Added the feature.")
    await h.end_turn(turn)
    lead_turn = await h.deliver("lead")
    recorded = await h.tool(lead_turn, "record_campaign_attempt", outcome="attempted",
                            hypothesis="A feature helps", change_summary="Added a feature")
    assert recorded["recorded"] is True and recorded["follow_up"] == []
    await h.tool(lead_turn, "complete_project_execution", execution_id=execution_id,
                 summary="Cycle 1 tried a feature.")

    async with h.sessions() as db:
        attempt = await db.scalar(select(CampaignAttempt).where(
            CampaignAttempt.campaign_id == cycle["campaign_id"]))
        assert attempt.execution_id == execution_id
        assert (attempt.eval_status, attempt.eval_job_id) == ("pending", attempt.id)
        assert attempt.closed_at is None
        assert (await db.get(Task, cycle["task_id"])).status == "done"
        machine = Machine(name="box", hostname="box", owner_user_id=h.ids["owner"])
        db.add(machine)
        await db.flush()
        (await db.get(Agent, h.ids["lead"])).placed_on_machine_id = machine.id
        attempt.eval_status = "running"
        await db.commit()
        machine_id, job_id = machine.id, attempt.eval_job_id

    # The lifespan normally installs these; ASGITransport skips it.
    from anygarden.scheduler.machine_bus import MachineBus

    h.app.state.machine_bus = MachineBus()
    h.app.state.agent_lifecycle = None
    await handle_machine_frame(h.app, machine_id, {
        "type": "campaign_eval_result", "job_id": job_id, "agent_id": h.ids["lead"],
        "status": "ok", "exit_code": 0, "stdout_tail": '{"score": 0.6, "valid": true}',
        "stderr_tail": "", "parsed": {"score": 0.6, "valid": True}, "pins": {}, "error": None,
    })
    async with h.sessions() as db:
        attempt = await db.get(CampaignAttempt, attempt.id)
        campaign = await db.get(Campaign, cycle["campaign_id"])
        assert (attempt.outcome, attempt.dev_score) == ("improved", 0.6)
        assert campaign.best_attempt_id == attempt.id
        assert campaign.status == "active" and campaign.next_cycle_at is not None
        execution_row = await db.get(ProjectExecution, execution_id)
        assert execution_row.status == "completed"


async def test_machine_bus_eval_request_waits_only_for_the_ack():
    import asyncio
    import json

    from anygarden.scheduler.machine_bus import MachineBus

    class _Socket:
        def __init__(self):
            self.sent = []

        async def send_text(self, text):
            self.sent.append(json.loads(text))

    bus = MachineBus()
    socket = _Socket()
    await bus.register("m-1", socket)
    request = asyncio.create_task(bus.request_campaign_eval(
        "m-1", {"job_id": "job-1", "agent_id": "a-1", "command": "x", "timeout_sec": 60}))
    await asyncio.sleep(0)
    frame = socket.sent[0]
    assert frame["type"] == "campaign_eval_request" and frame["job_id"] == "job-1"
    # Another machine cannot answer for it.
    assert not bus.resolve_campaign_eval_ack("m-2", {"request_id": frame["request_id"]})
    assert bus.resolve_campaign_eval_ack(
        "m-1", {"request_id": frame["request_id"], "job_id": "job-1", "accepted": True})
    assert (await request)["accepted"] is True
