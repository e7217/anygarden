"""Product wiring for the delegation coordinator (task #51).

Constructs the real ``DelegationService`` callbacks that previously existed
only as integration-test doubles, and installs guards/submitters/projections
on a composed ``ChannelService``. Mounted by ``_compose_federation_services``
— never when sharing is disabled.

Contract (architect GO task #52, dev01 review):
- ``resolve_principal``: local user/agent principals resolve through their
  real Participant rows; remote principals resolve through the **active
  roster + deterministic shadow row** (never a local credential).
- ``executor_allowed``: a remote executor needs the current inbound grant
  (active, unexpired, task.execute capability, agent principal listed among
  its actors); a local executor needs a valid-role Participant row plus an
  active roster entry. Re-evaluated per command — the shadow itself never
  authorizes anything.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Participant
from anygarden.federation.delegation import DelegationService
from anygarden.federation.delegation_projection import install_projections
from anygarden.federation.models import PeerGrant
from anygarden.federation.service import now
from anygarden.shared_channels.models import SharedParticipant
from anygarden.shared_channels.shadow import FENCED_ROLES, shadow_participant_id

TASK_KINDS = (
    "task.request",
    "task.accept",
    "task.reject",
    "task.started",
    "task.result",
    "task.cancel",
    "task.cancelled",
    "task.unknown",
)


async def _local_participant_id(
    db: AsyncSession, channel_id: str, kind: str, principal_id: str
):
    column = Participant.user_id if kind == "human" else Participant.agent_id
    return await db.scalar(
        select(Participant.id).where(
            Participant.room_id == channel_id,
            column == principal_id,
            Participant.role.in_(FENCED_ROLES),
        )
    )


async def _remote_participant_id(
    db: AsyncSession, node_id: str, channel_id: str, principal: dict
):
    roster = await db.get(
        SharedParticipant,
        (
            node_id,
            channel_id,
            principal["node_id"],
            principal["kind"],
            principal["principal_id"],
        ),
    )
    if roster is None or not roster.active or roster.role not in FENCED_ROLES:
        return None
    shadow_id = shadow_participant_id(
        node_id,
        channel_id,
        principal["node_id"],
        principal["kind"],
        principal["principal_id"],
    )
    return await db.scalar(
        select(Participant.id).where(
            Participant.id == shadow_id,
            Participant.room_id == channel_id,
            Participant.role.in_(FENCED_ROLES),
        )
    )


async def _grant_allows(
    db: AsyncSession, node_id: str, channel_id: str, executor: dict
) -> bool:
    # On the authority (this node, ``node_id``), an inbound grant from a
    # remote executor node E is stored as (peer_node_id=E,
    # authority_node_id=<this node>, channel) — the executor's node never
    # occupies the authority slot (task #53 P1 correction).
    grant = await db.get(
        PeerGrant,
        (executor["node_id"], node_id, channel_id),
        populate_existing=True,
    )
    if grant is None or not grant.active or grant.expires_at <= now():
        return False
    if "task.execute" not in (grant.capabilities or []):
        return False
    return any(
        actor.get("node_id") == executor["node_id"]
        and actor.get("kind") == "agent"
        and actor.get("principal_id") == executor["agent_id"]
        for actor in (grant.actors or [])
    )


def install_product_delegation(channel_service) -> DelegationService:
    """Install the product delegation coordinator on a composed service.

    Guards, submitters and projections are installed together on every node:
    each checks ``authority_node_id`` itself, so authority and mirror roles
    route correctly on a single ChannelService.
    """
    node_id = channel_service.node_id

    async def resolve_principal(db, channel_id, principal):
        if principal["node_id"] == node_id:
            return await _local_participant_id(
                db, channel_id, principal["kind"], principal["principal_id"]
            )
        return await _remote_participant_id(db, node_id, channel_id, principal)

    async def executor_allowed(db, channel_id, executor):
        if executor["node_id"] != node_id:
            return await _grant_allows(db, node_id, channel_id, executor)
        # Local executor: real agent participant row + active roster entry.
        participant_ok = await _local_participant_id(
            db, channel_id, "agent", executor["agent_id"]
        )
        if participant_ok is None:
            return False
        roster = await db.get(
            SharedParticipant,
            (node_id, channel_id, node_id, "agent", executor["agent_id"]),
        )
        return roster is not None and roster.active

    service = DelegationService(
        node_id, resolve_principal, executor_allowed=executor_allowed
    )
    service.install_guards(channel_service)
    service.install_submitters(channel_service)
    install_projections(channel_service)
    return service
