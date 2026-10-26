"""Campaign MCP tools (#930 §3.5, Phase 5, CMP-13).

The test plays the campaign's lead agent on the project-execution harness:
it receives the cycle assignment turn and calls the campaign tools with
that turn's proof, over the real ``/mcp/rpc`` endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from anygarden.campaigns import lifecycle
from anygarden.campaigns.scheduler import CampaignScheduler
from anygarden.campaigns.service import apply_transition, create_campaign
from anygarden.db.models import (
    Campaign,
    CampaignAttempt,
    CampaignEvent,
    ProjectExecution,
    Room,
    Task,
)

from . import test_project_execution_flow as _flow
from .test_project_execution_flow import Harness

flow_harness = _flow.harness

CAMPAIGN_TOOLS = {
    "record_campaign_attempt", "schedule_campaign_wakeup", "report_campaign_blocked",
    "read_campaign_attempts", "apply_campaign_learning",
}


async def _open_cycle(h: Harness, *, deadline: datetime | None = None, mode: str = "target") -> dict:
    async with h.sessions() as db:
        room = await db.get(Room, h.ids["lead_room"])
        campaign = await create_campaign(
            db, owner_id=h.ids["owner"], room=room, title="Raise accuracy",
            purpose="Win the contest.", mode=mode,
            metric={"name": "acc", "direction": "maximize",
                    "target": 0.9 if mode == "target" else None},
            evaluator={"command": "python bench.py"},
            budget={"max_total_tokens": 100_000}, deadline_at=deadline,
        )
        await db.commit()
        campaign_id = campaign.id
    outcome = await CampaignScheduler(h.sessions).advance(campaign_id)
    assert outcome.action == "opened"
    turn = await h.deliver("lead")
    return {"campaign_id": campaign_id, "task_id": outcome.task_id, "turn": turn}


async def _record(h: Harness, turn, **overrides) -> dict:
    arguments = {"outcome": "attempted", "hypothesis": "More features help",
                 "change_summary": "Added two features", **overrides}
    return await h.tool(turn, "record_campaign_attempt", **arguments)


async def _error(h: Harness, turn, name: str, **arguments) -> dict:
    result = await h.tool_error_or_ok(turn, name, **arguments)
    assert result.get("isError"), result
    return result.get("structuredContent") or {"code": None, "detail": result["content"][0]["text"]}


async def _attempt(h: Harness, campaign_id: str) -> CampaignAttempt:
    async with h.sessions() as db:
        return await db.scalar(select(CampaignAttempt).where(
            CampaignAttempt.campaign_id == campaign_id).order_by(CampaignAttempt.cycle_no.desc()))


async def _complete_and_score(h: Harness, cycle: dict, turn, *, score: float = 0.5) -> str:
    """Begin was called on *turn*; finish the run and deliver its score."""
    from anygarden.project_executions.service import _event

    execution_id = (await _attempt(h, cycle["campaign_id"])).execution_id
    async with h.sessions() as db:
        # The run's own completion path needs a sealed plan; end it through
        # the same terminal event instead.
        execution = await db.get(ProjectExecution, execution_id)
        task = await db.get(Task, execution.root_task_id)
        now = datetime.now(UTC)
        execution.status, execution.finished_at = "completed", now
        task.status, task.finished_at = "done", now
        await db.flush()
        await _event(db, execution, f"execution:{execution.id}:completed",
                     "execution_completed", task_id=task.id)
        await db.commit()
    attempt = await _attempt(h, cycle["campaign_id"])
    assert attempt.eval_status == "pending"
    async with h.sessions() as db:
        outcome = await lifecycle.on_eval_result(db, {
            "job_id": attempt.eval_job_id, "status": "ok",
            "parsed": {"score": score, "valid": True}, "pins": {},
        })
        await db.commit()
    return outcome


async def test_campaign_tools_are_listed(flow_harness):
    resp = await flow_harness.client.post(
        "/mcp/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {flow_harness.ids['lead_token']}"},
    )
    names = {tool["name"] for tool in resp.json()["result"]["tools"]}
    assert CAMPAIGN_TOOLS <= names


@pytest.mark.req("CMP-13")
async def test_record_is_accepted_once_per_cycle(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    first = await _record(h, cycle["turn"], artifact_ref="commit abc", lessons="Keep it small")
    assert first["recorded"] is True and first["cycle_no"] == 1
    assert first["follow_up"] == []
    attempt = await _attempt(h, cycle["campaign_id"])
    assert (attempt.agent_outcome, attempt.hypothesis, attempt.artifact_ref, attempt.lessons) == (
        "attempted", "More features help", "commit abc", "Keep it small")
    assert attempt.recorded_at is not None

    again = await _error(h, cycle["turn"], "record_campaign_attempt", outcome="abandoned",
                         hypothesis="x", change_summary="y")
    assert again["code"] == "CAMPAIGN_ATTEMPT_ALREADY_RECORDED"
    assert (await _attempt(h, cycle["campaign_id"])).agent_outcome == "attempted"


async def test_record_needs_an_explicit_outcome_and_fields(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    bad = await _error(h, cycle["turn"], "record_campaign_attempt", outcome="improved",
                       hypothesis="x", change_summary="y")
    assert bad["code"] == "CAMPAIGN_INVALID_ARGUMENT"
    missing = await _error(h, cycle["turn"], "record_campaign_attempt", outcome="attempted",
                           hypothesis="x")
    assert missing["code"] == "CAMPAIGN_INVALID_ARGUMENT"
    assert (await _attempt(h, cycle["campaign_id"])).recorded_at is None


async def test_record_is_refused_once_the_evaluation_started(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    async with h.sessions() as db:
        attempt = await db.scalar(select(CampaignAttempt).where(
            CampaignAttempt.campaign_id == cycle["campaign_id"]))
        attempt.eval_job_id, attempt.eval_status = attempt.id, "running"
        await db.commit()
    error = await _error(h, cycle["turn"], "record_campaign_attempt", outcome="attempted",
                         hypothesis="x", change_summary="y")
    assert error["code"] == "CAMPAIGN_EVAL_STARTED"


@pytest.mark.req("CMP-13")
async def test_a_closed_cycle_refuses_the_record(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    async with h.sessions() as db:
        campaign = await db.get(Campaign, cycle["campaign_id"])
        await lifecycle.stop_campaign(db, campaign, actor="user:x", stop_reason="user")
        await db.commit()
    error = await _error(h, cycle["turn"], "record_campaign_attempt", outcome="attempted",
                         hypothesis="x", change_summary="y")
    assert error["code"] == "CAMPAIGN_CYCLE_CLOSED"
    assert "stopped" in error["detail"]


async def test_a_turn_outside_any_cycle_is_refused(flow_harness):
    h = flow_harness
    await h.user_request("Just a question")
    turn = await h.deliver("lead")
    error = await _error(h, turn, "read_campaign_attempts")
    assert error["code"] == "CAMPAIGN_CYCLE_REQUIRED"


async def test_only_the_campaigns_agent_may_act(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    async with h.sessions() as db:
        campaign = await db.get(Campaign, cycle["campaign_id"])
        campaign.agent_id = h.ids["worker"]
        await db.commit()
    error = await _error(h, cycle["turn"], "read_campaign_attempts")
    assert error["code"] == "CAMPAIGN_AGENT_MISMATCH"


async def test_a_stale_turn_proof_is_refused(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    cycle["turn"].lease = "forged"
    error = await _error(h, cycle["turn"], "read_campaign_attempts")
    assert error["code"] == "STALE_TURN_PROOF"


@pytest.mark.req("CMP-13")
async def test_record_returns_follow_up_directives_given_during_the_cycle(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    async with h.sessions() as db:
        campaign = await db.get(Campaign, cycle["campaign_id"])
        assert await apply_transition(db, campaign, "pause", actor=f"user:{h.ids['owner']}",
                                      details={"open_cycle_no": 1})
        await db.commit()
    result = await _record(h, cycle["turn"])
    assert [d["type"] for d in result["follow_up"]] == ["status_changed"]
    assert "paused" in result["follow_up"][0]["instruction"]
    assert result["campaign"]["status"] == "paused"


async def test_the_cycle_keeps_working_after_begin_and_closes_terminal(flow_harness):
    """The tools follow the turn to its execution; once the cycle is scored
    neither the run nor its Task is left open (decision 7)."""
    h = flow_harness
    cycle = await _open_cycle(h)
    await h.tool(cycle["turn"], "begin_project_execution", objective="Improve accuracy")
    await _record(h, cycle["turn"])
    assert await _complete_and_score(h, cycle, cycle["turn"], score=0.5) == "judged"
    attempt = await _attempt(h, cycle["campaign_id"])
    assert (attempt.outcome, attempt.dev_score) == ("improved", 0.5)
    async with h.sessions() as db:
        assert (await db.get(Task, cycle["task_id"])).status == "done"
        assert (await db.get(ProjectExecution, attempt.execution_id)).status == "completed"


async def test_an_unrecorded_cycle_is_scored_as_no_record(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    await h.tool(cycle["turn"], "begin_project_execution", objective="Improve accuracy")
    assert await _complete_and_score(h, cycle, cycle["turn"], score=0.95) == "judged"
    attempt = await _attempt(h, cycle["campaign_id"])
    assert (attempt.outcome, attempt.dev_score) == ("no_record", 0.95)
    async with h.sessions() as db:
        campaign = await db.get(Campaign, cycle["campaign_id"])
        # Neither the best attempt nor the target: the run was not reported.
        assert campaign.best_attempt_id is None
        assert campaign.status == "active"
        assert campaign.cycles_since_improvement == 1


async def test_wakeup_range_and_deadline_are_checked(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h, deadline=datetime.now(UTC) + timedelta(hours=2))
    turn = cycle["turn"]
    assert (await _error(h, turn, "schedule_campaign_wakeup", delay_seconds=30,
                         reason="soon"))["code"] in {"CAMPAIGN_WAKEUP_OUT_OF_RANGE", None}
    late = await _error(h, turn, "schedule_campaign_wakeup", delay_seconds=3 * 3600,
                        reason="after the deadline")
    assert late["code"] == "CAMPAIGN_WAKEUP_AFTER_DEADLINE"
    both = await _error(h, turn, "schedule_campaign_wakeup", delay_seconds=120,
                        at=(datetime.now(UTC) + timedelta(hours=1)).isoformat(), reason="x")
    assert both["code"] == "CAMPAIGN_INVALID_ARGUMENT"
    naive = await _error(h, turn, "schedule_campaign_wakeup",
                         at=(datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None).isoformat(), reason="x")
    assert naive["code"] == "CAMPAIGN_INVALID_ARGUMENT"


async def test_the_last_wakeup_wins_and_delays_the_next_cycle(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    turn = cycle["turn"]
    await h.tool(turn, "begin_project_execution", objective="Improve accuracy")
    await h.tool(turn, "schedule_campaign_wakeup", delay_seconds=600, reason="first")
    wake_at = datetime.now(UTC) + timedelta(hours=3)
    result = await h.tool(turn, "schedule_campaign_wakeup", at=wake_at.isoformat(),
                          reason="the training job needs three hours")
    assert datetime.fromisoformat(result["wake_at"]) == wake_at
    await _record(h, turn)
    assert await _complete_and_score(h, cycle, turn) == "judged"
    attempt = await _attempt(h, cycle["campaign_id"])
    assert attempt.wake_reason == "the training job needs three hours"
    async with h.sessions() as db:
        campaign = await db.get(Campaign, cycle["campaign_id"])
        assert campaign.next_cycle_at == wake_at  # later than close + 300 s
        events = list(await db.scalars(select(CampaignEvent.type).where(
            CampaignEvent.campaign_id == campaign.id)))
        assert events.count("wakeup_scheduled") == 2


async def test_a_short_wakeup_still_keeps_the_minimum_gap(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    turn = cycle["turn"]
    await h.tool(turn, "begin_project_execution", objective="Improve accuracy")
    await h.tool(turn, "schedule_campaign_wakeup", delay_seconds=60, reason="quick")
    await _record(h, turn)
    assert await _complete_and_score(h, cycle, turn) == "judged"
    attempt = await _attempt(h, cycle["campaign_id"])
    async with h.sessions() as db:
        campaign = await db.get(Campaign, cycle["campaign_id"])
        assert campaign.next_cycle_at == attempt.closed_at + timedelta(seconds=300)


async def test_blocked_report_applies_when_the_cycle_closes(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    turn = cycle["turn"]
    await h.tool(turn, "begin_project_execution", objective="Improve accuracy")
    result = await h.tool(turn, "report_campaign_blocked", reason="The dataset is gone",
                          unblock_hint="Upload data/train.csv")
    assert result["blocked_after_cycle"] is True
    async with h.sessions() as db:
        assert (await db.get(Campaign, cycle["campaign_id"])).status == "active"
    await _record(h, turn, outcome="abandoned")
    assert await _complete_and_score(h, cycle, turn, score=0.4) == "judged"
    async with h.sessions() as db:
        campaign = await db.get(Campaign, cycle["campaign_id"])
        assert campaign.status == "blocked"
        assert "The dataset is gone" in campaign.status_detail
        assert "Upload data/train.csv" in campaign.status_detail


async def test_a_target_verdict_wins_over_a_blocked_report(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    turn = cycle["turn"]
    await h.tool(turn, "begin_project_execution", objective="Improve accuracy")
    await h.tool(turn, "report_campaign_blocked", reason="stuck", unblock_hint="help")
    await _record(h, turn)
    assert await _complete_and_score(h, cycle, turn, score=0.95) == "judged"
    async with h.sessions() as db:
        assert (await db.get(Campaign, cycle["campaign_id"])).status == "awaiting_confirmation"


async def test_read_attempts_pages_newest_first(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    async with h.sessions() as db:
        for no in (10, 11):
            db.add(CampaignAttempt(campaign_id=cycle["campaign_id"], cycle_no=no,
                                   opened_at=datetime.now(UTC), closed_at=datetime.now(UTC),
                                   outcome="no_gain", hypothesis=f"h{no}",
                                   eval_excerpt="x" * 2000))
        await db.commit()
    page = await h.tool(cycle["turn"], "read_campaign_attempts", limit=2)
    assert page["total"] == 3 and page["next_offset"] == 2
    assert [a["cycle_no"] for a in page["attempts"]] == [11, 10]
    assert len(page["attempts"][0]["eval_excerpt"]) == 500
    rest = await h.tool(cycle["turn"], "read_campaign_attempts", offset=2, limit=2)
    assert [a["cycle_no"] for a in rest["attempts"]] == [1] and rest["next_offset"] is None
    too_many = await _error(h, cycle["turn"], "read_campaign_attempts", limit=21)
    assert too_many["code"] in {"CAMPAIGN_INVALID_ARGUMENT", None}


async def test_learning_is_refused_outside_a_retrospective(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    error = await _error(h, cycle["turn"], "apply_campaign_learning", target="agents_md",
                         text="- Small changes win", rationale="seen twice")
    assert error["code"] == "CAMPAIGN_NOT_RETROSPECTIVE"


@pytest.mark.req("CMP-13")
async def test_an_api_edit_reaches_the_open_cycle_as_a_follow_up(flow_harness):
    """Phase 7 → Phase 5: a person edits the campaign through the REST API
    while the cycle runs; the agent sees it when it records the attempt."""
    h = flow_harness
    cycle = await _open_cycle(h, deadline=datetime.now(UTC) + timedelta(days=2))
    cid = cycle["campaign_id"]
    edit = await h.client.patch(f"/api/v1/campaigns/{cid}", headers=h.user(), json={
        "purpose": "Reach 0.95 without new dependencies.", "metric": {"target": 0.95},
    })
    assert edit.status_code == 200, edit.text
    later = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    extended = await h.client.post(f"/api/v1/campaigns/{cid}/extend", headers=h.user(),
                                   json={"deadline_at": later})
    assert extended.status_code == 200, extended.text
    outsider = await h.client.post(f"/api/v1/campaigns/{cid}/pause",
                                   headers=h.user("outsider"))
    assert outsider.status_code == 403

    result = await _record(h, cycle["turn"])
    follow_up = result["follow_up"]
    assert [d["type"] for d in follow_up] == ["edited", "status_changed"]
    assert "metric.target: 0.9 → 0.95" in follow_up[0]["instruction"]
    assert "purpose" in follow_up[0]["details"]["changes"]
    assert "extended" in follow_up[1]["instruction"]
    assert result["campaign"]["metric"]["target"] == 0.95
    assert result["campaign"]["purpose"] == "Reach 0.95 without new dependencies."


async def test_follow_ups_of_an_earlier_cycle_are_not_repeated(flow_harness):
    h = flow_harness
    cycle = await _open_cycle(h)
    cid = cycle["campaign_id"]
    async with h.sessions() as db:
        db.add(CampaignEvent(campaign_id=cid, event_key=f"{cid}:old", type="edited",
                             details={"changes": {"title": {"from": "a", "to": "b"}},
                                      "open_cycle_no": 0},
                             actor="user:x", created_at=datetime.now(UTC) + timedelta(hours=1)))
        await db.commit()
    assert (await _record(h, cycle["turn"]))["follow_up"] == []
