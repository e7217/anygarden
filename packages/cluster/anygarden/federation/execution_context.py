"""Fresh authority context and pinned transport for deployed executors."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    model_validator,
)
from sqlalchemy import select

from anygarden.auth.dependencies import Identity
from anygarden.db.models import Message, Participant, Task
from anygarden.federation.delegation import DelegationError
from anygarden.federation.delegation_models import Delegation
from anygarden.federation.schemas import Endpoint
from anygarden.federation.transport import post
from anygarden.shared_channels.models import (
    PublicationConsent,
    SharedMessage,
    SharedParticipant,
)
from anygarden.shared_channels.schemas import ChannelError, validate


class ContextExecutor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    node_id: UUID
    agent_id: UUID


class ExecutionContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol_version: StrictInt = Field(ge=1, le=1)
    authority_node_id: UUID
    channel_id: UUID
    delegation_id: UUID
    task_id: UUID
    source_message_id: UUID
    executor: ContextExecutor
    execution_id: UUID | None
    revision: StrictInt = Field(ge=1)
    state: Literal[
        "requested",
        "accepted",
        "running",
        "completed",
        "failed",
        "rejected",
        "cancel_requested",
        "cancelled",
        "unknown",
    ]
    process_state: Literal["not_started", "unknown", "running", "finished", "stopped"]
    task_title: str = Field(max_length=500, strict=True)
    prompt: str = Field(max_length=16384, strict=True)
    grant_epoch: StrictInt = Field(ge=1)
    peer_epoch: StrictInt = Field(ge=0)
    policy_epoch: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def consistent_state(self):
        if (
            self.state in {"accepted", "running", "completed", "failed", "unknown"}
            and self.execution_id is None
        ):
            raise ValueError("missing execution identity")
        if self.state in {"requested", "rejected"} and self.execution_id is not None:
            raise ValueError("unexpected execution identity")
        expected = {
            "requested": {"not_started"},
            "rejected": {"not_started"},
            "accepted": {"unknown"},
            "running": {"running"},
            "completed": {"finished"},
            "failed": {"finished"},
            "cancelled": {"not_started", "stopped"},
            "unknown": {"unknown"},
            "cancel_requested": {"not_started", "unknown", "running"},
        }
        if self.state in expected and self.process_state not in expected[self.state]:
            raise ValueError("inconsistent process state")
        return self


def validate_context(result):
    try:
        return ExecutionContext.model_validate(result).model_dump(mode="json")
    except ValidationError:
        raise DelegationError("INVALID_CONTEXT") from None


async def require_local_executor(service, db, identity, authority, channel):
    stream = await service.local_access(
        db, identity=identity, authority=authority, channel=channel, write=True
    )
    principal = service.local_principal(identity)
    roster = await db.get(
        SharedParticipant,
        (authority, channel, service.node_id, "agent", identity.id),
        populate_existing=True,
    )
    participant = await db.scalar(
        select(Participant).where(
            Participant.room_id == stream.local_room_id,
            Participant.agent_id == identity.id,
            Participant.role.in_(("member", "admin", "owner")),
        )
    )
    if (
        identity.kind != "agent"
        or participant is None
        or roster is None
        or not roster.active
        or roster.role not in {"member", "admin", "owner"}
    ):
        raise DelegationError("EXECUTOR_DENIED")
    if authority == service.node_id:
        consent = await db.get(
            PublicationConsent,
            (authority, channel, service.node_id, "agent", identity.id),
            populate_existing=True,
        )
        if consent is None or not consent.active:
            raise ChannelError("PUBLICATION_DENIED", 403)
        await service._admin(db, consent.approved_by)
        return stream, None, None
    peer, grant = await service.mirror_policy(db, authority, channel)
    if (
        principal not in grant.actors
        or "task.execute" not in grant.capabilities
        or grant.role == "observer"
    ):
        raise DelegationError("EXECUTOR_DENIED")
    return stream, peer, grant


async def authority_execution_context(
    service, db, scope, delegation_id, *, tls=None, identity=None
):
    """Read status/prompt only for the freshly authorized assigned executor."""
    authority, channel = scope["authority_node_id"], scope["channel_id"]
    if authority != service.node_id or scope["actor"]["kind"] != "agent":
        raise DelegationError("EXECUTOR_DENIED")
    if identity is None:
        authorization = await service._authorize(db, tls, scope, "task.execute")
        epochs = {
            "grant_epoch": authorization.grant_epoch,
            "peer_epoch": authorization.certificate_epoch,
            "policy_epoch": authorization.policy_epoch,
        }
        await service._stream(db, authority, channel, owner=True)
    else:
        if scope["actor"] != service.local_principal(identity):
            raise DelegationError("EXECUTOR_DENIED")
        await require_local_executor(service, db, identity, authority, channel)
        epochs = {"grant_epoch": 1, "peer_epoch": 0, "policy_epoch": 0}
    coordinator = getattr(service, "delegation_service", None)
    if coordinator is None:
        raise ChannelError("COMMAND_GUARD_REQUIRED", 503)
    participant = await coordinator._participant(db, channel, scope["actor"])
    record = await db.get(Delegation, delegation_id, populate_existing=True)
    if (
        record is None
        or record.authority_node_id != authority
        or record.channel_id != channel
    ):
        raise DelegationError("DELEGATION_MISSING")
    executor = {
        "node_id": record.executor_node_id,
        "agent_id": record.executor_agent_id,
    }
    if (
        scope["actor"]
        != {
            "node_id": executor["node_id"],
            "kind": "agent",
            "principal_id": executor["agent_id"],
        }
        or participant.id != record.executor_participant_id
        or not await coordinator.executor_allowed(db, channel, executor)
    ):
        raise DelegationError("EXECUTOR_DENIED")
    source = await db.get(SharedMessage, (authority, channel, record.source_message_id))
    task = await db.get(Task, record.task_id, populate_existing=True)
    message = await db.get(Message, source.local_message_id) if source else None
    if (
        source is None
        or source.thread_root_id is not None
        or task is None
        or message is None
        or message.room_id != channel
        or message.parent_message_id is not None
        or task.room_id != channel
        or task.source_message_id != source.local_message_id
    ):
        raise DelegationError("SOURCE_DENIED")
    return validate_context(
        {
            "protocol_version": 1,
            "authority_node_id": authority,
            "channel_id": channel,
            "delegation_id": record.id,
            "task_id": record.task_id,
            "source_message_id": record.source_message_id,
            "executor": executor,
            "execution_id": record.execution_id,
            "revision": record.revision,
            "state": record.state,
            "process_state": record.process_state,
            "task_title": task.title,
            "prompt": task.spec if task.spec is not None else message.content,
            **epochs,
        }
    )


async def resolve_execution_context(
    service, *, authority_node_id, channel_id, delegation_id, agent_id
):
    identity = Identity(kind="agent", id=agent_id)
    async with service.sessions.begin() as db:
        _, peer, grant = await require_local_executor(
            service, db, identity, authority_node_id, channel_id
        )
        scope = {
            "protocol_version": 1,
            "sender_node_id": service.node_id,
            "authority_node_id": authority_node_id,
            "channel_id": channel_id,
            "grant_epoch": grant.epoch if grant else 1,
            "actor": service.local_principal(identity),
        }
        if authority_node_id == service.node_id:
            result = await authority_execution_context(
                service, db, scope, delegation_id, identity=identity
            )
            return {**result, "local_peer_epoch": 0, "local_policy_epoch": 0}
        endpoint, pem = Endpoint(**peer.endpoint), peer.certificate_pem
    result = await post(
        service.peers,
        endpoint,
        pem,
        f"/api/v1/federation/channels/delegations/{delegation_id}/execution-context",
        scope,
    )
    async with service.sessions.begin() as db:
        await require_local_executor(
            service, db, identity, authority_node_id, channel_id
        )
        from anygarden.federation.certificates import inspect_certificate

        local_auth = await service.peers.authorize_delivery(
            db,
            inspect_certificate(pem),
            authority_node_id=authority_node_id,
            channel_id=channel_id,
            grant_epoch=scope["grant_epoch"],
        )
    result = validate_context(result)
    if (
        result.get("authority_node_id") != authority_node_id
        or result.get("channel_id") != channel_id
        or result.get("delegation_id") != delegation_id
        or result.get("executor") != {"node_id": service.node_id, "agent_id": agent_id}
        or result.get("grant_epoch") != scope["grant_epoch"]
        or result["peer_epoch"] < 1
        or result["policy_epoch"] < 1
    ):
        raise DelegationError("INVALID_CONTEXT")
    return {
        **result,
        "local_peer_epoch": local_auth["certificate_epoch"],
        "local_policy_epoch": local_auth["policy_epoch"],
    }


async def send_executor_command(service, envelope):
    """Send a durable executor command with its existing idempotency identity."""
    validate("command", envelope)
    principal = envelope["actor"]
    if (
        principal["kind"] != "agent"
        or principal["node_id"] != service.node_id
        or envelope["sender_node_id"] != service.node_id
    ):
        raise DelegationError("EXECUTOR_DENIED")
    identity = Identity(kind="agent", id=principal["principal_id"])
    authority, channel = envelope["authority_node_id"], envelope["channel_id"]
    async with service.sessions.begin() as db:
        _, peer, grant = await require_local_executor(
            service, db, identity, authority, channel
        )
        if authority == service.node_id:
            return await service.submit_local(db, envelope, identity=identity)
        if envelope["grant_epoch"] != grant.epoch:
            raise DelegationError("GRANT_DENIED")
        endpoint, pem = Endpoint(**peer.endpoint), peer.certificate_pem
    receipt = await post(
        service.peers, endpoint, pem, "/api/v1/federation/channels/commands", envelope
    )
    validate("receipt", receipt)
    if any(
        receipt.get(key) != envelope[key]
        for key in ("request_id", "authority_node_id", "channel_id")
    ):
        raise DelegationError("INVALID_RECEIPT")
    async with service.sessions.begin() as db:
        await require_local_executor(service, db, identity, authority, channel)
        from anygarden.federation.certificates import inspect_certificate

        await service.peers.authorize_delivery(
            db,
            inspect_certificate(pem),
            authority_node_id=authority,
            channel_id=channel,
            grant_epoch=envelope["grant_epoch"],
        )
    return receipt
