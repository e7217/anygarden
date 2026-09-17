"""Product delegation wiring (task #51) on the shared_channels `pair` harness.

Covers the architect's task #52 conditions and dev01's review conditions:
remote actors resolve through roster+shadow rows, roster removal demotes the
shadow below the fenced roles (post-revocation replay rejected before dedup),
grant revocation rejects independently of the shadow, and shadows never
surface through identity-based access.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from . import test_shared_channels
from .test_federation_trust import admit, pair  # noqa: F401
from .test_shared_channels import command, uid

#: pytest fixture registration (avoids an F811 import/def clash)
channels = test_shared_channels.channels


def _executor(a, agent_id):
    return {"node_id": a.s.node_id, "agent_id": agent_id}


async def _seed_local_executor(a, channel, agent_id):
    from anygarden.db.models import Agent, Participant

    async with a.s.sessions.begin() as db:
        db.add(Agent(id=agent_id, name="executor", engine="codex"))
        await db.flush()
        db.add(Participant(id=uid(), room_id=channel, agent_id=agent_id, role="member"))
    principal = {
        "node_id": a.s.node_id,
        "kind": "agent",
        "principal_id": agent_id,
    }
    async with a.s.sessions.begin() as db:
        await a.c.publication(
            db, actor_id=a.admin, channel_id=channel, principal=principal, active=True
        )
    async with a.s.sessions.begin() as db:
        await a.c.change_participant(
            db,
            actor_id=a.admin,
            channel_id=channel,
            operation_id=uid(),
            expected_revision=0,
            principal=principal,
            role="member",
            active=True,
        )


async def _add_remote_actor(a, b, channel, actor_pid):
    principal = {
        "node_id": b.s.node_id,
        "kind": "agent",
        "principal_id": actor_pid,
    }
    # The inbound grant must list the remote agent among its actors first
    # (peer-layer scope; mirrors what an admin grant update would set) —
    # publication and participant operations validate against it.
    from anygarden.federation.models import PeerGrant

    async with a.s.sessions.begin() as db:
        grant = await db.get(
            PeerGrant,
            (b.s.node_id, a.s.node_id, channel),
            populate_existing=True,
        )
        grant.actors = list(grant.actors or []) + [principal]
        for capability in ("task.request", "task.execute"):
            if capability not in (grant.capabilities or []):
                grant.capabilities = list(grant.capabilities or []) + [capability]
    async with a.s.sessions.begin() as db:
        await a.c.publication(
            db, actor_id=a.admin, channel_id=channel, principal=principal, active=True
        )
    async with a.s.sessions.begin() as db:
        await a.c.change_participant(
            db,
            actor_id=a.admin,
            channel_id=channel,
            operation_id=uid(),
            expected_revision=0,
            principal=principal,
            role="member",
            active=True,
        )


async def _send_source_message(a, b, actor_pid):
    source = uid()
    root = command(a, b, "work", root=None)
    root["actor"] = {
        "node_id": b.s.node_id,
        "kind": "agent",
        "principal_id": actor_pid,
    }
    root["payload"] = {"message_id": source, "thread_root_id": None, "text": "work"}
    async with a.s.sessions.begin():
        await a.c.submit(root, tls=b.s.identity)
    return source


async def _seed_task(a, channel, source):
    """Product flow: the source message is converted into an unassigned Task."""
    from anygarden.db.models import Task
    from anygarden.shared_channels.models import SharedMessage

    async with a.s.sessions() as db:
        projected = await db.get(SharedMessage, (a.s.node_id, channel, source))
        assert projected is not None
        local_id = projected.local_message_id
    task_id = uid()
    async with a.s.sessions.begin() as db:
        db.add(
            Task(
                id=task_id,
                room_id=channel,
                title="remote work",
                source_message_id=local_id,
            )
        )
    return task_id


def _task_request(a, b, channel, actor_pid, agent_id, source, task_id):
    return {
        "protocol_version": 1,
        "request_id": uid(),
        "sender_node_id": b.s.node_id,
        "authority_node_id": a.s.node_id,
        "channel_id": channel,
        "grant_epoch": 1,
        "actor": {
            "node_id": b.s.node_id,
            "kind": "agent",
            "principal_id": actor_pid,
        },
        "kind": "task.request",
        "payload": {
            "delegation_id": uid(),
            "expected_revision": 0,
            "task_id": task_id,
            "source_message_id": source,
            "executor": _executor(a, agent_id),
        },
    }


@pytest.fixture()
async def mounted(channels):
    from anygarden.federation.delegation_wiring import install_product_delegation

    a, b = channels
    a.delegation = install_product_delegation(a.c)
    b.delegation = install_product_delegation(b.c)
    return a, b


async def test_product_mount_routes_task_request_through_coordinator(mounted):
    a, b = mounted
    agent_id, actor_pid = uid(), uid()
    await _seed_local_executor(a, a.channel, agent_id)
    await _add_remote_actor(a, b, a.channel, actor_pid)
    source = await _send_source_message(a, b, actor_pid)
    task_id = await _seed_task(a, a.channel, source)
    cmd = _task_request(a, b, a.channel, actor_pid, agent_id, source, task_id)
    async with a.s.sessions.begin():
        receipt = await a.c.submit(cmd, tls=b.s.identity)
    assert receipt["request_id"] == cmd["request_id"]


async def test_roster_removal_rejects_replay_before_dedup(mounted):
    from anygarden.federation.delegation import DelegationError
    from anygarden.shared_channels.models import SharedParticipant

    a, b = mounted
    agent_id, actor_pid = uid(), uid()
    await _seed_local_executor(a, a.channel, agent_id)
    await _add_remote_actor(a, b, a.channel, actor_pid)
    source = await _send_source_message(a, b, actor_pid)
    task_id = await _seed_task(a, a.channel, source)
    cmd = _task_request(a, b, a.channel, actor_pid, agent_id, source, task_id)
    async with a.s.sessions.begin() as db:
        await a.c.submit(cmd, tls=b.s.identity)
    async with a.s.sessions.begin() as db:
        await a.c.change_participant(
            db,
            actor_id=a.admin,
            channel_id=a.channel,
            operation_id=uid(),
            expected_revision=1,
            principal={
                "node_id": b.s.node_id,
                "kind": "agent",
                "principal_id": actor_pid,
            },
            role="member",
            active=False,
        )
    # Replay of the same (already-receipted) request must be rejected by the
    # guard BEFORE dedup returns the cached receipt.
    async with a.s.sessions.begin() as db:
        with pytest.raises(DelegationError):
            await a.c.submit(cmd, tls=b.s.identity)
    async with a.s.sessions() as db:
        roster = await db.get(
            SharedParticipant,
            (a.s.node_id, a.channel, b.s.node_id, "agent", actor_pid),
        )
        assert roster.active is False


async def test_grant_revocation_rejects_regardless_of_shadow(mounted):
    from anygarden.federation.models import PeerGrant

    a, b = mounted
    agent_id, actor_pid = uid(), uid()
    await _seed_local_executor(a, a.channel, agent_id)
    await _add_remote_actor(a, b, a.channel, actor_pid)
    source = await _send_source_message(a, b, actor_pid)
    task_id = await _seed_task(a, a.channel, source)
    cmd = _task_request(a, b, a.channel, actor_pid, agent_id, source, task_id)
    async with a.s.sessions.begin() as db:
        await a.c.submit(cmd, tls=b.s.identity)
    async with a.s.sessions.begin() as db:
        from sqlalchemy import update

        await db.execute(update(PeerGrant).values(active=False))
    fresh = dict(cmd, request_id=uid())
    async with a.s.sessions.begin() as db:
        with pytest.raises(Exception) as excinfo:
            await a.c.submit(fresh, tls=b.s.identity)
    assert not isinstance(excinfo.value, AssertionError)


async def test_shadow_rows_never_grant_identity_access(mounted):
    from anygarden.db.models import Participant
    from anygarden.rooms.authorization import accessible_room_ids
    from anygarden.shared_channels.shadow import (
        shadow_participant_id,
    )

    a, b = mounted
    actor_pid = uid()
    await _add_remote_actor(a, b, a.channel, actor_pid)
    shadow_id = shadow_participant_id(
        a.s.node_id, a.channel, b.s.node_id, "agent", actor_pid
    )
    async with a.s.sessions() as db:
        shadow = await db.get(Participant, shadow_id)
        assert shadow is not None
        assert shadow.user_id is None and shadow.agent_id is None
        assert shadow.role == "member"
    # No identity predicate can match a NULL/NULL row: the shadow principal
    # itself has no local identity object, and user/agent listings of the
    # room never include it (rooms member branch joins on user_id/agent_id).
    async with a.s.sessions() as db:
        rows = (
            await db.scalars(
                select(Participant.id).where(
                    Participant.room_id == a.channel,
                    Participant.agent_id.is_not(None),
                )
            )
        ).all()
        assert shadow_id not in rows
    # Even a fabricated identity with the shadow id sees nothing.
    stranger = SimpleNamespace(kind="user", id=shadow_id, claims=None)
    async with a.s.sessions() as db:
        assert await accessible_room_ids(db, identity=stranger) == frozenset()


def test_shadow_uuid5_is_deterministic_and_namespaced():
    from anygarden.shared_channels.shadow import (
        SHADOW_NAMESPACE,
        shadow_participant_id,
    )

    first = shadow_participant_id("A", "C", "B", "agent", "p1")
    second = shadow_participant_id("A", "C", "B", "agent", "p1")
    other = shadow_participant_id("A", "C", "B", "human", "p1")
    assert first == second and first != other
    import uuid

    parsed = uuid.UUID(first)
    assert parsed.version == 5
    # Distinct from the delegation execution-id namespace discipline.
    from uuid import NAMESPACE_URL

    execution_style = str(uuid.uuid5(NAMESPACE_URL, "A:C:B:agent:p1"))
    assert first != execution_style
    assert SHADOW_NAMESPACE != NAMESPACE_URL


async def test_remote_executor_grant_path_and_revocation(mounted):
    """task #53 P1 regression: the remote-executor branch of executor_allowed.

    The executor is the remote agent itself: the inbound grant on the
    authority is keyed (executor_node, authority, channel) and must be
    honoured while active (with task.execute + the agent among actors) and
    deny after revocation.
    """
    from anygarden.federation.errors import PeerError
    from anygarden.federation.models import PeerGrant
    from sqlalchemy import update

    a, b = mounted
    actor_pid = uid()
    await _add_remote_actor(a, b, a.channel, actor_pid)
    source = await _send_source_message(a, b, actor_pid)
    task_id = await _seed_task(a, a.channel, source)

    def request():
        return {
            "protocol_version": 1,
            "request_id": uid(),
            "sender_node_id": b.s.node_id,
            "authority_node_id": a.s.node_id,
            "channel_id": a.channel,
            "grant_epoch": 1,
            "actor": {
                "node_id": b.s.node_id,
                "kind": "agent",
                "principal_id": actor_pid,
            },
            "kind": "task.request",
            "payload": {
                "delegation_id": uid(),
                "expected_revision": 0,
                "task_id": task_id,
                "source_message_id": source,
                "executor": {"node_id": b.s.node_id, "agent_id": actor_pid},
            },
        }

    async with a.s.sessions.begin() as db:
        grant = await db.get(
            PeerGrant,
            (b.s.node_id, a.s.node_id, a.channel),
            populate_existing=True,
        )
        assert grant is not None and grant.active
        assert "task.execute" in (grant.capabilities or [])
    async with a.s.sessions.begin() as db:
        receipt = await a.c.submit(request(), tls=b.s.identity)
    assert receipt["state"]

    # executor_allowed remote branch, exercised directly for each state:
    # the envelope-level peer grant would otherwise mask the executor check
    # on the full path (same grant row governs sender and executor here).
    executor = {"node_id": b.s.node_id, "agent_id": actor_pid}
    async with a.s.sessions() as db:
        assert await a.delegation.executor_allowed(db, a.channel, executor)
    async with a.s.sessions.begin() as db:
        await db.execute(
            update(PeerGrant)
            .where(
                PeerGrant.peer_node_id == b.s.node_id,
                PeerGrant.authority_node_id == a.s.node_id,
                PeerGrant.channel_id == a.channel,
            )
            .values(active=False)
        )
    async with a.s.sessions() as db:
        assert not await a.delegation.executor_allowed(db, a.channel, executor)
    # Full path with a revoked grant is rejected by the peer layer first.
    async with a.s.sessions.begin() as db:
        with pytest.raises(PeerError):
            await a.c.submit(request(), tls=b.s.identity)


# --------------------------------------------------------------------------
# task #56 — transport-layer local_policy (deny-by-default entry gate)


async def test_local_policy_allows_listed_and_denies_unlisted_or_revoked(mounted):
    from types import SimpleNamespace
    from uuid import UUID

    from anygarden.federation.delegation_wiring import make_local_policy
    from anygarden.federation.errors import PeerError
    from anygarden.federation.models import PeerGrant
    from anygarden.federation.schemas import Principal

    a, b = mounted
    # Production wiring installs this via make_local_policy(node_id); the
    # pair harness builds its PeerService bare, so mirror that step here.
    a.s.local_policy = make_local_policy(a.s.node_id)
    agent_pid = uid()
    agent_node = b.s.node_id
    principal = Principal(
        node_id=UUID(agent_node), kind="agent", principal_id=UUID(agent_pid)
    )

    def auth(channel_id):
        return SimpleNamespace(channel_id=channel_id, principal=principal)

    # Unlisted agent: the installed policy denies (returns False), and the
    # peer layer rejects the actor before the policy is even consulted.
    async with a.s.sessions() as db:
        assert not await a.delegation.executor_allowed(
            db, a.channel, {"node_id": agent_node, "agent_id": agent_pid}
        )
        assert not await a.s.local_policy(db, auth(a.channel))
        with pytest.raises(PeerError):
            await a.s.authorize(
                db,
                b.s.identity,
                sender_node_id=agent_node,
                authority_node_id=a.s.node_id,
                channel_id=a.channel,
                principal=principal,
                action="task.execute",
                grant_epoch=1,
            )
    # List the agent in the inbound grant (actors + task.execute): allowed.
    async with a.s.sessions.begin() as db:
        grant = await db.get(
            PeerGrant,
            (agent_node, a.s.node_id, a.channel),
            populate_existing=True,
        )
        grant.actors = list(grant.actors or []) + [
            {"node_id": agent_node, "kind": "agent", "principal_id": agent_pid}
        ]
        if "task.execute" not in (grant.capabilities or []):
            grant.capabilities = list(grant.capabilities or []) + ["task.execute"]
    async with a.s.sessions() as db:
        assert await a.s.local_policy(db, auth(a.channel))
        assert await a.delegation.executor_allowed(
            db, a.channel, {"node_id": agent_node, "agent_id": agent_pid}
        )
        await a.s.authorize(
            db,
            b.s.identity,
            sender_node_id=agent_node,
            authority_node_id=a.s.node_id,
            channel_id=a.channel,
            principal=principal,
            action="task.execute",
            grant_epoch=1,
        )
    # Revoked grant: deny again (predicate False branch).
    from sqlalchemy import update

    async with a.s.sessions.begin() as db:
        await db.execute(
            update(PeerGrant)
            .where(
                PeerGrant.peer_node_id == agent_node,
                PeerGrant.authority_node_id == a.s.node_id,
                PeerGrant.channel_id == a.channel,
            )
            .values(active=False)
        )
    async with a.s.sessions() as db:
        assert not await a.delegation.executor_allowed(
            db, a.channel, {"node_id": agent_node, "agent_id": agent_pid}
        )
        assert not await a.s.local_policy(db, auth(a.channel))
        with pytest.raises(PeerError):
            await a.s.authorize(
                db,
                b.s.identity,
                sender_node_id=agent_node,
                authority_node_id=a.s.node_id,
                channel_id=a.channel,
                principal=principal,
                action="task.execute",
                grant_epoch=1,
            )


async def test_local_policy_absence_keeps_deny(channels):
    """Without the callback, task.execute stays denied (pre-#56 behavior).

    The peer checks (trust, grant actors, capabilities) pass first; the
    missing local_policy is the layer that denies.
    """
    from uuid import UUID

    from anygarden.federation.errors import PeerError
    from anygarden.federation.models import PeerGrant
    from anygarden.federation.schemas import Principal

    a, b = channels
    assert a.s.local_policy is None
    principal = Principal(
        node_id=UUID(b.s.node_id), kind="agent", principal_id=UUID(b.actor)
    )
    async with a.s.sessions.begin() as db:
        grant = await db.get(
            PeerGrant,
            (b.s.node_id, a.s.node_id, a.channel),
            populate_existing=True,
        )
        grant.actors = list(grant.actors or []) + [principal.model_dump(mode="json")]
        if "task.execute" not in (grant.capabilities or []):
            grant.capabilities = list(grant.capabilities or []) + ["task.execute"]
    async with a.s.sessions.begin() as db:
        with pytest.raises(PeerError) as excinfo:
            await a.s.authorize(
                db,
                b.s.identity,
                sender_node_id=b.s.node_id,
                authority_node_id=a.s.node_id,
                channel_id=a.channel,
                principal=principal,
                action="task.execute",
                grant_epoch=1,
            )
    assert excinfo.value.code == "LOCAL_POLICY_DENIED"
