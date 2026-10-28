"""A goal reports its result in the room's main chat (#964).

When a goal needs a person, ends on its own terms, or every
``RETRO_EVERY_CYCLES`` cycles, the server posts one top-level system
message that mentions the goal's lead and opens the lead's turn: the
reply is the report people read. The message keeps the goal status card
(``goal_status``) and carries the cycle's deliverables as ``artifact``
references.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import select

from anygarden.campaigns.policy import RETRO_EVERY_CYCLES
from anygarden.db.models import (
    Agent,
    AgentTurn,
    Campaign,
    CampaignAttempt,
    Message,
    Participant,
    Room,
    RoomArtifact,
)

from .test_campaigns_cycles import GAP, T0, env  # noqa: F401 — env is a fixture


async def _artifact(env, name: str, *, at, room_id=None, agent_id=None) -> str:  # noqa: F811
    async with env.factory() as db:
        room_id = room_id or env.ids["room"]
        row = RoomArtifact(
            room_id=room_id, produced_by_agent_id=agent_id or env.ids["agent"],
            filename=name, storage_path=f"{room_id}/{name}",
            sha256=hashlib.sha256(f"{room_id}:{name}".encode()).hexdigest(),
            size_bytes=12, mime="text/markdown", created_at=at,
        )
        db.add(row)
        await db.commit()
        return row.id


async def _room_messages(env) -> list[Message]:  # noqa: F811
    async with env.factory() as db:
        return list(await db.scalars(select(Message).where(
            Message.room_id == env.ids["room"]).order_by(Message.seq)))


async def _goal_messages(env) -> list[Message]:  # noqa: F811
    return [m for m in await _room_messages(env) if (m.extra_metadata or {}).get("goal_status")]


async def _turns_for(env, message_id: str) -> list[AgentTurn]:  # noqa: F811
    async with env.factory() as db:
        return list(await db.scalars(select(AgentTurn).where(
            AgentTurn.trigger_message_id == message_id)))


# ── cycle_artifacts ─────────────────────────────────────────────────


@pytest.mark.req("CMP-18")
async def test_cycle_artifacts_are_the_leads_files_in_the_goal_room_during_the_cycle(env):  # noqa: F811
    from anygarden.campaigns.report import artifact_reference, cycle_artifacts

    cid = await env.create()
    first = await env.run_cycle(T0, score=0.5)
    second = await env.run_cycle(T0 + GAP + timedelta(minutes=30), score=0.6)
    a1 = await env.attempt(first)
    a2 = await env.attempt(second)
    async with env.factory() as db:
        other_agent = Agent(name="helper", engine="codex-cli")
        other_room = Room(name="elsewhere", project_id=env.ids["project"])
        db.add_all([other_agent, other_room])
        await db.commit()
        other_agent_id, other_room_id = other_agent.id, other_room.id

    inside = await _artifact(env, "ideas.md", at=a1.opened_at + timedelta(minutes=5))
    # A file the turn shipped right after the run finished still belongs to it.
    late = await _artifact(env, "table.csv", at=a1.closed_at + timedelta(minutes=1))
    await _artifact(env, "before.md", at=a1.opened_at - timedelta(minutes=1))
    await _artifact(env, "helper.md", at=a1.opened_at + timedelta(minutes=5),
                    agent_id=other_agent_id)
    await _artifact(env, "other-room.md", at=a1.opened_at + timedelta(minutes=5),
                    room_id=other_room_id)
    nxt = await _artifact(env, "next.md", at=a2.opened_at + timedelta(minutes=1))

    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        rows = await cycle_artifacts(db, campaign, await db.get(CampaignAttempt, a1.id))
        assert [r.id for r in rows] == [inside, late]
        rows2 = await cycle_artifacts(db, campaign, await db.get(CampaignAttempt, a2.id))
        assert [r.id for r in rows2] == [nxt]
        ref = artifact_reference(rows[0])
    assert ref == {
        "type": "artifact", "id": inside, "room_id": env.ids["room"], "name": "ideas.md",
        "mime": "text/markdown", "size_bytes": 12, "sha256": rows[0].sha256,
    }


@pytest.mark.req("CMP-18")
async def test_report_artifacts_join_best_and_latest_cycles_without_duplicates(env):  # noqa: F811
    from anygarden.campaigns.report import MAX_REPORT_ARTIFACTS, report_artifacts

    cid = await env.create()
    best = await env.run_cycle(T0, score=0.8)
    latest = await env.run_cycle(T0 + GAP + timedelta(minutes=30), score=0.5)
    best_a, latest_a = await env.attempt(best), await env.attempt(latest)
    for i in range(8):
        await _artifact(env, f"best-{i}.md", at=best_a.opened_at + timedelta(seconds=i + 1))
    for i in range(8):
        await _artifact(env, f"latest-{i}.md", at=latest_a.opened_at + timedelta(seconds=i + 1))
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        assert campaign.best_attempt_id == best_a.id
        rows = await report_artifacts(db, campaign)
    names = [r.filename for r in rows]
    assert len(names) == MAX_REPORT_ARTIFACTS == 10
    assert len(set(names)) == len(names)
    assert names[:5] == [f"best-{i}.md" for i in range(5)]
    assert names[5:] == [f"latest-{i}.md" for i in range(5)]


# ── The report request ─────────────────────────────────────────────


@pytest.mark.req("CMP-17")
async def test_reaching_the_target_asks_the_lead_to_report_in_the_main_chat(env):  # noqa: F811
    cid = await env.create(target=0.9)
    await env.run_cycle(T0, score=0.95)
    [notice] = await _goal_messages(env)
    assert (await env.campaign(cid)).status == "awaiting_confirmation"

    meta = notice.extra_metadata
    assert notice.participant_id is None
    assert notice.root_message_id is None  # top-level: the reply lands in the main chat
    assert meta["goal_status"]["status"] == "awaiting_confirmation"
    assert meta["goal_status"]["campaign_id"] == cid
    assert meta["mentions"] == [{"type": "user", "id": env.ids["agent_p"]}]
    assert meta["system_origin"] == "goal_report"
    assert meta["goal_report"]["reason"] == "confirmation"
    assert meta["goal_report"]["lead_participant_id"] == env.ids["agent_p"]
    assert notice.content.startswith(f"<@user:{env.ids['agent_p']}>")
    brief = notice.content
    for needle in ("0.95", "0.9", "awaiting_confirmation", "content", "outbox",
                   "language", "Do not mention"):
        assert needle in brief, needle

    [turn] = await _turns_for(env, notice.id)
    assert turn.target_participant_id == env.ids["agent_p"]
    assert turn.agent_id == env.ids["agent"]
    assert turn.thread_root_id is None
    assert turn.idempotency_key.startswith(f"goal-report:{cid}:confirmation:")

    # A second request for the same state is a no-op: one message, one turn.
    from anygarden.campaigns.report import request_report

    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        assert await request_report(db, campaign, reason="confirmation") is None
        await db.commit()
    assert len(await _goal_messages(env)) == 1


@pytest.mark.req("CMP-18")
async def test_the_report_request_carries_the_cycle_files(env):  # noqa: F811
    from anygarden.campaigns.report import request_report

    cid = await env.create(mode="optimize", target=None, deadline_at=T0 + timedelta(days=3))
    task = await env.run_cycle(T0, score=0.5)
    attempt = await env.attempt(task)
    art = await _artifact(env, "ideas.md", at=attempt.opened_at + timedelta(minutes=2))
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        message = await request_report(db, campaign, reason="progress")
        await db.commit()
    assert message is not None
    refs = message.extra_metadata["references"]
    assert [(r["type"], r["id"], r["name"]) for r in refs] == [("artifact", art, "ideas.md")]
    assert "ideas.md" in message.content
    assert message.extra_metadata["goal_status"]["status"] == "active"


@pytest.mark.req("CMP-17")
async def test_a_lead_outside_the_room_gets_the_message_but_no_turn(env):  # noqa: F811
    from anygarden.campaigns.report import request_report

    cid = await env.create()
    async with env.factory() as db:
        pid = await db.get(Participant, env.ids["agent_p"])
        await db.delete(pid)
        await db.commit()
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        message = await request_report(db, campaign, reason="needs_person")
        await db.commit()
    assert message is not None
    assert "mentions" not in message.extra_metadata
    assert "<@" not in message.content
    assert await _turns_for(env, message.id) == []


@pytest.mark.req("CMP-16")
@pytest.mark.req("CMP-17")
async def test_which_transitions_report_and_which_only_notify(env):  # noqa: F811
    from anygarden.campaigns.actions import confirm_campaign
    from anygarden.campaigns.service import apply_transition

    cid = await env.create(target=0.9)
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        await apply_transition(db, campaign, "block", actor="system",
                               status_detail="Fix the evaluator.")
        await db.commit()
    [blocked] = await _goal_messages(env)
    assert blocked.extra_metadata["goal_report"]["reason"] == "needs_person"
    assert "Fix the evaluator." in blocked.content
    assert len(await _turns_for(env, blocked.id)) == 1

    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        await apply_transition(db, campaign, "resume", actor="user:x")
        await db.commit()
    await env.run_cycle(T0, score=0.95)
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        # The person confirms what the lead already reported: no second report.
        await confirm_campaign(db, campaign, actor="user:x")
        await db.commit()
    notices = await _goal_messages(env)
    assert [n.extra_metadata["goal_status"]["status"] for n in notices] == [
        "blocked", "awaiting_confirmation", "achieved"]
    achieved = notices[-1]
    assert "goal_report" not in achieved.extra_metadata
    assert "<@" not in achieved.content
    assert await _turns_for(env, achieved.id) == []


@pytest.mark.req("CMP-16")
async def test_a_person_stopping_the_goal_is_not_reported(env):  # noqa: F811
    from anygarden.campaigns import lifecycle

    cid = await env.create()
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        await lifecycle.stop_campaign(db, campaign, actor="user:x", stop_reason="user")
        await db.commit()
    [stopped] = await _goal_messages(env)
    assert stopped.extra_metadata["goal_status"]["status"] == "stopped"
    # #969 — the card translates the code; the body never shows it.
    assert stopped.extra_metadata["goal_status"]["stop_reason"] == "user"
    assert "user" not in stopped.content.split("”")[-1]
    assert "goal_report" not in stopped.extra_metadata
    assert await _turns_for(env, stopped.id) == []


@pytest.mark.req("CMP-17")
async def test_a_spent_budget_ends_with_a_report(env):  # noqa: F811
    cid = await env.create(max_total_tokens=100_000)
    now = T0
    for _ in range(3):
        await env.run_cycle(now, score=0.1, tokens=40_000)
        now += timedelta(hours=1)
    assert (await env.campaign(cid)).status == "budget_exhausted"
    ended = (await _goal_messages(env))[-1]
    assert ended.extra_metadata["goal_report"]["reason"] == "ended"
    assert len(await _turns_for(env, ended.id)) == 1


@pytest.mark.req("CMP-17")
async def test_every_tenth_cycle_reports_progress_and_the_loop_goes_on(env):  # noqa: F811
    cid = await env.create(mode="optimize", target=None, deadline_at=T0 + timedelta(days=30))
    now = T0
    for i in range(RETRO_EVERY_CYCLES):
        await env.run_cycle(now, score=0.1 + i * 0.01)
        now += timedelta(hours=1)
    reports = [m for m in await _goal_messages(env)
               if (m.extra_metadata.get("goal_report") or {}).get("reason") == "progress"]
    assert len(reports) == 1
    [progress] = reports
    assert progress.extra_metadata["goal_status"]["status"] == "active"
    assert f"{RETRO_EVERY_CYCLES} cycles" in progress.content
    assert len(await _turns_for(env, progress.id)) == 1
    campaign = await env.campaign(cid)
    assert campaign.status == "active"
    # The loop keeps going: the retrospective, then the next cycle.
    await env.run_retrospective(now)
    await env.run_cycle(now + timedelta(hours=1), score=0.5)
    assert (await env.campaign(cid)).cycles_run == RETRO_EVERY_CYCLES + 1
