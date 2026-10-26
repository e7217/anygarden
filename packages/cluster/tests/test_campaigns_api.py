"""Campaign REST API and WS events (#930 §3.8, Phase 7).

Runs on the cycles environment (real scheduler, terminal hooks and
evaluation results) and drives people's actions through the HTTP routes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from anygarden.auth.jwt import create_user_token
from anygarden.db.models import (
    Campaign,
    CampaignEvent,
    Machine,
    Participant,
    Room,
    User,
)

from .test_campaigns_cycles import GAP, T0, env  # noqa: F401 — env is a fixture


class FakeManager:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    async def broadcast(self, room_id, frame, **_kwargs) -> None:
        self.frames.append((room_id, frame.model_dump()))

    async def push_to_users(self, user_ids, frame) -> None:
        pass

    def types(self) -> list[str]:
        return [frame["type"] for _, frame in self.frames]


def _app(env):  # noqa: F811
    return env.client._transport.app


def _body(**overrides) -> dict:
    body = {
        "title": "Win the contest",
        "purpose": "Reach 0.9 accuracy on the hidden set.",
        "mode": "target",
        "metric": {"name": "acc", "direction": "maximize", "target": 0.9},
        "evaluator": {"command": "python bench.py", "timeout_sec": 60},
        "budget": {"max_total_tokens": 1_000_000},
    }
    body.update(overrides)
    return body


async def _user(env, email: str, *, role: str | None, admin: bool = False) -> dict:  # noqa: F811
    async with env.factory() as db:
        user = User(email=email, password_hash="x", is_admin=admin)
        db.add(user)
        await db.flush()
        if role is not None:
            db.add(Participant(room_id=env.ids["room"], user_id=user.id, role=role))
        await db.commit()
        token = create_user_token(user.id, email, admin, secret=_app(env).state.config.jwt_secret)
        return {"id": user.id, "auth": {"Authorization": f"Bearer {token}"}}


async def _create(env, auth=None, **overrides) -> dict:  # noqa: F811
    resp = await env.client.post(f"/api/v1/rooms/{env.ids['room']}/campaigns",
                                 json=_body(**overrides), headers=auth or env.auth)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _post(env, campaign_id: str, action: str, auth=None, **body):  # noqa: F811
    return await env.client.post(f"/api/v1/campaigns/{campaign_id}/{action}",
                                 json=body or None, headers=auth or env.auth)


# ═══ Create / read ═════════════════════════════════════════════════


async def test_create_binds_the_room_lead_and_broadcasts(env):  # noqa: F811
    manager = FakeManager()
    _app(env).state.connection_manager = manager
    campaign = await _create(env)
    assert campaign["agent_id"] == env.ids["agent"]
    assert campaign["owner_id"] == env.ids["owner"]
    assert (campaign["status"], campaign["mode"], campaign["trusted_eval"]) == (
        "active", "target", "none")
    assert campaign["metric"] == {"name": "acc", "direction": "maximize", "target": 0.9,
                                  "min_delta": 0.0}
    assert campaign["usable_tokens"] == 950_000
    assert campaign["best"] is None and campaign["open_attempt"] is None
    assert manager.frames[-1][0] == env.ids["room"]
    assert manager.frames[-1][1]["type"] == "campaign.updated"
    assert manager.frames[-1][1]["campaign"]["id"] == campaign["id"]

    listed = await env.client.get(f"/api/v1/rooms/{env.ids['room']}/campaigns", headers=env.auth)
    assert [c["id"] for c in listed.json()] == [campaign["id"]]
    one = await env.client.get(f"/api/v1/campaigns/{campaign['id']}", headers=env.auth)
    assert one.json()["title"] == "Win the contest"


@pytest.mark.req("CMP-01")
async def test_create_validation_uses_the_error_envelope(env):  # noqa: F811
    resp = await env.client.post(
        f"/api/v1/rooms/{env.ids['room']}/campaigns",
        json=_body(metric={"name": "acc", "direction": "maximize"}), headers=env.auth,
    )
    assert resp.status_code == 422
    assert (resp.json()["code"], resp.json()["reason"]) == (
        "CAMPAIGN_INVALID", "TARGET_OR_DEADLINE_REQUIRED")
    unknown = await env.client.post(f"/api/v1/rooms/{env.ids['room']}/campaigns",
                                    json=_body(extra=1), headers=env.auth)
    assert unknown.status_code == 422


async def test_only_the_agents_machine_owner_or_an_admin_sets_the_evaluator(env):  # noqa: F811
    alice = await _user(env, "alice@test.com", role="owner")
    resp = await env.client.post(f"/api/v1/rooms/{env.ids['room']}/campaigns",
                                 json=_body(), headers=alice["auth"])
    assert resp.status_code == 403 and resp.json()["code"] == "CAMPAIGN_EVALUATOR_FORBIDDEN"
    async with env.factory() as db:
        (await db.get(Machine, env.ids["machine"])).owner_user_id = alice["id"]
        await db.commit()
    campaign = await _create(env, alice["auth"])
    assert campaign["owner_id"] == alice["id"]


async def test_members_read_outsiders_do_not_and_only_the_owner_changes(env):  # noqa: F811
    campaign = await _create(env)
    member = await _user(env, "bob@test.com", role="member")
    outsider = await _user(env, "eve@test.com", role=None)
    assert (await env.client.get(f"/api/v1/campaigns/{campaign['id']}",
                                 headers=member["auth"])).status_code == 200
    assert (await env.client.get(f"/api/v1/rooms/{env.ids['room']}/campaigns",
                                 headers=outsider["auth"])).status_code == 403
    assert (await env.client.get(f"/api/v1/campaigns/{campaign['id']}",
                                 headers=outsider["auth"])).status_code == 403
    paused = await _post(env, campaign["id"], "pause", member["auth"])
    assert paused.status_code == 403 and paused.json()["code"] == "CAMPAIGN_FORBIDDEN"
    edit = await env.client.patch(f"/api/v1/campaigns/{campaign['id']}",
                                  json={"title": "x"}, headers=member["auth"])
    assert edit.status_code == 403
    created = await env.client.post(f"/api/v1/rooms/{env.ids['room']}/campaigns",
                                    json=_body(), headers=member["auth"])
    assert created.status_code == 403


async def test_an_admin_may_act_on_someone_elses_campaign(env):  # noqa: F811
    alice = await _user(env, "alice@test.com", role="owner")
    async with env.factory() as db:
        (await db.get(Machine, env.ids["machine"])).owner_user_id = alice["id"]
        await db.commit()
    campaign = await _create(env, alice["auth"])
    resp = await _post(env, campaign["id"], "pause")  # env.auth is an admin
    assert resp.status_code == 200 and resp.json()["status"] == "paused"


# ═══ Status actions ════════════════════════════════════════════════


async def test_pause_resume_stop(env):  # noqa: F811
    campaign = await _create(env)
    cid = campaign["id"]
    assert (await _post(env, cid, "pause")).json()["status"] == "paused"
    assert await env.tick(T0) == []  # a paused campaign opens no cycle
    assert (await _post(env, cid, "resume")).json()["status"] == "active"
    stopped = await _post(env, cid, "stop")
    assert (stopped.json()["status"], stopped.json()["stop_reason"]) == ("stopped", "user")
    again = await _post(env, cid, "stop")
    assert again.status_code == 409 and again.json()["reason"] == "CAMPAIGN_ENDED"
    illegal = await _post(env, cid, "pause")
    assert illegal.status_code == 409
    assert (illegal.json()["code"], illegal.json()["reason"]) == (
        "CAMPAIGN_CONFLICT", "ILLEGAL_TRANSITION")
    assert (await env.campaign(cid)).retro_due is None  # no retrospective after a stop


async def test_stop_closes_the_open_cycle_and_publishes_its_task(env):  # noqa: F811
    manager = FakeManager()
    _app(env).state.connection_manager = manager
    cid = await env.create()
    task_id = (await env.tick(T0))[0]
    resp = await _post(env, cid, "stop")
    assert resp.status_code == 200
    assert resp.json()["open_attempt"] is None
    task_frames = [f for _, f in manager.frames if f["type"] == "task.updated"]
    assert task_frames and task_frames[-1]["task"]["id"] == task_id
    assert task_frames[-1]["task"]["status"] == "failed"
    assert "campaign.attempt_updated" in manager.types()


async def test_resume_from_blocked_resets_the_failure_streak(env):  # noqa: F811
    cid = await env.create()
    now = T0
    for _ in range(3):
        await env.run_cycle(now, status="failed")
        now += GAP + timedelta(minutes=30)
    assert (await env.campaign(cid)).status == "blocked"
    resumed = await _post(env, cid, "resume")
    assert resumed.json()["status"] == "active"
    assert resumed.json()["consecutive_eval_failures"] == 0
    assert resumed.json()["status_detail"] is None


@pytest.mark.req("CMP-06")
async def test_reject_then_confirm(env):  # noqa: F811
    cid = await env.create()
    await env.run_cycle(T0, score=0.95)
    assert (await env.campaign(cid)).status == "awaiting_confirmation"
    rejected = await _post(env, cid, "reject", reason="Overfits the dev set")
    assert rejected.json()["status"] == "active"
    now = T0 + GAP + timedelta(minutes=30)
    await env.run_cycle(now, score=0.97)
    confirmed = await _post(env, cid, "confirm")
    body = confirmed.json()
    assert (body["status"], body["stop_reason"], body["retro_due"]) == (
        "achieved", "achieved", "achieved")
    assert body["final"]["dev_score"] == pytest.approx(0.97)
    assert body["final"]["verified"] is False
    not_now = await _post(env, cid, "confirm")
    assert not_now.status_code == 409


@pytest.mark.req("CMP-07")
async def test_a_trusted_score_at_the_target_achieves(env):  # noqa: F811
    cid = await env.create(trusted_eval="external")
    task_id = await env.run_cycle(T0, score=0.95)
    attempt = await env.attempt(task_id)
    low = await env.client.post(
        f"/api/v1/campaigns/{cid}/attempts/{attempt.id}/trusted-score",
        json={"score": 0.8}, headers=env.auth)
    assert low.json()["status"] == "awaiting_confirmation"
    high = await env.client.post(
        f"/api/v1/campaigns/{cid}/attempts/{attempt.id}/trusted-score",
        json={"score": 0.92}, headers=env.auth)
    body = high.json()
    assert body["status"] == "achieved"
    assert body["final"] == {"attempt_id": attempt.id, "cycle_no": 1, "dev_score": 0.95,
                             "trusted_score": 0.92, "verified": True}
    missing = await env.client.post(
        f"/api/v1/campaigns/{cid}/attempts/nope/trusted-score",
        json={"score": 1.0}, headers=env.auth)
    assert missing.status_code == 404 and missing.json()["code"] == "CAMPAIGN_ATTEMPT_NOT_FOUND"
    types = [e.type for e in await env.events(cid)]
    assert types.count("trusted_score_recorded") == 2


@pytest.mark.req("CMP-09")
async def test_select_final_after_the_deadline(env):  # noqa: F811
    cid = await env.create(mode="optimize", target=None, deadline_at=T0 + timedelta(hours=3))
    first = await env.run_cycle(T0, score=0.5)
    early = await _post(env, cid, "select-final", attempt_id=(await env.attempt(first)).id)
    assert early.status_code == 409 and early.json()["reason"] == "CAMPAIGN_NOT_ENDED"
    second = await env.run_cycle(T0 + GAP + timedelta(minutes=30), score=0.7)
    await env.tick(T0 + timedelta(hours=4))
    campaign = await env.campaign(cid)
    assert campaign.status == "deadline_reached"
    assert campaign.final_attempt_id == (await env.attempt(second)).id  # best dev, unverified
    chosen = await _post(env, cid, "select-final", attempt_id=(await env.attempt(first)).id)
    assert chosen.json()["final"]["cycle_no"] == 1
    assert "final_selected" in [e.type for e in await env.events(cid)]


@pytest.mark.req("CMP-09")
async def test_extend_reopens_a_campaign_that_reached_its_deadline(env):  # noqa: F811
    cid = await env.create(mode="optimize", target=None, deadline_at=T0 + timedelta(hours=2))
    await env.run_cycle(T0, score=0.2)
    await env.tick(T0 + timedelta(hours=3))
    assert (await env.campaign(cid)).status == "deadline_reached"
    same = await _post(env, cid, "extend", deadline_at=(T0 + timedelta(hours=1)).isoformat())
    assert same.status_code == 422 and same.json()["reason"] == "INVALID_DEADLINE"
    later = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    extended = await _post(env, cid, "extend", deadline_at=later)
    assert extended.status_code == 200, extended.text
    assert extended.json()["status"] == "active" and extended.json()["retro_due"] is None
    opened = await env.tick(T0 + timedelta(hours=3, minutes=1))
    assert len(opened) == 1


async def test_extend_is_refused_for_an_archived_room(env):  # noqa: F811
    cid = await env.create(mode="optimize", target=None, deadline_at=T0 + timedelta(hours=2))
    await env.run_cycle(T0, score=0.2)
    await env.tick(T0 + timedelta(hours=3))
    async with env.factory() as db:
        (await db.get(Room, env.ids["room"])).archived_at = datetime.now(UTC)
        await db.commit()
    resp = await _post(env, cid, "extend",
                       deadline_at=(datetime.now(UTC) + timedelta(days=2)).isoformat())
    assert resp.status_code == 409 and resp.json()["reason"] == "ROOM_ARCHIVED"
    assert (await env.campaign(cid)).status == "deadline_reached"


# ═══ Edit ══════════════════════════════════════════════════════════


async def test_edit_records_the_changes_and_applies_from_the_next_cycle(env):  # noqa: F811
    cid = await env.create(pin_paths=["bench.py"])
    await env.run_cycle(T0, score=0.5)
    async with env.factory() as db:
        (await db.get(Campaign, cid)).evaluator_pins = {"bench.py": "abc"}
        await db.commit()
    resp = await env.client.patch(f"/api/v1/campaigns/{cid}", headers=env.auth, json={
        "purpose": "Reach 0.95.", "metric": {"target": 0.95},
        "budget": {"max_total_tokens": 2_000_000},
        "evaluator": {"command": "python bench.py --strict", "pin_paths": ["bench.py"]},
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["metric"]["target"] == 0.95 and body["budget"]["max_total_tokens"] == 2_000_000
    campaign = await env.campaign(cid)
    assert campaign.evaluator_pins is None  # pinned afresh at the next evaluation
    async with env.factory() as db:
        edited = await db.scalar(select(CampaignEvent).where(
            CampaignEvent.campaign_id == cid, CampaignEvent.type == "edited"))
    changes = edited.details["changes"]
    assert changes["metric.target"] == {"from": 0.9, "to": 0.95}
    assert {"purpose", "budget.max_total_tokens", "evaluator"} <= set(changes)
    assert edited.details["open_cycle_no"] is None
    opened = await env.tick(T0 + GAP + timedelta(minutes=30))
    async with env.factory() as db:
        from anygarden.db.models import Task

        assert "Reach 0.95." in (await db.get(Task, opened[0])).spec


async def test_edit_rules(env):  # noqa: F811
    cid = await env.create(mode="optimize", target=None, deadline_at=T0 + timedelta(days=1))
    clear = await env.client.patch(f"/api/v1/campaigns/{cid}", headers=env.auth,
                                   json={"deadline_at": None})
    assert clear.status_code == 422 and clear.json()["reason"] == "TARGET_OR_DEADLINE_REQUIRED"
    mode = await env.client.patch(f"/api/v1/campaigns/{cid}", headers=env.auth,
                                  json={"mode": "target"})
    assert mode.status_code == 422
    noop = await env.client.patch(f"/api/v1/campaigns/{cid}", headers=env.auth, json={})
    assert noop.status_code == 200
    assert "edited" not in [e.type for e in await env.events(cid)]
    await _post(env, cid, "stop")
    ended = await env.client.patch(f"/api/v1/campaigns/{cid}", headers=env.auth,
                                   json={"title": "new"})
    assert ended.status_code == 409


# ═══ History ═══════════════════════════════════════════════════════


async def test_attempts_and_events_are_paged_newest_first(env):  # noqa: F811
    cid = await env.create()
    now = T0
    for score in (0.1, 0.2, 0.3):
        await env.run_cycle(now, score=score)
        now += GAP + timedelta(minutes=30)
    page = await env.client.get(f"/api/v1/campaigns/{cid}/attempts?limit=2", headers=env.auth)
    body = page.json()
    assert body["total"] == 3 and body["next_offset"] == 2
    assert [a["cycle_no"] for a in body["items"]] == [3, 2]
    assert body["items"][0]["dev_score"] == pytest.approx(0.3)
    assert body["items"][0]["outcome"] == "improved"
    rest = await env.client.get(f"/api/v1/campaigns/{cid}/attempts?offset=2", headers=env.auth)
    assert [a["cycle_no"] for a in rest.json()["items"]] == [1]
    events = await env.client.get(f"/api/v1/campaigns/{cid}/events?limit=5", headers=env.auth)
    assert len(events.json()["items"]) == 5 and events.json()["total"] > 5
    missing = await env.client.get("/api/v1/campaigns/nope", headers=env.auth)
    assert missing.status_code == 404 and missing.json()["code"] == "CAMPAIGN_NOT_FOUND"
