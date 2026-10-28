"""#947/#948 — an agent drafts a goal or routine; a person creates it with one click."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from anygarden.db.models import (
    AgentTurn,
    AgentTurnAttempt,
    Campaign,
    Goal,
    Message,
    Room,
)
from anygarden.db.workspace_action_models import DraftRequest, WorkspaceActionRequest
from anygarden.workspace.drafts import close_unanswered_drafts
from tests.workspace.test_workspace_tools import (  # noqa: F401
    _count,
    _rpc,
    _turn_headers,
    ws,
)

GOAL = {
    "title": "Faster search",
    "purpose": "Search answers in under a second",
    "mode": "target",
    "metric": {"name": "p95_ms", "direction": "minimize", "target": 1000},
    "evaluator": {"command": "python3 bench.py", "score_key": "p95_ms"},
    "budget": {"max_total_tokens": 200000},
}
ROUTINE = {
    "title": "Morning report",
    "spec": "Summarise yesterday's failed tasks",
    "trigger_type": "cron",
    "trigger_config": {"cron": "0 9 * * *"},
}


async def _set_lead(env: dict, agent_key: str | None = "agent") -> None:
    async with env["factory"]() as db:
        await db.execute(update(Room).where(Room.id == env["room"]).values(
            representative_agent_id=env[agent_key] if agent_key else None,
        ))
        await db.commit()


async def _ask(env: dict, kind: str, description: str = "make search faster", **extra):
    return await env["client"].post(
        f"/api/v1/rooms/{env['room']}/drafts",
        json={"kind": kind, "description": description, **extra},
        headers=env["owner_headers"],
    )


async def _lease(env: dict, turn_request_id: str) -> dict:
    """Lease the drafter's turn the way delivery would."""

    async with env["factory"]() as db:
        turn = await db.get(AgentTurn, turn_request_id)
        attempt = await db.scalar(select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == turn_request_id))
        attempt.state = "leased"
        attempt.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        turn.state = "leased"
        await db.commit()
        return {
            "Authorization": f"Bearer {env['agent_token']}",
            "X-Anygarden-Turn-Request-Id": turn_request_id,
            "X-Anygarden-Turn-Attempt": str(attempt.attempt_number),
            "X-Anygarden-Turn-Generation": str(attempt.generation),
            "X-Anygarden-Turn-Lease": attempt.lease_token,
        }


async def _draft_turn(env: dict, kind: str, **extra) -> tuple[dict, dict]:
    resp = await _ask(env, kind, **extra)
    assert resp.status_code == 201, resp.text
    draft = resp.json()
    return draft, await _lease(env, draft["turn_request_id"])


# -- /goal ----------------------------------------------------------------------


@pytest.mark.req("WSP-06")
@pytest.mark.asyncio
async def test_goal_draft_wakes_the_lead_and_becomes_a_card(ws) -> None:
    resp = await _ask(ws, "goal")
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "ROOM_LEAD_REQUIRED"

    await _set_lead(ws)
    draft, headers = await _draft_turn(ws, "goal")
    assert draft["drafter_agent_id"] == ws["agent"]
    async with ws["factory"]() as db:
        notice = await db.get(Message, draft["request_message_id"])
        assert notice.participant_id is None
        assert notice.extra_metadata["mentions"] == [{"type": "user", "id": ws["bot"]}]
        assert "propose_goal" in notice.content and "make search faster" in notice.content

    bad = await _rpc(ws, "propose_goal", {**GOAL, "metric": {"name": "p95_ms", "direction": "minimize"}},
                     headers=headers)
    assert bad["isError"] and bad["structuredContent"]["code"] == "TARGET_OR_DEADLINE_REQUIRED"
    assert await _count(ws, WorkspaceActionRequest) == 0

    ok = await _rpc(ws, "propose_goal", GOAL, headers=headers)
    assert not ok["isError"], ok
    assert ok["structuredContent"]["status"] == "pending_approval"
    async with ws["factory"]() as db:
        row = await db.get(WorkspaceActionRequest, ok["structuredContent"]["request_id"])
        assert row.action == "create_goal" and row.requested_by_user_id == ws["owner"]
        assert row.params["room_id"] == ws["room"] and row.params["spec"]["title"] == "Faster search"
        stored = await db.get(DraftRequest, draft["id"])
        assert stored.status == "proposed" and stored.action_request_id == row.id

    again = await _rpc(ws, "propose_goal", GOAL, headers=headers)
    assert again["structuredContent"]["code"] == "DRAFT_ALREADY_PROPOSED"


@pytest.mark.req("WSP-06")
@pytest.mark.asyncio
async def test_propose_works_only_in_its_own_draft_turn(ws) -> None:
    await _set_lead(ws)
    ordinary = await _rpc(ws, "propose_goal", GOAL)
    assert ordinary["isError"] and ordinary["structuredContent"]["code"] == "NOT_A_DRAFT_TURN"

    _, headers = await _draft_turn(ws, "goal")
    wrong_kind = await _rpc(ws, "propose_routine", ROUTINE, headers=headers)
    assert wrong_kind["structuredContent"]["code"] == "NOT_A_DRAFT_TURN"
    by_server = await _turn_headers(ws, "notice", sender=None, metadata={"system_origin": "task_assignment"})
    assert (await _rpc(ws, "propose_goal", GOAL, headers=by_server))["structuredContent"]["code"] == "NOT_A_DRAFT_TURN"
    assert await _count(ws, WorkspaceActionRequest) == 0


@pytest.mark.req("WSP-06")
@pytest.mark.asyncio
async def test_a_turn_that_ends_without_a_draft_is_reported(ws) -> None:
    await _set_lead(ws)
    draft, _ = await _draft_turn(ws, "goal")
    assert await close_unanswered_drafts(ws["factory"]) == 0  # still working

    async with ws["factory"]() as db:
        await db.execute(update(AgentTurn).where(AgentTurn.request_id == draft["turn_request_id"])
                         .values(state="completed"))
        await db.commit()
    assert await close_unanswered_drafts(ws["factory"]) == 1
    assert await close_unanswered_drafts(ws["factory"]) == 0
    async with ws["factory"]() as db:
        assert (await db.get(DraftRequest, draft["id"])).status == "failed"
        notice = await db.scalar(select(Message).where(
            Message.room_id == ws["room"], Message.content.like("No goal draft%"),
        ))
        meta = notice.extra_metadata
        assert meta["system_origin"] == "draft_request_failed" and meta["draft_request_failed"]["kind"] == "goal"
        assert "mentions" not in meta  # it tells the person; it does not wake the agent


# -- approval -------------------------------------------------------------------------


@pytest.mark.req("WSP-08")
@pytest.mark.asyncio
async def test_goal_needs_the_evaluator_right_and_runs_edited_values_once(ws) -> None:
    await _set_lead(ws)
    _, headers = await _draft_turn(ws, "goal")
    request_id = (await _rpc(ws, "propose_goal", GOAL, headers=headers))["structuredContent"]["request_id"]

    # The lead agent is on no machine: only an admin may set its evaluator.
    seen = await ws["client"].get(f"/api/v1/workspace-actions/{request_id}", headers=ws["owner_headers"])
    assert seen.json()["can_decide"] is False
    refused = await ws["client"].post(f"/api/v1/workspace-actions/{request_id}/approve", headers=ws["owner_headers"])
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "EVALUATOR_FORBIDDEN"

    edited = {"spec": {**GOAL, "title": "Search under 800ms", "metric": {**GOAL["metric"], "target": 800}}}
    approved = await ws["client"].post(
        f"/api/v1/workspace-actions/{request_id}/approve", json={"params": edited}, headers=ws["admin_headers"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"
    assert approved.json()["params"]["room_id"] == ws["room"]
    twice = await ws["client"].post(
        f"/api/v1/workspace-actions/{request_id}/approve", json={"params": edited}, headers=ws["admin_headers"],
    )
    assert twice.status_code == 409
    async with ws["factory"]() as db:
        campaigns = list(await db.scalars(select(Campaign).where(Campaign.room_id == ws["room"])))
        assert [(c.title, c.metric["target"]) for c in campaigns] == [("Search under 800ms", 800)]


@pytest.mark.req("WSP-08")
@pytest.mark.asyncio
async def test_only_goals_and_routines_take_edited_values(ws) -> None:
    from tests.workspace.test_workspace_tools import _action

    pending = await _action(ws, "archive_room", {"room_id": ws["room"]})
    request_id = pending["structuredContent"]["request_id"]
    refused = await ws["client"].post(
        f"/api/v1/workspace-actions/{request_id}/approve",
        json={"params": {"room_id": ws["hidden"]}}, headers=ws["owner_headers"],
    )
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "NOT_EDITABLE"
    async with ws["factory"]() as db:
        assert (await db.get(WorkspaceActionRequest, request_id)).status == "pending"


# -- /routine ---------------------------------------------------------------------------


@pytest.mark.req("WSP-07")
@pytest.mark.asyncio
async def test_routine_drafter_follows_who_was_named(ws) -> None:
    # Two agents, no lead, nobody named: the person must pick one.
    resp = await _ask(ws, "routine")
    assert resp.status_code == 409 and resp.json()["detail"]["code"] == "AGENT_REQUIRED"

    named = (await _ask(ws, "routine", agent_id=ws["peer"])).json()
    assert (named["drafter_agent_id"], named["assignee_agent_id"]) == (ws["peer"], ws["peer"])
    outsider = await _ask(ws, "routine", agent_id="no-such-agent")
    assert outsider.status_code == 422

    await _set_lead(ws)
    led = (await _ask(ws, "routine")).json()
    assert (led["drafter_agent_id"], led["assignee_agent_id"]) == (ws["agent"], None)
    async with ws["factory"]() as db:
        notice = await db.get(Message, led["request_message_id"])
        assert ws["peer"] in notice.content  # the lead sees whom it can pick


@pytest.mark.req("WSP-07")
@pytest.mark.asyncio
async def test_routine_draft_is_checked_and_keeps_the_persons_timezone(ws) -> None:
    _, headers = await _draft_turn(ws, "routine", agent_id=ws["agent"], timezone="Asia/Seoul")

    bad_cron = await _rpc(ws, "propose_routine", {**ROUTINE, "trigger_config": {"cron": "every morning"}},
                          headers=headers)
    assert bad_cron["structuredContent"]["code"] == "INVALID_DRAFT"
    other = await _rpc(ws, "propose_routine", {**ROUTINE, "agent_id": ws["peer"]}, headers=headers)
    assert other["structuredContent"]["code"] == "ASSIGNEE_FIXED"
    away = await _rpc(ws, "propose_routine", {**ROUTINE, "report_room_id": ws["hidden"]}, headers=headers)
    assert away["structuredContent"]["code"] == "AGENT_NOT_IN_REPORT_ROOM"
    assert await _count(ws, WorkspaceActionRequest) == 0

    ok = await _rpc(ws, "propose_routine", ROUTINE, headers=headers)
    assert not ok["isError"], ok
    async with ws["factory"]() as db:
        row = await db.get(WorkspaceActionRequest, ok["structuredContent"]["request_id"])
    assert row.action == "create_routine"
    assert row.params["trigger_config"] == {"cron": "0 9 * * *", "timezone": "Asia/Seoul"}
    assert (row.params["agent_id"], row.params["report_room_id"]) == (ws["agent"], ws["room"])

    edited = {**row.params, "trigger_config": {"cron": "30 8 * * 1-5", "timezone": "Asia/Seoul"}}
    approved = await ws["client"].post(
        f"/api/v1/workspace-actions/{row.id}/approve", json={"params": edited}, headers=ws["owner_headers"],
    )
    assert approved.json()["status"] == "executed", approved.text
    async with ws["factory"]() as db:
        goals = list(await db.scalars(select(Goal).where(Goal.assignee_agent_id == ws["agent"])))
        assert [g.trigger_config["cron"] for g in goals] == ["30 8 * * 1-5"]
        assert goals[0].owner_id == ws["owner"]


@pytest.mark.req("WSP-07")
@pytest.mark.asyncio
async def test_routine_draft_respects_the_active_routine_limit(ws, monkeypatch) -> None:
    import anygarden.api.v1.goals as goals_api

    monkeypatch.setattr(goals_api, "MAX_ACTIVE_GOALS_PER_OWNER", 0)
    _, headers = await _draft_turn(ws, "routine", agent_id=ws["agent"])
    over = await _rpc(ws, "propose_routine", ROUTINE, headers=headers)
    assert over["structuredContent"]["code"] == "ROUTINE_LIMIT"
    async with ws["factory"]() as db:
        assert await db.scalar(select(func.count()).select_from(DraftRequest).where(
            DraftRequest.status == "pending")) == 1


def test_goal_guidance_names_the_required_valid_field() -> None:
    """#956 — the evaluator's last line needs ``valid`` too; a draft that
    followed the guidance alone failed every cycle with ``valid_missing``."""
    from anygarden.mcp.draft_tools import DRAFT_TOOL_SCHEMAS
    from anygarden.workspace.drafts import _goal_brief

    assert '"valid": true' in _goal_brief("p1", "ideas")
    goal = next(t for t in DRAFT_TOOL_SCHEMAS if t["name"] == "propose_goal")
    schema = goal.get("inputSchema") or goal.get("input_schema") or goal["parameters"]
    command = schema["properties"]["evaluator"]["properties"]["command"]["description"]
    assert '"valid"' in command


@pytest.mark.req("CMP-19")
def test_goal_guidance_makes_the_evaluator_count_what_the_metric_says() -> None:
    """#969 — a metric named "deduplicated ideas with feedback" was scored
    by a script that only caught identical titles: 30 by the score, 24 by
    the lead's own judgement in decisions.json, which the script never read."""
    from anygarden.mcp.draft_tools import DRAFT_TOOL_SCHEMAS
    from anygarden.workspace.drafts import _goal_brief

    brief = _goal_brief("p1", "ideas")
    assert "exactly what the metric name says" in brief
    assert "reads that field" in brief
    goal = next(t for t in DRAFT_TOOL_SCHEMAS if t["name"] == "propose_goal")
    schema = goal.get("inputSchema") or goal.get("input_schema") or goal["parameters"]
    name = schema["properties"]["metric"]["properties"]["name"]["description"]
    assert "what the evaluator counts" in name


@pytest.mark.req("CMP-01")
def test_goal_drafts_leave_the_token_budget_out_by_default() -> None:
    """#963 — the token budget is optional; a draft only sets one when the
    person asked for a token limit."""
    from anygarden.mcp.draft_tools import DRAFT_TOOL_SCHEMAS
    from anygarden.workspace.drafts import _goal_brief

    goal = next(t for t in DRAFT_TOOL_SCHEMAS if t["name"] == "propose_goal")
    schema = goal.get("inputSchema") or goal.get("input_schema") or goal["parameters"]
    assert "budget" not in schema["required"]
    assert "asked for a token limit" in schema["properties"]["budget"]["description"]
    assert "asked for a token limit" in _goal_brief("p1", "ideas")


@pytest.mark.req("CMP-01")
@pytest.mark.asyncio
async def test_a_goal_draft_without_a_budget_is_proposed(ws) -> None:  # noqa: F811
    await _set_lead(ws)
    _, headers = await _draft_turn(ws, "goal")
    spec = {k: v for k, v in GOAL.items() if k != "budget"}
    ok = await _rpc(ws, "propose_goal", spec, headers=headers)
    assert not ok["isError"], ok
    async with ws["factory"]() as db:
        row = await db.get(WorkspaceActionRequest, ok["structuredContent"]["request_id"])
        assert "max_total_tokens" not in row.params["spec"].get("budget", {})
