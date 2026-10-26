"""Campaign retrospectives (#930 §3.6, Phase 6, CMP-14).

When the loop ends on its own terms (achieved, deadline, budget) and every
``RETRO_EVERY_CYCLES`` cycles a retrospective cycle runs. Its
``apply_campaign_learning`` changes the agent's AGENTS.md / skills without
approval and leaves a ``source="campaign"`` revision, an event and a room
message behind.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from anygarden.campaigns import lifecycle
from anygarden.campaigns.retrospective import (
    LEARNINGS_HEADING,
    replace_learnings_section,
)
from anygarden.campaigns.scheduler import CampaignScheduler
from anygarden.campaigns.service import (
    apply_transition,
    create_campaign,
    extend_campaign,
)
from anygarden.db.models import (
    Agent,
    AgentInstructionRevision,
    Campaign,
    CampaignAttempt,
    CampaignEvent,
    Machine,
    Message,
    ProjectExecution,
    Room,
    Task,
)

from . import test_project_execution_flow as _flow
from .test_campaigns_cycles import T0, env  # noqa: F401 — env is a fixture
from .test_project_execution_flow import Harness

flow_harness = _flow.harness


# ═══ The AGENTS.md section ═════════════════════════════════════════


def test_the_section_is_appended_when_absent():
    text = replace_learnings_section("# Me\n\nI help.\n", "- Small steps win")
    assert text == f"# Me\n\nI help.\n\n{LEARNINGS_HEADING}\n- Small steps win"


def test_only_the_section_body_is_replaced():
    before = (
        "# Me\n\nIntro\n\n"
        f"{LEARNINGS_HEADING}\n- old lesson\n### Detail\nold\n\n"
        "## Tools\nUse ruff.\n"
    )
    after = replace_learnings_section(before, "- new lesson")
    assert after == f"# Me\n\nIntro\n\n{LEARNINGS_HEADING}\n- new lesson\n\n## Tools\nUse ruff.\n"


def test_an_empty_file_gets_just_the_section():
    assert replace_learnings_section("", "- a") == f"{LEARNINGS_HEADING}\n- a"


# ═══ Triggers (scheduler) ══════════════════════════════════════════


async def _retro(env, cid: str) -> CampaignAttempt | None:  # noqa: F811
    async with env.factory() as db:
        return await db.scalar(select(CampaignAttempt).where(
            CampaignAttempt.campaign_id == cid, CampaignAttempt.kind == "retrospective"))


async def test_deadline_end_runs_one_retrospective(env):  # noqa: F811
    cid = await env.create(mode="optimize", target=None, deadline_at=T0 + timedelta(hours=2))
    await env.run_cycle(T0, score=0.2)
    assert await env.tick(T0 + timedelta(hours=3)) == []  # → deadline_reached
    campaign = await env.campaign(cid)
    assert (campaign.status, campaign.retro_due) == ("deadline_reached", "deadline_reached")
    # The final result defaults to the best dev score (unverified).
    assert campaign.final_attempt_id == campaign.best_attempt_id is not None

    task_id = await env.run_retrospective(T0 + timedelta(hours=3, minutes=1))
    retro = await _retro(env, cid)
    assert retro.task_id == task_id and retro.cycle_no == 2
    assert (retro.eval_status, retro.outcome) == ("skipped", None)
    assert retro.closed_at is not None and retro.eval_job_id is None
    async with env.factory() as db:
        task = await db.get(Task, task_id)
        assert "## Changes that helped" in task.spec and "apply_campaign_learning" in task.spec
    campaign = await env.campaign(cid)
    assert campaign.retro_due is None and campaign.cycles_run == 1
    assert campaign.status == "deadline_reached"
    later = await env.scheduler(T0 + timedelta(days=1))._tick()
    assert [o.action for o in later] == []


@pytest.mark.req("CMP-10")
async def test_budget_end_runs_a_retrospective(env):  # noqa: F811
    cid = await env.create(max_total_tokens=100_000)
    now = T0
    for _ in range(3):
        await env.run_cycle(now, score=0.1, tokens=40_000)
        now += timedelta(hours=1)
    assert (await env.campaign(cid)).status == "budget_exhausted"
    await env.run_retrospective(now)
    assert (await _retro(env, cid)).closed_at is not None


async def test_confirmation_runs_a_retrospective(env):  # noqa: F811
    cid = await env.create()
    await env.run_cycle(T0, score=0.95)
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        assert await apply_transition(db, campaign, "confirm", actor="user:x")
        await db.commit()
    campaign = await env.campaign(cid)
    assert (campaign.status, campaign.retro_due) == ("achieved", "achieved")
    assert campaign.final_attempt_id == campaign.best_attempt_id
    await env.run_retrospective(T0 + timedelta(hours=1))


async def test_a_stopped_campaign_has_no_retrospective(env):  # noqa: F811
    cid = await env.create()
    await env.run_cycle(T0, score=0.5)
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        await lifecycle.stop_campaign(db, campaign, actor="user:x", stop_reason="user")
        await db.commit()
    assert (await env.campaign(cid)).retro_due is None
    assert await env.scheduler(T0 + timedelta(hours=2))._tick() == []


async def test_extending_an_ended_campaign_drops_its_retrospective(env):  # noqa: F811
    cid = await env.create(mode="optimize", target=None, deadline_at=T0 + timedelta(hours=2))
    await env.run_cycle(T0, score=0.2)
    await env.tick(T0 + timedelta(hours=3))
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        assert await extend_campaign(db, campaign, actor="user:x",
                                     deadline_at=datetime.now(UTC) + timedelta(days=3))
        await db.commit()
    campaign = await env.campaign(cid)
    assert (campaign.status, campaign.retro_due, campaign.final_attempt_id) == ("active", None, None)


async def test_a_retrospective_whose_agent_left_is_skipped(env):  # noqa: F811
    from anygarden.db.models import Participant

    cid = await env.create(mode="optimize", target=None, deadline_at=T0 + timedelta(hours=2))
    await env.run_cycle(T0, score=0.2)
    await env.tick(T0 + timedelta(hours=3))
    async with env.factory() as db:
        await db.delete(await db.get(Participant, env.ids["agent_p"]))
        await db.commit()
    outcomes = await env.scheduler(T0 + timedelta(hours=4))._tick()
    assert [o.action for o in outcomes] == ["retrospective_skipped"]
    assert (await env.campaign(cid)).retro_due is None
    assert "retrospective_skipped" in [e.type for e in await env.events(cid)]


# ═══ apply_campaign_learning (tools) ═══════════════════════════════


async def _open_retrospective(h: Harness, *, status: str = "deadline_reached",
                              tokens_used: int = 95_000) -> dict:
    async with h.sessions() as db:
        room = await db.get(Room, h.ids["lead_room"])
        campaign = await create_campaign(
            db, owner_id=h.ids["owner"], room=room, title="Raise accuracy",
            purpose="Win the contest.", mode="optimize",
            metric={"name": "acc", "direction": "maximize"},
            evaluator={"command": "python bench.py"},
            budget={"max_total_tokens": 100_000},
            deadline_at=datetime.now(UTC) + timedelta(hours=1),
        )
        campaign.status, campaign.retro_due, campaign.tokens_used = status, status, tokens_used
        lead = await db.get(Agent, h.ids["lead"])
        lead.agents_md = "# garden-pm\n\nI lead the garden project."
        await db.commit()
        campaign_id = campaign.id
    outcome = await CampaignScheduler(h.sessions).advance(campaign_id)
    assert outcome.action == "retrospective", outcome
    turn = await h.deliver("lead")
    begun = await h.tool(turn, "begin_project_execution", objective="Retrospective")
    return {"campaign_id": campaign_id, "task_id": outcome.task_id, "turn": turn,
            "execution_id": begun["execution_id"]}


async def _call(h: Harness, retro: dict, arguments: dict) -> dict:
    # Posted directly: the harness helpers take ``name`` as the tool name.
    turn = retro["turn"]
    resp = await h.client.post(
        "/mcp/rpc",
        headers={"Authorization": f"Bearer {h.ids['lead_token']}", **turn.headers},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
              "params": {"name": "apply_campaign_learning", "arguments": arguments}},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["result"]


async def _learn(h: Harness, retro: dict, **arguments) -> dict:
    result = await _call(h, retro, arguments)
    assert not result.get("isError"), result
    return result["structuredContent"]


async def _learn_error(h: Harness, retro: dict, **arguments) -> dict:
    result = await _call(h, retro, arguments)
    assert result.get("isError"), result
    return result["structuredContent"]


@pytest.mark.req("CMP-10")
async def test_a_retrospective_runs_on_the_reserve_after_the_deadline(flow_harness):
    h = flow_harness
    retro = await _open_retrospective(h, tokens_used=95_000)
    async with h.sessions() as db:
        execution = await db.get(ProjectExecution, retro["execution_id"])
        # Cycles had 95k of 100k; the retrospective gets the 5k reserve.
        assert execution.limits["max_total_tokens"] == 5_000
        assert execution.deadline_at <= datetime.now(UTC) + timedelta(hours=2)


@pytest.mark.req("CMP-14")
async def test_agents_md_learning_applies_without_approval_and_is_recorded(flow_harness):
    h = flow_harness
    retro = await _open_retrospective(h)
    result = await _learn(h, retro, target="agents_md", text="- Keep changes small.",
                          rationale="Small changes improved 3 of 4 cycles")
    assert result["changed"] is True and result["changes_left"] == 2
    async with h.sessions() as db:
        lead = await db.get(Agent, h.ids["lead"])
        assert lead.agents_md == (
            f"# garden-pm\n\nI lead the garden project.\n\n{LEARNINGS_HEADING}\n- Keep changes small."
        )
        revision = await db.scalar(select(AgentInstructionRevision).where(
            AgentInstructionRevision.agent_id == lead.id,
            AgentInstructionRevision.revision == result["revision"]))
        assert revision.source == "campaign" and revision.by_user_id is None
        assert revision.context["campaign_id"] == retro["campaign_id"]
        assert revision.context["rationale"] == "Small changes improved 3 of 4 cycles"
        attempt = await db.scalar(select(CampaignAttempt).where(
            CampaignAttempt.task_id == retro["task_id"]))
        assert revision.context["attempt_id"] == attempt.id
        event = await db.scalar(select(CampaignEvent).where(
            CampaignEvent.campaign_id == retro["campaign_id"],
            CampaignEvent.type == "instructions_changed"))
        assert event.details["revision"] == result["revision"]
        message = await db.scalar(select(Message).where(
            Message.room_id == h.ids["lead_room"],
            Message.content.contains("retrospective updated AGENTS.md")))
        assert message is not None and message.participant_id is None
        assert "Small changes improved" in message.content


@pytest.mark.req("CMP-14")
async def test_text_outside_the_section_is_refused(flow_harness):
    h = flow_harness
    retro = await _open_retrospective(h)
    error = await _learn_error(h, retro, target="agents_md",
                               text="- ok\n## Tools\nrewrite everything", rationale="r")
    assert error["code"] == "CAMPAIGN_LEARNING_OUTSIDE_SECTION"
    async with h.sessions() as db:
        assert (await db.get(Agent, h.ids["lead"])).agents_md == (
            "# garden-pm\n\nI lead the garden project.")


@pytest.mark.req("CMP-14")
async def test_at_most_three_changes_and_sixteen_kb(flow_harness):
    h = flow_harness
    retro = await _open_retrospective(h)
    big = await _learn_error(h, retro, target="agents_md", text="x" * (16 * 1024 + 1),
                             rationale="r")
    assert big["code"] == "CAMPAIGN_LEARNING_TOO_LARGE"
    for n in range(3):
        await _learn(h, retro, target="agents_md", text=f"- lesson {n}", rationale="r")
    fourth = await _learn_error(h, retro, target="agents_md", text="- more", rationale="r")
    assert fourth["code"] == "CAMPAIGN_LEARNING_LIMIT"


async def test_skills_are_created_or_changed_only_when_the_campaign_owns_them(flow_harness):
    from anygarden.db.models import AgentFile

    h = flow_harness
    async with h.sessions() as db:
        db.add(AgentFile(agent_id=h.ids["lead"], path="skills/deploy/SKILL.md", content="mine"))
        await db.commit()
    retro = await _open_retrospective(h)
    created = await _learn(h, retro, target="skill", name="tuning", text="# Tuning\nSweep lr.",
                           rationale="worked")
    assert (created["path"], created["created"]) == ("skills/tuning/SKILL.md", True)
    updated = await _learn(h, retro, target="skill", name="tuning", text="# Tuning\nSweep lr, wd.",
                           rationale="refined")
    assert updated["created"] is False
    foreign = await _learn_error(h, retro, target="skill", name="deploy", text="new", rationale="r")
    assert foreign["code"] == "CAMPAIGN_SKILL_NOT_OWNED"
    async with h.sessions() as db:
        skill = await db.scalar(select(AgentFile).where(
            AgentFile.agent_id == h.ids["lead"], AgentFile.path == "skills/tuning/SKILL.md"))
        assert skill.content == "# Tuning\nSweep lr, wd."
        assert (await db.scalar(select(AgentFile.content).where(
            AgentFile.path == "skills/deploy/SKILL.md"))) == "mine"


@pytest.mark.req("CMP-14")
async def test_a_learning_is_undone_with_the_revision_restore(flow_harness):
    from types import SimpleNamespace

    from anygarden.auth.jwt import create_user_token
    from anygarden.db.models import User
    from anygarden.instructions.service import current_snapshot, record_snapshot

    h = flow_harness
    retro = await _open_retrospective(h)
    async with h.sessions() as db:
        lead = await db.get(Agent, h.ids["lead"])
        # A person's earlier version to go back to.
        before = await record_snapshot(db, lead, await current_snapshot(db, lead), source="user")
        await db.commit()
    result = await _learn(h, retro, target="agents_md", text="- a bad lesson", rationale="oops")
    assert result["revision"] == before + 1
    async with h.sessions() as db:
        admin = User(email="admin@anygarden.io", password_hash="x", is_admin=True)
        db.add(admin)
        await db.commit()
        token = create_user_token(admin.id, admin.email, True, secret=h.app.state.config.jwt_secret)

    async def bump_generation(agent_id):
        return None

    h.app.state.agent_lifecycle = SimpleNamespace(bump_generation=bump_generation)
    h.app.state.machine_bus = None
    resp = await h.client.post(
        f"/api/v1/agents/{h.ids['lead']}/instructions/revisions/{before}/restore",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    async with h.sessions() as db:
        lead = await db.get(Agent, h.ids["lead"])
        assert lead.agents_md == "# garden-pm\n\nI lead the garden project."
        latest = await db.scalar(select(AgentInstructionRevision).where(
            AgentInstructionRevision.agent_id == lead.id,
            AgentInstructionRevision.revision == before + 2))
        assert (latest.source, latest.restored_from) == ("user", before)


async def test_an_offline_disk_machine_records_the_failure_and_the_retro_finishes(flow_harness):
    from anygarden.instructions.service import CAPABILITY
    from anygarden.project_executions.service import _event

    h = flow_harness
    retro = await _open_retrospective(h)
    async with h.sessions() as db:
        machine = Machine(name="box", hostname="box", owner_user_id=h.ids["owner"],
                          control_capabilities=[CAPABILITY])
        db.add(machine)
        await db.flush()
        (await db.get(Agent, h.ids["lead"])).placed_on_machine_id = machine.id
        await db.commit()
    error = await _learn_error(h, retro, target="agents_md", text="- lesson", rationale="r")
    assert error["code"] == "CAMPAIGN_LEARNING_FAILED"
    assert "offline" in error["detail"]
    async with h.sessions() as db:
        failed = await db.scalar(select(CampaignEvent).where(
            CampaignEvent.campaign_id == retro["campaign_id"],
            CampaignEvent.type == "instructions_change_failed"))
        assert failed is not None and "offline" in failed.details["error"]
        # The retrospective still ends normally with its run.
        execution = await db.get(ProjectExecution, retro["execution_id"])
        task = await db.get(Task, execution.root_task_id)
        execution.status, task.status = "completed", "done"
        await db.flush()
        await _event(db, execution, f"execution:{execution.id}:completed",
                     "execution_completed", task_id=task.id)
        await db.commit()
        attempt = await db.scalar(select(CampaignAttempt).where(
            CampaignAttempt.task_id == retro["task_id"]))
        assert attempt.closed_at is not None and attempt.outcome is None
        assert attempt.eval_status == "skipped" and attempt.eval_job_id is None


async def test_record_is_refused_in_a_retrospective(flow_harness):
    h = flow_harness
    retro = await _open_retrospective(h)
    result = await h.tool_error_or_ok(retro["turn"], "record_campaign_attempt",
                                      outcome="attempted", hypothesis="x", change_summary="y")
    assert result["structuredContent"]["code"] == "CAMPAIGN_RETROSPECTIVE_CYCLE"


async def test_a_retrospective_completes_without_a_child_task(flow_harness):
    h = flow_harness
    retro = await _open_retrospective(h)
    await h.tool(retro["turn"], "seal_project_plan", execution_id=retro["execution_id"],
                 required_task_ids=[])
    await h.tool(retro["turn"], "complete_project_execution",
                 execution_id=retro["execution_id"], summary="Kept two lessons.")
    async with h.sessions() as db:
        execution = await db.get(ProjectExecution, retro["execution_id"])
        assert execution.status == "completed"
        attempt = await db.scalar(select(CampaignAttempt).where(
            CampaignAttempt.task_id == retro["task_id"]))
        assert attempt.closed_at is not None
