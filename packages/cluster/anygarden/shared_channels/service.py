"""Transactional channel authority, replay and mirror projection.

Every method receives the caller's transaction. No commit, process launch or
network call happens in an effect callback. HTTP adapters commit before ACK.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.db.models import Agent, Message, Room, User
from anygarden.federation.schemas import Principal
from anygarden.shared_channels.models import (
    ChannelDelivery,
    ChannelEvent,
    ChannelStream,
    ChannelSubmission,
    CommandReceipt,
    InboxEvent,
    ParticipantOperation,
    PublicationConsent,
    SharedMessage,
    SharedParticipant,
)
from anygarden.shared_channels.schemas import (
    ChannelError,
    CommandEffect,
    canonical,
    command_action,
    validate,
)

Effect = Callable[[AsyncSession, dict], Awaitable[CommandEffect]]


class ChannelService:
    def __init__(self, *, node_id: str, peers, sessions):
        self.node_id, self.peers, self.sessions = node_id, peers, sessions
        self.command_guards: dict[str, Callable] = {}
        self.submitters: dict[str, Callable] = {}
        self.effects: dict[str, Effect] = {"message.send": self.apply_message}
        self.projections: dict[str, Callable] = {"message.send": self.project_message}

    async def _stream(self, db, authority, channel, *, owner: bool | None = None):
        if owner is not None and (authority == self.node_id) != owner:
            raise ChannelError("AUTHORITY_UNAVAILABLE", 503)
        # A real write lock, including SQLite: all event sequence allocation and
        # cursor movement serialize here. Do not substitute SELECT FOR UPDATE.
        result = await db.execute(
            update(ChannelStream)
            .where(
                ChannelStream.authority_node_id == authority,
                ChannelStream.channel_id == channel,
            )
            .values(last_seq=ChannelStream.last_seq)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ChannelError("CHANNEL_DENIED", 403)
        stream = await db.get(
            ChannelStream, (authority, channel), populate_existing=True
        )
        room = await db.get(Room, stream.local_room_id, populate_existing=True)
        if room is None or room.archived_at or room.is_dm:
            raise ChannelError("CHANNEL_DENIED", 403)
        return stream

    async def bind(self, db, *, actor_id, authority_node_id, channel_id, local_room_id):
        await self._admin(db, actor_id)
        user = await db.get(User, actor_id, populate_existing=True)
        room = await db.get(Room, local_room_id, populate_existing=True)
        if (
            user is None
            or not user.is_admin
            or room is None
            or room.archived_at
            or room.is_dm
        ):
            raise ChannelError("CHANNEL_DENIED", 403)
        if authority_node_id == self.node_id and channel_id != local_room_id:
            raise ChannelError("CHANNEL_ID_MISMATCH")
        existing = await db.get(ChannelStream, (authority_node_id, channel_id))
        if existing:
            if existing.local_room_id != local_room_id:
                raise ChannelError("CHANNEL_ID_MISMATCH")
            return existing
        # V1 has no history-export/resync protocol. Start a new empty stream,
        # never silently export pre-existing private history or reset tombstones.
        if await db.scalar(
            select(Message.id).where(Message.room_id == local_room_id).limit(1)
        ):
            raise ChannelError("EMPTY_CHANNEL_REQUIRED")
        stream = ChannelStream(
            authority_node_id=authority_node_id,
            channel_id=channel_id,
            local_room_id=local_room_id,
            last_seq=0,
            applied_seq=0,
        )
        db.add(stream)
        await db.flush()
        return stream

    async def _authorize(self, db, tls, envelope, action=None):
        kind = envelope.get("kind", "channel.read")
        action = action or command_action(kind)
        if self.peers is None:
            raise ChannelError("PEERING_DISABLED", 503)
        return await self.peers.authorize(
            db,
            tls,
            sender_node_id=envelope["sender_node_id"],
            authority_node_id=envelope["authority_node_id"],
            channel_id=envelope["channel_id"],
            principal=Principal(**envelope["actor"]),
            action=action,
            grant_epoch=envelope["grant_epoch"],
        )

    async def submit(self, envelope, *, tls):
        """Commit before replying, or delegate the full transaction lifecycle.

        #592 registers only its task submit coordinator here so a designated
        late-result refusal can roll back before a separately authorized audit.
        The coordinator must still call commit_command for normal mutations.
        """
        validate("command", envelope)
        submitter = self.submitters.get(envelope["kind"])
        if submitter is not None:
            return await submitter(envelope, tls=tls)
        async with self.sessions.begin() as db:
            return await self.commit_command(db, envelope, tls=tls)

    async def commit_command(
        self, db, envelope, apply_effect: Effect | None = None, *, tls
    ):
        """Stage receipt + effect + event together; caller must commit before ACK.

        ``apply_effect`` only mutates this DB transaction and returns CommandEffect.
        Its exception aborts the entire transaction, including partial effects.
        """
        validate("command", envelope)
        await self._authorize(db, tls, envelope)
        return await self._commit_authorized(db, envelope, apply_effect)

    async def _commit_authorized(self, db, envelope, apply_effect=None):
        stream = await self._stream(
            db, envelope["authority_node_id"], envelope["channel_id"], owner=True
        )
        guard = self.command_guards.get(envelope["kind"])
        if envelope["kind"].startswith("task.") and guard is None:
            raise ChannelError("COMMAND_GUARD_REQUIRED", 503)
        if guard is not None:
            await guard(db, envelope)
        key = (
            envelope["sender_node_id"],
            stream.authority_node_id,
            stream.channel_id,
            envelope["request_id"],
        )
        body = canonical(envelope)
        prior = await db.get(CommandReceipt, key)
        if prior:
            if prior.body != body:
                raise ChannelError("ID_CONFLICT")
            return dict(prior.receipt)
        effect = apply_effect or self.effects.get(envelope["kind"])
        if effect is None:
            raise ChannelError("KIND_UNSUPPORTED", 400)
        try:
            outcome = await effect(db, envelope)
            stream.last_seq += 1
            receipt = validate(
                "receipt",
                {
                    "protocol_version": 1,
                    "request_id": envelope["request_id"],
                    "authority_node_id": stream.authority_node_id,
                    "channel_id": stream.channel_id,
                    "event_id": str(uuid4()),
                    "seq": stream.last_seq,
                    **asdict(outcome),
                },
            )
            event = validate(
                "event",
                {
                    "protocol_version": 1,
                    "event_id": receipt["event_id"],
                    "authority_node_id": stream.authority_node_id,
                    "channel_id": stream.channel_id,
                    "seq": stream.last_seq,
                    "request": envelope,
                    "receipt": receipt,
                },
            )
            if len(canonical(event).encode("utf-8")) > 60000:
                raise ChannelError("EVENT_TOO_LARGE", 400)
            db.add(
                CommandReceipt(
                    sender_node_id=key[0],
                    authority_node_id=key[1],
                    channel_id=key[2],
                    request_id=key[3],
                    body=body,
                    receipt=receipt,
                )
            )
            db.add(
                ChannelEvent(
                    authority_node_id=stream.authority_node_id,
                    channel_id=stream.channel_id,
                    seq=stream.last_seq,
                    event_id=receipt["event_id"],
                    body=canonical(event),
                )
            )
            stream.applied_seq = stream.last_seq
            await db.flush()
            return receipt
        except BaseException:
            await db.rollback()
            raise

    async def apply_message(self, db, envelope):
        stream = await db.get(
            ChannelStream, (envelope["authority_node_id"], envelope["channel_id"])
        )
        await self._project_text(db, stream, envelope, stream.last_seq + 1)
        return CommandEffect(0, "message_committed", "not_applicable", None)

    async def project_message(self, db, stream, event):
        await self._project_text(db, stream, event["request"], event["seq"])

    async def _project_text(self, db, stream, envelope, seq):
        payload = envelope["payload"]
        key = (stream.authority_node_id, stream.channel_id, payload["message_id"])
        if await db.get(SharedMessage, key):
            raise ChannelError("MESSAGE_ID_CONFLICT")
        root = None
        if payload["thread_root_id"] is not None:
            root = await db.get(SharedMessage, (*key[:2], payload["thread_root_id"]))
            if root is None or root.thread_root_id is not None:
                raise ChannelError("THREAD_ROOT_DENIED", 400)
        local_id = str(uuid4())
        # Event seq includes control/task events, so gaps in the message-only
        # view are intentional. Every visible message retains its authority seq.
        db.add(
            Message(
                id=local_id,
                room_id=stream.local_room_id,
                participant_id=None,
                content=payload["text"],
                seq=seq,
                parent_message_id=root.local_message_id if root else None,
                root_message_id=root.local_message_id if root else None,
                extra_metadata={
                    "federation": {
                        "authority_node_id": key[0],
                        "channel_id": key[1],
                        "message_id": key[2],
                        "actor": envelope["actor"],
                        "seq": seq,
                        "thread_root_id": payload["thread_root_id"],
                        "confirmed": True,
                    }
                },
            )
        )
        await db.flush()
        db.add(
            SharedMessage(
                authority_node_id=key[0],
                channel_id=key[1],
                message_id=key[2],
                local_message_id=local_id,
                thread_root_id=payload["thread_root_id"],
                actor=envelope["actor"],
                seq=seq,
            )
        )
        await db.flush()

    async def events(self, db, request, *, tls, after_seq=0, limit=100):
        if (
            type(after_seq) is not int
            or after_seq < 0
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ChannelError("INVALID_CURSOR", 400)
        await self._authorize(db, tls, request, "channel.read")
        stream = await self._stream(
            db, request["authority_node_id"], request["channel_id"], owner=True
        )
        if after_seq > stream.last_seq:
            raise ChannelError("CURSOR_AHEAD")
        rows = (
            await db.scalars(
                select(ChannelEvent)
                .where(
                    ChannelEvent.authority_node_id == stream.authority_node_id,
                    ChannelEvent.channel_id == stream.channel_id,
                    ChannelEvent.seq > after_seq,
                )
                .order_by(ChannelEvent.seq)
                .limit(limit)
            )
        ).all()
        key = (request["sender_node_id"], stream.authority_node_id, stream.channel_id)
        delivery = await db.get(ChannelDelivery, key)
        if delivery is None:
            delivery = ChannelDelivery(
                peer_node_id=key[0],
                authority_node_id=key[1],
                channel_id=key[2],
                ack_seq=0,
                delivered_seq=0,
            )
            db.add(delivery)
        if after_seq > delivery.delivered_seq:
            raise ChannelError("CURSOR_GAP")
        bounded, size = [], 32
        for row in rows:
            size += len(row.body.encode("utf-8")) + 1
            if size > 62000:
                break
            bounded.append(row)
        rows = bounded
        if rows:
            delivery.delivered_seq = max(delivery.delivered_seq, rows[-1].seq)
        return [json.loads(row.body) for row in rows]

    async def ack(self, db, request, *, tls, seq):
        if type(seq) is not int or seq < 0:
            raise ChannelError("INVALID_CURSOR", 400)
        await self._authorize(db, tls, request, "channel.read")
        stream = await self._stream(
            db, request["authority_node_id"], request["channel_id"], owner=True
        )
        delivery = await db.get(
            ChannelDelivery,
            (request["sender_node_id"], stream.authority_node_id, stream.channel_id),
        )
        if delivery is None or seq > delivery.delivered_seq or seq > stream.last_seq:
            raise ChannelError("CURSOR_AHEAD")
        delivery.ack_seq = max(delivery.ack_seq, seq)
        return {"ack_seq": delivery.ack_seq}

    async def receive(
        self, db, events, *, tls, authority_node_id, channel_id, grant_epoch
    ):
        if not isinstance(events, list) or len(events) > 100:
            raise ChannelError("INVALID_SCHEMA", 400)
        if self.peers is None:
            raise ChannelError("PEERING_DISABLED", 503)
        await self.peers.authorize_delivery(
            db,
            tls,
            authority_node_id=authority_node_id,
            channel_id=channel_id,
            grant_epoch=grant_epoch,
        )
        stream = await self._stream(db, authority_node_id, channel_id, owner=False)
        try:
            for event in events:
                validate("event", event)
                if (event["authority_node_id"], event["channel_id"]) != (
                    authority_node_id,
                    channel_id,
                ):
                    raise ChannelError("EVENT_INTEGRITY")
                body = canonical(event)
                key = (authority_node_id, channel_id, event["seq"])
                prior = await db.get(InboxEvent, key)
                by_id = await db.scalar(
                    select(InboxEvent).where(
                        InboxEvent.authority_node_id == authority_node_id,
                        InboxEvent.channel_id == channel_id,
                        InboxEvent.event_id == event["event_id"],
                    )
                )
                if prior or by_id:
                    if (
                        prior is None
                        or by_id is None
                        or prior.body != body
                        or by_id.body != body
                    ):
                        raise ChannelError("EVENT_INTEGRITY")
                    continue
                db.add(
                    InboxEvent(
                        authority_node_id=authority_node_id,
                        channel_id=channel_id,
                        seq=event["seq"],
                        event_id=event["event_id"],
                        body=body,
                        applied=False,
                    )
                )
                await db.flush()
            while True:
                row = await db.get(
                    InboxEvent, (authority_node_id, channel_id, stream.applied_seq + 1)
                )
                if row is None:
                    break
                event = json.loads(row.body)
                projection = (
                    self.project_participant
                    if event.get("kind") == "participant.changed"
                    else self.projections.get(event["request"]["kind"])
                )
                if projection is None:
                    raise ChannelError("KIND_UNSUPPORTED", 400)
                await projection(db, stream, event)
                row.applied = True
                stream.applied_seq = row.seq
                stream.last_seq = row.seq
                await db.flush()
            pending = await db.scalar(
                select(func.min(InboxEvent.seq)).where(
                    InboxEvent.authority_node_id == authority_node_id,
                    InboxEvent.channel_id == channel_id,
                    InboxEvent.applied.is_(False),
                )
            )
            return {
                "ack_seq": stream.applied_seq,
                "missing_after_seq": stream.applied_seq
                if pending is not None
                else None,
            }
        except BaseException:
            await db.rollback()
            raise

    async def _admin(self, db, actor_id):
        # Serialize role changes with the operation, including on SQLite.
        await db.execute(
            update(User).where(User.id == actor_id).values(is_admin=User.is_admin)
        )
        actor = await db.get(User, actor_id, populate_existing=True)
        if actor is None or not actor.is_admin:
            raise ChannelError("ADMIN_REQUIRED", 403)

    def _principal_key(self, stream, principal):
        return (
            stream.authority_node_id,
            stream.channel_id,
            principal["node_id"],
            principal["kind"],
            principal["principal_id"],
        )

    async def publication(self, db, *, actor_id, channel_id, principal, active):
        await self._admin(db, actor_id)
        stream = await self._stream(db, self.node_id, channel_id, owner=True)
        # Validate the closed principal using the public event schema.
        validate(
            "event",
            self._participant_event(stream, actor_id, principal, "member", active, 1),
        )
        key = self._principal_key(stream, principal)
        row = await db.get(PublicationConsent, key)
        if row is None:
            row = PublicationConsent(
                authority_node_id=key[0],
                channel_id=key[1],
                node_id=key[2],
                kind=key[3],
                principal_id=key[4],
                approved_by=actor_id,
                active=active,
            )
            db.add(row)
        else:
            row.active, row.approved_by = active, actor_id
        await db.flush()

    def _participant_event(self, stream, actor, principal, role, active, revision):
        return {
            "protocol_version": 1,
            "event_id": str(uuid4()),
            "authority_node_id": self.node_id,
            "channel_id": stream.channel_id,
            "seq": stream.last_seq + 1,
            "kind": "participant.changed",
            "actor": {"node_id": self.node_id, "kind": "human", "principal_id": actor},
            "principal": principal,
            "role": role,
            "active": active,
            "revision": revision,
        }

    async def change_participant(
        self,
        db,
        *,
        actor_id,
        channel_id,
        operation_id,
        principal,
        role,
        active,
        expected_revision,
    ):
        await self._admin(db, actor_id)
        stream = await self._stream(db, self.node_id, channel_id, owner=True)
        if (
            type(expected_revision) is not int
            or not 0 <= expected_revision < 9007199254740991
        ):
            raise ChannelError("INVALID_SCHEMA", 400)
        event = validate(
            "event",
            self._participant_event(
                stream, actor_id, principal, role, active, expected_revision + 1
            ),
        )
        key = self._principal_key(stream, principal)
        # Consent and approving admin are checked BEFORE operation dedup. Removal
        # remains possible after consent withdrawal; no new publication occurs.
        if active:
            consent = await db.get(PublicationConsent, key, populate_existing=True)
            if consent is None or not consent.active:
                raise ChannelError("PUBLICATION_DENIED", 403)
            await self._admin(db, consent.approved_by)
            if principal["node_id"] != self.node_id:
                from anygarden.federation.models import PeerGrant

                grant = await db.get(
                    PeerGrant, (principal["node_id"], self.node_id, channel_id)
                )
                if grant is None:
                    raise ChannelError("PUBLICATION_DENIED", 403)
                from anygarden.federation.certificates import inspect_certificate

                peer = await self.peers.lock_peer(db, principal["node_id"])
                await self.peers.authorize(
                    db,
                    inspect_certificate(peer.certificate_pem),
                    sender_node_id=peer.node_id,
                    authority_node_id=self.node_id,
                    channel_id=channel_id,
                    principal=Principal(**principal),
                    action="channel.read",
                    grant_epoch=grant.epoch,
                )
            else:
                model = User if principal["kind"] == "human" else Agent
                if (
                    await db.get(
                        model, principal["principal_id"], populate_existing=True
                    )
                    is None
                ):
                    raise ChannelError("PUBLICATION_DENIED", 403)
        body = canonical(
            {
                "actor": actor_id,
                "principal": principal,
                "role": role,
                "active": active,
                "expected_revision": expected_revision,
            }
        )
        operation = await db.get(
            ParticipantOperation, (self.node_id, channel_id, operation_id)
        )
        if operation:
            if operation.body != body:
                raise ChannelError("ID_CONFLICT")
            return dict(operation.event)
        await self.project_participant(db, stream, event)
        stream.last_seq = stream.applied_seq = event["seq"]
        db.add(
            ParticipantOperation(
                authority_node_id=self.node_id,
                channel_id=channel_id,
                operation_id=operation_id,
                body=body,
                event=event,
            )
        )
        db.add(
            ChannelEvent(
                authority_node_id=self.node_id,
                channel_id=channel_id,
                seq=event["seq"],
                event_id=event["event_id"],
                body=canonical(event),
            )
        )
        await db.flush()
        return event

    async def project_participant(self, db, stream, event):
        key = self._principal_key(stream, event["principal"])
        row = await db.get(SharedParticipant, key)
        if event["revision"] != (row.revision if row else 0) + 1:
            raise ChannelError("PARTICIPANT_REVISION_CONFLICT")
        if row is None:
            row = SharedParticipant(
                authority_node_id=key[0],
                channel_id=key[1],
                node_id=key[2],
                kind=key[3],
                principal_id=key[4],
            )
            db.add(row)
        row.role, row.active, row.revision = (
            event["role"],
            event["active"],
            event["revision"],
        )
        await db.flush()
        from anygarden.shared_channels.shadow import sync_shadow

        # Single choke point: every roster transition (authority operation or
        # mirror replay) keeps the authority-side execution-role shadow in
        # lockstep. Removal demotes below the fenced roles immediately.
        await sync_shadow(db, self.node_id, stream, event)

    async def local_access(self, db, *, identity, authority, channel, write=False):
        from anygarden.rooms.authorization import Capability, require_capability

        stream = await self._stream(db, authority, channel)
        await require_capability(
            db,
            room_id=stream.local_room_id,
            identity=identity,
            capability=Capability.MESSAGE_SEND if write else Capability.ROOM_READ,
            allow_shared=True,
        )
        if identity.kind not in {"user", "agent"}:
            raise ChannelError("PRINCIPAL_DENIED", 403)
        # JWT admin claims are not sufficient when the user has since been demoted.
        if identity.kind == "user" and getattr(identity.claims, "is_admin", False):
            await self._admin(db, identity.id)
        if authority != self.node_id:
            _, grant = await self.mirror_policy(db, authority, channel)
            if self.local_principal(identity) not in grant.actors:
                raise ChannelError("PRINCIPAL_DENIED", 403)
        return stream

    async def mirror_policy(self, db, authority, channel, epoch=None):
        """Local policy check only; never used as proof of an inbound TLS peer."""
        from anygarden.federation.certificates import inspect_certificate
        from anygarden.federation.models import PeerGrant

        if self.peers is None:
            raise ChannelError("PEERING_DISABLED", 503)
        peer = await self.peers.lock_peer(db, authority)
        grant = await db.get(
            PeerGrant, (authority, authority, channel), populate_existing=True
        )
        if grant is None:
            raise ChannelError("CHANNEL_DENIED", 403)
        await self.peers.authorize_delivery(
            db,
            inspect_certificate(peer.certificate_pem),
            authority_node_id=authority,
            channel_id=channel,
            grant_epoch=grant.epoch if epoch is None else epoch,
        )
        return peer, grant

    def local_principal(self, identity):
        if identity.kind not in {"user", "agent"}:
            raise ChannelError("PRINCIPAL_DENIED", 403)
        return {
            "node_id": self.node_id,
            "kind": "human" if identity.kind == "user" else "agent",
            "principal_id": identity.id,
        }

    async def queue(self, db, envelope, *, identity):
        validate("command", envelope)
        if envelope["sender_node_id"] != self.node_id or envelope[
            "actor"
        ] != self.local_principal(identity):
            raise ChannelError("PRINCIPAL_DENIED", 403)
        authority, channel = envelope["authority_node_id"], envelope["channel_id"]
        await self.local_access(
            db, identity=identity, authority=authority, channel=channel, write=True
        )
        if authority == self.node_id:
            raise ChannelError("LOCAL_AUTHORITY_REQUIRED", 400)
        _, grant = await self.mirror_policy(
            db, authority, channel, envelope["grant_epoch"]
        )
        if (
            envelope["actor"] not in grant.actors
            or command_action(envelope["kind"]) not in grant.capabilities
            or grant.role == "observer"
        ):
            raise ChannelError("SCOPE_DENIED", 403)
        key = (authority, channel, envelope["request_id"])
        body = canonical(envelope)
        row = await db.get(ChannelSubmission, key)
        if row:
            if row.body != body:
                raise ChannelError("ID_CONFLICT")
            return self.submission_view(row)
        row = ChannelSubmission(
            authority_node_id=authority,
            channel_id=channel,
            request_id=key[2],
            body=body,
            state="unconfirmed",
        )
        db.add(row)
        await db.flush()
        return self.submission_view(row)

    def submission_view(self, row):
        return {
            "request_id": row.request_id,
            "state": row.state,
            "receipt": row.receipt,
            "error_code": row.error_code,
        }

    async def submission_status(self, db, *, identity, authority, channel, request_id):
        await self.local_access(
            db, identity=identity, authority=authority, channel=channel
        )
        row = await db.get(ChannelSubmission, (authority, channel, request_id))
        if row is None or json.loads(row.body)["actor"] != self.local_principal(
            identity
        ):
            raise ChannelError("SUBMISSION_NOT_FOUND", 404)
        return self.submission_view(row)

    async def require_local_admin(self, db, actor_id):
        """Public admin gate for local router endpoints (fresh DB role check)."""
        await self._admin(db, actor_id)

    async def bindings(self, db):
        """Metadata-only listing of shared-channel bindings (#593 task #34).

        Authority/channel/local-room identity and stream sequence state only:
        no invite tokens, certificate material, credentials, or message and
        submission bodies ever leave this view.
        """
        from sqlalchemy import select

        rows = (await db.scalars(select(ChannelStream))).unique().all()
        return [
            {
                "authority_node_id": row.authority_node_id,
                "channel_id": row.channel_id,
                "local_room_id": row.local_room_id,
                "last_seq": row.last_seq,
                "applied_seq": row.applied_seq,
            }
            for row in rows
        ]

    async def submit_local(self, db, envelope, *, identity):
        """Local authority command: authenticated identity + explicit publication.

        Uses the same durable command pipeline and no forged peer identity.
        """
        validate("command", envelope)
        if envelope["sender_node_id"] != self.node_id or envelope[
            "actor"
        ] != self.local_principal(identity):
            raise ChannelError("PRINCIPAL_DENIED", 403)
        stream = await self.local_access(
            db,
            identity=identity,
            authority=self.node_id,
            channel=envelope["channel_id"],
            write=True,
        )
        if (
            envelope["authority_node_id"] != self.node_id
            or envelope["kind"] != "message.send"
        ):
            raise ChannelError("KIND_UNSUPPORTED", 400)
        consent = await db.get(
            PublicationConsent,
            self._principal_key(stream, envelope["actor"]),
            populate_existing=True,
        )
        if consent is None or not consent.active:
            raise ChannelError("PUBLICATION_DENIED", 403)
        await self._admin(db, consent.approved_by)
        return await self._commit_authorized(db, envelope)

    async def snapshot(
        self, db, *, identity, authority, channel, after_seq=0, limit=50
    ):
        stream = await self.local_access(
            db, identity=identity, authority=authority, channel=channel
        )
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.room_id == stream.local_room_id, Message.seq > after_seq)
                .order_by(Message.seq)
                .limit(limit)
            )
        ).all()
        participants = (
            await db.scalars(
                select(SharedParticipant).where(
                    SharedParticipant.authority_node_id == authority,
                    SharedParticipant.channel_id == channel,
                )
            )
        ).all()
        return {
            "authority_node_id": authority,
            "channel_id": channel,
            "applied_seq": stream.applied_seq,
            "messages": [
                {
                    "message_id": m.extra_metadata["federation"]["message_id"],
                    "text": m.content,
                    **m.extra_metadata["federation"],
                }
                for m in messages
            ],
            "participants": [
                {
                    "principal": {
                        "node_id": p.node_id,
                        "kind": p.kind,
                        "principal_id": p.principal_id,
                    },
                    "active": p.active,
                    "role": p.role,
                    "revision": p.revision,
                }
                for p in participants
            ],
        }
