"""#938 — agents look over and act on the workspace for the person who asked."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from anygarden.db.models import (
    Agent,
    AgentToken,
    AgentTurn,
    AgentTurnAttempt,
    Goal,
    Message,
    Participant,
    Project,
    Room,
    Task,
    User,
)
from anygarden.db.workspace_action_models import WorkspaceActionRequest
from anygarden.messages.service import append_message
from anygarden.turns.service import create_turn

ROUTINE = {
    "agent_id": None,  # filled per test
    "title": "Morning report",
    "spec": "Summarise yesterday's failed tasks",
    "trigger_type": "cron",
    "trigger_config": {"cron": "0 9 * * *"},
    "report_room_id": None,
}


@pytest_asyncio.fixture()
async def ws(tmp_path):
    from anygarden.app import create_app
    from anygarden.auth.jwt import create_user_token
    from anygarden.auth.token import generate_token, hash_agent_token
    from anygarden.config import AnygardenSettings
    from anygarden.db.engine import build_engine, build_session_factory
    from anygarden.db.models import Base

    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'ws.db'}", jwt_secret=secrets.token_urlsafe(32),
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    app.state.agent_lifecycle = SimpleNamespace(request_start=AsyncMock(), bump_generation=AsyncMock())
    async with factory() as db:
        owner = User(email="owner@example.com", password_hash="x")
        admin = User(email="admin@example.com", password_hash="x", is_admin=True)
        outsider = User(email="outsider@example.com", password_hash="x")
        agent = Agent(name="ops-bot", engine="codex", desired_state="running", workspace_tools=True)
        peer = Agent(name="peer-bot", engine="codex", desired_state="running")
        hidden_agent = Agent(name="hidden-bot", engine="codex", desired_state="running")
        db.add_all([owner, admin, outsider, agent, peer, hidden_agent])
        await db.flush()
        project = Project(name="Proj", created_by=owner.id)
        db.add(project)
        await db.flush()
        room = Room(name="ops", project_id=project.id)
        hidden = Room(name="secret", project_id=project.id)
        db.add_all([room, hidden])
        await db.flush()
        human = Participant(room_id=room.id, user_id=owner.id, role="owner")
        bot = Participant(room_id=room.id, agent_id=agent.id, role="member")
        peer_p = Participant(room_id=room.id, agent_id=peer.id, role="member")
        db.add_all([
            human, bot, peer_p,
            Participant(room_id=hidden.id, user_id=outsider.id, role="owner"),
            Participant(room_id=hidden.id, agent_id=hidden_agent.id, role="member"),
        ])
        await db.flush()
        plain = generate_token()
        token_hash, hint = hash_agent_token(plain)
        db.add(AgentToken(agent_id=agent.id, token_hash=token_hash, lookup_hint=hint))
        await db.commit()
        ids = {
            "owner": owner.id, "admin": admin.id, "outsider": outsider.id, "agent": agent.id,
            "peer": peer.id, "room": room.id, "hidden": hidden.id, "project": project.id,
            "human": human.id, "bot": bot.id, "peer_p": peer_p.id,
        }

    def user_headers(user_id: str, email: str, is_admin: bool = False) -> dict:
        return {"Authorization": f"Bearer {create_user_token(user_id, email, is_admin, secret=config.jwt_secret)}"}

    env = {
        **ids, "factory": factory, "agent_token": plain,
        "owner_headers": user_headers(ids["owner"], "owner@example.com"),
        "admin_headers": user_headers(ids["admin"], "admin@example.com", True),
        "outsider_headers": user_headers(ids["outsider"], "outsider@example.com"),
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        env["client"] = client
        env["headers"] = await _turn_headers(env, "Please organise the workspace")
        yield env
    await engine.dispose()


async def _turn_headers(env: dict, content: str, *, sender: str | None = "human", metadata=None) -> dict:
    """Post a message in the ops room and lease the bot's turn on it."""

    async with env["factory"]() as db:
        participant = env[sender] if sender else None
        trigger = await append_message(db, env["room"], participant, content, metadata or {})
        turn = await create_turn(
            db, room_id=env["room"], participant_id=env["bot"], agent_id=env["agent"],
            trigger_message_id=trigger.id,
        )
        attempt = await db.scalar(select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == turn.request_id))
        attempt.state = "leased"
        attempt.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        turn.state = "leased"
        await db.commit()
        return {
            "Authorization": f"Bearer {env['agent_token']}",
            "X-Anygarden-Turn-Request-Id": turn.request_id,
            "X-Anygarden-Turn-Attempt": str(attempt.attempt_number),
            "X-Anygarden-Turn-Generation": str(attempt.generation),
            "X-Anygarden-Turn-Lease": attempt.lease_token,
        }


async def _rpc(env: dict, name: str, arguments: dict, *, headers: dict | None = None) -> dict:
    resp = await env["client"].post(
        "/mcp/rpc",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        headers=headers or env["headers"],
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


async def _action(env: dict, action: str, params: dict, **kw) -> dict:
    return await _rpc(env, "workspace_action", {"action": action, "params": params}, **kw)


def _routine(env: dict) -> dict:
    return {**ROUTINE, "agent_id": env["agent"], "report_room_id": env["room"]}


async def _count(env: dict, model, *where) -> int:
    async with env["factory"]() as db:
        return await db.scalar(select(func.count()).select_from(model).where(*where))


# -- exposure -----------------------------------------------------------------


@pytest.mark.req("WSP-01")
@pytest.mark.asyncio
async def test_tools_are_listed_only_for_agents_with_workspace_tools(ws) -> None:
    async def names() -> set[str]:
        resp = await ws["client"].post(
            "/mcp/rpc", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, headers=ws["headers"],
        )
        return {tool["name"] for tool in resp.json()["result"]["tools"]}

    assert {"workspace_query", "workspace_action"} <= await names()
    async with ws["factory"]() as db:
        await db.execute(update(Agent).where(Agent.id == ws["agent"]).values(workspace_tools=False))
        await db.commit()
    assert not {"workspace_query", "workspace_action"} & await names()
    refused = await _rpc(ws, "workspace_query", {"kind": "rooms"})
    assert refused["isError"] and refused["structuredContent"]["code"] == "WORKSPACE_TOOLS_OFF"


# -- whose rights ---------------------------------------------------------------


@pytest.mark.req("WSP-02")
@pytest.mark.asyncio
async def test_a_turn_started_by_an_agent_or_the_server_cannot_act(ws) -> None:
    by_agent = await _turn_headers(ws, "<@user:x> please make a room", sender="peer_p")
    by_server = await _turn_headers(ws, "notice", sender=None, metadata={"system_origin": "task_assignment"})
    for headers in (by_agent, by_server):
        result = await _action(ws, "create_room", {"name": "x", "parent_room_id": ws["room"]}, headers=headers)
        assert result["isError"], result
        assert result["structuredContent"]["code"] == "NO_PERSON_TO_ACT_FOR"
    assert await _count(ws, Room, Room.parent_room_id == ws["room"]) == 0


@pytest.mark.req("WSP-02")
@pytest.mark.asyncio
async def test_queries_show_only_what_the_person_can_see(ws) -> None:
    rooms = await _rpc(ws, "workspace_query", {"kind": "rooms"})
    assert not rooms["isError"], rooms
    room_ids = {r["id"] for r in rooms["structuredContent"]["items"]}
    assert ws["room"] in room_ids and ws["hidden"] not in room_ids

    agents = await _rpc(ws, "workspace_query", {"kind": "agents"})
    agent_ids = {a["id"] for a in agents["structuredContent"]["items"]}
    assert {ws["agent"], ws["peer"]} <= agent_ids and ws["agent"] in agent_ids
    assert not any(a["name"] == "hidden-bot" for a in agents["structuredContent"]["items"])

    hidden = await _rpc(ws, "workspace_query", {"kind": "routines", "room_id": ws["hidden"]})
    assert hidden["isError"]

    members = await _rpc(ws, "workspace_query", {"kind": "room", "room_id": ws["room"]})
    assert {p["id"] for p in members["structuredContent"]["participants"]} >= {ws["human"], ws["bot"]}


@pytest.mark.req("WSP-02")
@pytest.mark.asyncio
async def test_an_action_in_a_room_the_person_cannot_use_is_refused(ws) -> None:
    result = await _action(ws, "create_task", {"room_id": ws["hidden"], "title": "sneak"})
    assert result["isError"], result
    assert await _count(ws, Task, Task.room_id == ws["hidden"]) == 0


# -- allowed actions ------------------------------------------------------------


@pytest.mark.req("WSP-03")
@pytest.mark.asyncio
async def test_allowed_actions_run_at_once(ws) -> None:
    sub = await _action(ws, "create_room", {"name": "qa", "parent_room_id": ws["room"],
                                             "member_participant_ids": [ws["peer_p"]]})
    assert not sub["isError"], sub
    sub_id = sub["structuredContent"]["result"]["id"]
    async with ws["factory"]() as db:
        child = await db.get(Room, sub_id)
        assert child.parent_room_id == ws["room"]
        members = set(await db.scalars(select(Participant.agent_id).where(Participant.room_id == sub_id)))
        assert ws["peer"] in members

    task = await _action(ws, "create_task", {"room_id": ws["room"], "title": "Check logs",
                                              "assignee_participant_id": ws["peer_p"]})
    assert not task["isError"], task
    assert task["structuredContent"]["result"]["assignee_participant_id"] == ws["peer_p"]

    moved = await _action(ws, "reassign_task", {"task_id": task["structuredContent"]["result"]["id"],
                                                 "assignee_participant_id": ws["bot"]})
    assert not moved["isError"], moved
    assert moved["structuredContent"]["result"]["assignee_participant_id"] == ws["bot"]


@pytest.mark.asyncio
async def test_bad_params_and_unknown_actions_are_explained(ws) -> None:
    unknown = await _action(ws, "delete_everything", {})
    assert unknown["structuredContent"]["code"] == "UNKNOWN_ACTION"
    bad = await _action(ws, "create_room", {"name": "x"})
    assert bad["structuredContent"]["code"] == "INVALID_PARAMS"


# -- actions that need approval ---------------------------------------------------


async def _pending_routine(ws) -> str:
    asked = await _action(ws, "create_routine", _routine(ws))
    assert not asked["isError"], asked
    assert asked["structuredContent"]["status"] == "pending_approval"
    return asked["structuredContent"]["request_id"]


@pytest.mark.req("WSP-04")
@pytest.mark.asyncio
async def test_an_ask_action_waits_and_runs_once_when_approved(ws) -> None:
    request_id = await _pending_routine(ws)
    assert await _count(ws, Goal) == 0
    async with ws["factory"]() as db:
        card = await db.scalar(select(Message).where(Message.participant_id == ws["bot"]).order_by(Message.seq.desc()))
        assert card.extra_metadata["workspace_action_request"]["request_id"] == request_id

    seen = await ws["client"].get(f"/api/v1/workspace-actions/{request_id}", headers=ws["owner_headers"])
    assert seen.status_code == 200 and seen.json()["can_decide"] is True

    approved = await ws["client"].post(f"/api/v1/workspace-actions/{request_id}/approve",
                                       headers=ws["owner_headers"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed"
    async with ws["factory"]() as db:
        goal = await db.scalar(select(Goal))
        assert goal.owner_id == ws["owner"] and goal.title == "Morning report"
        report = await db.scalar(select(Message).where(Message.room_id == ws["room"]).order_by(Message.seq.desc()))
        assert report.participant_id is None  # a server notice, not words put in the approver's mouth
        assert report.extra_metadata["workspace_action_result"]["status"] == "executed"
        assert await db.scalar(select(func.count()).select_from(AgentTurn).where(
            AgentTurn.trigger_message_id == report.id)) == 1

    again = await ws["client"].post(f"/api/v1/workspace-actions/{request_id}/approve",
                                    headers=ws["owner_headers"])
    assert again.status_code == 409
    assert await _count(ws, Goal) == 1


@pytest.mark.req("WSP-04")
@pytest.mark.asyncio
async def test_rejecting_runs_nothing_and_tells_the_agent(ws) -> None:
    request_id = await _pending_routine(ws)
    rejected = await ws["client"].post(f"/api/v1/workspace-actions/{request_id}/reject",
                                       headers=ws["owner_headers"])
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert await _count(ws, Goal) == 0
    async with ws["factory"]() as db:
        report = await db.scalar(select(Message).where(Message.room_id == ws["room"]).order_by(Message.seq.desc()))
        assert report.extra_metadata["workspace_action_result"]["status"] == "rejected"


@pytest.mark.req("WSP-04")
@pytest.mark.asyncio
async def test_only_the_asker_or_an_admin_decides_and_expired_requests_stay_undone(ws) -> None:
    request_id = await _pending_routine(ws)
    stranger = await ws["client"].post(f"/api/v1/workspace-actions/{request_id}/approve",
                                       headers=ws["outsider_headers"])
    assert stranger.status_code == 403

    async with ws["factory"]() as db:
        await db.execute(update(WorkspaceActionRequest).values(expires_at=datetime.now(UTC) - timedelta(minutes=1)))
        await db.commit()
    late = await ws["client"].post(f"/api/v1/workspace-actions/{request_id}/approve", headers=ws["owner_headers"])
    assert late.status_code == 409
    assert await _count(ws, Goal) == 0
    async with ws["factory"]() as db:
        assert (await db.get(WorkspaceActionRequest, request_id)).status == "expired"


@pytest.mark.asyncio
async def test_an_approved_action_that_fails_is_reported_not_left_running(ws) -> None:
    params = {**_routine(ws), "agent_id": ws["peer"], "report_room_id": ws["room"]}
    async with ws["factory"]() as db:  # the peer leaves the report room before approval
        await db.execute(Participant.__table__.delete().where(Participant.id == ws["peer_p"]))
        await db.commit()
    asked = await _action(ws, "create_routine", params)
    request_id = asked["structuredContent"]["request_id"]
    approved = await ws["client"].post(f"/api/v1/workspace-actions/{request_id}/approve",
                                       headers=ws["owner_headers"])
    assert approved.status_code == 200
    assert approved.json()["status"] == "failed" and approved.json()["error"]


# -- hiring ---------------------------------------------------------------------


HIRE = {"name": "researcher", "engine": "codex", "description": "Reads papers"}


@pytest.mark.req("WSP-05")
@pytest.mark.asyncio
async def test_hiring_needs_the_flag_and_an_admin(ws) -> None:
    no_flag = await _action(ws, "hire_agent", HIRE)
    assert no_flag["structuredContent"]["code"] == "HIRE_NOT_ALLOWED"

    async with ws["factory"]() as db:
        await db.execute(update(Agent).where(Agent.id == ws["agent"]).values(can_create_agents=True))
        await db.commit()
    above = await _action(ws, "hire_agent", {**HIRE, "permission_level": "trusted"})
    assert above["structuredContent"]["code"] == "PERMISSION_ABOVE_CREATOR"

    asked = await _action(ws, "hire_agent", HIRE)
    request_id = asked["structuredContent"]["request_id"]
    by_owner = await ws["client"].post(f"/api/v1/workspace-actions/{request_id}/approve",
                                       headers=ws["owner_headers"])
    assert by_owner.status_code == 403
    assert await _count(ws, Agent, Agent.name == "researcher") == 0


@pytest.mark.req("WSP-05")
@pytest.mark.asyncio
async def test_a_hired_agent_never_outranks_its_creator_or_gets_its_powers(ws) -> None:
    async with ws["factory"]() as db:
        await db.execute(update(Agent).where(Agent.id == ws["agent"]).values(
            can_create_agents=True, permission_level="standard"))
        await db.commit()
    asked = await _action(ws, "hire_agent", {**HIRE, "permission_level": "standard"})
    request_id = asked["structuredContent"]["request_id"]
    # The creator is demoted before the admin approves: the cap follows.
    async with ws["factory"]() as db:
        await db.execute(update(Agent).where(Agent.id == ws["agent"]).values(permission_level="restricted"))
        await db.commit()
    approved = await ws["client"].post(f"/api/v1/workspace-actions/{request_id}/approve",
                                       headers=ws["admin_headers"])
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "executed", approved.json()
    async with ws["factory"]() as db:
        hired = await db.scalar(select(Agent).where(Agent.name == "researcher"))
        assert hired.permission_level == "restricted"
        assert hired.workspace_tools is False and hired.can_create_agents is False


@pytest.mark.req("WSP-01")
@pytest.mark.asyncio
async def test_only_an_admin_turns_workspace_tools_on(ws) -> None:
    body = {"workspace_tools": False, "workspace_tools_set": True,
            "can_create_agents": True, "can_create_agents_set": True}
    denied = await ws["client"].put(f"/api/v1/agents/{ws['agent']}", json=body, headers=ws["owner_headers"])
    assert denied.status_code == 403
    done = await ws["client"].put(f"/api/v1/agents/{ws['agent']}", json=body, headers=ws["admin_headers"])
    assert done.status_code == 200, done.text
    assert done.json()["workspace_tools"] is False and done.json()["can_create_agents"] is True
    # The switches must be committed, not only echoed in the response.
    async with ws["factory"]() as db:
        saved = await db.get(Agent, ws["agent"])
        assert saved.workspace_tools is False and saved.can_create_agents is True


async def _lease_turn_on(env: dict, message_id: str) -> dict:
    async with env["factory"]() as db:
        turn = await db.scalar(select(AgentTurn).where(AgentTurn.trigger_message_id == message_id))
        attempt = await db.scalar(select(AgentTurnAttempt).where(AgentTurnAttempt.turn_id == turn.request_id))
        attempt.state = "leased"
        attempt.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
        turn.state = "leased"
        await db.commit()
        return {
            "Authorization": f"Bearer {env['agent_token']}",
            "X-Anygarden-Turn-Request-Id": turn.request_id,
            "X-Anygarden-Turn-Attempt": str(attempt.attempt_number),
            "X-Anygarden-Turn-Generation": str(attempt.generation),
            "X-Anygarden-Turn-Lease": attempt.lease_token,
        }


@pytest.mark.req("WSP-04")
@pytest.mark.asyncio
async def test_the_woken_turn_acts_for_the_asker_not_an_admin_approver(ws) -> None:
    request_id = await _pending_routine(ws)
    approved = await ws["client"].post(f"/api/v1/workspace-actions/{request_id}/approve",
                                       headers=ws["admin_headers"])
    assert approved.json()["status"] == "executed"
    async with ws["factory"]() as db:
        report = await db.scalar(select(Message).where(Message.room_id == ws["room"]).order_by(Message.seq.desc()))
    headers = await _lease_turn_on(ws, report.id)
    # The owner cannot use the hidden room; an admin could. The turn must not.
    hidden = await _action(ws, "create_task", {"room_id": ws["hidden"], "title": "via admin"}, headers=headers)
    assert hidden["isError"], hidden
    own = await _action(ws, "create_task", {"room_id": ws["room"], "title": "follow-up"}, headers=headers)
    assert not own["isError"], own


@pytest.mark.asyncio
async def test_a_repeated_proposal_in_one_turn_is_one_card(ws) -> None:
    first = await _action(ws, "create_routine", _routine(ws))
    second = await _action(ws, "create_routine", _routine(ws))
    assert first["structuredContent"]["request_id"] == second["structuredContent"]["request_id"]
    assert await _count(ws, WorkspaceActionRequest) == 1


@pytest.mark.asyncio
async def test_a_proposal_that_could_never_run_is_refused_up_front(ws) -> None:
    bad_cron = await _action(ws, "create_routine", {**_routine(ws), "trigger_config": {"cron": "nope"}})
    assert bad_cron["structuredContent"]["code"] == "INVALID_PARAMS"
    bad_goal = await _action(ws, "create_goal", {"room_id": ws["room"], "spec": {"title": "x"}})
    assert bad_goal["structuredContent"]["code"] == "INVALID_PARAMS"
    assert await _count(ws, WorkspaceActionRequest) == 0


@pytest.mark.asyncio
async def test_a_row_stuck_executing_is_closed_as_unknown(ws) -> None:
    request_id = await _pending_routine(ws)
    async with ws["factory"]() as db:
        await db.execute(update(WorkspaceActionRequest).values(
            status="executing", decided_at=datetime.now(UTC) - timedelta(hours=1)))
        await db.commit()
    seen = await ws["client"].get(f"/api/v1/workspace-actions/{request_id}", headers=ws["owner_headers"])
    assert seen.json()["status"] == "failed" and "unknown" in seen.json()["error"]
