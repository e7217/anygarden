"""Durable channel tests using two real SQLite DBs and product PeerService.

Peer calls below are direct authenticated-boundary injections, not a claim of
network/TLS coverage; the transport integration test is named separately.
"""

from __future__ import annotations

import asyncio
import copy
from uuid import uuid4

import pytest
from anygarden.db.models import Message, Room
from anygarden.federation.errors import PeerError
from anygarden.shared_channels.models import ChannelEvent, CommandReceipt, InboxEvent
from anygarden.shared_channels.schemas import ChannelError, parse_json
from anygarden.shared_channels.service import ChannelService
from sqlalchemy import func, select

from .test_federation_trust import admit, pair  # noqa: F401


def uid():
    return str(uuid4())


@pytest.fixture
async def channels(pair):  # noqa: F811
    a, b = pair
    await admit(a, b)
    mirror = uid()
    for n in (a, b):
        n.c = ChannelService(node_id=n.s.node_id, peers=n.s, sessions=n.s.sessions)
    async with a.s.sessions.begin() as db:
        await a.c.bind(
            db,
            actor_id=a.admin,
            authority_node_id=a.s.node_id,
            channel_id=a.channel,
            local_room_id=a.channel,
        )
    async with b.s.sessions.begin() as db:
        db.add(Room(id=mirror, name="mirror"))
        await db.flush()
        await b.c.bind(
            db,
            actor_id=b.admin,
            authority_node_id=a.s.node_id,
            channel_id=a.channel,
            local_room_id=mirror,
        )
    b.mirror = mirror
    return a, b


def command(a, b, text="shared", root=None):
    return {
        "protocol_version": 1,
        "request_id": uid(),
        "sender_node_id": b.s.node_id,
        "authority_node_id": a.s.node_id,
        "channel_id": a.channel,
        "grant_epoch": 1,
        "actor": {"node_id": b.s.node_id, "kind": "agent", "principal_id": b.actor},
        "kind": "message.send",
        "payload": {"message_id": uid(), "thread_root_id": root, "text": text},
    }


async def send(a, b, cmd, effect=None):
    async with a.s.sessions.begin() as db:
        return await a.c.commit_command(db, cmd, effect, tls=b.s.identity)


async def events(a, b, cmd, after=0):
    async with a.s.sessions.begin() as db:
        return await a.c.events(db, cmd, tls=b.s.identity, after_seq=after)


async def receive(a, b, items):
    async with b.s.sessions.begin() as db:
        return await b.c.receive(
            db,
            items,
            tls=a.s.identity,
            authority_node_id=a.s.node_id,
            channel_id=a.channel,
            grant_epoch=1,
        )


async def count(node, model):
    async with node.s.sessions() as db:
        return await db.scalar(select(func.count()).select_from(model))


async def test_durable_concurrent_command_receipt_and_cross_node_projection(channels):
    a, b = channels
    cmd = command(a, b)
    receipts = await asyncio.gather(*(send(a, b, cmd) for _ in range(6)))
    assert all(r == receipts[0] for r in receipts)
    assert (
        await count(a, Message)
        == await count(a, CommandReceipt)
        == await count(a, ChannelEvent)
        == 1
    )
    a.c = ChannelService(node_id=a.s.node_id, peers=a.s, sessions=a.s.sessions)
    assert await send(a, b, dict(reversed(list(cmd.items())))) == receipts[0]
    log = await events(a, b, cmd)
    assert (await receive(a, b, log))["ack_seq"] == 1
    b.c = ChannelService(node_id=b.s.node_id, peers=b.s, sessions=b.s.sessions)
    assert (await receive(a, b, log))["ack_seq"] == 1
    assert await count(b, Message) == 1
    async with a.s.sessions() as da, b.s.sessions() as db:
        ma = (await da.scalars(select(Message))).one()
        mb = (await db.scalars(select(Message))).one()
        assert ma.id != mb.id and ma.content == mb.content
        assert ma.extra_metadata == mb.extra_metadata


async def test_gap_reordered_replay_lost_ack_and_thread_root(channels):
    a, b = channels
    root = command(a, b, "root")
    reply = command(a, b, "reply", root["payload"]["message_id"])
    await send(a, b, root)
    await send(a, b, reply)
    log = await events(a, b, root)
    assert await receive(a, b, [log[1]]) == {"ack_seq": 0, "missing_after_seq": 0}
    assert await count(b, Message) == 0
    assert await receive(a, b, [log[0]]) == {"ack_seq": 2, "missing_after_seq": None}
    assert (await receive(a, b, log))["ack_seq"] == 2
    async with b.s.sessions() as db:
        msgs = list((await db.scalars(select(Message).order_by(Message.seq))).all())
        assert msgs[1].root_message_id == msgs[0].id
    async with a.s.sessions.begin() as db:
        assert await a.c.ack(db, root, tls=b.s.identity, seq=2) == {"ack_seq": 2}
    async with a.s.sessions.begin() as db:
        assert await a.c.ack(db, root, tls=b.s.identity, seq=1) == {"ack_seq": 2}


async def test_changed_request_body_and_event_identity_conflicts(channels):
    a, b = channels
    cmd = command(a, b)
    await send(a, b, cmd)
    changed = copy.deepcopy(cmd)
    changed["payload"]["text"] = "changed"
    with pytest.raises(ChannelError, match="ID_CONFLICT"):
        await send(a, b, changed)
    log = await events(a, b, cmd)
    await receive(a, b, log)
    for change in ("body", "seq", "id"):
        item = copy.deepcopy(log[0])
        if change == "body":
            item["request"]["payload"]["text"] = "different"
        elif change == "seq":
            item["seq"] = item["receipt"]["seq"] = 2
        else:
            item["event_id"] = item["receipt"]["event_id"] = uid()
        with pytest.raises(ChannelError, match="EVENT_INTEGRITY"):
            await receive(a, b, [item])
    assert await count(b, Message) == 1


async def test_effect_exception_rolls_back_message_receipt_sequence_outbox(channels):
    a, b = channels
    cmd = command(a, b)

    async def fail(db, body):
        await a.c.apply_message(db, body)
        raise RuntimeError("simulated crash before receipt")

    with pytest.raises(RuntimeError):
        await send(a, b, cmd, fail)
    assert (
        await count(a, Message)
        == await count(a, CommandReceipt)
        == await count(a, ChannelEvent)
        == 0
    )
    assert (await send(a, b, cmd))["seq"] == 1


async def test_revocation_checked_before_receipt_and_replay(channels):
    a, b = channels
    cmd = command(a, b)
    await send(a, b, cmd)
    log = await events(a, b, cmd)
    await a.s.revoke_grant(a.admin, b.s.node_id, a.channel)
    with pytest.raises(PeerError):
        await send(a, b, cmd)
    with pytest.raises(PeerError):
        await events(a, b, cmd)
    await b.s.revoke(b.admin, a.s.node_id)
    with pytest.raises(PeerError):
        await receive(a, b, log)
    assert await count(b, Message) == 0


async def test_separate_connection_revoke_waits_for_authorized_effect_commit(channels):
    a, b = channels
    cmd = command(a, b)
    entered, release = asyncio.Event(), asyncio.Event()
    order = []

    async def paused(db, body):
        entered.set()
        await release.wait()
        return await a.c.apply_message(db, body)

    async def writer():
        result = await send(a, b, cmd, paused)
        order.append("effect_committed")
        return result

    async def revoke():
        await a.s.revoke_grant(a.admin, b.s.node_id, a.channel)
        order.append("revoked")

    writing = asyncio.create_task(writer())
    await asyncio.wait_for(entered.wait(), 2)
    revoking = asyncio.create_task(revoke())
    await asyncio.sleep(0.1)
    assert not revoking.done()
    release.set()
    await asyncio.gather(writing, revoking)
    assert order == ["effect_committed", "revoked"]
    with pytest.raises(PeerError):
        await send(a, b, command(a, b))


@pytest.mark.parametrize("raw", ['{"a":1,"a":2}', '{"a":1.0}', '{"a":NaN}'])
def test_canonical_rejects_ambiguous_json(raw):
    with pytest.raises(ChannelError):
        parse_json(raw)


async def participant(a, b, *, active, revision, operation_id=None):
    async with a.s.sessions.begin() as db:
        return await a.c.change_participant(
            db,
            actor_id=a.admin,
            channel_id=a.channel,
            operation_id=operation_id or uid(),
            principal=command(a, b)["actor"],
            role="member",
            active=active,
            expected_revision=revision,
        )


async def test_participant_mixed_stream_tombstone_revision_and_consent(channels):
    from anygarden.shared_channels.models import SharedParticipant

    a, b = channels
    actor = command(a, b)["actor"]
    with pytest.raises(ChannelError, match="PUBLICATION_DENIED"):
        await participant(a, b, active=True, revision=0)
    async with a.s.sessions.begin() as db:
        await a.c.publication(
            db, actor_id=a.admin, channel_id=a.channel, principal=actor, active=True
        )
    op = uid()
    add = await participant(a, b, active=True, revision=0, operation_id=op)
    assert await participant(a, b, active=True, revision=0, operation_id=op) == add
    cmd = command(a, b)
    assert (await send(a, b, cmd))["seq"] == 2
    async with a.s.sessions.begin() as db:
        await a.c.publication(
            db, actor_id=a.admin, channel_id=a.channel, principal=actor, active=False
        )
    with pytest.raises(ChannelError, match="PUBLICATION_DENIED"):
        await participant(a, b, active=True, revision=0, operation_id=op)
    removed = await participant(a, b, active=False, revision=1)
    assert removed["seq"] == 3
    with pytest.raises(ChannelError, match="PARTICIPANT_REVISION_CONFLICT"):
        await participant(a, b, active=False, revision=0)
    log = await events(a, b, cmd)
    assert (await receive(a, b, list(reversed(log))))["ack_seq"] == 3
    assert (await receive(a, b, [add]))["ack_seq"] == 3
    async with b.s.sessions() as db:
        row = (await db.scalars(select(SharedParticipant))).one()
        assert row.active is False and row.revision == 2
    assert await count(b, Message) == 1


async def test_inbox_projection_failure_rolls_back_batch_and_cursor(channels):
    a, b = channels
    cmd = command(a, b)
    await send(a, b, cmd)
    log = await events(a, b, cmd)
    original = b.c.project_message

    async def fail(db, stream, event):
        await original(db, stream, event)
        raise RuntimeError("projection crash")

    b.c.projections["message.send"] = fail
    with pytest.raises(RuntimeError):
        await receive(a, b, log)
    assert await count(b, InboxEvent) == await count(b, Message) == 0
    b.c.projections["message.send"] = original
    assert (await receive(a, b, log))["ack_seq"] == 1


async def test_revoke_first_denies_effect_and_ack_jump(channels):
    a, b = channels
    cmd = command(a, b)
    await send(a, b, cmd)
    with pytest.raises(ChannelError, match="CURSOR_GAP"):
        await events(a, b, cmd, after=1)
    async with a.s.sessions.begin() as db:
        with pytest.raises(ChannelError, match="CURSOR_AHEAD"):
            await a.c.ack(db, cmd, tls=b.s.identity, seq=1)
    from anygarden.federation.models import PeerGrant
    from sqlalchemy import update

    locked, release = asyncio.Event(), asyncio.Event()

    async def revoke():
        async with a.s.sessions.begin() as db:
            await a.s.lock_peer(db, b.s.node_id)
            await db.execute(update(PeerGrant).values(active=False))
            locked.set()
            await release.wait()

    revoking = asyncio.create_task(revoke())
    await locked.wait()
    writing = asyncio.create_task(send(a, b, command(a, b)))
    await asyncio.sleep(0.1)
    assert not writing.done()
    release.set()
    await revoking
    with pytest.raises(PeerError):
        await writing
    assert await count(a, Message) == 1


@pytest.fixture
async def human_channels(pair):  # noqa: F811
    from anygarden.auth.dependencies import Identity
    from anygarden.auth.jwt import UserClaims
    from anygarden.federation.schemas import InviteAccept, Principal

    from .test_federation_trust import endpoint, invitation

    a, b = pair
    body = invitation(a, b)
    body.scopes[0].actors = [
        Principal(node_id=b.s.node_id, kind="human", principal_id=b.admin)
    ]
    bundle = await a.s.create_invite(a.admin, body)
    receipt = await a.s.redeem(
        b.s.identity,
        b.s.node_id,
        str(bundle.invite_id),
        bundle.token.get_secret_value(),
    )
    acceptance = InviteAccept(bundle=bundle, issuer_endpoint=endpoint())
    await b.s.begin_accept(b.admin, acceptance)
    await b.s.finish_accept(b.admin, acceptance, receipt)
    for n in (a, b):
        n.c = ChannelService(node_id=n.s.node_id, peers=n.s, sessions=n.s.sessions)
        n.identity = Identity(
            "user", n.admin, UserClaims(n.admin, "test@example.test", True)
        )
    b.mirror = uid()
    async with a.s.sessions.begin() as db:
        await a.c.bind(
            db,
            actor_id=a.admin,
            authority_node_id=a.s.node_id,
            channel_id=a.channel,
            local_room_id=a.channel,
        )
    async with b.s.sessions.begin() as db:
        db.add(Room(id=b.mirror, name="human mirror"))
        await db.flush()
        await b.c.bind(
            db,
            actor_id=b.admin,
            authority_node_id=a.s.node_id,
            channel_id=a.channel,
            local_room_id=b.mirror,
        )
    b.command = command(a, b)
    b.command["actor"] = {
        "node_id": b.s.node_id,
        "kind": "human",
        "principal_id": b.admin,
    }
    return a, b


async def test_actual_mtls_durable_retry_human_replay_and_local_isolation(
    human_channels, monkeypatch
):
    from anygarden.db.repository import append_message
    from anygarden.federation.models import Peer
    from anygarden.federation.transport import PeerListener
    from anygarden.rooms.authorization import accessible_room_ids, resolve_access
    from anygarden.shared_channels.sync import pull, retry_submission
    from fastapi import HTTPException

    from .test_federation_trust import endpoint

    a, b = human_channels
    listener = await PeerListener(a.s, channel_service=a.c).start()
    try:
        async with b.s.sessions.begin() as db:
            peer = await db.get(Peer, a.s.node_id)
            peer.endpoint = endpoint(listener.port).model_dump()
            queued = await b.c.queue(db, b.command, identity=b.identity)
            assert queued["state"] == "unconfirmed"
        assert await count(b, Message) == 0
        result = await retry_submission(
            b.c, b.identity, a.s.node_id, a.channel, b.command["request_id"]
        )
        assert result["state"] == "confirmed"
        assert await count(a, Message) == 1 and await count(b, Message) == 0
        # Retry after restarting service returns same persisted receipt.
        b.c = ChannelService(node_id=b.s.node_id, peers=b.s, sessions=b.s.sessions)
        assert (
            await retry_submission(
                b.c, b.identity, a.s.node_id, a.channel, b.command["request_id"]
            )
            == result
        )
        scope = {
            k: b.command[k]
            for k in (
                "protocol_version",
                "sender_node_id",
                "authority_node_id",
                "channel_id",
                "grant_epoch",
                "actor",
            )
        }
        assert (await pull(b.c, b.identity, scope))["ack_seq"] == 1
        assert (await pull(b.c, b.identity, scope))["ack_seq"] == 1
        async with b.s.sessions.begin() as db:
            snapshot = await b.c.snapshot(
                db, identity=b.identity, authority=a.s.node_id, channel=a.channel
            )
            assert snapshot["messages"][0]["actor"]["kind"] == "human"
            assert b.mirror not in await accessible_room_ids(db, identity=b.identity)
            with pytest.raises(HTTPException, match="shared-channel"):
                await resolve_access(db, room_id=b.mirror, identity=b.identity)
            with pytest.raises(ChannelError, match="SHARED_CHANNEL_API_REQUIRED"):
                await append_message(db, b.mirror, None, "bypass")
            await append_message(db, b.channel, None, "independent local work")
        await b.s.revoke(b.admin, a.s.node_id)
        async with b.s.sessions.begin() as db:
            with pytest.raises(PeerError):
                await b.c.snapshot(
                    db, identity=b.identity, authority=a.s.node_id, channel=a.channel
                )
        assert await count(b, Message) == 2
    finally:
        await listener.stop()


async def test_outage_and_lost_response_never_confirm_locally(
    human_channels, monkeypatch
):
    from anygarden.shared_channels import sync

    a, b = human_channels
    async with b.s.sessions.begin() as db:
        await b.c.queue(db, b.command, identity=b.identity)

    async def unavailable(*args):
        raise PeerError("PEER_UNAVAILABLE", 503)

    monkeypatch.setattr(sync, "post", unavailable)
    result = await sync.retry_submission(
        b.c, b.identity, a.s.node_id, a.channel, b.command["request_id"]
    )
    assert result["state"] == "unconfirmed" and await count(b, Message) == 0

    async def lost(*args):
        await send(a, b, b.command)
        raise PeerError("PEER_UNAVAILABLE", 503)

    monkeypatch.setattr(sync, "post", lost)
    assert (
        await sync.retry_submission(
            b.c, b.identity, a.s.node_id, a.channel, b.command["request_id"]
        )
    )["state"] == "unconfirmed"
    assert await count(a, Message) == 1

    async def retry(*args):
        return await send(a, b, b.command)

    monkeypatch.setattr(sync, "post", retry)
    assert (
        await sync.retry_submission(
            b.c, b.identity, a.s.node_id, a.channel, b.command["request_id"]
        )
    )["state"] == "confirmed"
    assert await count(a, Message) == 1 and await count(b, Message) == 0


async def test_http_rejects_spoofed_tls_duplicate_json_and_identity(human_channels):
    from anygarden.dependencies import get_admin_identity, get_current_identity
    from anygarden.federation.router import create_peer_app
    from anygarden.shared_channels.router import mount_local
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    a, b = human_channels
    remote = create_peer_app(a.s, a.c)
    async with AsyncClient(
        transport=ASGITransport(app=remote), base_url="https://test"
    ) as c:
        r = await c.post(
            "/api/v1/federation/channels/commands",
            json=b.command,
            headers={"X-Peer-Node": b.s.node_id},
        )
        assert r.status_code == 401 and r.json()["code"] == "MTLS_REQUIRED"
    app = FastAPI()
    mount_local(app, b.c)
    app.dependency_overrides[get_current_identity] = lambda: b.identity
    app.dependency_overrides[get_admin_identity] = lambda: b.identity
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        r = await c.post(
            "/api/v1/shared-channels/commands",
            content='{"protocol_version":1,"protocol_version":1}',
        )
        assert r.status_code == 400
        bad = copy.deepcopy(b.command)
        bad["actor"]["principal_id"] = uid()
        assert (
            await c.post("/api/v1/shared-channels/commands", json=bad)
        ).status_code == 403
        assert (
            await c.post("/api/v1/shared-channels/commands", json=b.command)
        ).json()["state"] == "unconfirmed"


async def test_guard_applies_before_cached_receipt(channels):
    a, b = channels
    cmd = command(a, b)
    await send(a, b, cmd)

    async def deny(db, envelope):
        raise ChannelError("ROLE_DENIED", 403)

    a.c.command_guards["message.send"] = deny
    with pytest.raises(ChannelError, match="ROLE_DENIED"):
        await send(a, b, cmd)


async def test_task_guard_mandatory_on_new_and_duplicate(channels):
    from anygarden.shared_channels.schemas import CommandEffect

    a, b = channels

    async def policy(db, auth):
        return True

    a.s.local_policy = policy
    cmd = command(a, b)
    cmd["kind"] = "task.started"
    cmd["payload"] = {
        "delegation_id": uid(),
        "expected_revision": 1,
        "execution_id": uid(),
    }

    async def effect(db, envelope):
        return CommandEffect(2, "running", "running", "in_progress")

    with pytest.raises(ChannelError, match="COMMAND_GUARD_REQUIRED"):
        await send(a, b, cmd, effect)

    async def guard(db, envelope):
        pass

    a.c.command_guards["task.started"] = guard
    receipt = await send(a, b, cmd, effect)
    assert receipt["seq"] == 1
    del a.c.command_guards["task.started"]
    with pytest.raises(ChannelError, match="COMMAND_GUARD_REQUIRED"):
        await send(a, b, cmd, effect)
    assert await count(a, CommandReceipt) == 1


async def test_local_authority_message_requires_publication_and_fresh_admin(
    human_channels,
):
    from anygarden.db.models import User
    from sqlalchemy import update

    a, b = human_channels
    cmd = command(a, b)
    cmd["sender_node_id"] = a.s.node_id
    cmd["actor"] = a.c.local_principal(a.identity)
    async with a.s.sessions.begin() as db:
        with pytest.raises(ChannelError, match="PUBLICATION_DENIED"):
            await a.c.submit_local(db, cmd, identity=a.identity)
    async with a.s.sessions.begin() as db:
        await a.c.publication(
            db,
            actor_id=a.admin,
            channel_id=a.channel,
            principal=cmd["actor"],
            active=True,
        )
    async with a.s.sessions.begin() as db:
        receipt = await a.c.submit_local(db, cmd, identity=a.identity)
    assert receipt["seq"] == 1 and await count(a, Message) == 1
    async with a.s.sessions.begin() as db:
        await db.execute(update(User).where(User.id == a.admin).values(is_admin=False))
    async with a.s.sessions.begin() as db:
        with pytest.raises(ChannelError, match="ADMIN_REQUIRED"):
            await a.c.submit_local(db, cmd, identity=a.identity)


async def test_wire_rejects_unapproved_data_and_mismatched_actor(channels):
    a, b = channels
    for extra in ("attachments", "metadata", "credentials", "runtime_home", "api_key"):
        cmd = command(a, b)
        cmd["payload"][extra] = "not-exported"
        with pytest.raises(ChannelError, match="INVALID_SCHEMA"):
            await send(a, b, cmd)
    cmd = command(a, b)
    cmd["protocol_version"] = 2
    with pytest.raises(ChannelError, match="VERSION_UNSUPPORTED"):
        await send(a, b, cmd)
    assert await count(a, ChannelEvent) == 0


async def test_each_node_can_author_a_different_channel(channels):
    a, b = channels
    await admit(b, a)
    a.mirror = uid()
    async with b.s.sessions.begin() as db:
        await b.c.bind(
            db,
            actor_id=b.admin,
            authority_node_id=b.s.node_id,
            channel_id=b.channel,
            local_room_id=b.channel,
        )
    async with a.s.sessions.begin() as db:
        db.add(Room(id=a.mirror, name="reverse mirror"))
        await db.flush()
        await a.c.bind(
            db,
            actor_id=a.admin,
            authority_node_id=b.s.node_id,
            channel_id=b.channel,
            local_room_id=a.mirror,
        )
    ab = command(a, b, "owned by A")
    ba = command(b, a, "owned by B")
    assert (await send(a, b, ab))["seq"] == (await send(b, a, ba))["seq"] == 1
    assert (await receive(a, b, await events(a, b, ab)))["ack_seq"] == 1
    assert (await receive(b, a, await events(b, a, ba)))["ack_seq"] == 1
    assert await count(a, Message) == await count(b, Message) == 2
    async with a.s.sessions() as db:
        messages = list((await db.scalars(select(Message))).all())
        assert {
            m.extra_metadata["federation"]["authority_node_id"] for m in messages
        } == {a.s.node_id, b.s.node_id}


async def test_actor_removed_from_current_grant_cannot_reuse_receipt(channels):
    from anygarden.federation.schemas import GrantReplace, Principal

    from .test_federation_trust import invitation

    a, b = channels
    cmd = command(a, b)
    await send(a, b, cmd)
    scope = invitation(a, b).scopes[0]
    scope.actors = [Principal(node_id=b.s.node_id, kind="agent", principal_id=uid())]
    await a.s.replace_grant(
        a.admin,
        b.s.node_id,
        a.channel,
        GrantReplace(scope=scope, expected_grant_epoch=1),
    )
    with pytest.raises(PeerError):
        await send(a, b, cmd)
    cmd["grant_epoch"] = 2
    with pytest.raises(PeerError, match="PRINCIPAL_DENIED"):
        await send(a, b, cmd)
    with pytest.raises(PeerError):
        await events(a, b, cmd)
    assert await count(a, Message) == 1


def test_shared_migration_matches_models_and_round_trips(tmp_path):
    from alembic import command as migrate
    from anygarden.db.models import Base
    from sqlalchemy import create_engine, inspect

    from .test_migrations import _alembic_config

    path = tmp_path / "shared-schema.db"
    cfg = _alembic_config(str(path))
    migrate.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{path}")
    try:
        inspector = inspect(engine)
        tables = {
            t.name: t
            for t in Base.metadata.tables.values()
            if t.name.startswith("shared_")
        }
        assert len(tables) == 10
        for name, model in tables.items():
            actual = {c["name"]: c for c in inspector.get_columns(name)}
            assert set(actual) == set(model.columns.keys())
            assert set(inspector.get_pk_constraint(name)["constrained_columns"]) == {
                c.name for c in model.primary_key
            }
            assert {
                tuple(f["constrained_columns"])
                for f in inspector.get_foreign_keys(name)
            } == {
                tuple(c.parent.name for c in f.elements)
                for f in model.foreign_key_constraints
            }
        migrate.downgrade(cfg, "064_peer_trust")
        assert not any(
            t.startswith("shared_") for t in inspect(engine).get_table_names()
        )
        migrate.upgrade(cfg, "head")
        assert set(tables) <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


async def test_submission_status_and_caller_membership_are_fresh(human_channels):
    from anygarden.auth.dependencies import Identity
    from anygarden.auth.jwt import UserClaims
    from anygarden.db.models import Participant
    from fastapi import HTTPException

    a, b = human_channels
    # An ordinary local member can submit its exported identity, but removal
    # must block cached status and retransmission as well as new requests.
    async with b.s.sessions.begin() as db:
        participant = Participant(
            id=uid(),
            room_id=b.mirror,
            user_id=b.admin,
            role="member",
        )
        db.add(participant)
    # Keep the real DB admin for the consent approver; JWT requests no bypass.
    identity = Identity(
        "user", b.admin, UserClaims(b.admin, "test@example.test", False)
    )
    async with b.s.sessions.begin() as db:
        result = await b.c.queue(db, b.command, identity=identity)
    b.c = ChannelService(node_id=b.s.node_id, peers=b.s, sessions=b.s.sessions)
    async with b.s.sessions.begin() as db:
        assert (
            await b.c.submission_status(
                db,
                identity=identity,
                authority=a.s.node_id,
                channel=a.channel,
                request_id=b.command["request_id"],
            )
            == result
        )
    async with b.s.sessions.begin() as db:
        await db.delete(await db.get(Participant, participant.id))
    async with b.s.sessions.begin() as db:
        with pytest.raises(HTTPException):
            await b.c.queue(db, b.command, identity=identity)
    async with b.s.sessions.begin() as db:
        with pytest.raises(HTTPException):
            await b.c.submission_status(
                db,
                identity=identity,
                authority=a.s.node_id,
                channel=a.channel,
                request_id=b.command["request_id"],
            )
