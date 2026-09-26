"""Authenticated user facade for shared messages and source-backed delegation."""

from __future__ import annotations

import json
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select

from anygarden.db.models import Agent, Participant, Task, User
from anygarden.federation.delegation import TASK_STATUS, source_task_id
from anygarden.federation.delegation_models import Delegation, DelegationMirror
from anygarden.shared_channels.models import (
    ChannelEvent,
    ChannelSubmission,
    CommandReceipt,
    InboxEvent,
    PublicationConsent,
    SharedMessage,
    SharedParticipant,
)
from anygarden.shared_channels.schemas import ChannelError

EXECUTION_ROLES = {"member", "admin", "owner"}
CANCELLABLE = {"requested", "accepted", "running", "unknown"}


def derived_id(purpose, authority, channel, request_id):
    return str(
        uuid5(
            NAMESPACE_URL, f"anygarden:{purpose}:v1:{authority}:{channel}:{request_id}"
        )
    )


async def snapshot_access(service, db, identity, authority, channel):
    stream = await service.local_access(
        db, identity=identity, authority=authority, channel=channel
    )
    if authority == service.node_id:
        principal = service.local_principal(identity)
        roster = await db.get(
            SharedParticipant,
            (
                authority,
                channel,
                principal["node_id"],
                principal["kind"],
                principal["principal_id"],
            ),
            populate_existing=True,
        )
        if roster is None or not roster.active:
            raise ChannelError("PRINCIPAL_DENIED", 403)
    return stream


async def actor_policy(service, db, identity, stream):
    principal = service.local_principal(identity)
    roster = await db.get(
        SharedParticipant,
        (
            stream.authority_node_id,
            stream.channel_id,
            principal["node_id"],
            principal["kind"],
            principal["principal_id"],
        ),
        populate_existing=True,
    )
    column = Participant.user_id if identity.kind == "user" else Participant.agent_id
    participant = await db.scalar(
        select(Participant).where(
            Participant.room_id == stream.local_room_id,
            column == identity.id,
        )
    )
    writable = bool(
        participant
        and participant.role in EXECUTION_ROLES
        and roster
        and roster.active
        and roster.role in EXECUTION_ROLES
    )
    capabilities = set()
    epoch = 1
    if stream.authority_node_id == service.node_id:
        consent = await db.get(
            PublicationConsent,
            (
                stream.authority_node_id,
                stream.channel_id,
                principal["node_id"],
                principal["kind"],
                principal["principal_id"],
            ),
            populate_existing=True,
        )
        if consent and consent.active:
            try:
                await service._admin(db, consent.approved_by)
                capabilities = {"message.send", "task.request", "task.cancel"}
            except ChannelError:
                pass
    else:
        _, grant = await service.mirror_policy(
            db, stream.authority_node_id, stream.channel_id
        )
        epoch = grant.epoch
        if grant.role != "observer" and principal in grant.actors:
            capabilities = set(grant.capabilities)
    return {
        "principal": principal,
        "grant_epoch": epoch,
        "can_send": writable and "message.send" in capabilities,
        "can_delegate": writable and "task.request" in capabilities,
        "can_cancel": writable and "task.cancel" in capabilities,
        "is_channel_admin": bool(
            roster and roster.active and roster.role in {"admin", "owner"}
        ),
    }


async def submit_product(
    service, *, identity, authority, channel, kind, body, delegation_id=None
):
    async with service.sessions.begin() as db:
        stream = await service.local_access(
            db, identity=identity, authority=authority, channel=channel, write=True
        )
        policy = await actor_policy(service, db, identity, stream)
        required = {
            "message.send": "can_send",
            "task.request": "can_delegate",
            "task.cancel": "can_cancel",
        }[kind]
        if not policy[required]:
            raise ChannelError("SCOPE_DENIED", 403)
        request_id = body["request_id"]
        if kind == "message.send":
            payload = {
                "message_id": derived_id(
                    "shared-message", authority, channel, request_id
                ),
                "text": body["text"],
                "thread_root_id": body.get("thread_root_id"),
            }
        elif kind == "task.request":
            source = await db.get(
                SharedMessage, (authority, channel, body["source_message_id"])
            )
            if source is None or source.thread_root_id is not None:
                raise ChannelError("SOURCE_DENIED", 403)
            roster = await db.get(
                SharedParticipant,
                (
                    authority,
                    channel,
                    body["executor"]["node_id"],
                    "agent",
                    body["executor"]["agent_id"],
                ),
                populate_existing=True,
            )
            if (
                roster is None
                or not roster.active
                or roster.role not in EXECUTION_ROLES
            ):
                raise ChannelError("EXECUTOR_DENIED", 403)
            payload = {
                "delegation_id": derived_id(
                    "shared-delegation", authority, channel, request_id
                ),
                "expected_revision": 0,
                "task_id": source_task_id(
                    authority, channel, body["source_message_id"]
                ),
                "source_message_id": body["source_message_id"],
                "executor": body["executor"],
            }
        else:
            record = (
                await db.get(Delegation, delegation_id)
                if authority == service.node_id
                else await db.get(DelegationMirror, (authority, channel, delegation_id))
            )
            if (
                record is None
                or record.authority_node_id != authority
                or record.channel_id != channel
            ):
                raise ChannelError("DELEGATION_MISSING", 404)
            if (
                record.requester != policy["principal"]
                and not policy["is_channel_admin"]
            ):
                raise ChannelError("ACTOR_DENIED", 403)
            payload = {
                "delegation_id": delegation_id,
                "expected_revision": body["expected_revision"],
            }
        envelope = {
            "protocol_version": 1,
            "request_id": request_id,
            "sender_node_id": service.node_id,
            "authority_node_id": authority,
            "channel_id": channel,
            "grant_epoch": policy["grant_epoch"],
            "actor": policy["principal"],
            "kind": kind,
            "payload": payload,
        }
        if authority == service.node_id:
            receipt = await service.submit_local(db, envelope, identity=identity)
            result = {
                "request_id": request_id,
                "state": "confirmed",
                "receipt": receipt,
                "error_code": None,
            }
        else:
            # Freeze the original grant epoch across retries. Reauthorization
            # against it rejects revoked/replaced grants instead of rewriting
            # a previously queued command under the same idempotency key.
            prior = await db.get(ChannelSubmission, (authority, channel, request_id))
            if prior:
                old = json.loads(prior.body)
                envelope["grant_epoch"] = old["grant_epoch"]
            result = await service.queue(db, envelope, identity=identity)
        return {
            **result,
            **{
                key: payload[key]
                for key in ("delegation_id", "task_id")
                if key in payload
            },
        }


async def display_principal(service, db, principal):
    if principal["node_id"] != service.node_id:
        return None
    if principal["kind"] == "agent":
        agent = await db.get(Agent, principal["principal_id"])
        return agent.name if agent else None
    user = await db.get(User, principal["principal_id"])
    return (
        (user.display_name or (user.email or "").split("@")[0] or None)
        if user
        else None
    )


async def delegation_views(service, db, stream, policy):
    authority, channel = stream.authority_node_id, stream.channel_id
    local = authority == service.node_id
    model = Delegation if local else DelegationMirror
    rows = list(
        (
            await db.scalars(
                select(model).where(
                    model.authority_node_id == authority,
                    model.channel_id == channel,
                )
            )
        ).all()
    )
    # The immutable confirmed event log already persists remote result bodies.
    # Reading it avoids inventing a second mutable result authority on mirrors.
    result_by_id = {}
    events = ChannelEvent if local else InboxEvent
    if rows:
        query = select(events).where(
            events.authority_node_id == authority,
            events.channel_id == channel,
        )
        if not local:
            query = query.where(InboxEvent.applied.is_(True))
        records = await db.scalars(query.order_by(events.seq))
        for event in records:
            data = json.loads(event.body)
            command = data.get("request", {})
            if command.get("kind") in {"task.result", "task.reject", "task.cancelled"}:
                payload = command["payload"]
                result_by_id[payload["delegation_id"]] = payload
    out = []
    for row in rows:
        did = row.id if local else row.delegation_id
        payload = result_by_id.get(did, {})
        task = await db.get(Task, row.task_id) if local else None
        out.append(
            {
                "authority_node_id": authority,
                "channel_id": channel,
                "delegation_id": did,
                "task_id": row.task_id,
                "source_message_id": row.source_message_id,
                "requester": row.requester,
                "executor": {
                    "node_id": row.executor_node_id,
                    "agent_id": row.executor_agent_id,
                }
                if local
                else row.executor,
                "execution_id": row.execution_id,
                "revision": row.revision,
                "state": row.state,
                "process_state": row.process_state,
                "task_status": TASK_STATUS[row.state] if local else row.task_status,
                "result_markdown": task.result_markdown
                if task
                else payload.get("text"),
                "error": (task.error if task else None)
                or payload.get("error_code")
                or payload.get("reason"),
                "created_at": row.created_at.isoformat() if local else None,
                "finished_at": task.finished_at.isoformat()
                if task and task.finished_at
                else None,
                "can_cancel": bool(
                    policy["can_cancel"]
                    and row.state in CANCELLABLE
                    and (
                        row.requester == policy["principal"]
                        or policy["is_channel_admin"]
                    )
                ),
            }
        )
    return out


async def snapshot_extras(service, db, identity, stream):
    policy = await actor_policy(service, db, identity, stream)
    submissions = []
    pending = await db.scalars(
        select(ChannelSubmission).where(
            ChannelSubmission.authority_node_id == stream.authority_node_id,
            ChannelSubmission.channel_id == stream.channel_id,
        )
    )
    for row in pending:
        command = json.loads(row.body)
        if command["actor"] != policy["principal"]:
            continue
        allowed = {
            "message.send": policy["can_send"],
            "task.request": policy["can_delegate"],
            "task.cancel": policy["can_cancel"],
        }.get(command["kind"], False)
        submissions.append(
            {
                **service.submission_view(row),
                "kind": command["kind"],
                "can_retry": bool(
                    allowed
                    and row.receipt is None
                    and command["grant_epoch"] == policy["grant_epoch"]
                ),
                **{
                    key: command["payload"].get(key)
                    for key in (
                        "message_id",
                        "source_message_id",
                        "delegation_id",
                        "executor",
                        "text",
                        "thread_root_id",
                    )
                },
            }
        )
    if stream.authority_node_id == service.node_id:
        receipts = await db.scalars(
            select(CommandReceipt).where(
                CommandReceipt.authority_node_id == stream.authority_node_id,
                CommandReceipt.channel_id == stream.channel_id,
                CommandReceipt.sender_node_id == service.node_id,
            )
        )
        for row in receipts:
            command = json.loads(row.body)
            if command["actor"] != policy["principal"] or command["kind"] not in {
                "message.send",
                "task.request",
                "task.cancel",
            }:
                continue
            submissions.append(
                {
                    "request_id": row.request_id,
                    "kind": command["kind"],
                    "state": "confirmed",
                    "receipt": row.receipt,
                    "error_code": None,
                    "can_retry": False,
                    **{
                        key: command["payload"].get(key)
                        for key in (
                            "message_id",
                            "source_message_id",
                            "delegation_id",
                            "executor",
                            "text",
                            "thread_root_id",
                        )
                    },
                }
            )

    from anygarden.shared_channels.directory import local_targets

    local = {
        (row["node_id"], row["agent_id"]): row
        for row in await local_targets(service, db, stream)
    }
    targets = []
    roster = await db.scalars(
        select(SharedParticipant).where(
            SharedParticipant.authority_node_id == stream.authority_node_id,
            SharedParticipant.channel_id == stream.channel_id,
            SharedParticipant.kind == "agent",
            SharedParticipant.active.is_(True),
        )
    )
    for row in roster:
        targets.append(
            {
                "node_id": row.node_id,
                "agent_id": row.principal_id,
                "name": None,
                "node_name": None,
                "description": None,
                "can_execute": False,
                "unavailable_code": "metadata_unavailable"
                if row.role in EXECUTION_ROLES
                else "role_denied",
                **local.get((row.node_id, row.principal_id), {}),
                "server_label": None,
                "is_local": row.node_id == service.node_id,
            }
        )
    return {
        "permissions": {key: policy[key] for key in ("can_send", "can_delegate")},
        "targets": targets,
        "delegations": await delegation_views(service, db, stream, policy),
        "submissions": submissions,
    }
