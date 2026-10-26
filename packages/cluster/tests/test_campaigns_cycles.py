"""Campaign cycles end to end on real services (#930 Phase 3–4).

Ports the storage spike's 14 scenarios (``.tmp/spike-930``) to the real
``CampaignScheduler``, execution terminal hook (``_event``), evaluation
dispatch / result handling, sweeper, ``retire_room`` and the Task API —
plus the judging rules of §3.3 (CMP-04, 05, 08–12).

Executions are stand-ins (ORM rows like the ones ``begin_execution``
leaves) but end through the real terminal paths: the ``_event`` hook for
completion and cancellation, ``expire_execution_deadlines`` for a run that
never reports, and ``cancel_cycle_execution`` for a stop. Measured tokens
come from a patched ``execution_usage_summary``; the machine is a fake bus
that acknowledges evaluation requests. Runs on a file-backed SQLite DB.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.campaigns import evaluation, lifecycle
from anygarden.campaigns.evaluation import (
    handle_eval_result_frame,
    request_pending_evals,
)
from anygarden.campaigns.scheduler import CampaignScheduler
from anygarden.campaigns.service import attach_cycle_execution, create_campaign
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    Base,
    Campaign,
    CampaignAttempt,
    CampaignEvent,
    ExecutionMutation,
    Machine,
    Message,
    Participant,
    Project,
    ProjectExecution,
    Room,
    Task,
    User,
)
from anygarden.goals.sweeper import sweep_stuck_tasks
from anygarden.rooms.retire import retire_room
from anygarden.scheduler.machine_bus import MachineRequestError

T0 = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=1)
GAP = timedelta(seconds=301)


class FakeBus:
    """Machine bus stand-in: records evaluation requests and acknowledges."""

    def __init__(self) -> None:
        self.online: set[str] = set()
        self.frames: list[dict] = []
        self.ack: dict = {"accepted": True, "reason": None}

    def is_connected(self, machine_id: str) -> bool:
        return machine_id in self.online

    async def request_campaign_eval(self, machine_id: str, frame: dict, *, timeout: float = 5):
        if machine_id not in self.online:
            raise MachineRequestError("offline")
        self.frames.append(frame)
        return {"job_id": frame["job_id"], **self.ack}


class Env:
    def __init__(self, factory, client, ids, bus, tokens) -> None:
        self.factory = factory
        self.client = client
        self.ids = ids
        self.bus = bus
        self.tokens = tokens  # execution_id -> measured total tokens
        self.auth = {"Authorization": f"Bearer {ids['token']}"}

    def scheduler(self, now: datetime) -> CampaignScheduler:
        return CampaignScheduler(self.factory, machine_bus=self.bus, clock=lambda: now)

    async def create(self, *, mode="target", target=0.9, deadline_at=None,
                     max_total_tokens=1_000_000, min_delta=0.0, trusted_eval="none",
                     pin_paths=None, now=T0) -> str:
        async with self.factory() as db:
            room = await db.get(Room, self.ids["room"])
            campaign = await create_campaign(
                db, owner_id=self.ids["owner"], room=room, title="Win the contest",
                purpose="Win the contest.", mode=mode,
                metric={"name": "acc", "direction": "maximize", "target": target,
                        "min_delta": min_delta},
                evaluator={"command": "python bench.py", "timeout_sec": 60,
                           "pin_paths": pin_paths or []},
                budget={"max_total_tokens": max_total_tokens}, deadline_at=deadline_at,
                trusted_eval=trusted_eval, clock=lambda: now - timedelta(seconds=1),
            )
            await db.commit()
            return campaign.id

    async def tick(self, now: datetime) -> list[str]:
        outcomes = await self.scheduler(now)._tick()
        return [o.task_id for o in outcomes if o.action == "opened"]

    async def run_retrospective(self, now: datetime) -> str:
        """Open the due retrospective and let its run complete."""
        outcomes = await self.scheduler(now)._tick()
        retros = [o.task_id for o in outcomes if o.action == "retrospective"]
        assert len(retros) == 1, outcomes
        execution_id = await self.start(retros[0], now, record=False)
        await self.finish(execution_id, tokens=1_000, now=now + timedelta(minutes=10))
        return retros[0]

    async def start(self, task_id: str, now: datetime, *, record: bool = True) -> str:
        """Begin the cycle's run; ``record`` also stores the agent's one
        attempt record (``record_campaign_attempt``), as the brief asks."""
        async with self.factory() as db:
            task = await db.get(Task, task_id)
            seq = (await db.scalar(select(func.coalesce(func.max(Message.seq), 0)).where(
                Message.room_id == self.ids["room"]))) + 1
            message = Message(room_id=self.ids["room"], content="cycle start", seq=seq)
            db.add(message)
            await db.flush()
            execution = ProjectExecution(
                project_id=self.ids["project"], operating_room_id=self.ids["room"],
                lead_agent_id=self.ids["agent"], owner_user_id=self.ids["owner"],
                source_message_id=message.id, root_task_id=task.id, objective=task.title,
                status="running", created_at=now,
            )
            db.add(execution)
            await db.flush()
            task.execution_id = execution.id
            task.status = "in_progress"
            task.started_at = now
            await attach_cycle_execution(db, root=task, execution_id=execution.id)
            if record:
                attempt = await db.scalar(select(CampaignAttempt).where(
                    CampaignAttempt.task_id == task.id))
                attempt.agent_outcome, attempt.recorded_at = "attempted", now
                attempt.hypothesis, attempt.change_summary = "try", "changed"
            await db.commit()
            return execution.id

    async def finish(self, execution_id: str, *, outcome: str = "completed",
                     tokens: int = 10_000, now: datetime) -> None:
        """End the run through its real terminal path."""
        from anygarden.project_executions.limits import expire_execution_deadlines
        from anygarden.project_executions.service import _event

        self.tokens[execution_id] = tokens
        async with self.factory() as db:
            execution = await db.get(ProjectExecution, execution_id)
            task = await db.get(Task, execution.root_task_id)
            if outcome == "completed":
                execution.status, execution.finished_at = "completed", now
                task.status, task.finished_at = "done", now
                await db.flush()
                await _event(db, execution, f"execution:{execution.id}:completed",
                             "execution_completed", task_id=task.id)
            elif outcome == "cancelled":
                execution.status, execution.finished_at = "cancelled", now
                await db.flush()
                await _event(db, execution, f"execution:{execution.id}:cancelled",
                             "execution_cancelled", task_id=task.id)
            elif outcome == "deadline":
                # A run that never reports is ended by its execution deadline.
                execution.deadline_at = now - timedelta(seconds=1)
                await db.flush()
                assert await expire_execution_deadlines(db, now=now) == [execution.id]
            else:  # pragma: no cover
                raise AssertionError(outcome)
            await db.commit()

    async def dispatch(self, task_id: str, now: datetime) -> str:
        attempt = await self.attempt(task_id)
        return await self.scheduler(now).dispatch_eval(attempt.id)

    async def result(self, task_id: str, *, score=None, valid=True, status="ok",
                     now: datetime, pins=None, machine_id=None) -> str:
        attempt = await self.attempt(task_id)
        parsed = {"score": score, "valid": valid} if status == "ok" else None
        frame = {"type": "campaign_eval_result", "job_id": attempt.eval_job_id,
                 "agent_id": self.ids["agent"], "status": status, "exit_code": 0,
                 "stdout_tail": "ok", "stderr_tail": "", "parsed": parsed,
                 "pins": pins or {}, "error": None if status == "ok" else status}
        return await handle_eval_result_frame(
            self.factory, machine_id or self.ids["machine"], frame, clock=lambda: now
        )

    async def run_cycle(self, now: datetime, *, score=None, valid=True, tokens=10_000,
                        outcome="completed", status="ok") -> str:
        opened = await self.tick(now)
        assert len(opened) == 1, f"expected one cycle at {now}, got {opened}"
        task_id = opened[0]
        execution_id = await self.start(task_id, now)
        await self.finish(execution_id, outcome=outcome, tokens=tokens,
                          now=now + timedelta(minutes=20))
        if outcome == "completed":
            assert await self.dispatch(task_id, now + timedelta(minutes=21)) == "accepted"
            assert await self.result(task_id, score=score, valid=valid, status=status,
                                     now=now + timedelta(minutes=25)) == "judged"
        return task_id

    async def campaign(self, campaign_id: str) -> Campaign:
        async with self.factory() as db:
            return await db.get(Campaign, campaign_id)

    async def attempt(self, task_id: str) -> CampaignAttempt:
        async with self.factory() as db:
            return await db.scalar(select(CampaignAttempt).where(CampaignAttempt.task_id == task_id))

    async def best_score(self, campaign_id: str) -> float | None:
        async with self.factory() as db:
            campaign = await db.get(Campaign, campaign_id)
            if campaign.best_attempt_id is None:
                return None
            return (await db.get(CampaignAttempt, campaign.best_attempt_id)).dev_score

    async def events(self, campaign_id: str) -> list[CampaignEvent]:
        async with self.factory() as db:
            return list(await db.scalars(select(CampaignEvent).where(
                CampaignEvent.campaign_id == campaign_id).order_by(CampaignEvent.created_at)))


@pytest_asyncio.fixture()
async def env(tmp_path, monkeypatch) -> AsyncIterator[Env]:
    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'campaigns.db'}",
        jwt_secret=secrets.token_urlsafe(32), log_level="WARNING",
        mcp_secrets_key=Fernet.generate_key().decode("ascii"),
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with factory() as db:
        owner = User(email="owner@test.com", password_hash="x", is_admin=True)
        db.add(owner)
        await db.flush()
        machine = Machine(name="box", hostname="box", owner_user_id=owner.id, status="online")
        db.add(machine)
        await db.flush()
        agent = Agent(name="lead", engine="codex-cli", placed_on_machine_id=machine.id)
        project = Project(name="contest", created_by=owner.id)
        db.add_all([agent, project])
        await db.flush()
        room = Room(name="contest", project_id=project.id, representative_agent_id=agent.id)
        db.add(room)
        await db.flush()
        agent_p = Participant(room_id=room.id, agent_id=agent.id, role="member")
        db.add_all([Participant(room_id=room.id, user_id=owner.id, role="owner"), agent_p])
        await db.commit()
        ids = {"owner": owner.id, "agent": agent.id, "project": project.id, "room": room.id,
               "agent_p": agent_p.id, "machine": machine.id,
               "token": create_user_token(owner.id, owner.email, True, secret=config.jwt_secret)}

    tokens: dict[str, int] = {}

    async def fake_summary(db, *, execution_id):
        return {"known_total_tokens": tokens.get(execution_id, 0)}

    monkeypatch.setattr(
        "anygarden.project_executions.usage.execution_usage_summary", fake_summary
    )
    bus = FakeBus()
    bus.online.add(ids["machine"])
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield Env(factory, client, ids, bus, tokens)
    evaluation.uninstall_dispatcher()
    await engine.dispose()


# ═══ Spike scenarios on real services ══════════════════════════════


async def test_s1_cycles_improve_until_target_then_await_confirmation(env):
    cid = await env.create()
    now = T0
    for score in (0.5, 0.7, 0.65, 0.95):
        await env.run_cycle(now, score=score)
        now += GAP + timedelta(minutes=30)
    campaign = await env.campaign(cid)
    assert campaign.cycles_run == 4
    assert await env.best_score(cid) == pytest.approx(0.95)
    assert campaign.tokens_used == 40_000
    assert campaign.status == "awaiting_confirmation"
    assert await env.tick(now + timedelta(hours=1)) == []


async def test_s1b_min_gap_respected(env):
    await env.create()
    await env.run_cycle(T0, score=0.5)
    # Closed at T0+25m; the next cycle waits 300s after that.
    assert await env.tick(T0 + timedelta(minutes=26)) == []
    assert len(await env.tick(T0 + timedelta(minutes=30, seconds=1))) == 1


async def test_s2_campaign_survives_weeks_of_sweeper_passes(env):
    cid = await env.create(mode="optimize", target=None, deadline_at=T0 + timedelta(days=30))
    now = T0
    retrospectives = 0
    for day in range(21):
        if (await env.campaign(cid)).retro_due == "periodic":
            # Phase 6 — every 10 cycles a retrospective runs first.
            await env.run_retrospective(now)
            retrospectives += 1
            now += timedelta(hours=1)
        await env.run_cycle(now, score=0.1 + day * 0.01)
        for offset in (timedelta(minutes=11), timedelta(hours=3), timedelta(hours=23)):
            async with env.factory() as db:
                await sweep_stuck_tasks(db, manager=None, now=now + offset)
                await db.commit()
        now += timedelta(days=1)
    campaign = await env.campaign(cid)
    assert campaign.status == "active" and campaign.cycles_run == 21
    assert retrospectives == 2
    async with env.factory() as db:
        failed = list(await db.scalars(select(Task.id).where(
            Task.campaign_id == cid, Task.status == "failed")))
    assert failed == []


async def test_s2b_unpicked_cycle_is_swept_and_campaign_moves_on(env):
    cid = await env.create()
    created = await env.tick(T0)
    async with env.factory() as db:  # never picked up: pickup timeout is 300s
        assert await sweep_stuck_tasks(db, manager=None, now=T0 + timedelta(minutes=6)) == 1
        await db.commit()
    attempt = await env.attempt(created[0])
    # Closed at once by the sweeper hook (not by a later tick), CMP-11.
    assert (attempt.outcome, attempt.eval_status) == ("unknown", "skipped")
    assert attempt.closed_at is not None
    assert (await env.campaign(cid)).next_cycle_at is not None
    nxt = await env.tick(T0 + timedelta(minutes=12))
    assert len(nxt) == 1 and nxt[0] != created[0]
    assert (await env.campaign(cid)).status == "active"


@pytest.mark.req("CMP-02")
async def test_s3_restart_mid_cycle_no_duplicate(env):
    cid = await env.create()
    first = await env.tick(T0)
    assert await env.tick(T0 + timedelta(minutes=1)) == []  # a fresh scheduler
    execution_id = await env.start(first[0], T0 + timedelta(minutes=2))
    await env.finish(execution_id, tokens=5, now=T0 + timedelta(minutes=30))
    # The server restarts while the evaluation is out: a new session
    # factory still matches the result by its persisted job id.
    assert await env.dispatch(first[0], T0 + timedelta(minutes=30)) == "accepted"
    env.factory = build_session_factory(env.factory.kw["bind"])
    assert await env.result(first[0], score=0.3, now=T0 + timedelta(minutes=31)) == "judged"
    campaign = await env.campaign(cid)
    assert campaign.cycles_run == 1 and await env.best_score(cid) == pytest.approx(0.3)


@pytest.mark.req("CMP-02")
async def test_s4_concurrent_ticks_create_one_cycle(env):
    await env.create()
    results = await asyncio.gather(*(env.tick(T0) for _ in range(4)))
    assert sum(len(r) for r in results) == 1
    async with env.factory() as db:
        n = await db.scalar(select(func.count()).select_from(Task).where(
            Task.room_id == env.ids["room"], Task.status == "todo"))
    assert n == 1


@pytest.mark.req("CMP-04")
async def test_s5_eval_after_stop_does_not_reopen_or_score(env):
    cid = await env.create()
    cycle = (await env.tick(T0))[0]
    execution_id = await env.start(cycle, T0)
    await env.finish(execution_id, tokens=5, now=T0 + timedelta(minutes=10))
    assert await env.dispatch(cycle, T0 + timedelta(minutes=10)) == "accepted"
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        assert await lifecycle.stop_campaign(db, campaign, actor="user:x", stop_reason="user")
        await db.commit()
    assert await env.result(cycle, score=0.99, now=T0 + timedelta(minutes=12)) == "late"
    campaign = await env.campaign(cid)
    assert campaign.status == "stopped" and campaign.best_attempt_id is None
    attempt = await env.attempt(cycle)
    assert attempt.outcome == "unknown" and attempt.dev_score is None
    assert "late_eval_result" in [e.type for e in await env.events(cid)]
    assert await env.tick(T0 + timedelta(hours=2)) == []


@pytest.mark.req("CMP-11")
async def test_s6_unknown_cycle_is_not_rerun_and_loop_continues(env):
    cid = await env.create()
    first = await env.run_cycle(T0, outcome="deadline")
    attempt = await env.attempt(first)
    assert (attempt.outcome, attempt.eval_status) == ("unknown", "skipped")
    async with env.factory() as db:
        task = await db.get(Task, first)
        execution = await db.get(ProjectExecution, attempt.execution_id)
        # Decision 7: the closed cycle's run and Task are terminal.
        assert execution.status == "failed" and task.status == "failed"
    second = await env.run_cycle(T0 + timedelta(hours=1), score=0.4)
    assert first != second
    campaign = await env.campaign(cid)
    assert campaign.status == "active" and campaign.cycles_run == 2
    assert await env.best_score(cid) == pytest.approx(0.4)


@pytest.mark.req("CMP-12")
async def test_s7_retiring_the_room_stops_the_campaign_in_the_same_transaction(env):
    cid = await env.create()
    cycle = (await env.tick(T0))[0]
    async with env.factory() as db:
        room = await db.get(Room, env.ids["room"])
        owner = await db.get(User, env.ids["owner"])
        await retire_room(db, room=room, identity=owner)
        await db.commit()
    # No tick in between: the stop is part of the retire commit.
    campaign = await env.campaign(cid)
    assert (campaign.status, campaign.stop_reason) == ("stopped", "room_archived")
    attempt = await env.attempt(cycle)
    assert attempt.closed_at is not None and attempt.outcome == "unknown"
    async with env.factory() as db:
        assert (await db.get(Task, cycle)).status == "failed"
    assert await env.tick(T0 + timedelta(days=1)) == []


async def test_s8a_room_task_list_is_sane(env):
    cid = await env.create()
    await env.run_cycle(T0, score=0.5)
    await env.tick(T0 + timedelta(hours=1))
    r = await env.client.get(f"/api/v1/rooms/{env.ids['room']}/tasks", headers=env.auth)
    assert r.status_code == 200, r.text
    async with env.factory() as db:
        owned = set(await db.scalars(select(Task.id).where(Task.campaign_id == cid)))
    statuses = {t["id"]: t["status"] for t in r.json() if t["id"] in owned}
    assert sorted(statuses.values()) == ["done", "todo"]


async def test_s8b_agent_tasks_endpoint_ok(env):
    await env.create()
    await env.tick(T0)
    r = await env.client.get(f"/api/v1/agents/{env.ids['agent']}/tasks", headers=env.auth)
    assert r.status_code == 200, r.text


@pytest.mark.req("CMP-12")
async def test_s8c_task_api_refuses_an_open_cycle_and_history_survives(env):
    cid = await env.create()
    cycle = (await env.tick(T0))[0]

    r = await env.client.put(f"/api/v1/tasks/{cycle}", headers=env.auth, json={"status": "done"})
    assert r.status_code == 409, r.text
    assert "campaign" in r.text.lower()
    r = await env.client.delete(f"/api/v1/tasks/{cycle}", headers=env.auth)
    assert r.status_code == 409, r.text
    r = await env.client.post(f"/api/v1/tasks/{cycle}/requeue", headers=env.auth,
                              json={"reason": "retry"})
    assert r.status_code == 409, r.text
    # A title edit is not a lifecycle change.
    r = await env.client.put(f"/api/v1/tasks/{cycle}", headers=env.auth, json={"title": "Renamed"})
    assert r.status_code == 200, r.text
    campaign = await env.campaign(cid)
    assert campaign.status == "active"
    assert (await env.attempt(cycle)).closed_at is None

    # Once the cycle has closed (here: swept), the terminal Task may be
    # deleted; the attempt history stays (task link SET NULL).
    async with env.factory() as db:
        await sweep_stuck_tasks(db, manager=None, now=T0 + timedelta(minutes=6))
        await db.commit()
    r = await env.client.delete(f"/api/v1/tasks/{cycle}", headers=env.auth)
    assert r.status_code == 200, r.text
    async with env.factory() as db:
        attempts = list(await db.scalars(select(CampaignAttempt).where(
            CampaignAttempt.campaign_id == cid)))
    assert len(attempts) == 1
    assert attempts[0].task_id is None and attempts[0].outcome == "unknown"
    assert attempts[0].cycle_no == 1
    nxt = await env.tick(T0 + timedelta(hours=1))
    assert len(nxt) == 1


@pytest.mark.req("CMP-12")
async def test_s8d_agent_cannot_mark_its_cycle_task_done(env):
    from anygarden.mcp.tools import mark_task_status

    await env.create()
    cycle = (await env.tick(T0))[0]
    async with env.factory() as db:
        for status in ("done", "failed", "blocked"):
            result = await mark_task_status(
                db, agent_id=env.ids["agent"], arguments={"task_id": cycle, "status": status},
            )
            assert result.get("isError"), result
            assert "CAMPAIGN_CYCLE_TASK_MANAGED" in result["content"][0]["text"]
            assert "complete_project_execution" in result["content"][0]["text"]
        await db.rollback()
    async with env.factory() as db:
        assert (await db.get(Task, cycle)).status == "todo"
    assert (await env.attempt(cycle)).closed_at is None


@pytest.mark.req("CMP-10")
async def test_s9a_budget_exhausted(env):
    cid = await env.create(max_total_tokens=100_000)
    now = T0
    for _ in range(3):
        await env.run_cycle(now, score=0.1, tokens=40_000)
        now += timedelta(hours=1)
    campaign = await env.campaign(cid)
    assert (campaign.status, campaign.stop_reason) == ("budget_exhausted", "budget")
    assert campaign.tokens_used == 120_000
    assert await env.tick(now) == []


@pytest.mark.req("CMP-09")
async def test_s9b_deadline_reached(env):
    cid = await env.create(mode="optimize", target=None, deadline_at=T0 + timedelta(hours=2))
    await env.run_cycle(T0, score=0.2)
    assert await env.tick(T0 + timedelta(hours=3)) == []
    assert (await env.campaign(cid)).status == "deadline_reached"


# ═══ Evaluation requests (Phase 3) ═════════════════════════════════


async def _completed_cycle(env, cid=None, *, now=T0) -> str:
    cid = cid or await env.create()
    cycle = (await env.tick(now))[0]
    execution_id = await env.start(cycle, now)
    await env.finish(execution_id, now=now + timedelta(minutes=10))
    return cycle


async def test_completed_run_requests_an_evaluation_with_a_persisted_job(env):
    cid = await env.create(pin_paths=["bench.py"])
    cycle = await _completed_cycle(env, cid)
    attempt = await env.attempt(cycle)
    assert attempt.eval_status == "pending" and attempt.eval_job_id == attempt.id
    # The server-side deadline starts once the machine accepts the job.
    assert attempt.eval_deadline_at is None and attempt.closed_at is None
    assert await env.dispatch(cycle, T0 + timedelta(minutes=11)) == "accepted"
    frame = env.bus.frames[-1]
    assert frame["job_id"] == attempt.id and frame["agent_id"] == env.ids["agent"]
    assert frame["command"] == "python bench.py" and frame["timeout_sec"] == 60
    assert frame["pin_paths"] == ["bench.py"] and frame["expected_pins"] is None
    attempt = await env.attempt(cycle)
    assert attempt.eval_status == "running"
    assert attempt.eval_started_at == T0 + timedelta(minutes=11)
    # The cycle stays open while the evaluation runs.
    assert await env.tick(T0 + timedelta(hours=1)) == []


async def test_after_commit_dispatch_sends_the_request_at_once(env):
    sent: list[str] = []

    async def dispatch(attempt_id):
        sent.append(attempt_id)

    evaluation.install_dispatcher(dispatch)
    cycle = await _completed_cycle(env)
    for _ in range(20):
        if sent:
            break
        await asyncio.sleep(0.01)
    assert sent == [(await env.attempt(cycle)).id]


async def test_offline_machine_leaves_the_eval_pending_until_reconnect(env):
    cycle = await _completed_cycle(env)
    env.bus.online.clear()
    assert await env.dispatch(cycle, T0 + timedelta(minutes=11)) == "offline"
    assert (await env.attempt(cycle)).eval_status == "pending"
    # A tick while offline changes nothing.
    await env.scheduler(T0 + timedelta(minutes=12)).reconcile()
    assert (await env.attempt(cycle)).eval_status == "pending" and env.bus.frames == []
    # The machine comes back: the register hook / next tick re-sends it.
    env.bus.online.add(env.ids["machine"])
    sent = await request_pending_evals(env.factory, env.bus, machine_id=env.ids["machine"])
    assert list(sent.values()) == ["accepted"]
    assert (await env.attempt(cycle)).eval_status == "running"


async def test_busy_machine_keeps_the_eval_pending_and_the_tick_retries(env):
    cycle = await _completed_cycle(env)
    env.bus.ack = {"accepted": False, "reason": "busy"}
    assert await env.dispatch(cycle, T0 + timedelta(minutes=11)) == "busy"
    assert (await env.attempt(cycle)).eval_status == "pending"
    env.bus.ack = {"accepted": True, "reason": None}
    await env.scheduler(T0 + timedelta(minutes=12)).reconcile()
    assert (await env.attempt(cycle)).eval_status == "running"
    assert len(env.bus.frames) == 2


@pytest.mark.req("CMP-04")
async def test_server_side_eval_deadline_closes_as_eval_failed(env):
    cid = await env.create()
    cycle = await _completed_cycle(env, cid)
    assert await env.dispatch(cycle, T0 + timedelta(minutes=11)) == "accepted"
    attempt = await env.attempt(cycle)
    # 60s evaluator timeout + 120s grace after it started running.
    assert attempt.eval_deadline_at == T0 + timedelta(minutes=11, seconds=180)
    await env.scheduler(T0 + timedelta(minutes=14)).reconcile()
    attempt = await env.attempt(cycle)
    assert (attempt.outcome, attempt.eval_status) == ("eval_failed", "timed_out")
    campaign = await env.campaign(cid)
    assert campaign.consecutive_eval_failures == 1 and campaign.status == "active"
    assert campaign.next_cycle_at == attempt.closed_at + timedelta(seconds=300)
    # A result that arrives afterwards is history only.
    assert await env.result(cycle, score=0.99, now=T0 + timedelta(minutes=15)) == "late"
    assert (await env.attempt(cycle)).outcome == "eval_failed"
    assert campaign.best_attempt_id is None


@pytest.mark.req("CMP-04")
async def test_result_from_another_machine_is_ignored(env):
    cycle = await _completed_cycle(env)
    assert await env.dispatch(cycle, T0 + timedelta(minutes=11)) == "accepted"
    assert await env.result(cycle, score=0.5, now=T0 + timedelta(minutes=12),
                            machine_id="other-machine") == "wrong_machine"
    assert (await env.attempt(cycle)).closed_at is None


# ═══ Judging (Phase 4) ════════════════════════════════════════════


@pytest.mark.req("CMP-05")
async def test_first_result_records_pins_and_sets_the_next_cycle(env):
    cid = await env.create()
    cycle = await _completed_cycle(env, cid)
    await env.dispatch(cycle, T0 + timedelta(minutes=11))
    closed = T0 + timedelta(minutes=12)
    assert await env.result(cycle, score=0.5, now=closed, pins={"bench.py": "a" * 64}) == "judged"
    attempt = await env.attempt(cycle)
    assert (attempt.outcome, attempt.eval_status, attempt.dev_score, attempt.valid) == (
        "improved", "ok", 0.5, True)
    assert attempt.closed_at == closed and attempt.metrics == {"score": 0.5, "valid": True}
    campaign = await env.campaign(cid)
    assert campaign.best_attempt_id == attempt.id
    assert campaign.evaluator_pins == {"bench.py": "a" * 64}
    assert campaign.next_cycle_at == closed + timedelta(seconds=300)
    assert "cycle_closed" in [e.type for e in await env.events(cid)]
    # Later requests carry the recorded pins.
    second = (await env.tick(closed + GAP))[0]
    execution_id = await env.start(second, closed + GAP)
    await env.finish(execution_id, now=closed + GAP + timedelta(minutes=5))
    await env.dispatch(second, closed + GAP + timedelta(minutes=6))
    assert env.bus.frames[-1]["expected_pins"] == {"bench.py": "a" * 64}


async def test_invalid_score_is_a_guardrail_violation_and_keeps_the_best(env):
    cid = await env.create()
    await env.run_cycle(T0, score=0.5)
    task = await env.run_cycle(T0 + timedelta(hours=1), score=0.8, valid=False)
    assert (await env.attempt(task)).outcome == "guardrail_violation"
    assert await env.best_score(cid) == pytest.approx(0.5)
    assert (await env.campaign(cid)).cycles_since_improvement == 1


@pytest.mark.req("CMP-04")
async def test_three_eval_failures_in_a_row_block_and_a_success_resets(env):
    cid = await env.create()
    now = T0
    await env.run_cycle(now, status="failed")
    now += timedelta(hours=1)
    await env.run_cycle(now, status="failed")
    now += timedelta(hours=1)
    await env.run_cycle(now, score=0.3)
    assert (await env.campaign(cid)).consecutive_eval_failures == 0
    for _ in range(3):
        now += timedelta(hours=1)
        await env.run_cycle(now, status="timed_out")
    campaign = await env.campaign(cid)
    assert campaign.status == "blocked" and campaign.consecutive_eval_failures == 3
    assert campaign.status_detail
    assert await env.tick(now + timedelta(hours=1)) == []


@pytest.mark.req("CMP-05")
async def test_pin_mismatch_is_invalid_and_blocks(env):
    cid = await env.create()
    task = await env.run_cycle(T0, status="invalid")
    attempt = await env.attempt(task)
    assert (attempt.outcome, attempt.eval_status) == ("invalid", "invalid")
    campaign = await env.campaign(cid)
    assert campaign.status == "blocked" and "pinned" in campaign.status_detail


@pytest.mark.req("CMP-06")
@pytest.mark.parametrize("trusted_eval", ["none", "external"])
async def test_dev_target_only_awaits_confirmation(env, trusted_eval):
    cid = await env.create(trusted_eval=trusted_eval)
    await env.run_cycle(T0, score=0.95)
    assert (await env.campaign(cid)).status == "awaiting_confirmation"
    change = [e for e in await env.events(cid) if e.type == "status_changed"][-1]
    assert change.details["awaiting"] == ("trusted_score" if trusted_eval == "external"
                                          else "confirmation")


@pytest.mark.req("CMP-08")
async def test_target_mode_plateaus_after_the_window(env):
    cid = await env.create()
    now = T0
    await env.run_cycle(now, score=0.5)
    for _ in range(5):
        now += timedelta(hours=1)
        await env.run_cycle(now, score=0.5)  # no gain
    campaign = await env.campaign(cid)
    assert campaign.status == "plateau" and campaign.cycles_since_improvement == 5


@pytest.mark.req("CMP-08")
async def test_optimize_mode_keeps_going_and_asks_for_a_new_strategy(env):
    cid = await env.create(mode="optimize", target=None, deadline_at=T0 + timedelta(days=5))
    now = T0
    await env.run_cycle(now, score=0.5)
    for _ in range(5):
        now += timedelta(hours=1)
        await env.run_cycle(now, score=0.4)  # regressions
    campaign = await env.campaign(cid)
    assert campaign.status == "active"
    assert campaign.cycles_since_improvement == 0
    assert campaign.wake_reason == lifecycle.STRATEGY_CHANGE
    assert "strategy_change" in [e.type for e in await env.events(cid)]
    nxt = (await env.tick(now + timedelta(hours=1)))[0]
    async with env.factory() as db:
        assert "Change strategy" in (await db.get(Task, nxt)).spec
    # Consumed by the cycle that carried the directive.
    assert (await env.campaign(cid)).wake_reason is None


async def test_cancelled_run_closes_without_an_evaluation(env):
    cid = await env.create()
    task = await env.run_cycle(T0, outcome="cancelled", tokens=7_000)
    attempt = await env.attempt(task)
    assert (attempt.outcome, attempt.eval_status, attempt.eval_job_id) == (
        "unknown", "skipped", None)
    assert attempt.closed_at == T0 + timedelta(minutes=20)
    async with env.factory() as db:
        assert (await db.get(Task, task)).status == "failed"
    campaign = await env.campaign(cid)
    assert campaign.tokens_used == 7_000 and campaign.status == "active"
    assert campaign.next_cycle_at == attempt.closed_at + timedelta(seconds=300)
    assert env.bus.frames == []


async def test_stop_cancels_a_running_cycle_execution(env):
    cid = await env.create()
    cycle = (await env.tick(T0))[0]
    execution_id = await env.start(cycle, T0)
    async with env.factory() as db:
        campaign = await db.get(Campaign, cid)
        assert await lifecycle.stop_campaign(db, campaign, actor="user:x", stop_reason="user")
        await db.commit()
    async with env.factory() as db:
        execution = await db.get(ProjectExecution, execution_id)
        mutation = await db.scalar(select(ExecutionMutation).where(
            ExecutionMutation.execution_id == execution_id))
        # No live turn to wait for, so the system cancel settles at once.
        assert execution.status == "cancelled"
        assert (mutation.action, mutation.requested_by_user_id) == ("cancel", None)
        assert (await db.get(Task, cycle)).status == "failed"
    attempt = await env.attempt(cycle)
    assert attempt.closed_at is not None and attempt.outcome == "unknown"


@pytest.mark.req("CMP-11")
async def test_reconcile_closes_a_cycle_whose_task_disappeared(env):
    cid = await env.create()
    cycle = (await env.tick(T0))[0]
    async with env.factory() as db:
        attempt = await db.scalar(select(CampaignAttempt).where(CampaignAttempt.task_id == cycle))
        attempt.task_id = None  # e.g. removed by hand in the DB
        await db.commit()
    await env.scheduler(T0 + timedelta(minutes=1)).reconcile()
    async with env.factory() as db:
        attempt = await db.scalar(select(CampaignAttempt).where(CampaignAttempt.campaign_id == cid))
    assert attempt.closed_at is not None and attempt.outcome == "unknown"


@pytest.mark.req("CMP-12")
async def test_reconcile_stops_a_campaign_whose_room_was_archived_mid_cycle(env):
    cid = await env.create()
    cycle = (await env.tick(T0))[0]
    async with env.factory() as db:
        (await db.get(Room, env.ids["room"])).archived_at = T0
        await db.commit()
    await env.tick(T0 + timedelta(minutes=1))
    campaign = await env.campaign(cid)
    assert (campaign.status, campaign.stop_reason) == ("stopped", "room_archived")
    assert (await env.attempt(cycle)).closed_at is not None


@pytest.mark.req("CMP-12")
async def test_archive_endpoint_stops_the_rooms_campaigns(env):
    cid = await env.create()
    await env.tick(T0)
    r = await env.client.post(f"/api/v1/rooms/{env.ids['room']}/archive", headers=env.auth)
    assert r.status_code == 200, r.text
    assert (await env.campaign(cid)).status == "stopped"
