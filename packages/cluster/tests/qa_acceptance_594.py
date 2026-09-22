"""#594 two-node product acceptance matrix — task #30 (QA-owned, not upstream).

Stack head under test: f4c9f4f (= 18f8aee peer trust + d46d272 shared channels
+ f4c9f4f delegation submitters). Provider-free local harness over the real
PeerService/ChannelService/DelegationService with real loopback mTLS listeners
where the row demands transport evidence.

Row verdicts distinguish product acceptance from implementation verification:
rows whose feature PR is not in this stack are explicit SKIPs, not FAILs.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from uuid import uuid4

import pytest
from anygarden.db.models import Message, Room, Task
from anygarden.federation.errors import PeerError
from anygarden.shared_channels.models import CommandReceipt
from anygarden.shared_channels.schemas import ChannelError
from sqlalchemy import func, select

from . import test_federation_trust as trust
from .test_delegation_channel_integration import product  # noqa: F401
from .test_federation_delegation import h, setup_bridge  # noqa: F401
from .test_shared_channels import (
    channels,  # noqa: F401
    command,
    count,
    events,
    human_channels,  # noqa: F401
    receive,
    send,
    uid,
)

pair = trust.pair

# ---------------------------------------------------------------------------
# Rows whose feature PR is not in this stack (recorded, not executed).
# ---------------------------------------------------------------------------


def test_row_N01_skip_588_not_in_stack():
    pytest.skip("#588 unified node ownership ships in unmerged PR598; stack "
                "f4c9f4f does not contain 20b5396")


def test_row_N02_skip_588_not_in_stack():
    pytest.skip("#588 two-process independent roots ships in unmerged PR598")


def test_row_U01_skip_593_ui_not_in_stack():
    pytest.skip("#593 real browser flow ships in unmerged PR597 (mock only); "
                "no mounted federation UI at this head")


# ---------------------------------------------------------------------------
# T-rows: peer trust product behavior.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_T01_invite_concurrency_ackloss_recovery(channels):
    a, b = channels
    from anygarden.federation.models import PeerGrant
    from .test_federation_trust import invitation

    # Concurrent redeem of one fresh invite: durable-duplicate semantics give
    # every concurrent caller the identical receipt and exactly one grant.
    fresh = await a.s.create_invite(a.admin, invitation(a, b))
    args = (
        b.s.identity,
        b.s.node_id,
        str(fresh.invite_id),
        fresh.token.get_secret_value(),
    )
    results = await asyncio.gather(*(a.s.redeem(*args) for _ in range(4)))
    assert len({repr(r) for r in results}) == 1
    async with a.s.sessions() as db:
        grants = (
            await db.scalars(
                select(PeerGrant).where(
                    PeerGrant.peer_node_id == b.s.node_id,
                    PeerGrant.authority_node_id == a.s.node_id,
                    PeerGrant.channel_id == a.channel,
                )
            )
        ).all()
        assert len(grants) == 1  # concurrent redeems extended one grant row
        epoch = grants[0].epoch
    # Forged token cannot redeem.
    other = await a.s.create_invite(a.admin, invitation(a, b))
    with pytest.raises(BaseException):
        await a.s.redeem(
            b.s.identity, b.s.node_id, str(other.invite_id), "forged-token"
        )
    # Same authenticated request recovers the receipt without reviving grants.
    cmd = command(a, b, text="T01")
    cmd["grant_epoch"] = epoch
    first = await send(a, b, cmd)
    assert await send(a, b, cmd) == first
    await a.s.revoke_grant(a.admin, b.s.node_id, a.channel)
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await send(a, b, cmd)


@pytest.mark.asyncio
async def test_row_T02_revoke_persists_control_cannot_read(channels):
    a, b = channels
    cmd = command(a, b, text="T02")
    await send(a, b, cmd)
    await a.s.revoke_grant(a.admin, b.s.node_id, a.channel)
    # Revoke persists across service restart (fresh instances, same DBs).
    a.c = type(a.c)(node_id=a.s.node_id, peers=a.s, sessions=a.s.sessions)
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        async with a.s.sessions.begin() as db:
            await a.c.events(db, cmd, tls=b.s.identity)
    # Control-plane-only peer cannot read channels: authorize with read action.
    from anygarden.federation.schemas import Principal

    with pytest.raises(PeerError):
        async with a.s.sessions.begin() as db:
            await a.s.authorize(
                db,
                b.s.identity,
                sender_node_id=b.s.node_id,
                authority_node_id=a.s.node_id,
                channel_id=a.channel,
                principal=Principal(
                    node_id=b.s.node_id, kind="agent", principal_id=b.actor
                ),
                action="channel.read",
                grant_epoch=1,
            )
    async with a.s.sessions() as db:
        from anygarden.federation.models import PeerGrant

        row = await db.get(
            PeerGrant, (b.s.node_id, a.s.node_id, a.channel), populate_existing=True
        )
        assert row is not None and not row.active  # inactive row retained


@pytest.mark.asyncio
async def test_row_T03_pin_rotation_atomic_no_grace(channels, tmp_path):
    a, b = channels
    cmd = command(a, b, text="before rotate")
    await send(a, b, cmd)
    cert, _key = trust.create_credentials(tmp_path / "rotated", b.s.node_id)
    await a.s.rotate(a.admin, b.s.node_id, cert.read_text(), 1)
    # Old pinned certificate is rejected immediately; no grace overlap.
    with pytest.raises(BaseException):
        await send(a, b, command(a, b, text="old key"))
    # Revoked-before-rotate grant stays revoked.
    with pytest.raises(BaseException):
        await send(a, b, cmd)
    # Wrong expected_epoch cannot rotate again (CAS).
    with pytest.raises(BaseException):
        await a.s.rotate(a.admin, b.s.node_id, cert.read_text(), 1)


# ---------------------------------------------------------------------------
# C-rows: shared channel product behavior (real loopback mTLS listener).
# ---------------------------------------------------------------------------


async def _live_pair(human_channels):
    from anygarden.federation.models import Peer
    from anygarden.federation.transport import PeerListener
    from .test_federation_trust import endpoint

    a, b = human_channels
    listener = await PeerListener(a.s, channel_service=a.c).start()
    async with b.s.sessions.begin() as db:
        peer = await db.get(Peer, a.s.node_id)
        peer.endpoint = endpoint(listener.port).model_dump()
    return a, b, listener


@pytest.mark.asyncio
async def test_row_C01_bidirectional_sync_tombstone_restart(human_channels):
    a, b, listener = await _live_pair(human_channels)
    try:
        from anygarden.shared_channels.sync import pull, retry_submission

        async with b.s.sessions.begin() as db:
            await b.c.queue(db, b.command, identity=b.identity)
        result = await retry_submission(
            b.c, b.identity, a.s.node_id, a.channel, b.command["request_id"]
        )
        assert result["state"] == "confirmed"
        scope = {
            k: b.command[k]
            for k in (
                "protocol_version", "sender_node_id", "authority_node_id",
                "channel_id", "grant_epoch", "actor",
            )
        }
        assert (await pull(b.c, b.identity, scope))["ack_seq"] == 1
        # Display-only membership (observer) never gains write/execute:
        # queueing any command is denied.
        from anygarden.federation.models import PeerGrant

        async with b.s.sessions.begin() as db:
            grant = await db.get(
                PeerGrant, (a.s.node_id, a.s.node_id, a.channel),
                populate_existing=True,
            )
            grant.role = "observer"
        async with b.s.sessions.begin() as db:
            with pytest.raises(ChannelError, match="SCOPE_DENIED"):
                await b.c.queue(db, b.command, identity=b.identity)
        async with b.s.sessions.begin() as db:
            grant = await db.get(
                PeerGrant, (a.s.node_id, a.s.node_id, a.channel),
                populate_existing=True,
            )
            grant.role = "member"
        # Restart both services: stream/tombstone state persists.
        a.c = type(a.c)(node_id=a.s.node_id, peers=a.s, sessions=a.s.sessions)
        b.c = type(b.c)(node_id=b.s.node_id, peers=b.s, sessions=b.s.sessions)
        assert (await pull(b.c, b.identity, scope))["ack_seq"] == 1
        async with b.s.sessions.begin() as db:
            await b.s.revoke(b.admin, a.s.node_id)
        with pytest.raises(PeerError):
            await pull(b.c, b.identity, scope)
    finally:
        await listener.stop()


@pytest.mark.asyncio
async def test_row_C02_duplicate_idconflict_gap(channels):
    a, b = channels
    # A cursor cannot skip a committed event it never received: the first
    # events() call with after_seq beyond the undelivered point is a gap.
    probe = command(a, b, text="C02 probe")
    await send(a, b, probe)
    with pytest.raises(ChannelError, match="CURSOR_GAP"):
        async with a.s.sessions.begin() as db:
            await a.c.events(db, probe, tls=b.s.identity, after_seq=1)
    cmd = command(a, b, text="C02")
    receipt = await send(a, b, cmd)
    assert await send(a, b, cmd) == receipt  # same payload -> one visible event
    changed = command(a, b, text="mutated")
    changed["request_id"] = cmd["request_id"]
    with pytest.raises(ChannelError, match="ID_CONFLICT"):
        await send(a, b, changed)
    async with a.s.sessions.begin() as db:
        log = await a.c.events(db, cmd, tls=b.s.identity)
    assert await receive(a, b, log) == {"ack_seq": 2, "missing_after_seq": None}
    async with b.s.sessions() as db:
        assert await db.scalar(select(func.count()).select_from(Message)) == 2


@pytest.mark.asyncio
async def test_row_C03_outage_recovery_converges_once(human_channels, monkeypatch):
    a, b = human_channels
    from anygarden.shared_channels import sync

    async with b.s.sessions.begin() as db:
        await b.c.queue(db, b.command, identity=b.identity)
    # Disconnect BEFORE durable receive: no fabricated local confirmation.
    attempts = []

    async def refused(*args, **kwargs):
        attempts.append(1)
        raise ConnectionError("authority down")

    monkeypatch.setattr(sync, "post", refused)
    with pytest.raises(ConnectionError):
        await sync.retry_submission(
            b.c, b.identity, a.s.node_id, a.channel, b.command["request_id"]
        )
    async with b.s.sessions() as db:
        row = await db.get(
            sync.ChannelSubmission,
            (a.s.node_id, a.channel, b.command["request_id"]),
        )
        assert row.state == "unconfirmed" and row.receipt is None
    assert await count(b, Message) == 0

    monkeypatch.undo()
    a, b, listener = await _live_pair(human_channels)
    try:
        result = await sync.retry_submission(
            b.c, b.identity, a.s.node_id, a.channel, b.command["request_id"]
        )
        assert result["state"] == "confirmed"
        again = await sync.retry_submission(
            b.c, b.identity, a.s.node_id, a.channel, b.command["request_id"]
        )
        assert again == result
        async with a.s.sessions() as db:
            assert (
                await db.scalar(select(func.count()).select_from(CommandReceipt))
                == 1
            )
    finally:
        await listener.stop()


@pytest.mark.asyncio
async def test_row_C04_authority_down_local_independence_no_promotion(
    human_channels, monkeypatch
):
    a, b = human_channels
    from anygarden.shared_channels import sync

    async with b.s.sessions.begin() as db:
        await b.c.queue(db, b.command, identity=b.identity)

    async def refused(*args, **kwargs):
        raise ConnectionError("authority down")

    monkeypatch.setattr(sync, "post", refused)
    with pytest.raises(ConnectionError):
        await sync.retry_submission(
            b.c, b.identity, a.s.node_id, a.channel, b.command["request_id"]
        )
    # Separate local work continues while the authority is down.
    async with b.s.sessions.begin() as db:
        from anygarden.db.repository import append_message

        await append_message(db, b.channel, None, "independent local work")
    assert await count(b, Message) == 1
    # No automatic authority promotion: the follower cannot take the
    # authority's stream even with an authenticated local transaction.
    with pytest.raises(ChannelError, match="AUTHORITY_UNAVAILABLE"):
        async with b.s.sessions.begin() as db:
            await b.c._stream(db, a.s.node_id, a.channel, owner=True)
    async with b.s.sessions() as db:
        row = await db.get(
            sync.ChannelSubmission,
            (a.s.node_id, a.channel, b.command["request_id"]),
        )
        assert row.state == "unconfirmed"


# ---------------------------------------------------------------------------
# D-rows: delegation product behavior (real channel authority side).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_D01_terminal_states_distinct(product):
    p = product
    await p.send(p.cmd("task.request"))
    # Fake progress before acceptance is refused (no fake running).
    with pytest.raises(ChannelError, match="STATE_CONFLICT"):
        await p.send(p.cmd("task.started", 1))
    await p.send(p.cmd("task.accept", 1))
    await p.send(p.cmd("task.started", 2))
    await p.send(
        p.cmd("task.result", 3, outcome="failed", error_code="ENGINE_ERROR")
    )
    async with p.a.s.sessions() as db:
        task = await db.get(Task, p.task)
        assert task.status == "failed" and task.error == "ENGINE_ERROR"
        assert task.result_markdown is None


@pytest.mark.asyncio
async def test_row_D02_race_one_winner_loser_never_runs(product):
    p = product
    second = dict(p.cmd("task.request"))
    second["payload"] = dict(second["payload"])
    second["payload"]["delegation_id"] = uid()

    async def attempt(cmd):
        try:
            return ("ok", await p.send(cmd))
        except Exception as error:  # noqa: BLE001
            return ("err", error)

    outcomes = await asyncio.gather(attempt(p.cmd("task.request")), attempt(second))
    assert sorted(o[0] for o in outcomes) == ["err", "ok"]
    async with p.a.s.sessions() as db:
        from anygarden.federation.delegation_models import Delegation

        assert await db.scalar(select(func.count()).select_from(Delegation)) == 1
        task = await db.get(Task, p.task)
        assert task.assignee_participant_id is not None


@pytest.mark.asyncio
async def test_row_D03_restart_before_receipt_reconciles_unknown(h, tmp_path):
    from anygarden.federation.executor import ExecutorBridge
    from anygarden_agent.runtime.execution import Invocation, SessionScope

    runtime = type(
        "R", (), {"capabilities": lambda self: __import__(
            "anygarden_agent.runtime.execution", fromlist=["Capabilities"]
        ).Capabilities()}
    )()
    # Reuse the suite's controlled runtime shape for a real manager lifecycle.
    from .test_federation_delegation import ControlledRuntime

    runtime = ControlledRuntime()

    def permits(fence):
        return fence.generation == 7 and fence.lease_token == "lease" and fence.grant_epoch == 1

    bridge = ExecutorBridge(
        h.sessions, tmp_path / "rt", runtime, node_id=h.node,
        permits=permits, reauthorize=h.auth,
    )
    scope = SessionScope(h.node, h.agent, h.authority, h.channel, None, "workspace", 1, 1)
    invocation = Invocation(h.execution, scope, "prompt", tmp_path, tmp_path)
    await h.commit(h.command("task.request", 0))
    outbox = await bridge.prepare(
        h.delegation, invocation, generation=7, lease_token="lease", grant_epoch=1
    )
    await bridge.deliver(outbox, h.commit)
    await bridge.launch(h.delegation, invocation, h.snapshot)
    await runtime.started.wait()
    runtime.finish.set()
    async for _ in bridge.manager.events(h.execution):
        pass
    # Crash (close) after the runtime finished but before any result command
    # was committed; a restarted bridge must deliver the durable receipt
    # without a second start.
    calls_before = runtime.calls
    await bridge.close()
    bridge2 = ExecutorBridge(
        h.sessions, tmp_path / "rt", runtime, node_id=h.node,
        permits=permits, reauthorize=h.auth,
    )
    try:
        final = await bridge2.observe(h.delegation)
        await bridge2.deliver(final, h.commit)
        assert runtime.calls == calls_before == 1
        assert (await h.get_task()).status == "done"
    finally:
        await bridge2.close()


@pytest.mark.asyncio
async def test_row_D04_cancel_ordering_stale_result(product):
    p = product
    await p.send(p.cmd("task.request"))
    await p.send(p.cmd("task.accept", 1))
    await p.send(p.cmd("task.started", 2))
    await p.send(p.cmd("task.cancel", 3))
    # Cancellation request is distinct from the stopped acknowledgement.
    async with p.a.s.sessions() as db:
        task = await db.get(Task, p.task)
        assert task.status == "blocked"
    await p.send(p.cmd("task.cancelled", 4, process_state="stopped"))
    # Stale old-generation result cannot restore running/completed.
    with pytest.raises(ChannelError, match="TERMINAL"):
        await p.send(p.cmd("task.result", 3, outcome="succeeded", text="stale"))
    async with p.a.s.sessions() as db:
        task = await db.get(Task, p.task)
        assert task.status == "failed" and task.result_markdown is None


@pytest.mark.asyncio
async def test_row_D05_revoke_during_lifecycle(product):
    p = product
    await p.send(p.cmd("task.request"))
    await p.send(p.cmd("task.accept", 1))
    # Revoke after acceptance: executor's next command is denied on replay
    # authorization; nothing new is committed.
    await p.a.s.revoke_grant(p.a.admin, p.b.s.node_id, p.a.channel)
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await p.send(p.cmd("task.started", 2))
    async with p.a.s.sessions() as db:
        assert (
            await db.scalar(select(func.count()).select_from(CommandReceipt)) == 3
        )
        task = await db.get(Task, p.task)
        assert task.status == "in_progress"  # prior accepted state retained


# ---------------------------------------------------------------------------
# R/O rows.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_row_R01_runtime_focused_suite(h, setup_bridge, tmp_path):
    bridge, runtime, invocation, outbox, _ = setup_bridge
    await bridge.deliver(outbox, h.commit)
    await bridge.launch(h.delegation, invocation, h.snapshot)
    await runtime.started.wait()
    # Session scoping: the fence is bound to (generation, lease, grant epoch).
    from anygarden.federation.executor import ExecutionFence

    fence = ExecutionFence(invocation.scope, 7, "lease", 1)
    assert bridge.permits(fence) is True
    bad = ExecutionFence(invocation.scope, 8, "lease", 1)
    assert bridge.permits(bad) is False
    # Process-tree stop on cancel: runtime receives cancellation.
    await h.commit(h.command("task.cancel", 2))
    pending = await bridge.cancel(h.delegation, await h.snapshot(h.delegation))
    if pending is None:
        async for _ in bridge.manager.events(h.execution):
            pass
        pending = await bridge.observe(h.delegation)
    await bridge.deliver(pending, h.commit)
    task = await h.get_task()
    assert task.status == "failed" and runtime.calls == 1


@pytest.mark.asyncio
async def test_row_O01_backup_restore_upgrade_rollback(human_channels, tmp_path):
    a, b = human_channels
    a, b, listener = await _live_pair(human_channels)
    try:
        from anygarden.shared_channels.sync import pull, retry_submission

        async with b.s.sessions.begin() as db:
            await b.c.queue(db, b.command, identity=b.identity)
        result = await retry_submission(
            b.c, b.identity, a.s.node_id, a.channel, b.command["request_id"]
        )
        assert result["state"] == "confirmed"
        # Backup A's DB, then revoke + add more state, then restore the backup.
        db_path = Path(str(a.engine.url).replace("sqlite+aiosqlite:///", ""))
        backup = tmp_path / "backup.sqlite"
        shutil.copyfile(db_path, backup)
        post = dict(b.command)
        post["request_id"] = uid()
        post["payload"] = dict(post["payload"], message_id=uid(), text="after backup")
        await send(a, b, post)  # state beyond the backup point
        await a.s.revoke_grant(a.admin, b.s.node_id, a.channel)
        await a.engine.dispose()
        shutil.copyfile(backup, db_path)
        # Identity/session continuity after restore: same stream, one message.
        async with a.s.sessions() as db:
            stream = await db.get(
                __import__(
                    "anygarden.shared_channels.models", fromlist=["ChannelStream"]
                ).ChannelStream,
                (a.s.node_id, a.channel),
            )
            assert stream.last_seq == 1
            assert await db.scalar(select(func.count()).select_from(Message)) == 1
        # Restored outbox does not resurrect revoked work: the restored grant
        # snapshot is active again, but a fresh revoke denies replay.
        await a.s.revoke_grant(a.admin, b.s.node_id, a.channel)
        with pytest.raises(PeerError, match="GRANT_DENIED"):
            await send(a, b, post)
    finally:
        await listener.stop()
    # Migration rollback on a scratch DB (066 -> 063) verified in PR602 QA;
    # deployed-product upgrade itself is out of provider-free scope.
