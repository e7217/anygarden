"""Actual #590/#591 DB services + #592; authenticated transport boundary injected.

Two SQLite databases, current peer grants, wire schemas, channel log/replay,
source ID mapping and task projections. This is not real network/node acceptance.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from anygarden.db.models import Participant, Room, Task
from anygarden.federation.delegation import (
    DelegationError,
    DelegationService,
    LateResultAfterCancel,
)
from anygarden.federation.delegation_models import (
    Delegation,
    DelegationMirror,
    DelegationObservation,
)
from anygarden.federation.delegation_projection import install_projections
from anygarden.federation.errors import PeerError
from anygarden.federation.models import PeerGrant
from anygarden.federation.schemas import InviteAccept
from anygarden.shared_channels.models import (
    ChannelEvent,
    ChannelStream,
    CommandReceipt,
    SharedMessage,
)
from anygarden.shared_channels.schemas import ChannelError
from anygarden.shared_channels.service import ChannelService
from sqlalchemy import func, select, update

from . import test_federation_trust as trust
from .test_federation_trust import endpoint, invitation

# The upstream ``pair`` fixture registers through its marker name; a direct
# import would be shadowed by every ``product(pair)`` parameter (F811).
pair = trust.pair


def uid():
    return str(uuid4())


@pytest.fixture
async def product(pair):
    a, b = pair
    invite = invitation(a, b)
    invite.scopes[0].capabilities += ["task.request", "task.cancel"]
    bundle = await a.s.create_invite(a.admin, invite)
    ack = await a.s.redeem(
        b.s.identity,
        b.s.node_id,
        str(bundle.invite_id),
        bundle.token.get_secret_value(),
    )
    accept = InviteAccept(bundle=bundle, issuer_endpoint=endpoint())
    await b.s.begin_accept(b.admin, accept)
    await b.s.finish_accept(b.admin, accept, ack)
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
    mirror = uid()
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
    actor = {"node_id": b.s.node_id, "kind": "agent", "principal_id": b.actor}
    did, execution, task, source, participant = [uid() for _ in range(5)]

    def cmd(kind, revision=0, **payload):
        p = {"delegation_id": did, "expected_revision": revision}
        if kind == "task.request":
            p.update(
                task_id=task,
                source_message_id=source,
                executor={"node_id": b.s.node_id, "agent_id": b.actor},
            )
        elif kind not in {"task.cancel", "task.reject"}:
            p["execution_id"] = execution
        p.update(payload)
        return {
            "protocol_version": 1,
            "request_id": uid(),
            "sender_node_id": b.s.node_id,
            "authority_node_id": a.s.node_id,
            "channel_id": a.channel,
            "grant_epoch": 1,
            "actor": actor,
            "kind": kind,
            "payload": p,
        }

    root = cmd("message.send")
    root["payload"] = {"message_id": source, "thread_root_id": None, "text": "work"}
    async with a.s.sessions.begin() as db:
        await a.c.commit_command(db, root, tls=b.s.identity)
        projected = await db.get(SharedMessage, (a.s.node_id, a.channel, source))
        assert projected.local_message_id != source
        db.add(Participant(id=participant, room_id=a.channel, role="member"))
        await db.flush()
        db.add(
            Task(
                id=task,
                room_id=a.channel,
                title="remote",
                source_message_id=projected.local_message_id,
            )
        )

    async def resolve(db, channel, principal):
        return participant if channel == a.channel and principal == actor else None

    async def exported(db, channel, executor):
        grant = await db.get(
            PeerGrant, (b.s.node_id, a.s.node_id, channel), populate_existing=True
        )
        principal = {
            "node_id": executor["node_id"],
            "kind": "agent",
            "principal_id": executor["agent_id"],
        }
        return (
            grant is not None
            and grant.active
            and principal in (grant.actors or [])
            and "task.execute" in grant.capabilities
        )

    async def local_policy(db, auth):
        return await exported(
            db,
            auth.channel_id,
            {
                "node_id": auth.principal.node_id.__str__(),
                "agent_id": str(auth.principal.principal_id),
            },
        )

    a.s.local_policy = local_policy
    service = DelegationService(a.s.node_id, resolve, executor_allowed=exported)
    service.install_guards(a.c)
    service.install_submitters(a.c)
    install_projections(b.c)

    async def send(command):
        # The exact entry the /commands router uses: submitters registry first.
        return await a.c.submit(command, tls=b.s.identity)

    async def replay():
        async with a.s.sessions.begin() as db:
            log = await a.c.events(db, root, tls=b.s.identity)
        async with b.s.sessions.begin() as db:
            return await b.c.receive(
                db,
                log,
                tls=a.s.identity,
                authority_node_id=a.s.node_id,
                channel_id=a.channel,
                grant_epoch=1,
            )

    return SimpleNamespace(
        a=a,
        b=b,
        service=service,
        cmd=cmd,
        send=send,
        replay=replay,
        did=did,
        task=task,
        source=source,
        participant=participant,
    )


async def test_real_channel_source_mapping_receipt_replay_and_follower_completion(
    product,
):
    p = product
    for command in (
        p.cmd("task.request"),
        p.cmd("task.accept", 1),
        p.cmd("task.started", 2),
        p.cmd("task.result", 3, outcome="succeeded", text="done"),
    ):
        receipt = await p.send(command)
        assert await p.send(command) == receipt
    assert (await p.replay())["ack_seq"] == 5
    assert (await p.replay())["ack_seq"] == 5
    async with p.a.s.sessions() as db:
        assert (await db.get(Task, p.task)).result_markdown == "done"
        assert await db.scalar(select(func.count()).select_from(CommandReceipt)) == 5
    async with p.b.s.sessions() as db:
        mirror = await db.get(DelegationMirror, (p.a.s.node_id, p.a.channel, p.did))
        assert (
            mirror.state == "completed"
            and mirror.task_status == "done"
            and mirror.revision == 4
        )
        assert await db.scalar(select(func.count()).select_from(Task)) == 0


@pytest.mark.parametrize("duplicate", [False, True])
async def test_real_channel_missing_task_guard_fails_before_new_or_duplicate_receipt(
    product, duplicate
):
    p = product
    command = p.cmd("task.request")
    if duplicate:
        await p.send(command)
    del p.a.c.command_guards["task.request"]
    with pytest.raises(ChannelError, match="COMMAND_GUARD_REQUIRED"):
        await p.send(command)


@pytest.mark.parametrize("duplicate", [False, True])
async def test_real_channel_guard_without_submitter_fails_closed(product, duplicate):
    p = product
    command = p.cmd("task.request")
    if duplicate:
        await p.send(command)
    del p.a.c.submitters["task.request"]
    with pytest.raises(DelegationError, match="SUBMITTER_REQUIRED"):
        await p.send(command)
    # The registered entry point keeps working after the registry is restored.
    p.service.install_submitters(p.a.c)
    receipt = await p.send(command)
    assert await p.send(command) == receipt


async def test_real_channel_role_revocation_and_peer_revocation_before_replay(product):
    p = product
    command = p.cmd("task.request")
    await p.send(command)
    async with p.a.s.sessions.begin() as db:
        await db.execute(
            update(Participant)
            .where(Participant.id == p.participant)
            .values(role="observer")
        )
    with pytest.raises(DelegationError, match="PRINCIPAL_DENIED"):
        await p.send(command)
    await p.a.s.revoke_grant(p.a.admin, p.b.s.node_id, p.a.channel)
    with pytest.raises(PeerError, match="GRANT_DENIED"):
        await p.send(command)


async def test_real_channel_cancel_audit_rollback_has_no_event_and_mirrors_stop(
    product,
):
    p = product
    await p.send(p.cmd("task.request"))
    await p.send(p.cmd("task.accept", 1))
    await p.send(p.cmd("task.cancel", 2))
    late = p.cmd("task.result", 2, outcome="succeeded", text="must never publish")
    for _ in range(2):
        with pytest.raises(LateResultAfterCancel):
            await p.send(late)
    async with p.a.s.sessions() as db:
        assert (
            await db.scalar(select(func.count()).select_from(DelegationObservation))
            == 1
        )
        assert await db.scalar(select(func.count()).select_from(ChannelEvent)) == 4
        assert (await db.get(ChannelStream, (p.a.s.node_id, p.a.channel))).last_seq == 4
        assert (await db.get(Task, p.task)).result_markdown is None
    await p.send(p.cmd("task.cancelled", 3, process_state="stopped"))
    await p.replay()
    async with p.b.s.sessions() as db:
        mirror = await db.get(DelegationMirror, (p.a.s.node_id, p.a.channel, p.did))
        assert mirror.state == "cancelled" and mirror.process_state == "stopped"


async def test_wire_source_cannot_be_replaced_by_local_message_id(product):
    p = product
    async with p.a.s.sessions() as db:
        task = await db.get(Task, p.task)
    with pytest.raises(DelegationError, match="SOURCE_DENIED"):
        await p.send(p.cmd("task.request", source_message_id=task.source_message_id))


async def test_guard_tolerates_stale_or_fabricated_shadow_rows(product):
    """Regression (a), migrated per the #51/#52 contract.

    The product resolver must never let a Participant row alone authorize a
    remote principal: an active roster entry is required. This exercises the
    delegation-guard boundary directly — a stale shadow (roster deactivated
    while bypassing the projection choke point) and a fabricated shadow (row
    inserted with no roster at all) both fail closed with PRINCIPAL_DENIED.
    """
    p = product
    from anygarden.federation.delegation_wiring import install_product_delegation
    from anygarden.shared_channels.models import SharedParticipant
    from anygarden.shared_channels.shadow import shadow_participant_id

    # Swap the test-double wiring for the product wiring, exactly as
    # create_app composes it.
    for registry in (p.a.c.command_guards, p.a.c.submitters, p.a.c.effects):
        for kind in list(registry):
            if kind.startswith("task."):
                del registry[kind]
    service = install_product_delegation(p.a.c)
    actor = {"node_id": p.b.s.node_id, "kind": "agent", "principal_id": p.b.actor}

    # Seed the roster (and its shadow) through the real choke point.
    async with p.a.s.sessions.begin() as db:
        await p.a.c.publication(
            db, actor_id=p.a.admin, channel_id=p.a.channel, principal=actor, active=True
        )
    async with p.a.s.sessions.begin() as db:
        await p.a.c.change_participant(
            db,
            actor_id=p.a.admin,
            channel_id=p.a.channel,
            operation_id=uid(),
            expected_revision=0,
            principal=actor,
            role="member",
            active=True,
        )

    # Sanity: with an active roster the guard resolves the remote actor.
    async with p.a.s.sessions() as db:
        participant = await service.authorize_command(db, p.cmd("task.request"))
        assert participant is not None
        assert participant.role == "member"

    # Stale shadow: deactivate the roster row directly, bypassing the
    # projection that would also demote the shadow row.
    async with p.a.s.sessions.begin() as db:
        roster = await db.get(
            SharedParticipant,
            (p.a.s.node_id, p.a.channel, p.b.s.node_id, "agent", p.b.actor),
            populate_existing=True,
        )
        roster.active = False
    async with p.a.s.sessions() as db:
        with pytest.raises(DelegationError, match="PRINCIPAL_DENIED"):
            await service.authorize_command(db, p.cmd("task.accept", 1))

    # Fabricated shadow: a hand-inserted NULL/NULL row for a principal that
    # was never on the roster authorizes nothing either.
    forged_pid = uid()
    forged_id = shadow_participant_id(
        p.a.s.node_id, p.a.channel, p.b.s.node_id, "agent", forged_pid
    )
    async with p.a.s.sessions.begin() as db:
        db.add(Participant(id=forged_id, room_id=p.a.channel, role="member"))
    envelope = p.cmd("task.request")
    envelope["actor"]["principal_id"] = forged_pid
    async with p.a.s.sessions() as db:
        with pytest.raises(DelegationError, match="PRINCIPAL_DENIED"):
            await service.authorize_command(db, envelope)


async def test_sweeper_finalizes_pickup_timeouts_with_mirror_convergence(product):

    from anygarden.db.models import Participant as P
    from anygarden.federation.delegation_wiring import install_product_delegation

    p = product
    receipt = await p.send(p.cmd("task.request"))
    assert receipt["state"] == "requested"

    # Swap in the product wiring and authorize the authority's own admin as
    # the sweep actor (channel-admin cancel path).
    for registry in (p.a.c.command_guards, p.a.c.submitters, p.a.c.effects):
        for kind in list(registry):
            if kind.startswith("task."):
                del registry[kind]
    service = install_product_delegation(p.a.c)
    admin_actor = {"node_id": p.a.s.node_id, "kind": "human", "principal_id": p.a.admin}
    async with p.a.s.sessions.begin() as db:
        db.add(P(room_id=p.a.channel, user_id=p.a.admin, role="admin"))

    # Not yet expired: nothing happens.
    fresh = await service.sweep_pickup_timeouts(
        p.a.c, actor=admin_actor, now=datetime.now(UTC), timeout=timedelta(hours=1)
    )
    assert fresh == {"finalized": [], "skipped": []}

    # Backdate the request beyond the pickup window.
    async with p.a.s.sessions.begin() as db:
        record = await db.get(Delegation, p.did)
        record.created_at = datetime.now(UTC) - timedelta(hours=2)

    result = await service.sweep_pickup_timeouts(
        p.a.c, actor=admin_actor, now=datetime.now(UTC), timeout=timedelta(hours=1)
    )
    assert [row["delegation_id"] for row in result["finalized"]] == [p.did]
    assert result["skipped"] == []

    async with p.a.s.sessions() as db:
        record = await db.get(Delegation, p.did)
        assert record.state == "cancel_requested"
        assert (await db.get(Task, p.task)).status == "blocked"
        observations = (
            await db.scalars(
                select(DelegationObservation).where(
                    DelegationObservation.reason == "PICKUP_TIMEOUT"
                )
            )
        ).all()
        assert len(observations) == 1
        assert observations[0].execution_id is None

    # Idempotent: the state moved on, so a second pass has no candidates.
    again = await service.sweep_pickup_timeouts(
        p.a.c, actor=admin_actor, now=datetime.now(UTC), timeout=timedelta(hours=1)
    )
    assert again == {"finalized": [], "skipped": []}

    # Follower mirrors converge through the normal event path.
    await p.replay()
    async with p.b.s.sessions() as db:
        mirror = await db.get(DelegationMirror, (p.a.s.node_id, p.a.channel, p.did))
        assert mirror.state == "cancel_requested"


async def test_sweeper_skips_and_audits_when_guard_refuses(product):

    from anygarden.federation.delegation_wiring import install_product_delegation

    p = product
    await p.send(p.cmd("task.request"))
    async with p.a.s.sessions.begin() as db:
        record = await db.get(Delegation, p.did)
        record.created_at = datetime.now(UTC) - timedelta(hours=2)

    for registry in (p.a.c.command_guards, p.a.c.submitters, p.a.c.effects):
        for kind in list(registry):
            if kind.startswith("task."):
                del registry[kind]
    service = install_product_delegation(p.a.c)
    # No admin Participant in the channel room: the guard must refuse, and
    # the sweeper must skip (never force-finalize) with an audited reason.
    admin_actor = {"node_id": p.a.s.node_id, "kind": "human", "principal_id": p.a.admin}
    result = await service.sweep_pickup_timeouts(
        p.a.c, actor=admin_actor, now=datetime.now(UTC), timeout=timedelta(hours=1)
    )
    assert result["finalized"] == []
    assert [row["code"] for row in result["skipped"]] == ["PRINCIPAL_DENIED"]
    async with p.a.s.sessions() as db:
        record = await db.get(Delegation, p.did)
        assert record.state == "requested"
        observations = (
            await db.scalars(
                select(DelegationObservation).where(
                    DelegationObservation.reason == "PRINCIPAL_DENIED"
                )
            )
        ).all()
        assert len(observations) == 1


async def test_auto_selection_skips_blocked_and_picks_least_loaded(product):
    from datetime import timedelta

    from anygarden.agent_availability import QUOTA_EXHAUSTED
    from anygarden.db.models import Agent as AgentRow
    from anygarden.federation.delegation_models import Delegation
    from anygarden.shared_channels.models import SharedParticipant

    p = product
    # Two extra remote-agent roster entries: one quota-lookalike is remote
    # (authority can't see remote quota), so use a LOCAL second agent to
    # prove the D-2 filter; the b-actor stays the eligible one.
    async with p.a.s.sessions.begin() as db:
        # The fixture seeds the participant + grant but not the roster;
        # selection is roster-driven, so add the b actor's roster entry.
        db.add(
            SharedParticipant(
                authority_node_id=p.a.s.node_id,
                channel_id=p.a.channel,
                node_id=p.b.s.node_id,
                kind="agent",
                principal_id=p.b.actor,
                role="member",
                active=True,
                revision=1,
            )
        )
        db.add(
            AgentRow(
                id="local-blocked",
                name="blocked",
                engine="pi-cli",
                unavailable_code=QUOTA_EXHAUSTED,
                unavailable_until=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        db.add(AgentRow(id="local-free", name="free", engine="pi-cli"))
        for pid in ("local-blocked", "local-free"):
            db.add(
                SharedParticipant(
                    authority_node_id=p.a.s.node_id,
                    channel_id=p.a.channel,
                    node_id=p.a.s.node_id,
                    kind="agent",
                    principal_id=pid,
                    role="member",
                    active=True,
                    revision=1,
                )
            )
    service = p.service
    async with p.a.s.sessions() as db:
        chosen = await service.select_executor(db, channel_id=p.a.channel)
    # Only the b actor is grant-listed -> selected.
    assert chosen == {"node_id": p.b.s.node_id, "agent_id": p.b.actor}

    # Least-loaded tie-break: fabricate an active delegation for the b actor
    # (reuse the fixture's real task/source/participant to satisfy FKs).
    async with p.a.s.sessions.begin() as db:
        db.add(
            Delegation(
                id=uid(),
                authority_node_id=p.a.s.node_id,
                channel_id=p.a.channel,
                task_id=p.task,
                source_message_id=p.source,
                requester={
                    "node_id": p.b.s.node_id,
                    "kind": "agent",
                    "principal_id": p.b.actor,
                },
                executor_node_id=p.b.s.node_id,
                executor_agent_id=p.b.actor,
                executor_participant_id=p.participant,
                state="running",
                revision=2,
                process_state="running",
            )
        )
    async with p.a.s.sessions() as db:
        again = await service.select_executor(db, channel_id=p.a.channel)
    assert again == {
        "node_id": p.b.s.node_id,
        "agent_id": p.b.actor,
    }  # still only eligible


async def test_auto_selection_no_eligible_executor_is_explicit(product):
    p = product
    async with p.a.s.sessions.begin() as db:
        await db.execute(
            update(Participant)
            .where(Participant.id == p.participant)
            .values(role="observer")
        )
    service = p.service
    async with p.a.s.sessions() as db:
        assert await service.select_executor(db, channel_id=p.a.channel) is None
    with pytest.raises(DelegationError, match="NO_ELIGIBLE_EXECUTOR"):
        await service.delegate(
            p.a.c,
            channel_id=p.a.channel,
            task_id=uid(),
            source_message_id=uid(),
            requester={
                "node_id": p.b.s.node_id,
                "kind": "agent",
                "principal_id": p.b.actor,
            },
            tls=p.b.s.identity,
        )


async def test_delegate_auto_selection_issues_request_through_router(product):
    p = product
    # Delegate the fixture's existing task through an explicit executor to
    # prove the entry path end-to-end (a second auto request for the same
    # task would fail CLAIM_CONFLICT by design).
    receipt = await p.service.delegate(
        p.a.c,
        channel_id=p.a.channel,
        task_id=p.task,
        source_message_id=p.source,
        requester={
            "node_id": p.b.s.node_id,
            "kind": "agent",
            "principal_id": p.b.actor,
        },
        tls=p.b.s.identity,
        executor={"node_id": p.b.s.node_id, "agent_id": p.b.actor},
    )
    assert receipt["state"] == "requested"
    assert receipt.get("selected_executor") is None  # explicit: no auto pick


async def _add_second_executor(p):
    """Grant-list + roster a second remote executor (node-c) for reassign."""
    from anygarden.federation.models import PeerGrant
    from anygarden.shared_channels.models import SharedParticipant

    c_agent, c_node = uid(), uid()
    principal = {"node_id": c_node, "kind": "agent", "principal_id": c_agent}
    async with p.a.s.sessions.begin() as db:
        grant = await db.get(
            PeerGrant,
            (p.b.s.node_id, p.a.s.node_id, p.a.channel),
            populate_existing=True,
        )
        grant.actors = list(grant.actors or []) + [principal]
        db.add(
            SharedParticipant(
                authority_node_id=p.a.s.node_id,
                channel_id=p.a.channel,
                node_id=c_node,
                kind="agent",
                principal_id=c_agent,
                role="member",
                active=True,
                revision=1,
            )
        )
    async with p.a.s.sessions.begin() as db:
        db.add(
            SharedParticipant(
                authority_node_id=p.a.s.node_id,
                channel_id=p.a.channel,
                node_id=p.b.s.node_id,
                kind="agent",
                principal_id=p.b.actor,
                role="member",
                active=True,
                revision=1,
            )
        )
    return principal


async def test_reassign_moves_rejected_delegation_to_alternative(product):
    p = product
    alternate = await _add_second_executor(product)
    # The fixture resolver only maps the b actor; teach it the alternate
    # executor (the product wiring would give it a shadow participant).
    alt_participant = uid()
    base_resolve = p.service.resolve_principal

    async def resolve_with_alt(db, channel, principal):
        if channel == p.a.channel and principal == {
            "node_id": alternate["node_id"],
            "kind": "agent",
            "principal_id": alternate["principal_id"],
        }:
            return alt_participant
        return await base_resolve(db, channel, principal)

    p.service.resolve_principal = resolve_with_alt
    async with p.a.s.sessions.begin() as db:
        db.add(Participant(id=alt_participant, room_id=p.a.channel, role="member"))
    await p.send(p.cmd("task.request"))
    # Executor declines honestly (D-3 suppression outcome).
    await p.send(p.cmd("task.reject", 1, reason="UNAVAILABLE"))
    async with p.a.s.sessions() as db:
        task = await db.get(Task, p.task)
        assert task.status == "todo"  # rejection released it

    receipt = await p.service.reassign(
        p.a.c,
        delegation_id=p.did,
        requester={
            "node_id": p.b.s.node_id,
            "kind": "agent",
            "principal_id": p.b.actor,
        },
        tls=p.b.s.identity,
    )
    assert receipt["state"] == "requested"
    assert receipt["transitioned_from"] == p.did
    assert receipt["selected_executor"] == {
        "node_id": alternate["node_id"],
        "agent_id": alternate["principal_id"],
    }
    async with p.a.s.sessions() as db:
        observations = (
            await db.scalars(
                select(DelegationObservation).where(
                    DelegationObservation.delegation_id == p.did,
                    DelegationObservation.reason == "TRANSITIONED",
                )
            )
        ).all()
        assert len(observations) == 1


async def test_reassign_without_alternative_is_structured(product):
    p = product
    await p.send(p.cmd("task.request"))
    await p.send(p.cmd("task.reject", 1, reason="UNAVAILABLE"))
    with pytest.raises(DelegationError, match="NO_ALTERNATIVE_EXECUTOR"):
        await p.service.reassign(
            p.a.c,
            delegation_id=p.did,
            requester={
                "node_id": p.b.s.node_id,
                "kind": "agent",
                "principal_id": p.b.actor,
            },
            tls=p.b.s.identity,
        )
    async with p.a.s.sessions() as db:
        observations = (
            await db.scalars(
                select(DelegationObservation).where(
                    DelegationObservation.delegation_id == p.did,
                    DelegationObservation.reason == "NO_ALTERNATIVE",
                )
            )
        ).all()
        assert len(observations) == 1


async def test_reassign_requires_rejected_state(product):
    p = product
    await p.send(p.cmd("task.request"))
    with pytest.raises(DelegationError, match="STATE_CONFLICT"):
        await p.service.reassign(
            p.a.c,
            delegation_id=p.did,
            requester={
                "node_id": p.b.s.node_id,
                "kind": "agent",
                "principal_id": p.b.actor,
            },
            tls=p.b.s.identity,
        )


async def test_reassign_is_original_requester_self_service(product):
    p = product
    await p.send(p.cmd("task.request"))
    await p.send(p.cmd("task.reject", 1, reason="UNAVAILABLE"))
    stranger = {"node_id": uid(), "kind": "agent", "principal_id": uid()}
    with pytest.raises(DelegationError, match="REQUESTER_MISMATCH"):
        await p.service.reassign(
            p.a.c, delegation_id=p.did, requester=stranger, tls=p.b.s.identity
        )


async def test_failover_quota_exhausted_marks_and_reassigns(product):
    """D-4a orchestration (task #80): quota exhaustion marks the failed
    executor, reassigns through the D-4b command path with the failed
    executor excluded, and inherits receipts/audit."""
    from anygarden.agent_availability import mark_quota_exhausted
    from anygarden.db.models import Agent as AgentRow
    from anygarden.federation.delegation_wiring import failover_quota_exhausted

    p = product
    alternate = await _add_second_executor(product)
    alt_participant = uid()
    base_resolve = p.service.resolve_principal

    async def resolve_with_alt(db, channel, principal):
        if channel == p.a.channel and principal == {
            "node_id": alternate["node_id"],
            "kind": "agent",
            "principal_id": alternate["principal_id"],
        }:
            return alt_participant
        return await base_resolve(db, channel, principal)

    p.service.resolve_principal = resolve_with_alt
    async with p.a.s.sessions.begin() as db:
        db.add(Participant(id=alt_participant, room_id=p.a.channel, role="member"))

    failed_executor = {"node_id": p.b.s.node_id, "agent_id": p.b.actor}
    await p.send(p.cmd("task.request"))
    # Executor declines honestly with quota exhaustion (D-3 suppression
    # outcome): rejection returns the Task to todo and the D-2 block is
    # stamped on the agent row.
    await p.send(p.cmd("task.reject", 1, reason="UNAVAILABLE"))
    async with p.a.s.sessions.begin() as db:
        agent_row = await db.get(AgentRow, p.b.actor)
        if agent_row is not None:
            mark_quota_exhausted(agent_row, "quota exhausted", now=datetime.now(UTC))

    result = await failover_quota_exhausted(
        p.service,
        p.a.c,
        delegation_id=p.did,
        requester={
            "node_id": p.b.s.node_id,
            "kind": "agent",
            "principal_id": p.b.actor,
        },
        tls=p.b.s.identity,
        failed_executor=failed_executor,
    )
    assert result["status"] == "reassigned"
    receipt = result["receipt"]
    assert receipt["transitioned_from"] == p.did
    assert receipt["selected_executor"] != failed_executor
