"""Project execution acceptance flows without a model (#778).

The test plays both agents ("fake engine"): it receives the leased turn
the server delivers over a fake room socket, asks for the native start
permit, then calls the project MCP tools with that turn's proof — the
same protocol a real Codex/Pi runtime follows. Users act through the REST
API. Scenario IDs refer to docs/e2e/2026-09-30-project-room-autonomy-qa.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import AsyncIterator
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Agent,
    AgentToken,
    AgentTurn,
    Base,
    Message,
    Participant,
    Project,
    Room,
    Task,
    User,
)
from anygarden.project_executions.stop_service import deliver_pending_stops
from anygarden.turns.service import (
    create_turn,
    deliver_pending_outbox,
    record_lifecycle,
)
from anygarden.turns.start_service import authorize_native_start
from anygarden.ws.manager import ConnectionManager
from anygarden.ws.protocol import LifecycleFrame

GENERATION = 1


class _AgentSocket:
    """Room socket of a fake agent runtime: records every frame it receives."""

    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.frames.append(json.loads(payload))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        pass


@dataclass
class Turn:
    """A delivered turn the fake agent holds, with its tool-call proof."""

    agent: str
    request_id: str
    attempt: int
    lease: str
    execution_id: str | None
    input_revision: int | None
    frame: dict
    local_execution_id: str = ""

    @property
    def headers(self) -> dict[str, str]:
        return {
            "X-Anygarden-Turn-Request-Id": self.request_id,
            "X-Anygarden-Turn-Attempt": str(self.attempt),
            "X-Anygarden-Turn-Generation": str(GENERATION),
            "X-Anygarden-Turn-Lease": self.lease,
        }


class Harness:
    def __init__(self, app, client: AsyncClient, sessions, ids: dict) -> None:
        self.app = app
        self.client = client
        self.sessions = sessions
        self.ids = ids
        self.manager: ConnectionManager = app.state.connection_manager
        self.sockets: dict[str, _AgentSocket] = {}

    # -- users ---------------------------------------------------------

    def user(self, who: str = "owner") -> dict[str, str]:
        return {"Authorization": f"Bearer {self.ids[f'{who}_token']}"}

    # -- fake agent runtime -------------------------------------------

    async def connect(self, agent: str) -> None:
        """Subscribe the agent's room socket with stop-capable turn control."""
        socket = _AgentSocket()
        self.sockets[agent] = socket
        await self.manager.subscribe(
            self.ids[f"{agent}_room"],
            self.ids[f"{agent}_pid"],
            socket,
            generation=GENERATION,
            turn_control=True,
        )

    async def deliver(self, agent: str) -> Turn:
        """Let the server deliver the agent's next turn and start it natively."""
        socket = self.sockets[agent]
        before = len(socket.frames)
        await deliver_pending_outbox(self.sessions, self.manager, app=self.app)
        delivered = [f for f in socket.frames[before:] if f.get("metadata", {}).get("turn_lease")]
        assert delivered, f"no turn delivered to {agent}: {socket.frames[before:]}"
        frame = delivered[-1]
        meta = frame["metadata"]
        turn = Turn(
            agent=agent,
            request_id=meta["request_id"],
            attempt=meta["turn_attempt"],
            lease=meta["turn_lease"],
            execution_id=meta.get("execution_id"),
            input_revision=meta.get("input_revision"),
            frame=frame,
            local_execution_id=str(uuid4()),
        )
        async with self.sessions() as db:
            permit = await authorize_native_start(
                db,
                agent_id=self.ids[agent],
                room_id=self.ids[f"{agent}_room"],
                participant_id=self.ids[f"{agent}_pid"],
                packet=SimpleNamespace(
                    request_id=turn.request_id,
                    attempt=turn.attempt,
                    generation=GENERATION,
                    lease=turn.lease,
                    local_execution_id=turn.local_execution_id,
                    execution_id=turn.execution_id,
                    input_revision=turn.input_revision,
                ),
            )
            await db.commit()
        assert permit.allowed, permit
        return turn

    async def end_turn(self, turn: Turn, outcome: str = "skipped") -> None:
        """End the turn without a reply, as a runtime does after asking/waiting.

        ``outcome="failed"`` reports a native failure with the terminal
        receipt a runtime attaches: the process finished and the model call
        failed, so the server knows a retry cannot duplicate side effects.
        """
        receipt = {}
        if outcome == "failed":
            receipt = dict(
                local_execution_id=turn.local_execution_id,
                native_process_state="finished",
                native_outcome="failed",
                native_reason_code="MODEL_EXECUTION_FAILED",
            )
        async with self.sessions() as db:
            applied = await record_lifecycle(
                db,
                agent_id=self.ids[turn.agent],
                frame=LifecycleFrame(
                    request_id=turn.request_id,
                    room_id=self.ids[f"{turn.agent}_room"],
                    turn_attempt=turn.attempt,
                    turn_generation=GENERATION,
                    turn_lease=turn.lease,
                    event="handler_finished",
                    outcome=outcome,
                    **receipt,
                ),
            )
            await db.commit()
        assert applied

    async def tool(self, turn: Turn, name: str, **arguments) -> dict:
        """Call a project MCP tool as *turn*'s agent; return structured content."""
        resp = await self.client.post(
            "/mcp/rpc",
            headers={
                "Authorization": f"Bearer {self.ids[f'{turn.agent}_token']}",
                **turn.headers,
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        assert resp.status_code == 200, resp.text
        result = resp.json()["result"]
        assert not result.get("isError"), result["content"]
        return result.get("structuredContent") or result

    async def tool_error_or_ok(self, turn: Turn, name: str, **arguments) -> dict:
        """Call a tool and return the raw result, whether or not it errored."""
        resp = await self.client.post(
            "/mcp/rpc",
            headers={
                "Authorization": f"Bearer {self.ids[f'{turn.agent}_token']}",
                **turn.headers,
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        return resp.json()["result"]

    async def tool_error(self, turn: Turn, name: str, **arguments) -> str:
        resp = await self.client.post(
            "/mcp/rpc",
            headers={
                "Authorization": f"Bearer {self.ids[f'{turn.agent}_token']}",
                **turn.headers,
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        result = resp.json()["result"]
        assert result.get("isError"), result
        return result["content"][0]["text"]

    # -- scenario steps ------------------------------------------------

    async def user_request(self, content: str) -> str:
        """Owner posts a request in the operating room; the lead gets a turn."""
        async with self.sessions() as db:
            seq = (
                await db.scalar(
                    select(func.coalesce(func.max(Message.seq), 0)).where(
                        Message.room_id == self.ids["lead_room"]
                    )
                )
            ) + 1
            message = Message(
                room_id=self.ids["lead_room"],
                participant_id=self.ids["owner_pid"],
                content=content,
                seq=seq,
            )
            db.add(message)
            await db.flush()
            await create_turn(
                db,
                room_id=self.ids["lead_room"],
                participant_id=self.ids["lead_pid"],
                agent_id=self.ids["lead"],
                trigger_message_id=message.id,
            )
            await db.commit()
            return message.id

    async def begin_and_delegate(
        self, objective: str = "Fix the login bug", **begin_args
    ) -> dict:
        """QA-03 prefix: lead begins the execution and delegates to the worker."""
        await self.user_request(objective)
        lead_turn = await self.deliver("lead")
        execution = await self.tool(
            lead_turn, "begin_project_execution", objective=objective, **begin_args
        )
        delegated = await self.tool(
            lead_turn,
            "delegate_project_task",
            execution_id=execution["execution_id"],
            parent_task_id=execution["root_task_id"],
            target_room_id=self.ids["worker_room"],
            assignee_participant_id=self.ids["worker_pid"],
            delegation_key="fix-login",
            title="Reproduce and fix the login bug",
            spec="Login fails for accounts with uppercase emails.",
        )
        return {"lead_turn": lead_turn, "execution": execution, "delegated": delegated}


@pytest_asyncio.fixture()
async def harness(config: AnygardenSettings, tmp_path) -> AsyncIterator[Harness]:
    config.room_files_dir = tmp_path / "room-files"
    config.artifact_files_dir = tmp_path / "artifacts"
    config.project_action_targets = {
        "mock-portal": {"url": "https://portal.test/submit", "label": "Mock portal",
                        "action_kind": "submission", "supports_idempotency": False},
    }
    engine = build_engine(config.db_url)
    sessions = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    ids: dict[str, str] = {}
    async with sessions() as db:
        owner = User(email="owner@anygarden.io", password_hash="x")
        outsider = User(email="outsider@anygarden.io", password_hash="x")
        lead = Agent(name="garden-pm", engine="codex-cli", desired_state="running", actual_state="running",
                     generation=GENERATION)
        worker = Agent(name="garden-dev", engine="codex-cli", desired_state="running", actual_state="running",
                       generation=GENERATION)
        releaser = Agent(name="garden-release", engine="codex-cli", desired_state="running", actual_state="running",
                         generation=GENERATION)
        db.add_all([owner, outsider, lead, worker, releaser])
        await db.flush()
        project = Project(name="garden", created_by=owner.id)
        db.add(project)
        await db.flush()
        operating = Room(project_id=project.id, name="operations",
                         representative_agent_id=lead.id)
        db.add(operating)
        await db.flush()
        sub = Room(project_id=project.id, name="development",
                   parent_room_id=operating.id, representative_agent_id=worker.id)
        release = Room(project_id=project.id, name="release",
                       parent_room_id=operating.id, representative_agent_id=releaser.id)
        db.add_all([sub, release])
        await db.flush()
        owner_pid = Participant(room_id=operating.id, user_id=owner.id, role="owner")
        lead_pid = Participant(room_id=operating.id, agent_id=lead.id, role="member")
        worker_pid = Participant(room_id=sub.id, agent_id=worker.id, role="member")
        releaser_pid = Participant(room_id=release.id, agent_id=releaser.id, role="member")
        db.add_all([owner_pid, lead_pid, worker_pid, releaser_pid,
                    Participant(room_id=sub.id, user_id=owner.id, role="owner"),
                    Participant(room_id=release.id, user_id=owner.id, role="owner")])
        for name, agent in (("lead", lead), ("worker", worker), ("releaser", releaser)):
            plain = generate_token()
            token_hash, hint = hash_agent_token(plain)
            db.add(AgentToken(agent_id=agent.id, token_hash=token_hash, lookup_hint=hint))
            ids[f"{name}_token"] = plain
        await db.commit()
        ids.update(
            owner=owner.id, outsider=outsider.id, lead=lead.id, worker=worker.id,
            releaser=releaser.id, project=project.id, lead_room=operating.id,
            worker_room=sub.id, releaser_room=release.id, owner_pid=owner_pid.id,
            lead_pid=lead_pid.id, worker_pid=worker_pid.id, releaser_pid=releaser_pid.id,
            owner_token=create_user_token(owner.id, owner.email, False,
                                          secret=config.jwt_secret),
            outsider_token=create_user_token(outsider.id, outsider.email, False,
                                             secret=config.jwt_secret),
        )

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions
    # The lifespan normally installs the manager; ASGITransport skips it.
    app.state.connection_manager = ConnectionManager()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        h = Harness(app, client, sessions, ids)
        await h.connect("lead")
        await h.connect("worker")
        await h.connect("releaser")
        yield h
    await engine.dispose()


# -- QA-03 delegation ---------------------------------------------------------


@pytest.mark.req("EXE-11", "EXE-12")
async def test_lead_delegates_work_to_sub_room_assignee(harness):
    flow = await harness.begin_and_delegate()
    execution, delegated = flow["execution"], flow["delegated"]

    assert execution["operating_room_id"] == harness.ids["lead_room"]
    assert delegated["room_id"] == harness.ids["worker_room"]
    assert delegated["assignee_participant_id"] == harness.ids["worker_pid"]
    assert delegated["execution_id"] == execution["execution_id"]

    # The worker receives the assignment as its own delivered turn, bound to
    # the execution and carrying the task spec.
    worker_turn = await harness.deliver("worker")
    assert worker_turn.execution_id == execution["execution_id"]
    assignment = worker_turn.frame["metadata"]["task_assignment"]
    assert assignment["task_id"] == delegated["task_id"]
    async with harness.sessions() as db:
        task = await db.get(Task, delegated["task_id"])
        # QA-04: the assignee gets the objective, the user's original request
        # and the lead's instructions — not just the lead's summary.
        assert f"Project execution {execution['execution_id']}" in task.spec
        assert "Fix the login bug" in task.spec
        assert "Login fails for accounts with uppercase emails." in task.spec
        turn = await db.get(AgentTurn, worker_turn.request_id)
        assert turn.task_id == task.id


# -- QA-06 input request · QA-09 inbox ---------------------------------------


async def _ask(harness: Harness) -> tuple[dict, Turn, dict]:
    flow = await harness.begin_and_delegate()
    worker_turn = await harness.deliver("worker")
    question = await harness.tool(
        worker_turn,
        "request_project_input",
        task_id=flow["delegated"]["task_id"],
        question_key="email-normalization",
        question="Should emails be lower-cased on write or only compared case-insensitively?",
    )
    return flow, worker_turn, question


@pytest.mark.req("EXE-09")
async def test_worker_question_reaches_operating_room_and_answer_resumes_task(harness):
    flow, worker_turn, question = await _ask(harness)
    task_id = flow["delegated"]["task_id"]
    assert question["status"] == "pending"
    assert question["operating_room_id"] == harness.ids["lead_room"]

    # Asking the same question again does not open a second request; reusing
    # the key for a different question is refused instead of overwriting it.
    again = await harness.tool(
        worker_turn, "request_project_input", task_id=task_id,
        question_key="email-normalization", question=question["question"],
    )
    assert again["id"] == question["id"]
    conflict = await harness.tool_error(
        worker_turn, "request_project_input", task_id=task_id,
        question_key="email-normalization", question="A different question",
    )
    assert "different text" in conflict

    # The owner sees it from the operating room without visiting the sub-room.
    listed = await harness.client.get(
        f"/api/v1/rooms/{harness.ids['lead_room']}/execution-requests",
        headers=harness.user(),
    )
    assert listed.status_code == 200
    assert [r["id"] for r in listed.json() if r["status"] == "pending"] == [question["id"]]

    answer = "Lower-case on write; migrate existing rows."
    resp = await harness.client.post(
        f"/api/v1/execution-requests/{question['id']}/answer",
        json={"answer": answer}, headers=harness.user(),
    )
    assert resp.status_code == 200, resp.text

    async with harness.sessions() as db:
        task = await db.get(Task, task_id)
        assert answer in task.spec
        # The assignee is woken again for the same task.
        resumed = (await db.scalars(select(AgentTurn).where(
            AgentTurn.agent_id == harness.ids["worker"],
            AgentTurn.task_id == task_id,
            AgentTurn.request_id != worker_turn.request_id,
        ))).all()
    assert len(resumed) == 1

    # Answering twice is rejected rather than appended again.
    twice = await harness.client.post(
        f"/api/v1/execution-requests/{question['id']}/answer",
        json={"answer": "Something else"}, headers=harness.user(),
    )
    assert twice.status_code == 409


@pytest.mark.req("INB-01", "INB-02", "INB-03")
async def test_inbox_lists_open_question_for_members_only(harness):
    _, _, question = await _ask(harness)

    inbox = await harness.client.get("/api/v1/inbox", headers=harness.user())
    assert inbox.status_code == 200
    items = inbox.json()["items"]
    ids = [item["id"] for item in items]
    assert f"question:{question['id']}" in ids
    # One entry per record: the question is not duplicated.
    assert len(ids) == len(set(ids))
    # Items that need the user's action come before informational ones.
    first_info = next((i for i, it in enumerate(items) if it["type"] == "task"), len(items))
    assert ids.index(f"question:{question['id']}") < first_info

    outsider = await harness.client.get("/api/v1/inbox", headers=harness.user("outsider"))
    assert outsider.status_code == 200
    assert outsider.json()["items"] == []


# -- QA-13 cancellation -------------------------------------------------------


async def _cancel(harness: Harness, execution_id: str, who: str = "owner"):
    detail = await harness.client.get(
        f"/api/v1/executions/{execution_id}", headers=harness.user()
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()["execution"]
    return await harness.client.post(
        f"/api/v1/executions/{execution_id}/cancel",
        headers=harness.user(who),
        json={
            "operation_id": str(uuid4()),
            "expected_input_revision": body["input_revision"],
            "expected_state_revision": body["state_revision"],
            "reason": "Requirements changed",
        },
    )


@pytest.mark.req("EXE-07")
async def test_cancel_stops_running_work_and_blocks_new_work(harness):
    flow = await harness.begin_and_delegate()
    execution_id = flow["execution"]["execution_id"]
    worker_turn = await harness.deliver("worker")

    assert (await _cancel(harness, execution_id, who="outsider")).status_code in {403, 404}
    resp = await _cancel(harness, execution_id)
    assert resp.status_code == 200, resp.text

    # The running worker is told to stop its turn (durable stop, delivered by
    # the same background loop the server runs).
    await deliver_pending_stops(harness.sessions, harness.manager)
    stop_frames = [f for f in harness.sockets["worker"].frames if f.get("type") == "turn_stop"]
    assert [f["request_id"] for f in stop_frames] == [worker_turn.request_id]

    # Neither the lead nor the worker can start new work for it.
    error = await harness.tool_error(
        flow["lead_turn"], "delegate_project_task",
        execution_id=execution_id,
        parent_task_id=flow["execution"]["root_task_id"],
        target_room_id=harness.ids["worker_room"],
        assignee_participant_id=harness.ids["worker_pid"],
        delegation_key="after-cancel", title="Late work", spec="Should not start",
    )
    assert error
    error = await harness.tool_error(
        worker_turn, "request_project_input",
        task_id=flow["delegated"]["task_id"],
        question_key="late", question="Still running?",
    )
    assert error
    async with harness.sessions() as db:
        late = await db.scalar(select(func.count()).select_from(Task).where(
            Task.title == "Late work"))
    assert late == 0


# -- QA-08 approval -----------------------------------------------------------


async def _ready_for_approval(harness: Harness) -> dict:
    """Worker delivers an accepted artifact; the releaser holds a submission task."""
    flow = await harness.begin_and_delegate(
        objective="Prepare and submit the release notes",
        allowed_actions=["internal_work", "managed_submission"],
    )
    execution = flow["execution"]
    worker_turn = await harness.deliver("worker")
    work_task = flow["delegated"]["task_id"]
    published = await harness.tool(
        worker_turn, "publish_project_artifact", task_id=work_task,
        filename="release-notes.md", content="# Release notes\n- Fixed login.\n",
    )
    artifact = published["artifacts"][0]
    # Omitted artifacts: the server links this invocation's publications.
    await harness.tool(
        worker_turn, "mark_task_status", task_id=work_task, status="done",
        result_markdown="Release notes written.",
    )
    action = await harness.tool(
        flow["lead_turn"], "delegate_project_task",
        execution_id=execution["execution_id"],
        parent_task_id=execution["root_task_id"],
        target_room_id=harness.ids["releaser_room"],
        assignee_participant_id=harness.ids["releaser_pid"],
        delegation_key="submit-notes", title="Submit release notes",
        spec="Submit the accepted release notes to the mock portal.",
    )
    releaser_turn = await harness.deliver("releaser")
    return {**flow, "work_task": work_task, "artifact": artifact,
            "action_task": action["task_id"], "releaser_turn": releaser_turn}


class _Portal:
    """Stands in for the configured submission target; counts real sends."""

    def __init__(self, monkeypatch) -> None:
        self.requests: list[httpx.Request] = []
        transport = httpx.MockTransport(self._handle)
        real_client = httpx.AsyncClient

        def client(*args, **kwargs):
            kwargs["transport"] = transport
            return real_client(*args, **kwargs)

        monkeypatch.setattr(
            "anygarden.project_executions.action_executor.httpx.AsyncClient", client
        )

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, json={"receipt": "PORTAL-1"})


async def _request_approval(harness: Harness, ready: dict) -> dict:
    return await harness.tool(
        ready["releaser_turn"], "request_project_approval",
        task_id=ready["action_task"], action_key="submit-release-notes",
        action_kind="submission", target_alias="mock-portal",
        source_task_id=ready["work_task"], source_result_version=1,
        artifact_id=ready["artifact"]["artifact_id"],
        summary="Submit release notes to the mock portal.",
    )


async def _decide(harness: Harness, approval_id: str, decision: str):
    return await harness.client.post(
        f"/api/v1/execution-approvals/{approval_id}/decision",
        json={"decision": decision}, headers=harness.user(),
    )


@pytest.mark.req("EXE-06")
async def test_external_action_runs_once_and_only_after_approval(harness, monkeypatch):
    portal = _Portal(monkeypatch)
    ready = await _ready_for_approval(harness)
    approval = await _request_approval(harness, ready)
    assert approval["status"] == "pending"

    # No permit before the user decides.
    await harness.tool_error(
        ready["releaser_turn"], "execute_approved_project_action", approval_id=approval["id"]
    )
    assert portal.requests == []

    listed = await harness.client.get(
        f"/api/v1/rooms/{harness.ids['lead_room']}/execution-approvals",
        headers=harness.user(),
    )
    assert listed.status_code == 200, listed.text
    assert approval["id"] in [a["id"] for a in listed.json()]
    assert (await _decide(harness, approval["id"], "approve")).status_code == 200

    # The approval resumes the action task in a fresh turn; only that turn
    # may use the permit.
    await harness.end_turn(ready["releaser_turn"])
    resumed = await harness.deliver("releaser")
    executed = await harness.tool(
        resumed, "execute_approved_project_action", approval_id=approval["id"]
    )
    assert executed["status"] == "succeeded"
    assert len(portal.requests) == 1
    assert portal.requests[0].headers["Idempotency-Key"] == approval["id"]

    # Calling again returns the consumed permit without sending twice.
    await harness.tool(resumed, "execute_approved_project_action", approval_id=approval["id"])
    assert len(portal.requests) == 1


@pytest.mark.req("EXE-06")
async def test_rejected_action_is_never_sent(harness, monkeypatch):
    portal = _Portal(monkeypatch)
    ready = await _ready_for_approval(harness)
    approval = await _request_approval(harness, ready)

    assert (await _decide(harness, approval["id"], "reject")).status_code == 200
    assert (await _decide(harness, approval["id"], "approve")).status_code == 409

    await harness.tool_error(
        ready["releaser_turn"], "execute_approved_project_action", approval_id=approval["id"]
    )
    assert portal.requests == []


# -- Execution-managed tasks reject the generic task API ----------------------


@pytest.mark.req("EXE-13")
async def test_generic_task_api_refuses_execution_tasks_with_409(harness):
    flow = await harness.begin_and_delegate()
    task_id = flow["delegated"]["task_id"]

    # Before the registry fix this raised ValueError and answered 500.
    resp = await harness.client.delete(f"/api/v1/tasks/{task_id}", headers=harness.user())
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["code"] == "PROJECT_EXECUTION_TASK_MANAGED"

    async with harness.sessions() as db:
        assert await db.get(Task, task_id) is not None


# -- QA-05 dependencies -------------------------------------------------------


@pytest.mark.req("EXE-01", "EXE-14")
async def test_dependent_task_waits_for_prerequisite_and_receives_its_result(harness):
    flow = await harness.begin_and_delegate()
    execution = flow["execution"]
    prerequisite = flow["delegated"]["task_id"]
    dependent = await harness.tool(
        flow["lead_turn"], "delegate_project_task",
        execution_id=execution["execution_id"],
        parent_task_id=execution["root_task_id"],
        target_room_id=harness.ids["releaser_room"],
        assignee_participant_id=harness.ids["releaser_pid"],
        delegation_key="write-changelog", title="Write the changelog entry",
        spec="Describe the login fix for users.", depends_on=[prerequisite],
    )

    # The prerequisite's assignee is woken; the dependent one is not while
    # the prerequisite is open.
    worker_turn = await harness.deliver("worker")
    assert not [f for f in harness.sockets["releaser"].frames
                if f.get("metadata", {}).get("turn_lease")]
    async with harness.sessions() as db:
        assert (await db.get(Task, dependent["task_id"])).status != "in_progress"

    await harness.tool(
        worker_turn, "mark_task_status", task_id=prerequisite, status="done",
        result_markdown="Root cause: emails compared case-sensitively. Fixed in auth/login.py.",
    )

    # Completing the prerequisite releases the dependent task with its result.
    releaser_turn = await harness.deliver("releaser")
    assert releaser_turn.frame["metadata"]["task_assignment"]["task_id"] == dependent["task_id"]
    async with harness.sessions() as db:
        task = await db.get(Task, dependent["task_id"])
    context = task.spec + json.dumps(releaser_turn.frame)
    assert "emails compared case-sensitively" in context


# -- INB-03 artifact access · QA-07/21 result collection ----------------------


async def _finish_work(harness: Harness) -> dict:
    flow = await harness.begin_and_delegate()
    task_id = flow["delegated"]["task_id"]
    # The lead fixes the required work while planning, before results arrive.
    await harness.tool(
        flow["lead_turn"], "seal_project_plan",
        execution_id=flow["execution"]["execution_id"], required_task_ids=[task_id],
    )
    worker_turn = await harness.deliver("worker")
    published = await harness.tool(
        worker_turn, "publish_project_artifact", task_id=task_id,
        filename="fix-report.md", content="# Fix\\nLower-case emails before compare.\\n",
    )
    await harness.tool(
        worker_turn, "mark_task_status", task_id=task_id, status="done",
        result_markdown="Fixed: emails are lower-cased before comparison.",
    )
    return {**flow, "worker_turn": worker_turn, "artifact": published["artifacts"][0]}


@pytest.mark.req("INB-03")
async def test_execution_artifact_is_hidden_from_non_members(harness):
    done = await _finish_work(harness)
    url = done["artifact"]["url"]

    assert (await harness.client.get(url, headers=harness.user())).status_code == 200
    denied = await harness.client.get(url, headers=harness.user("outsider"))
    assert denied.status_code in {403, 404}


@pytest.mark.req("EXE-08", "EXE-15")
async def test_lead_collects_child_result_and_completes_execution(harness):
    done = await _finish_work(harness)
    execution_id = done["execution"]["execution_id"]

    # The lead ends its planning turn; the finished child result wakes it
    # again in the operating room.
    await harness.end_turn(done["lead_turn"])
    lead_turn = await harness.deliver("lead")
    assert "emails are lower-cased" in json.dumps(lead_turn.frame)

    completed = await harness.tool(
        lead_turn, "complete_project_execution", execution_id=execution_id,
        summary="Login bug fixed: emails are normalized before comparison.",
    )
    assert completed["status"] == "completed"

    # The final report is posted in the operating room.
    async with harness.sessions() as db:
        reports = (await db.scalars(select(Message).where(
            Message.room_id == harness.ids["lead_room"],
            Message.content.contains("Login bug fixed"),
        ))).all()
    assert reports

    # A completed execution accepts no further tool calls, so a repeated
    # completion cannot rewrite the report.
    await harness.tool_error(
        lead_turn, "complete_project_execution", execution_id=execution_id,
        summary="A different summary",
    )
    async with harness.sessions() as db:
        assert (await db.scalar(select(func.count()).select_from(Message).where(
            Message.content.contains("A different summary")))) == 0


# -- QA-12 failure and retry --------------------------------------------------


@pytest.mark.req("EXE-10", "EXE-16")
async def test_failed_task_turn_can_be_retried_once_by_the_user(harness):
    flow = await harness.begin_and_delegate()
    task_id = flow["delegated"]["task_id"]
    worker_turn = await harness.deliver("worker")

    await harness.end_turn(worker_turn, outcome="failed")
    async with harness.sessions() as db:
        failed = await db.get(AgentTurn, worker_turn.request_id)
    assert failed.state == "failed"

    body = {
        "operation_id": str(uuid4()),
        "expected_input_revision": 1,
        "expected_request_id": worker_turn.request_id,
        "expected_attempt": worker_turn.attempt,
    }
    url = f"/api/v1/execution-tasks/{task_id}/retry"
    assert (await harness.client.post(url, json=body, headers=harness.user("outsider"))
            ).status_code in {403, 404}
    resp = await harness.client.post(url, json=body, headers=harness.user())
    assert resp.status_code == 200, resp.text
    # Replaying the same operation is idempotent rather than a second retry.
    replay = await harness.client.post(url, json=body, headers=harness.user())
    assert replay.status_code == 200, replay.text

    retried = await harness.deliver("worker")
    assert (retried.request_id, retried.attempt) != (worker_turn.request_id, worker_turn.attempt)
    assert retried.frame["metadata"]["task_assignment"]["task_id"] == task_id
    async with harness.sessions() as db:
        open_turns = (await db.scalars(select(AgentTurn).where(
            AgentTurn.task_id == task_id,
            AgentTurn.state.in_({"pending", "leased", "retrying"}),
        ))).all()
    assert len(open_turns) == 1


# -- QA-20 limits -------------------------------------------------------------


@pytest.mark.req("EXE-17")
async def test_delegation_limit_stops_further_delegation(harness):
    flow = await harness.begin_and_delegate(limits={"max_delegations": 1})
    execution = flow["execution"]

    error = await harness.tool_error(
        flow["lead_turn"], "delegate_project_task",
        execution_id=execution["execution_id"],
        parent_task_id=execution["root_task_id"],
        target_room_id=harness.ids["releaser_room"],
        assignee_participant_id=harness.ids["releaser_pid"],
        delegation_key="second", title="Second task", spec="Over the limit",
    )
    assert error
    async with harness.sessions() as db:
        count = await db.scalar(select(func.count()).select_from(Task).where(
            Task.execution_id == execution["execution_id"],
            Task.id != execution["root_task_id"],
        ))
    assert count == 1


@pytest.mark.req("EXE-17")
async def test_unsupported_limits_and_actions_are_refused_at_begin(harness):
    await harness.user_request("Do something risky")
    lead_turn = await harness.deliver("lead")

    error = await harness.tool_error(
        lead_turn, "begin_project_execution", objective="x",
        allowed_actions=["send_email"],
    )
    assert "send_email" in error


# -- F1: an answer that arrives before the asking turn ends -------------------


async def _ask_then_answer_early(harness: Harness, *, worker_blocks: bool):
    flow = await harness.begin_and_delegate()
    task_id = flow["delegated"]["task_id"]
    worker_turn = await harness.deliver("worker")
    question = await harness.tool(
        worker_turn, "request_project_input", task_id=task_id,
        question_key="release-date", question="What is the release date?",
    )
    # The user answers while the asking turn is still running.
    resp = await harness.client.post(
        f"/api/v1/execution-requests/{question['id']}/answer",
        json={"answer": "2026-10-20"}, headers=harness.user(),
    )
    assert resp.status_code == 200, resp.text
    if worker_blocks:
        # Live runtimes may report the waiting task as blocked before ending.
        stale = await harness.tool_error_or_ok(
            worker_turn, "mark_task_status", task_id=task_id, status="blocked",
            error="Waiting for the release date from the user",
        )
        # The answer already resumed the task in a newer turn.
        assert stale.get("isError"), stale
        assert "TASK_TURN_SUPERSEDED" in stale["content"][0]["text"]
    await harness.end_turn(worker_turn)
    return task_id


@pytest.mark.req("EXE-18")
@pytest.mark.parametrize("worker_blocks", [False, True])
async def test_early_answer_still_resumes_the_task(harness, worker_blocks):
    task_id = await _ask_then_answer_early(harness, worker_blocks=worker_blocks)

    resumed = await harness.deliver("worker")
    assert resumed.frame["metadata"]["task_assignment"]["task_id"] == task_id
    # The resumed turn owns the task and can finish it.
    await harness.tool(
        resumed, "mark_task_status", task_id=task_id, status="done",
        result_markdown="Release notes dated 2026-10-20.",
    )
    async with harness.sessions() as db:
        assert (await db.get(Task, task_id)).status == "done"


# -- F2: a worker's deliberate block is not an unknown failure ----------------


@pytest.mark.req("EXE-19")
async def test_worker_block_is_reported_as_task_blocked(harness):
    flow = await harness.begin_and_delegate()
    task_id = flow["delegated"]["task_id"]
    execution_id = flow["execution"]["execution_id"]
    worker_turn = await harness.deliver("worker")
    await harness.tool(
        worker_turn, "mark_task_status", task_id=task_id, status="blocked",
        error="Need the v2.4 change list; none was provided.",
    )

    tasks = await harness.client.get(
        f"/api/v1/rooms/{harness.ids['worker_room']}/tasks", headers=harness.user())
    row = next(t for t in tasks.json() if t["id"] == task_id)
    detail = await harness.client.get(f"/api/v1/executions/{execution_id}", headers=harness.user())
    detail_row = next(t for t in detail.json()["tasks"] if t["id"] == task_id)
    inbox = await harness.client.get("/api/v1/inbox", headers=harness.user())
    inbox_task = next(i["task"] for i in inbox.json()["items"]
                      if i.get("task", {}).get("id") == task_id)

    assert row["status"] == "blocked"
    assert row["error"] == detail_row["error"] == inbox_task["error"] == "TASK_BLOCKED"
    # The free text never leaks through the public error field.
    assert "change list" not in json.dumps([row["error"], detail_row["error"], inbox_task["error"]])


# -- F3: a rejected action returns control to the lead ------------------------


@pytest.mark.req("EXE-20")
async def test_rejection_wakes_the_lead_with_the_reason(harness, monkeypatch):
    portal = _Portal(monkeypatch)
    ready = await _ready_for_approval(harness)
    approval = await _request_approval(harness, ready)
    await harness.end_turn(ready["lead_turn"])

    assert (await _decide(harness, approval["id"], "reject")).status_code == 200

    # Earlier child outcomes (the finished release notes) are delivered to
    # the lead first, in order; the rejection notice follows.
    notices = []
    for _ in range(3):
        lead_turn = await harness.deliver("lead")
        notices.append(json.dumps(lead_turn.frame, ensure_ascii=False))
        if ready["action_task"] in notices[-1]:
            break
        await harness.end_turn(lead_turn)
    assert ready["action_task"] in notices[-1]
    assert "APPROVAL_REJECTED" in notices[-1]

    tasks = await harness.client.get(
        f"/api/v1/rooms/{harness.ids['releaser_room']}/tasks", headers=harness.user())
    action = next(t for t in tasks.json() if t["id"] == ready["action_task"])
    assert (action["status"], action["error"]) == ("blocked", "APPROVAL_REJECTED")
    assert portal.requests == []
