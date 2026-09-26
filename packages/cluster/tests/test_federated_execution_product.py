"""User facades and fresh executor context through HTTP and real pinned mTLS."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from anygarden.auth.dependencies import Identity
from anygarden.db.models import Agent, Machine, Participant, Room, Task
from anygarden.dependencies import get_current_identity
from anygarden.federation.delegation import source_task_id
from anygarden.federation.delegation_models import Delegation
from anygarden.federation.delegation_wiring import make_local_policy
from anygarden.federation.errors import PeerError
from anygarden.federation.execution_context import (
    resolve_execution_context,
    send_executor_command,
)
from anygarden.federation.models import Peer, PeerConsent, PeerGrant
from anygarden.federation.transport import PeerListener, post
from anygarden.shared_channels.models import PublicationConsent, SharedParticipant
from anygarden.shared_channels.router import mount_local
from anygarden.shared_channels.schemas import ChannelError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from . import test_delegation_product_mount as mount_tests
from .test_federation_trust import endpoint, pair  # noqa: F401
from .test_shared_channels import channels, uid  # noqa: F401

mounted = mount_tests.mounted


async def add_human(node, channel, *, role="owner"):
    async with node.s.sessions.begin() as db:
        db.add(Participant(room_id=channel, user_id=node.admin, role=role))
    principal = {"node_id": node.s.node_id, "kind": "human", "principal_id": node.admin}
    async with node.s.sessions.begin() as db:
        await node.c.publication(
            db,
            actor_id=node.admin,
            channel_id=channel,
            principal=principal,
            active=True,
        )
    async with node.s.sessions.begin() as db:
        await node.c.change_participant(
            db,
            actor_id=node.admin,
            channel_id=channel,
            operation_id=uid(),
            expected_revision=0,
            principal=principal,
            role=role,
            active=True,
        )


@asynccontextmanager
async def client_for(node, identity):
    app = FastAPI()
    mount_local(app, node.c)
    app.dependency_overrides[get_current_identity] = lambda: identity
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://product.test"
    ) as client:
        yield client


def base(node):
    return f"/api/v1/shared-channels/{node.s.node_id}/{node.channel}"


async def ready_agent(node, agent_id):
    # Directory unit readiness only; the root integration suite exercises the
    # real canonical DM socket, placement and advertised control generation.
    async with node.s.sessions.begin() as db:
        machine = Machine(
            id=uid(), name="runner", hostname="runner", owner_user_id=node.admin
        )
        db.add(machine)
        await db.flush()
        agent = await db.get(Agent, agent_id)
        agent.engine = "codex-cli"
        agent.desired_state = agent.actual_state = "running"
        agent.placed_on_machine_id = machine.id
        dm = Room(
            id=uid(), name="agent dm", is_dm=True, representative_agent_id=agent.id
        )
        db.add(dm)
        await db.flush()
        db.add(Participant(room_id=dm.id, agent_id=agent.id, role="member"))
        generation = agent.generation
    node.c.execution_transport = SimpleNamespace(
        manager=SimpleNamespace(
            execution_connection=AsyncMock(return_value=(generation, "ready-socket"))
        )
    )


@pytest.fixture
async def product(mounted):
    a, b = mounted
    await add_human(a, a.channel)
    agent = uid()
    await mount_tests._seed_local_executor(a, a.channel, agent)
    await ready_agent(a, agent)
    a.executor = agent
    return a, b


async def create_request(
    client, a, *, executor=None, text="Inspect the source\nKeep its exact instructions"
):
    sent = await client.post(
        base(a) + "/messages", json={"request_id": uid(), "text": text}
    )
    assert sent.status_code == 200, sent.text
    source = (await client.get(base(a))).json()["messages"][-1]["message_id"]
    body = {
        "request_id": uid(),
        "source_message_id": source,
        "executor": executor or {"node_id": a.s.node_id, "agent_id": a.executor},
    }
    response = await client.post(base(a) + "/delegations", json=body)
    assert response.status_code == 200, response.text
    return body, response.json()


async def test_user_facade_atomically_creates_immutable_source_task_and_receipt(
    product,
):
    a, _ = product
    async with client_for(a, Identity(kind="user", id=a.admin)) as client:
        body, result = await create_request(client, a)
        again = await client.post(base(a) + "/delegations", json=body)
        assert again.json() == result
        forged = await client.post(
            base(a) + "/delegations", json={**body, "task_id": uid()}
        )
        assert forged.status_code == 400
        changed = await client.post(
            base(a) + "/delegations",
            json={**body, "executor": {"node_id": a.s.node_id, "agent_id": uid()}},
        )
        assert changed.status_code == 403
        snapshot = (await client.get(base(a))).json()
    async with a.s.sessions() as db:
        task = await db.get(Task, result["task_id"])
        assert task.id == source_task_id(
            a.s.node_id, a.channel, body["source_message_id"]
        )
        assert task.spec == "Inspect the source\nKeep its exact instructions"
        assert task.title == "Inspect the source"
        assert await db.scalar(select(func.count()).select_from(Task)) == 1
        assert await db.scalar(select(func.count()).select_from(Delegation)) == 1
    assert snapshot["permissions"] == {"can_send": True, "can_delegate": True}
    assert snapshot["targets"][0]["name"] == "executor"
    assert snapshot["targets"][0]["can_execute"] is True
    assert snapshot["targets"][0]["is_local"] is True
    assert snapshot["delegations"][0]["can_cancel"] is True
    assert {s["kind"] for s in snapshot["submissions"]} == {
        "message.send",
        "task.request",
    }
    assert all(not s["can_retry"] for s in snapshot["submissions"])


async def test_request_and_cancel_recheck_current_actor_before_duplicate_receipt(
    product,
):
    a, _ = product
    async with client_for(a, Identity(kind="user", id=a.admin)) as client:
        body, result = await create_request(client, a)
        cancel = {"request_id": uid(), "expected_revision": 1}
        path = base(a) + f"/delegations/{result['delegation_id']}/cancel"
        response = await client.post(path, json=cancel)
        assert response.status_code == 200, response.text
        assert response.json()["receipt"]["state"] == "cancel_requested"
        assert (await client.post(path, json=cancel)).json() == response.json()
        async with a.s.sessions.begin() as db:
            consent = await db.get(
                PublicationConsent,
                (a.s.node_id, a.channel, a.s.node_id, "human", a.admin),
            )
            consent.active = False
        assert (await client.post(path, json=cancel)).status_code == 403
        assert (
            await client.post(base(a) + "/delegations", json=body)
        ).status_code == 403
        snapshot = (await client.get(base(a))).json()
        assert not snapshot["permissions"]["can_delegate"]
        assert not snapshot["delegations"][0]["can_cancel"]


async def test_thread_source_and_observer_executor_are_not_admitted(product):
    a, _ = product
    async with client_for(a, Identity(kind="user", id=a.admin)) as client:
        source_body, _ = await create_request(client, a)
        response = await client.post(
            base(a) + "/messages",
            json={
                "request_id": uid(),
                "text": "reply",
                "thread_root_id": source_body["source_message_id"],
            },
        )
        assert response.status_code == 200
        thread = (await client.get(base(a))).json()["messages"][-1]["message_id"]
        denied = await client.post(
            base(a) + "/delegations",
            json={**source_body, "request_id": uid(), "source_message_id": thread},
        )
        assert denied.status_code == 403
        async with a.s.sessions.begin() as db:
            roster = await db.get(
                SharedParticipant,
                (a.s.node_id, a.channel, a.s.node_id, "agent", a.executor),
            )
            roster.role = "observer"
        denied = await client.post(base(a) + "/delegations", json=source_body)
        assert denied.status_code == 403
        assert not (await client.get(base(a))).json()["targets"][0]["can_execute"]


async def test_snapshot_latest_older_and_poll_pages_are_separate(product):
    a, _ = product
    async with client_for(a, Identity(kind="user", id=a.admin)) as client:
        for index in range(6):
            assert (
                await client.post(
                    base(a) + "/messages",
                    json={"request_id": uid(), "text": str(index)},
                )
            ).status_code == 200
        latest = (await client.get(base(a), params={"limit": 2})).json()
        assert [m["text"] for m in latest["messages"]] == ["4", "5"]
        assert latest["cursor"]["has_more_before"]
        assert latest["messages"][0]["actor_name"] == "a"
        assert latest["messages"][0]["created_at"]
        older = (
            await client.get(
                base(a),
                params={"limit": 2, "before_seq": latest["cursor"]["oldest_seq"]},
            )
        ).json()
        assert [m["text"] for m in older["messages"]] == ["2", "3"]
        poll = (
            await client.get(
                base(a), params={"after_seq": latest["cursor"]["newest_seq"]}
            )
        ).json()
        assert poll["messages"] == []
        assert len(poll["submissions"]) == 6
        assert (
            await client.get(base(a), params={"after_seq": 0, "before_seq": 2})
        ).status_code == 400


async def test_local_executor_context_is_exact_fresh_and_observes_cancellation(product):
    a, _ = product
    async with client_for(a, Identity(kind="user", id=a.admin)) as client:
        _, result = await create_request(client, a)
        kwargs = {
            "authority_node_id": a.s.node_id,
            "channel_id": a.channel,
            "delegation_id": result["delegation_id"],
            "agent_id": a.executor,
        }
        context = await resolve_execution_context(a.c, **kwargs)
        assert context["state"] == "requested" and context["revision"] == 1
        assert context["local_peer_epoch"] == context["local_policy_epoch"] == 0
        other = uid()
        await mount_tests._seed_local_executor(a, a.channel, other)
        with pytest.raises(ChannelError, match="EXECUTOR_DENIED"):
            await resolve_execution_context(a.c, **{**kwargs, "agent_id": other})
        await client.post(
            base(a) + f"/delegations/{result['delegation_id']}/cancel",
            json={"request_id": uid(), "expected_revision": 1},
        )
        assert (await resolve_execution_context(a.c, **kwargs))[
            "state"
        ] == "cancel_requested"
        async with a.s.sessions.begin() as db:
            row = await db.get(
                PublicationConsent,
                (a.s.node_id, a.channel, a.s.node_id, "agent", a.executor),
            )
            row.active = False
        with pytest.raises(ChannelError):
            await resolve_execution_context(a.c, **kwargs)


async def seed_remote_executor(a, b):
    await mount_tests._add_remote_actor(a, b, a.channel, b.actor)
    async with b.s.sessions.begin() as db:
        db.add(
            Agent(
                id=b.actor,
                name="Remote analyst",
                description="Analyzes a chosen source",
                engine="codex",
                actual_state="running",
                desired_state="running",
            )
        )
        await db.flush()
        db.add(Participant(room_id=b.mirror, agent_id=b.actor, role="member"))
    await ready_agent(b, b.actor)
    scope = {
        "protocol_version": 1,
        "sender_node_id": b.s.node_id,
        "authority_node_id": a.s.node_id,
        "channel_id": a.channel,
        "grant_epoch": 1,
        "actor": {"node_id": b.s.node_id, "kind": "agent", "principal_id": b.actor},
    }
    async with a.s.sessions.begin() as db:
        events = await a.c.events(db, scope, tls=b.s.identity, after_seq=0)
    async with b.s.sessions.begin() as db:
        await b.c.receive(
            db,
            events,
            tls=a.s.identity,
            authority_node_id=a.s.node_id,
            channel_id=a.channel,
            grant_epoch=1,
        )
    a.s.local_policy = make_local_policy(a.s.node_id)
    return scope


@asynccontextmanager
async def listeners(a, b):
    la, lb = (
        PeerListener(a.s, channel_service=a.c),
        PeerListener(b.s, channel_service=b.c),
    )
    await la.start()
    await lb.start()
    try:
        for node, remote, listener in ((a, b, lb), (b, a, la)):
            async with node.s.sessions.begin() as db:
                peer = await db.get(Peer, remote.s.node_id)
                peer.endpoint = endpoint(listener.port).model_dump(mode="json")
        yield la, lb
    finally:
        await la.stop()
        await lb.stop()


async def test_real_mtls_context_directory_result_and_revocation(product):
    a, b = product
    scope = await seed_remote_executor(a, b)
    async with (
        listeners(a, b) as (la, _),
        client_for(a, Identity(kind="user", id=a.admin)) as client,
    ):
        _body, result = await create_request(
            client, a, executor={"node_id": b.s.node_id, "agent_id": b.actor}
        )
        snapshot = (await client.get(base(a))).json()
        remote = next(t for t in snapshot["targets"] if t["node_id"] == b.s.node_id)
        assert remote["name"] == "Remote analyst" and remote["can_execute"]
        assert (
            remote["server_label"].startswith("127.0.0.1:") and not remote["is_local"]
        )
        kwargs = {
            "authority_node_id": a.s.node_id,
            "channel_id": a.channel,
            "delegation_id": result["delegation_id"],
            "agent_id": b.actor,
        }
        context = await resolve_execution_context(b.c, **kwargs)
        assert context["prompt"] == "Inspect the source\nKeep its exact instructions"
        assert context["local_peer_epoch"] >= 1 and context["local_policy_epoch"] >= 1
        execution = uid()
        envelope = {
            **scope,
            "request_id": uid(),
            "kind": "task.accept",
            "payload": {
                "delegation_id": result["delegation_id"],
                "expected_revision": 1,
                "execution_id": execution,
            },
        }
        receipt = await send_executor_command(b.c, envelope)
        assert receipt["state"] == "accepted"
        envelope.update(
            request_id=uid(),
            kind="task.result",
            payload={
                "delegation_id": result["delegation_id"],
                "expected_revision": 2,
                "execution_id": execution,
                "outcome": "succeeded",
                "text": "Verified result",
            },
        )
        await send_executor_command(b.c, envelope)
        finished = await resolve_execution_context(b.c, **kwargs)
        assert (
            finished["state"] == "completed" and finished["execution_id"] == execution
        )
        snapshot = (await client.get(base(a))).json()
        assert snapshot["delegations"][0]["result_markdown"] == "Verified result"
        assert snapshot["delegations"][0]["finished_at"]
        assert not snapshot["delegations"][0]["can_cancel"]
        async with b.s.sessions.begin() as db:
            consent = await db.get(PeerConsent, (a.s.node_id, a.s.node_id, a.channel))
            consent.active = False
            consent.policy_epoch += 1
        with pytest.raises((PeerError, ChannelError)):
            await resolve_execution_context(b.c, **kwargs)
        snapshot = (await client.get(base(a))).json()
        remote = next(t for t in snapshot["targets"] if t["node_id"] == b.s.node_id)
        assert remote["name"] is None and not remote["can_execute"]
        forged = {
            **scope,
            "actor": {"node_id": b.s.node_id, "kind": "agent", "principal_id": uid()},
        }
        with pytest.raises(PeerError):
            await post(
                b.s,
                endpoint(la.port),
                a.s.certificate_pem,
                f"/api/v1/federation/channels/delegations/{result['delegation_id']}/execution-context",
                forged,
            )


async def test_local_revocation_during_real_context_request_is_rechecked(
    product, monkeypatch
):
    import anygarden.federation.execution_context as context_module

    a, b = product
    await seed_remote_executor(a, b)
    async with (
        listeners(a, b),
        client_for(a, Identity(kind="user", id=a.admin)) as client,
    ):
        _, result = await create_request(
            client, a, executor={"node_id": b.s.node_id, "agent_id": b.actor}
        )
        real_post = context_module.post

        async def revoke_after_response(*args, **kwargs):
            value = await real_post(*args, **kwargs)
            async with b.s.sessions.begin() as db:
                consent = await db.get(
                    PeerConsent, (a.s.node_id, a.s.node_id, a.channel)
                )
                consent.active = False
                consent.policy_epoch += 1
            return value

        monkeypatch.setattr(context_module, "post", revoke_after_response)
        with pytest.raises(PeerError):
            await resolve_execution_context(
                b.c,
                authority_node_id=a.s.node_id,
                channel_id=a.channel,
                delegation_id=result["delegation_id"],
                agent_id=b.actor,
            )


@pytest.mark.parametrize(
    "mutation",
    [
        {"revision": True},
        {"revision": 0},
        {"execution_id": "not-a-uuid"},
        {"state": "running", "execution_id": None},
        {"prompt": "x" * 16385},
        {"peer_epoch": -1},
        {"process_state": "finished"},
    ],
)
async def test_peer_context_response_rejects_invalid_proof_fields(
    product, monkeypatch, mutation
):
    import anygarden.federation.execution_context as context_module

    a, b = product
    await seed_remote_executor(a, b)
    async with client_for(a, Identity(kind="user", id=a.admin)) as client:
        _, result = await create_request(
            client, a, executor={"node_id": b.s.node_id, "agent_id": b.actor}
        )
    scope = {
        "authority_node_id": a.s.node_id,
        "channel_id": a.channel,
        "grant_epoch": 1,
        "sender_node_id": b.s.node_id,
        "actor": {"node_id": b.s.node_id, "kind": "agent", "principal_id": b.actor},
    }
    async with a.s.sessions.begin() as db:
        valid = await context_module.authority_execution_context(
            a.c, db, scope, result["delegation_id"], tls=b.s.identity
        )

    async def bad_response(*args, **kwargs):
        return {**valid, **mutation}

    monkeypatch.setattr(context_module, "post", bad_response)
    with pytest.raises(ChannelError, match="INVALID_CONTEXT"):
        await resolve_execution_context(
            b.c,
            authority_node_id=a.s.node_id,
            channel_id=a.channel,
            delegation_id=result["delegation_id"],
            agent_id=b.actor,
        )


async def test_authority_rechecks_executor_consent_before_request_replay(product):
    a, b = product
    await seed_remote_executor(a, b)
    async with client_for(a, Identity(kind="user", id=a.admin)) as client:
        body, _ = await create_request(
            client, a, executor={"node_id": b.s.node_id, "agent_id": b.actor}
        )
        async with a.s.sessions.begin() as db:
            consent = await db.get(PeerConsent, (b.s.node_id, a.s.node_id, a.channel))
            consent.active = False
        replay = await client.post(base(a) + "/delegations", json=body)
        assert replay.status_code == 409 and replay.json()["code"] == "EXECUTOR_DENIED"


async def test_shared_room_binding_pin_and_read_marker_keep_visibility_gate(product):
    from anygarden.dependencies import get_db
    from anygarden.rooms.router import router as rooms_router

    a, _ = product
    app = FastAPI()
    mount_local(app, a.c)
    app.include_router(rooms_router)
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        kind="user", id=a.admin
    )

    async def session():
        async with a.s.sessions() as db:
            yield db

    app.dependency_overrides[get_db] = session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://rooms.test"
    ) as client:
        detail = await client.get(f"/api/v1/rooms/{a.channel}")
        assert detail.status_code == 200, detail.text
        binding = {"authority_node_id": a.s.node_id, "channel_id": a.channel}
        assert detail.json()["shared_channel"] == binding
        listing = (await client.get("/api/v1/rooms")).json()
        assert (
            next(row for row in listing if row["id"] == a.channel)["shared_channel"]
            == binding
        )
        assert (
            await client.patch(f"/api/v1/rooms/{a.channel}/pin", json={"pinned": True})
        ).status_code == 200
        assert (
            await client.put("/api/v1/rooms/pin-order", json={"room_ids": [a.channel]})
        ).status_code == 200
        assert (await client.post(f"/api/v1/rooms/{a.channel}/read")).status_code == 200
        async with a.s.sessions.begin() as db:
            row = await db.get(
                SharedParticipant,
                (a.s.node_id, a.channel, a.s.node_id, "human", a.admin),
            )
            row.active = False
        assert (
            await client.patch(f"/api/v1/rooms/{a.channel}/pin", json={"pinned": False})
        ).status_code == 404
        assert (await client.post(f"/api/v1/rooms/{a.channel}/read")).status_code == 404
        assert (await client.get(f"/api/v1/rooms/{a.channel}")).status_code == 404
        assert (await client.get(base(a))).status_code == 403


async def test_mirror_user_request_and_cancel_are_durable_and_revocation_fenced(
    product,
):
    from anygarden.federation.router import error_handler as peer_error_handler
    from anygarden.shared_channels.sync import pull

    a, b = product
    human = {"node_id": b.s.node_id, "kind": "human", "principal_id": b.admin}
    for node, peer in ((a, b), (b, a)):
        async with node.s.sessions.begin() as db:
            grant = await db.get(PeerGrant, (peer.s.node_id, a.s.node_id, a.channel))
            grant.actors = [*grant.actors, human]
            grant.capabilities = [*grant.capabilities, "task.request", "task.cancel"]
    async with b.s.sessions.begin() as db:
        db.add(Participant(room_id=b.mirror, user_id=b.admin, role="member"))
    async with a.s.sessions.begin() as db:
        await a.c.publication(
            db, actor_id=a.admin, channel_id=a.channel, principal=human, active=True
        )
    async with a.s.sessions.begin() as db:
        await a.c.change_participant(
            db,
            actor_id=a.admin,
            channel_id=a.channel,
            operation_id=uid(),
            expected_revision=0,
            principal=human,
            role="member",
            active=True,
        )
    identity = Identity(kind="user", id=b.admin)
    scope = {
        "protocol_version": 1,
        "sender_node_id": b.s.node_id,
        "authority_node_id": a.s.node_id,
        "channel_id": a.channel,
        "grant_epoch": 1,
        "actor": human,
    }
    async with (
        listeners(a, b),
        client_for(a, Identity(kind="user", id=a.admin)) as authority_client,
        client_for(b, identity) as mirror_client,
    ):
        mirror_client._transport.app.add_exception_handler(
            PeerError, peer_error_handler
        )
        sent = await authority_client.post(
            base(a) + "/messages",
            json={"request_id": uid(), "text": "A source chosen on the mirror"},
        )
        assert sent.status_code == 200
        await pull(b.c, identity, scope)
        view = (await mirror_client.get(base(a))).json()
        source = view["messages"][0]["message_id"]
        body = {
            "request_id": uid(),
            "source_message_id": source,
            "executor": {"node_id": a.s.node_id, "agent_id": a.executor},
        }
        queued = await mirror_client.post(base(a) + "/delegations", json=body)
        assert queued.status_code == 200, queued.text
        assert queued.json()["state"] == "unconfirmed"
        assert (
            await mirror_client.post(base(a) + "/delegations", json=body)
        ).json() == queued.json()
        view = (await mirror_client.get(base(a))).json()
        assert view["submissions"][0]["can_retry"]
        retry = base(a) + f"/submissions/{body['request_id']}/retry"
        confirmed = await mirror_client.post(retry)
        assert confirmed.json()["state"] == "confirmed"
        await pull(b.c, identity, scope)
        view = (await mirror_client.get(base(a))).json()
        assert view["delegations"][0]["can_cancel"]
        did = queued.json()["delegation_id"]
        cancel_body = {"request_id": uid(), "expected_revision": 1}
        cancelled = await mirror_client.post(
            base(a) + f"/delegations/{did}/cancel", json=cancel_body
        )
        assert cancelled.status_code == 200, cancelled.text
        assert (
            await mirror_client.post(
                base(a) + f"/submissions/{cancel_body['request_id']}/retry"
            )
        ).json()["receipt"]["state"] == "cancel_requested"
        await pull(b.c, identity, scope)
        view = (await mirror_client.get(base(a))).json()
        assert view["delegations"][0]["state"] == "cancel_requested"
        assert not view["delegations"][0]["can_cancel"]
        assert all(not row["can_retry"] for row in view["submissions"])
        async with b.s.sessions.begin() as db:
            consent = await db.get(PeerConsent, (a.s.node_id, a.s.node_id, a.channel))
            consent.active = False
        assert (await mirror_client.post(retry)).status_code == 403
        assert (
            await mirror_client.post(base(a) + "/delegations", json=body)
        ).status_code == 403


async def test_picker_readiness_requires_supported_engine_and_current_control_socket(
    product,
):
    a, _ = product
    async with client_for(a, Identity(kind="user", id=a.admin)) as client:
        async with a.s.sessions.begin() as db:
            agent = await db.get(Agent, a.executor)
            agent.engine = "claude-code"
        target = (await client.get(base(a))).json()["targets"][0]
        assert (
            not target["can_execute"]
            and target["unavailable_code"] == "unsupported_runtime"
        )
        async with a.s.sessions.begin() as db:
            agent = await db.get(Agent, a.executor)
            agent.engine = "codex-cli"
        a.c.execution_transport.manager.execution_connection.return_value = None
        target = (await client.get(base(a))).json()["targets"][0]
        assert (
            not target["can_execute"]
            and target["unavailable_code"] == "execution_unavailable"
        )
        a.c.execution_transport.manager.execution_connection.return_value = (
            999,
            "stale",
        )
        assert not (await client.get(base(a))).json()["targets"][0]["can_execute"]
