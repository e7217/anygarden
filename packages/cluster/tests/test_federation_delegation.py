"""#592 DB/runtime integration with EXPLICIT #590/#591 transport/auth doubles.

The fake channel commits into file SQLite with independent connections and
validates the actual PR596 wire schemas. It is not peer/mTLS integration.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

import pytest
import pytest_asyncio
from anygarden_agent.runtime.execution import (
    Capabilities,
    Invocation,
    SessionScope,
)
from anygarden_agent.runtime.execution.contracts import RuntimeResult
from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy import (
    JSON,
    Column,
    Integer,
    String,
    Table,
    insert,
    select,
    text,
    update,
)

from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Message, Participant, Room, Task
from anygarden.federation.delegation import (
    DelegationError,
    DelegationService,
    LateResultAfterCancel,
)
from anygarden.federation.delegation_models import (
    Delegation,
    DelegationObservation,
    DelegationOutbox,
    ExecutorBinding,
)
from anygarden.federation.executor import AuthoritySnapshot, ExecutorBridge
from anygarden.shared_channels.models import ChannelStream, SharedMessage

ROOT = Path(__file__).resolve().parents[3]
VALIDATORS = [
    Draft202012Validator(
        json.loads((ROOT / "contracts/federation/v1" / name).read_text()),
        format_checker=FormatChecker(),
    )
    for name in ("envelope.schema.json", "receipt.schema.json")
]
# Distinct test-only tables: these do not represent product #590/#591 models.
from datetime import UTC

from sqlalchemy import MetaData

TEST_META = MetaData()
GRANT = Table(
    "test_peer_grant",
    TEST_META,
    Column("id", Integer, primary_key=True),
    Column("active", Integer),
)
RECEIPTS = Table(
    "test_channel_receipt",
    TEST_META,
    Column("id", String, primary_key=True),
    Column("body", String),
    Column("receipt", JSON),
)


def uid():
    return str(uuid4())


class Harness:
    def __init__(self, sessions):
        self.sessions = sessions
        self.authority, self.node, self.channel, self.human, self.agent = [
            uid() for _ in range(5)
        ]
        self.source, self.task, self.local_task, self.delegation, self.execution = [
            uid() for _ in range(5)
        ]
        self.requester = {
            "node_id": self.authority,
            "kind": "human",
            "principal_id": self.human,
        }
        self.executor = {
            "node_id": self.node,
            "kind": "agent",
            "principal_id": self.agent,
        }
        self.mapping = {self.human: uid(), self.agent: uid()}
        self.service = DelegationService(
            self.authority, self.resolve, executor_allowed=self.exported
        )
        self.local_source = uid()
        self.export_active = True

    async def exported(self, db, channel, executor):
        return self.export_active and executor == {
            "node_id": self.node,
            "agent_id": self.agent,
        }

    async def resolve(self, db, channel, principal):
        assert channel == self.channel
        if principal not in (self.requester, self.executor):
            return None
        return self.mapping[principal["principal_id"]]

    async def auth(self, db, envelope):
        if not await db.scalar(select(GRANT.c.active)):
            raise DelegationError("GRANT_DENIED")
        # Product auth is #590/#591; double tests exact authorization order only.

    async def commit(self, command):
        VALIDATORS[0].validate(command)
        async with self.sessions() as db:
            await db.execute(text("BEGIN IMMEDIATE"))
            await self.auth(db, command)
            await self.service.authorize_command(db, command)
            body = json.dumps(command, sort_keys=True)
            old = (
                await db.execute(
                    select(RECEIPTS).where(RECEIPTS.c.id == command["request_id"])
                )
            ).first()
            if old:
                if old.body != body:
                    raise DelegationError("ID_CONFLICT")
                return old.receipt
            effect = await self.service.apply_effect(db, command)
            count = len((await db.execute(select(RECEIPTS.c.id))).all())
            receipt = {
                k: command[k]
                for k in (
                    "protocol_version",
                    "request_id",
                    "authority_node_id",
                    "channel_id",
                )
            }
            receipt.update(event_id=uid(), seq=count + 1, **asdict(effect))
            VALIDATORS[1].validate(receipt)
            await db.execute(
                insert(RECEIPTS).values(
                    id=command["request_id"], body=body, receipt=receipt
                )
            )
            await db.commit()
            return receipt

    def command(self, kind, revision, **payload):
        actor = (
            self.requester if kind in {"task.request", "task.cancel"} else self.executor
        )
        body = {"delegation_id": self.delegation, "expected_revision": revision}
        if kind == "task.request":
            body.update(
                task_id=self.task,
                source_message_id=self.source,
                executor={"node_id": self.node, "agent_id": self.agent},
            )
        elif kind not in {"task.cancel", "task.reject"}:
            body["execution_id"] = self.execution
        body.update(payload)
        return {
            "protocol_version": 1,
            "request_id": uid(),
            "sender_node_id": actor["node_id"],
            "authority_node_id": self.authority,
            "channel_id": self.channel,
            "grant_epoch": 1,
            "actor": actor,
            "kind": kind,
            "payload": body,
        }

    async def snapshot(self, did):
        async with self.sessions() as db:
            await self.auth(db, {})
            d = await db.get(Delegation, did)
            return AuthoritySnapshot(
                d.authority_node_id,
                d.channel_id,
                d.id,
                d.execution_id,
                d.revision,
                d.state,
            )

    async def get_task(self):
        async with self.sessions() as db:
            return await db.get(Task, self.task)


@pytest_asyncio.fixture
async def h(tmp_path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path}/authority.sqlite")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(TEST_META.create_all)
    h = Harness(build_session_factory(engine))
    async with h.sessions() as db:
        db.add(Room(id=h.channel, name="shared"))
        await db.flush()
        db.add_all(
            [
                Participant(id=pid, room_id=h.channel, role="member")
                for pid in h.mapping.values()
            ]
        )
        await db.flush()
        db.add(Message(id=h.local_source, room_id=h.channel, content="work", seq=1))
        await db.flush()
        db.add(
            ChannelStream(
                authority_node_id=h.authority,
                channel_id=h.channel,
                local_room_id=h.channel,
            )
        )
        await db.flush()
        db.add(
            SharedMessage(
                authority_node_id=h.authority,
                channel_id=h.channel,
                message_id=h.source,
                local_message_id=h.local_source,
                thread_root_id=None,
                actor=h.requester,
                seq=1,
            )
        )
        await db.flush()
        db.add_all(
            [
                Task(
                    id=h.task,
                    room_id=h.channel,
                    source_message_id=h.local_source,
                    title="remote",
                ),
                Task(id=h.local_task, room_id=h.channel, title="local"),
            ]
        )
        await db.execute(insert(GRANT).values(id=1, active=1))
        await db.commit()
    yield h
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["succeeded", "failed"])
async def test_authority_request_accept_start_result_preserves_local_task(h, outcome):
    await h.commit(h.command("task.request", 0))
    await h.commit(h.command("task.accept", 1))
    await h.commit(h.command("task.started", 2))
    payload = (
        {"text": "done"} if outcome == "succeeded" else {"error_code": "ENGINE_ERROR"}
    )
    command = h.command("task.result", 3, outcome=outcome, **payload)
    receipt = await h.commit(command)
    assert await h.commit(command) == receipt
    task = await h.get_task()
    assert task.status == ("done" if outcome == "succeeded" else "failed")
    assert task.result_markdown == ("done" if outcome == "succeeded" else None)
    async with h.sessions() as db:
        local = await db.get(Task, h.local_task)
        assert local.status == "todo" and local.assignee_participant_id is None


@pytest.mark.asyncio
async def test_independent_connections_compete_for_one_task(h):
    first, second = (
        h.command("task.request", 0),
        h.command("task.request", 0, delegation_id=uid()),
    )
    result = await asyncio.gather(
        h.commit(first), h.commit(second), return_exceptions=True
    )
    assert sum(isinstance(r, dict) for r in result) == 1
    assert [r.code for r in result if isinstance(r, DelegationError)] == [
        "CLAIM_CONFLICT"
    ]
    async with h.sessions() as db:
        assert len((await db.scalars(select(Delegation))).all()) == 1


@pytest.mark.asyncio
async def test_reject_releases_and_second_request_can_reserve(h):
    await h.commit(h.command("task.request", 0))
    await h.commit(h.command("task.reject", 1, reason="UNAVAILABLE"))
    assert (await h.get_task()).assignee_participant_id is None
    h.delegation = uid()
    assert (await h.commit(h.command("task.request", 0)))["state"] == "requested"


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["role", "archive", "task", "execution", "revision"])
async def test_stale_terminal_cannot_change_task(h, change):
    await h.commit(h.command("task.request", 0))
    await h.commit(h.command("task.accept", 1))
    command = h.command("task.result", 2, outcome="succeeded", text="forbidden")
    async with h.sessions() as db:
        if change == "role":
            await db.execute(
                update(Participant)
                .where(Participant.id == h.mapping[h.agent])
                .values(role="observer")
            )
        elif change == "archive":
            from datetime import datetime

            await db.execute(update(Room).values(archived_at=datetime.now(UTC)))
        elif change == "task":
            await db.execute(
                update(Task).where(Task.id == h.task).values(status="blocked")
            )
        elif change == "execution":
            command["payload"]["execution_id"] = uid()
        else:
            command["payload"]["expected_revision"] = 1
        await db.commit()
    with pytest.raises(DelegationError):
        await h.commit(command)
    assert (await h.get_task()).result_markdown is None
    async with h.sessions() as db:
        assert (await db.get(Delegation, h.delegation)).state == "accepted"


@pytest.mark.asyncio
async def test_revoke_commits_before_duplicate_and_late_audit(h):
    command = h.command("task.request", 0)
    await h.commit(command)
    # A different connection commits revocation before the new operation reads.
    async with h.sessions() as db:
        await db.execute(update(GRANT).values(active=0))
        await db.commit()
    with pytest.raises(DelegationError, match="GRANT_DENIED"):
        await h.commit(command)


@pytest.mark.asyncio
async def test_cancel_late_result_separate_bounded_audit(h):
    await h.commit(h.command("task.request", 0))
    await h.commit(h.command("task.accept", 1))
    await h.commit(h.command("task.cancel", 2))
    command = h.command("task.result", 2, outcome="succeeded", text="PRIVATE RESULT")
    with pytest.raises(LateResultAfterCancel):
        await h.commit(command)
    for expected in (True, False):
        async with h.sessions() as db:
            await db.execute(text("BEGIN IMMEDIATE"))
            assert (
                await h.service.record_late_result(db, command, reauthorize=h.auth)
                is expected
            )
            await db.commit()
    async with h.sessions() as db:
        audit = (await db.scalars(select(DelegationObservation))).one()
        assert audit.reason == "LATE_RESULT_AFTER_CANCEL" and not hasattr(audit, "text")
        assert len((await db.execute(select(RECEIPTS))).all()) == 3
        await db.execute(update(GRANT).values(active=0))
        await db.commit()
    async with h.sessions() as db:
        with pytest.raises(DelegationError, match="GRANT_DENIED"):
            await h.service.record_late_result(db, command, reauthorize=h.auth)
    assert (await h.get_task()).status == "blocked"


@pytest.mark.asyncio
async def test_unknown_blocks_automatic_result_and_cancel_needs_stop_proof(h):
    await h.commit(h.command("task.request", 0))
    await h.commit(h.command("task.accept", 1))
    await h.commit(h.command("task.unknown", 2))
    with pytest.raises(DelegationError, match="RECONCILE_REQUIRED"):
        await h.commit(h.command("task.result", 3, outcome="succeeded", text="no"))
    await h.commit(h.command("task.cancel", 3))
    result = await h.commit(h.command("task.cancelled", 4, process_state="stopped"))
    assert result["state"] == "cancelled" and (await h.get_task()).status == "failed"


class ControlledRuntime:
    def __init__(self):
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.calls = 0
        self.writes = 0

    def capabilities(self):
        return Capabilities()

    async def run(self, invocation, session_handle, emit, launched, authorized):
        self.calls += 1
        try:
            if not authorized():
                return RuntimeResult("cancelled", "not_started", "denied")
            self.writes += 1
            launched(1234)
            self.started.set()
            await self.finish.wait()
            return RuntimeResult("succeeded", "finished", "ok", text="done")
        except asyncio.CancelledError:
            return RuntimeResult("cancelled", "stopped", "cancelled")


@pytest_asyncio.fixture
async def setup_bridge(h, tmp_path):
    runtime = ControlledRuntime()
    allowed = {"generation": 7, "lease": "lease", "grant": 1}

    def permits(fence):
        return (
            fence.generation == allowed["generation"]
            and fence.lease_token == allowed["lease"]
            and fence.grant_epoch == allowed["grant"]
        )

    bridge = ExecutorBridge(
        h.sessions,
        tmp_path / "runtime",
        runtime,
        node_id=h.node,
        permits=permits,
        reauthorize=h.auth,
    )
    scope = SessionScope(
        h.node, h.agent, h.authority, h.channel, None, "workspace", 1, 1
    )
    invocation = Invocation(h.execution, scope, "prompt", tmp_path, tmp_path)
    await h.commit(h.command("task.request", 0))
    outbox = await bridge.prepare(
        h.delegation, invocation, generation=7, lease_token="lease", grant_epoch=1
    )
    yield bridge, runtime, invocation, outbox, allowed
    await bridge.close()


@pytest.mark.asyncio
async def test_accept_ack_loss_retries_same_body_without_execution(h, setup_bridge):
    bridge, runtime, invocation, outbox, _ = setup_bridge
    sent = []

    async def lost_ack(command):
        sent.append(command)
        await h.commit(command)
        raise ConnectionError("ack lost")

    with pytest.raises(ConnectionError):
        await bridge.deliver(outbox, lost_ack)
    with pytest.raises(DelegationError, match="LAUNCH_ALREADY_CLAIMED"):
        await bridge.launch(h.delegation, invocation, h.snapshot)
    receipt = await bridge.deliver(outbox, h.commit)
    assert receipt["state"] == "accepted" and runtime.calls == 0
    async with h.sessions() as db:
        row = await db.get(DelegationOutbox, outbox)
        assert sent[0] == row.command and row.state == "delivered"
    await bridge.launch(h.delegation, invocation, h.snapshot)
    await runtime.started.wait()
    runtime.finish.set()
    async for _ in bridge.manager.events(h.execution):
        pass
    terminal = await bridge.observe(h.delegation)
    await bridge.deliver(terminal, h.commit)
    assert (await h.get_task()).status == "done" and runtime.calls == 1


@pytest.mark.asyncio
async def test_started_then_success_and_duplicate_launch_fenced(h, setup_bridge):
    bridge, runtime, invocation, outbox, _ = setup_bridge
    await bridge.deliver(outbox, h.commit)
    await bridge.launch(h.delegation, invocation, h.snapshot)
    await runtime.started.wait()
    with pytest.raises(DelegationError, match="LAUNCH_ALREADY_CLAIMED"):
        await bridge.launch(h.delegation, invocation, h.snapshot)
    started = await bridge.observe(h.delegation)
    await bridge.deliver(started, h.commit)
    runtime.finish.set()
    async for _ in bridge.manager.events(h.execution):
        pass
    final = await bridge.observe(h.delegation)
    await bridge.deliver(final, h.commit)
    assert runtime.calls == 1 and (await h.get_task()).status == "done"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["generation", "lease", "grant"])
async def test_current_local_fence_before_launch_and_duplicate(h, setup_bridge, field):
    bridge, runtime, invocation, outbox, allowed = setup_bridge
    await bridge.deliver(outbox, h.commit)
    allowed[field] = "revoked"
    with pytest.raises(DelegationError, match="LOCAL_FENCE_DENIED"):
        await bridge.launch(h.delegation, invocation, h.snapshot)
    with pytest.raises(DelegationError, match="LOCAL_FENCE_DENIED"):
        await bridge.deliver(outbox, h.commit)
    assert runtime.calls == 0


@pytest.mark.asyncio
async def test_runtime_input_rechecks_generation_after_launch_commit(h, setup_bridge):
    bridge, runtime, invocation, outbox, allowed = setup_bridge
    await bridge.deliver(outbox, h.commit)
    await bridge.launch(h.delegation, invocation, h.snapshot)
    allowed["generation"] = 8
    # No yield occurred after launch's manager.start accepted the queued task.
    await asyncio.sleep(0)
    assert runtime.writes == 0
    await bridge.revoke(invocation.scope)


@pytest.mark.asyncio
@pytest.mark.parametrize("launch", [False, True])
async def test_cancel_never_publishes_completion(h, setup_bridge, launch):
    bridge, runtime, invocation, outbox, _ = setup_bridge
    await bridge.deliver(outbox, h.commit)
    if launch:
        await bridge.launch(h.delegation, invocation, h.snapshot)
        await runtime.started.wait()
    await h.commit(h.command("task.cancel", 2))
    pending = await bridge.cancel(h.delegation, await h.snapshot(h.delegation))
    if pending is None:
        async for _ in bridge.manager.events(h.execution):
            pass
        pending = await bridge.observe(h.delegation)
    assert pending is not None
    await bridge.deliver(pending, h.commit)
    task = await h.get_task()
    assert task.status == "failed" and task.result_markdown is None
    assert runtime.calls == int(launch)


@pytest.mark.asyncio
async def test_recover_launch_intent_without_receipt_is_unknown_never_spawn(
    h, setup_bridge, tmp_path
):
    bridge, runtime, invocation, outbox, allowed = setup_bridge
    await bridge.deliver(outbox, h.commit)
    async with h.sessions() as db:
        await db.execute(update(ExecutorBinding).values(local_state="launch_intent"))
        await db.commit()
    await bridge.close()
    recovered = ExecutorBridge(
        h.sessions,
        tmp_path / "runtime",
        runtime,
        node_id=h.node,
        permits=lambda f: f.generation == allowed["generation"],
        reauthorize=h.auth,
    )
    try:
        pending = await recovered.observe(h.delegation)
        await recovered.deliver(pending, h.commit)
        with pytest.raises(DelegationError):
            await recovered.launch(h.delegation, invocation, h.snapshot)
        assert runtime.calls == 0 and (await h.get_task()).status == "blocked"
    finally:
        await recovered.close()


@pytest.mark.asyncio
async def test_wrong_snapshot_and_changed_invocation_cannot_launch(h, setup_bridge):
    bridge, runtime, invocation, outbox, _ = setup_bridge
    await bridge.deliver(outbox, h.commit)

    async def stale(did):
        return replace(await h.snapshot(did), execution_id=uid())

    with pytest.raises(DelegationError, match="STALE_AUTHORITY"):
        await bridge.launch(h.delegation, invocation, stale)
    with pytest.raises(DelegationError, match="BINDING_CONFLICT"):
        await bridge.launch(
            h.delegation, replace(invocation, prompt="different"), h.snapshot
        )
    assert runtime.calls == 0


@pytest.mark.asyncio
async def test_role_removal_denies_duplicate_receipt(h):
    command = h.command("task.request", 0)
    await h.commit(command)
    async with h.sessions() as db:
        await db.execute(
            update(Participant)
            .where(Participant.id == h.mapping[h.human])
            .values(role="observer")
        )
        await db.commit()
    with pytest.raises(DelegationError, match="PRINCIPAL_DENIED"):
        await h.commit(command)


@pytest.mark.asyncio
async def test_revoke_wins_while_request_waits_on_independent_connection(h):
    async with h.sessions() as revoker:
        await revoker.execute(text("BEGIN IMMEDIATE"))
        await revoker.execute(update(GRANT).values(active=0))
        pending = asyncio.create_task(h.commit(h.command("task.request", 0)))
        await asyncio.sleep(0.05)
        assert not pending.done()
        await revoker.commit()
    with pytest.raises(DelegationError, match="GRANT_DENIED"):
        await pending
    assert (await h.get_task()).assignee_participant_id is None


@pytest.mark.asyncio
async def test_duplicate_cancel_before_launch_keeps_not_started_proof(h, setup_bridge):
    bridge, runtime, _invocation, outbox, _ = setup_bridge
    await bridge.deliver(outbox, h.commit)
    await h.commit(h.command("task.cancel", 2))
    snapshot = await h.snapshot(h.delegation)
    pending = await bridge.cancel(h.delegation, snapshot)
    assert await bridge.cancel(h.delegation, snapshot) == pending
    result = await bridge.deliver(pending, h.commit)
    assert result["process_state"] == "not_started" and runtime.calls == 0


@pytest.mark.asyncio
async def test_cancel_supersedes_unacknowledged_completed_outbox(h, setup_bridge):
    bridge, runtime, invocation, outbox, _ = setup_bridge
    await bridge.deliver(outbox, h.commit)
    await bridge.launch(h.delegation, invocation, h.snapshot)
    await runtime.started.wait()
    runtime.finish.set()
    async for _ in bridge.manager.events(h.execution):
        pass
    terminal = await bridge.observe(h.delegation)
    await h.commit(h.command("task.cancel", 2))
    pending = await bridge.cancel(h.delegation, await h.snapshot(h.delegation))
    with pytest.raises(DelegationError, match="OUTBOX_SUPERSEDED"):
        await bridge.deliver(terminal, h.commit)
    result = await bridge.deliver(pending, h.commit)
    assert (
        result["state"] == "cancelled" and (await h.get_task()).result_markdown is None
    )


@pytest.mark.asyncio
async def test_restart_reuses_terminal_runtime_receipt_without_second_start(
    h, setup_bridge, tmp_path
):
    bridge, runtime, invocation, outbox, _ = setup_bridge
    await bridge.deliver(outbox, h.commit)
    await bridge.launch(h.delegation, invocation, h.snapshot)
    await runtime.started.wait()
    runtime.finish.set()
    async for _ in bridge.manager.events(h.execution):
        pass
    await bridge.close()
    recovered = ExecutorBridge(
        h.sessions,
        tmp_path / "runtime",
        runtime,
        node_id=h.node,
        permits=lambda f: f.generation == 7 and f.lease_token == "lease",
        reauthorize=h.auth,
    )
    try:
        result = await recovered.observe(h.delegation)
        await recovered.deliver(result, h.commit)
        assert runtime.calls == 1 and (await h.get_task()).status == "done"
    finally:
        await recovered.close()


@pytest.mark.asyncio
async def test_migration_upgrade_downgrade_matches_only_owned_tables(h):
    # Direct operations validate #066 without pretending missing #064/#065 ran.
    import importlib.util

    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = (
        ROOT
        / "packages/cluster/anygarden/db/migrations/versions/066_remote_delegation.py"
    )
    spec = importlib.util.spec_from_file_location("delegation_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    names = {
        "federation_delegation_mirrors",
        "federation_delegations",
        "federation_delegation_reservations",
        "federation_delegation_observations",
        "federation_executor_bindings",
        "federation_delegation_outbox",
    }
    async with h.sessions() as db:
        conn = await db.connection()

        def check(sync):
            context = MigrationContext.configure(
                sync,
                opts={
                    "include_object": lambda obj, name, kind, reflected, other: (
                        name in names if kind == "table" else True
                    )
                },
            )
            with Operations.context(context):
                migration.downgrade()
                migration.upgrade()
            assert compare_metadata(context, Base.metadata) == []

        await conn.run_sync(check)
        await db.commit()
    assert (await h.get_task()).status == "todo"


@pytest.mark.asyncio
async def test_cancel_before_accept_has_not_started_confirmation(h, setup_bridge):
    bridge, runtime, _invocation, outbox, _ = setup_bridge
    await h.commit(h.command("task.cancel", 1))
    pending = await bridge.cancel(h.delegation, await h.snapshot(h.delegation))
    with pytest.raises(DelegationError, match="OUTBOX_SUPERSEDED"):
        await bridge.deliver(outbox, h.commit)
    result = await bridge.deliver(pending, h.commit)
    assert result["process_state"] == "not_started" and runtime.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", 99),
        ("task_status", "done"),
        ("process_state", "running"),
        ("event_id", "invalid"),
    ],
)
async def test_invalid_ack_cannot_authorize_launch(h, setup_bridge, field, value):
    bridge, runtime, invocation, outbox, _ = setup_bridge

    async def corrupt(command):
        return {**await h.commit(command), field: value}

    with pytest.raises(DelegationError, match="INVALID_RECEIPT"):
        await bridge.deliver(outbox, corrupt)
    with pytest.raises(DelegationError, match="LAUNCH_ALREADY_CLAIMED"):
        await bridge.launch(h.delegation, invocation, h.snapshot)
    assert runtime.calls == 0
    # The proper ACK for the same request can still be recovered.
    assert (await bridge.deliver(outbox, h.commit))["state"] == "accepted"
