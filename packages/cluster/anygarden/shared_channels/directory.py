"""Fresh, opt-in display metadata from a channel's pinned peers.

Names are display data only. They never stand in for executor identity or a
current execution permit. A mirror exports only its locally approved actors
back to the authority that issued its active channel grant.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from sqlalchemy import select

from anygarden.db.models import Agent, Participant, Room
from anygarden.federation.certificates import inspect_certificate
from anygarden.federation.errors import PeerError
from anygarden.federation.models import PeerGrant
from anygarden.federation.schemas import Endpoint, Principal
from anygarden.federation.transport import post
from anygarden.shared_channels.models import PublicationConsent, SharedParticipant
from anygarden.shared_channels.schemas import ChannelError

ROLES = {"member", "admin", "owner"}


class TargetDisplay(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: UUID
    agent_id: UUID
    name: str | None = Field(max_length=500)
    node_name: str | None = Field(max_length=500)
    description: str | None = Field(max_length=16384)
    can_execute: StrictBool
    unavailable_code: str | None = Field(max_length=100)


async def runtime_unavailable(service, db, agent):
    if agent.engine not in {"codex-cli", "pi-cli"}:
        return "unsupported_runtime"
    transport = getattr(service, "execution_transport", None)
    if agent.placed_on_machine_id is None or transport is None:
        return "execution_unavailable"
    candidates = list(
        await db.scalars(
            select(Participant.id)
            .join(Room, Participant.room_id == Room.id)
            .where(
                Participant.agent_id == agent.id,
                Participant.role.in_(ROLES),
                Room.is_dm.is_(True),
                Room.representative_agent_id == agent.id,
                Room.archived_at.is_(None),
            )
        )
    )
    if len(candidates) != 1:
        return "execution_unavailable"
    connection = await transport.manager.execution_connection(candidates[0])
    if connection is None or connection[0] != agent.generation:
        return "execution_unavailable"
    return None


async def local_targets(service, db, stream):
    rows = await db.scalars(
        select(SharedParticipant).where(
            SharedParticipant.authority_node_id == stream.authority_node_id,
            SharedParticipant.channel_id == stream.channel_id,
            SharedParticipant.node_id == service.node_id,
            SharedParticipant.kind == "agent",
            SharedParticipant.active.is_(True),
        )
    )
    grant = None
    if stream.authority_node_id != service.node_id:
        _, grant = await service.mirror_policy(
            db, stream.authority_node_id, stream.channel_id
        )
    result = []
    for row in rows:
        principal = {
            "node_id": row.node_id,
            "kind": "agent",
            "principal_id": row.principal_id,
        }
        if grant and principal not in grant.actors:
            continue
        agent = await db.get(Agent, row.principal_id)
        participant = await db.scalar(
            select(Participant).where(
                Participant.room_id == stream.local_room_id,
                Participant.agent_id == row.principal_id,
            )
        )
        reason = None
        if agent is None or participant is None:
            reason = "agent_missing"
        elif row.role not in ROLES or participant.role not in ROLES:
            reason = "role_denied"
        elif grant and (
            grant.role == "observer" or "task.execute" not in grant.capabilities
        ):
            reason = "execution_denied"
        elif not grant:
            consent = await db.get(
                PublicationConsent,
                (
                    stream.authority_node_id,
                    stream.channel_id,
                    service.node_id,
                    "agent",
                    row.principal_id,
                ),
                populate_existing=True,
            )
            if consent is None or not consent.active:
                reason = "publication_denied"
            else:
                try:
                    await service._admin(db, consent.approved_by)
                except ChannelError:
                    reason = "publication_denied"
        if reason is None and agent:
            reason = agent.unavailable_code or (
                "agent_not_running"
                if agent.actual_state != "running" or agent.desired_state != "running"
                else None
            )
        if reason is None and agent:
            reason = await runtime_unavailable(service, db, agent)
        result.append(
            {
                "node_id": service.node_id,
                "agent_id": row.principal_id,
                "name": agent.name if agent else None,
                "node_name": None,
                "description": agent.description if agent else None,
                "can_execute": reason is None,
                "unavailable_code": reason,
            }
        )
    return result


async def peer_directory(service, db, scope, tls):
    authority, channel = scope["authority_node_id"], scope["channel_id"]
    if authority == service.node_id:
        await service._authorize(db, tls, scope, "channel.read")
        actor = scope["actor"]
        roster = await db.get(
            SharedParticipant,
            (
                authority,
                channel,
                actor["node_id"],
                actor["kind"],
                actor["principal_id"],
            ),
            populate_existing=True,
        )
        if roster is None or not roster.active:
            raise ChannelError("PRINCIPAL_DENIED", 403)
        stream = await service._stream(db, authority, channel, owner=True)
    else:
        # This reverse read is only for the authority. The independent local
        # consent limits which local agents may be described to that node.
        if scope["sender_node_id"] != authority:
            raise ChannelError("WRONG_AUTHORITY", 403)
        await service.peers.authorize_delivery(
            db,
            tls,
            authority_node_id=authority,
            channel_id=channel,
            grant_epoch=scope["grant_epoch"],
        )
        stream = await service._stream(db, authority, channel)
    return {
        "authority_node_id": authority,
        "channel_id": channel,
        "targets": await local_targets(service, db, stream),
    }


async def _target_peer(service, db, identity, authority, channel, node):
    await service.local_access(
        db, identity=identity, authority=authority, channel=channel
    )
    if authority != service.node_id:
        if node != authority:
            raise ChannelError("DIRECTORY_UNAVAILABLE", 403)
        peer, grant = await service.mirror_policy(db, authority, channel)
    else:
        peer = await service.peers.lock_peer(db, node)
        grant = await db.get(
            PeerGrant, (node, authority, channel), populate_existing=True
        )
        if grant is None:
            raise ChannelError("CHANNEL_DENIED", 403)
        # Reuse the full pin/consent/approver/scope gate before and after I/O.
        actor = next((a for a in grant.actors if a["kind"] == "agent"), None)
        if actor is None:
            raise ChannelError("EXECUTOR_DENIED", 403)
        await service.peers.authorize(
            db,
            inspect_certificate(peer.certificate_pem),
            sender_node_id=node,
            authority_node_id=authority,
            channel_id=channel,
            principal=Principal(**actor),
            action="channel.read",
            grant_epoch=grant.epoch,
        )
    return peer, grant


async def enrich_remote_targets(service, identity, snapshot):
    authority, channel = snapshot["authority_node_id"], snapshot["channel_id"]
    nodes = {
        t["node_id"] for t in snapshot["targets"] if t["node_id"] != service.node_id
    }

    async def fetch(node):
        try:
            async with service.sessions.begin() as db:
                peer, grant = await _target_peer(
                    service, db, identity, authority, channel, node
                )
                endpoint, pem, epoch, peer_epoch = (
                    Endpoint(**peer.endpoint),
                    peer.certificate_pem,
                    grant.epoch,
                    peer.epoch,
                )
                scope = {
                    "protocol_version": 1,
                    "sender_node_id": service.node_id,
                    "authority_node_id": authority,
                    "channel_id": channel,
                    "grant_epoch": epoch,
                    "actor": service.local_principal(identity),
                }
            result = await post(
                service.peers,
                endpoint,
                pem,
                "/api/v1/federation/channels/directory",
                scope,
            )
            async with service.sessions.begin() as db:
                current, grant = await _target_peer(
                    service, db, identity, authority, channel, node
                )
                if (
                    current.epoch != peer_epoch
                    or current.certificate_pem != pem
                    or grant.epoch != epoch
                ):
                    return []
            if (
                result.get("authority_node_id") != authority
                or result.get("channel_id") != channel
                or not isinstance(result.get("targets"), list)
                or len(result["targets"]) > 100
            ):
                return []
            displays = [
                TargetDisplay.model_validate(t).model_dump(mode="json")
                for t in result["targets"]
            ]
            address = urlsplit(endpoint.url).netloc
            return [
                {**t, "server_label": address, "is_local": False}
                for t in displays
                if t["node_id"] == node
            ]
        except (ChannelError, PeerError, ValidationError):
            return []

    results = await asyncio.gather(*(fetch(node) for node in sorted(nodes)))
    by_id = {(t["node_id"], t["agent_id"]): t for values in results for t in values}
    merged = []
    for target in snapshot["targets"]:
        display = by_id.get((target["node_id"], target["agent_id"]))
        if display and target["unavailable_code"] == "role_denied":
            display = {
                **display,
                "can_execute": False,
                "unavailable_code": "role_denied",
            }
        merged.append(display or target)
    snapshot["targets"] = merged
    for message in snapshot["messages"]:
        actor = message["actor"]
        if actor["kind"] == "agent" and actor["node_id"] != service.node_id:
            target = by_id.get((actor["node_id"], actor["principal_id"]))
            if target:
                message["actor_name"] = target["name"]
    from anygarden.shared_channels.product import snapshot_access

    async with service.sessions.begin() as db:
        await snapshot_access(service, db, identity, authority, channel)
    return snapshot
