"""Trust transactions. Callers must keep authorization and effect in one transaction.

No receipt cache may be consulted before ``authorize``. External execution needs
an additional fresh authority confirmation and the injected local policy check.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from anygarden.db.models import Room, User
from anygarden.federation.certificates import CertificateIdentity, inspect_certificate
from anygarden.federation.endpoint import validate_endpoint
from anygarden.federation.errors import PeerError
from anygarden.federation.models import (
    Peer,
    PeerAcceptance,
    PeerAudit,
    PeerConsent,
    PeerControlEvent,
    PeerGrant,
    PeerInvite,
)
from anygarden.federation.schemas import (
    InviteAccept,
    InviteBundle,
    InviteCreate,
    Principal,
)


def now():
    return datetime.now(UTC)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def bundle_digest(bundle: InviteBundle) -> str:
    data = bundle.model_dump(mode="json", exclude={"token"})
    data["token_hash"] = digest(bundle.token.get_secret_value())
    return digest(json.dumps(data, sort_keys=True, separators=(",", ":")))


@dataclass(frozen=True)
class AuthorizedPeer:
    peer_node_id: str
    authority_node_id: str
    channel_id: str
    certificate_epoch: int
    grant_epoch: int
    policy_epoch: int
    principal: Principal
    action: str


class PeerService:
    def __init__(
        self,
        *,
        node_id: str,
        cert_path: Path,
        key_path: Path,
        sessions: async_sessionmaker[AsyncSession],
        allow_loopback: bool = False,
        local_policy: Callable[[AsyncSession, AuthorizedPeer], Awaitable[bool]]
        | None = None,
    ):
        self.node_id = node_id
        self.cert_path, self.key_path = cert_path, key_path
        self.certificate_pem = cert_path.read_text()
        self.identity = inspect_certificate(self.certificate_pem)
        if self.identity.node_id != node_id:
            raise PeerError("IDENTITY_MISMATCH", 400)
        self.sessions = sessions
        self.allow_loopback = (
            allow_loopback  # explicit test-only injection, not remote input
        )
        self.local_policy = local_policy
        self.trust_changed = None
        self._closers: dict[str, set[Callable[[], None]]] = {}

    async def refresh_trust(self):
        if self.trust_changed:
            await self.trust_changed()

    async def admin(self, db: AsyncSession, actor_id: str):
        # Fresh DB role; a previously issued admin JWT alone is insufficient.
        user = await db.get(User, actor_id, populate_existing=True)
        if user is None or not user.is_admin:
            raise PeerError("ADMIN_REQUIRED")

    def audit(self, db, actor, peer, action):
        db.add(
            PeerAudit(
                id=str(uuid4()),
                actor_id=actor,
                peer_node_id=peer,
                action=action,
                created_at=now(),
            )
        )

    async def lock_peer(self, db: AsyncSession, node_id: str) -> Peer:
        # A write-lock even on SQLite; READ followed by ordinary UPDATE is unsafe.
        result = await db.execute(
            update(Peer)
            .where(Peer.node_id == node_id)
            .values(epoch=Peer.epoch)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise PeerError("PEER_DENIED", 401)
        return (
            await db.execute(
                select(Peer)
                .where(Peer.node_id == node_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()

    async def ensure_peer(self, db, node_id, pem, endpoint, actor):
        cert = inspect_certificate(pem)
        if cert.node_id != node_id or node_id == self.node_id:
            raise PeerError("IDENTITY_MISMATCH", 400)
        validate_endpoint(endpoint, allow_loopback=self.allow_loopback)
        peer = await db.get(Peer, node_id)
        if peer is None:
            peer = Peer(
                node_id=node_id,
                certificate_pem=pem,
                fingerprint=cert.fingerprint,
                endpoint=endpoint.model_dump(),
                state="pending",
                epoch=1,
                approved_by=actor,
                updated_at=now(),
            )
            db.add(peer)
            await db.flush()
        peer = await self.lock_peer(db, node_id)
        if peer.fingerprint != cert.fingerprint:
            raise PeerError("PIN_ROTATION_REQUIRED", 409)
        # Explicit local admin request approves a new endpoint policy.
        peer.endpoint = endpoint.model_dump()
        return peer

    async def create_invite(self, actor: str, body: InviteCreate) -> InviteBundle:
        async with self.sessions.begin() as db:
            await self.admin(db, actor)
            node = str(body.intended_node_id)
            peer = await self.ensure_peer(
                db, node, body.certificate_pem, body.endpoint, actor
            )
            channel_ids = [s.channel_id for s in body.scopes]
            if len(channel_ids) != len(set(channel_ids)):
                raise PeerError("INVALID_SCOPE", 400)
            for scope in body.scopes:
                room = await db.get(Room, str(scope.channel_id))
                if room is None or room.archived_at or room.is_dm:
                    raise PeerError("CHANNEL_DENIED")
                if any(str(p.node_id) != node for p in scope.actors):
                    raise PeerError("IDENTITY_MISMATCH", 400)
            token = secrets.token_urlsafe(32)
            row = PeerInvite(
                id=str(uuid4()),
                intended_node_id=node,
                certificate_pem=body.certificate_pem,
                fingerprint=peer.fingerprint,
                endpoint=body.endpoint.model_dump(),
                token_hash=digest(token),
                state="pending",
                expected_peer_epoch=peer.epoch,
                scopes=[s.model_dump(mode="json") for s in body.scopes],
                expires_at=now() + timedelta(seconds=body.expires_in_seconds),
                grant_expires_at=now()
                + timedelta(seconds=body.grant_expires_in_seconds),
                approved_by=actor,
                created_at=now(),
            )
            db.add(row)
            self.audit(db, actor, node, "invite.create")
            return InviteBundle(
                invite_id=row.id,
                issuer_node_id=self.node_id,
                issuer_certificate_pem=self.certificate_pem,
                intended_node_id=node,
                intended_fingerprint=peer.fingerprint,
                token=token,
                scopes=body.scopes,
                expires_at=row.expires_at,
                grant_expires_at=row.grant_expires_at,
            )

    async def authenticate(
        self, db, tls: CertificateIdentity, sender: str, *, control=False
    ) -> Peer:
        if tls.node_id != sender:
            raise PeerError("IDENTITY_MISMATCH", 401)
        peer = await self.lock_peer(db, sender)
        if not hmac.compare_digest(peer.fingerprint, tls.fingerprint):
            raise PeerError("PIN_MISMATCH", 401)
        inspect_certificate(
            peer.certificate_pem
        )  # expiry rechecked on long-lived sockets
        if peer.state != "active" and not (control and peer.state == "revoked"):
            raise PeerError("PEER_DENIED", 401)
        return peer

    async def bootstrap(self, db, tls: CertificateIdentity, sender: str) -> Peer:
        if tls.node_id != sender:
            raise PeerError("IDENTITY_MISMATCH", 401)
        peer = await self.lock_peer(db, sender)
        if peer.fingerprint != tls.fingerprint:
            raise PeerError("PIN_MISMATCH", 401)
        inspect_certificate(peer.certificate_pem)
        if peer.state == "active":
            return peer
        pending = (
            await db.execute(
                select(PeerInvite.id).where(
                    PeerInvite.intended_node_id == sender,
                    PeerInvite.fingerprint == tls.fingerprint,
                    PeerInvite.state == "pending",
                    PeerInvite.expires_at > now(),
                    PeerInvite.expected_peer_epoch == peer.epoch,
                )
            )
        ).first()
        if pending is None:
            raise PeerError("BOOTSTRAP_DENIED", 401)
        return peer

    async def hello(self, tls: CertificateIdentity, sender: str, version: int):
        if version != 1:
            raise PeerError("VERSION_UNSUPPORTED", 426)
        async with self.sessions.begin() as db:
            await self.bootstrap(db, tls, sender)
        return {"protocol_version": 1, "node_id": self.node_id}

    async def redeem(
        self, tls: CertificateIdentity, sender: str, invite_id: str, token: str
    ):
        async with self.sessions.begin() as db:
            peer = await self.bootstrap(db, tls, sender)
            row = await db.get(PeerInvite, invite_id, populate_existing=True)
            if (
                row is None
                or row.intended_node_id != sender
                or row.fingerprint != tls.fingerprint
                or not hmac.compare_digest(row.token_hash, digest(token))
            ):
                raise PeerError("INVITE_DENIED")
            if row.expected_peer_epoch != peer.epoch:
                raise PeerError("INVITE_STALE", 409)
            if row.state == "accepted":
                # Reauthorize before returning the durable ACK; never mint another grant.
                if peer.state != "active" or row.grant_expires_at <= now():
                    raise PeerError("GRANT_DENIED")
                for grant_info in row.receipt["grants"]:
                    g = await db.get(
                        PeerGrant, (sender, self.node_id, grant_info["channel_id"])
                    )
                    if (
                        g is None
                        or not g.active
                        or g.epoch != grant_info["grant_epoch"]
                    ):
                        raise PeerError("GRANT_DENIED")
                return row.receipt
            if row.state != "pending" or row.expires_at <= now():
                raise PeerError("INVITE_CONSUMED_OR_EXPIRED", 409)
            await self.admin(db, row.approved_by)
            grants = []
            for scope in row.scopes:
                room = await db.get(Room, scope["channel_id"])
                if room is None or room.archived_at or room.is_dm:
                    raise PeerError("CHANNEL_DENIED")
                key = (sender, self.node_id, scope["channel_id"])
                grant = await db.get(PeerGrant, key)
                epoch = grant.epoch + 1 if grant else 1
                if grant is None:
                    grant = PeerGrant(
                        peer_node_id=sender,
                        authority_node_id=self.node_id,
                        channel_id=scope["channel_id"],
                    )
                    db.add(grant)
                grant.epoch, grant.active = epoch, True
                grant.actors, grant.capabilities, grant.role = (
                    scope["actors"],
                    scope["capabilities"],
                    scope["role"],
                )
                grant.expires_at, grant.approved_by = (
                    row.grant_expires_at,
                    row.approved_by,
                )
                consent = await db.get(PeerConsent, key)
                if consent is None:
                    consent = PeerConsent(
                        peer_node_id=sender,
                        authority_node_id=self.node_id,
                        channel_id=scope["channel_id"],
                        policy_epoch=1,
                        active=True,
                    )
                    db.add(consent)
                else:
                    consent.policy_epoch += 1
                    consent.active = True
                grants.append({"channel_id": scope["channel_id"], "grant_epoch": epoch})
            peer.state = "active"
            peer.updated_at = now()
            row.state = "accepted"
            row.receipt = {
                "protocol_version": 1,
                "invite_id": row.id,
                "issuer_node_id": self.node_id,
                "intended_node_id": sender,
                "grants": grants,
                "state": "accepted",
            }
            self.audit(db, row.approved_by, sender, "invite.redeem")
            return row.receipt

    async def begin_accept(self, actor: str, body: InviteAccept):
        b = body.bundle
        if (
            str(b.intended_node_id) != self.node_id
            or b.intended_fingerprint != self.identity.fingerprint
            or b.expires_at.tzinfo is None
            or b.grant_expires_at.tzinfo is None
        ):
            raise PeerError("IDENTITY_MISMATCH", 400)
        if any(
            str(p.node_id) != self.node_id for scope in b.scopes for p in scope.actors
        ):
            raise PeerError("IDENTITY_MISMATCH", 400)
        async with self.sessions.begin() as db:
            await self.admin(db, actor)
            peer = await self.ensure_peer(
                db,
                str(b.issuer_node_id),
                b.issuer_certificate_pem,
                body.issuer_endpoint,
                actor,
            )
            row = await db.get(PeerAcceptance, str(b.invite_id))
            hashed = bundle_digest(b)
            if row:
                if row.bundle_hash != hashed or row.expected_peer_epoch != peer.epoch:
                    raise PeerError("ACCEPTANCE_CONFLICT", 409)
                if row.receipt:
                    if b.grant_expires_at <= now():
                        raise PeerError("GRANT_DENIED")
                    await self.check_received_grants(
                        db, str(b.issuer_node_id), row.receipt
                    )
                return row.receipt
            if b.expires_at <= now():
                raise PeerError("INVITE_CONSUMED_OR_EXPIRED", 409)
            db.add(
                PeerAcceptance(
                    invite_id=str(b.invite_id),
                    issuer_node_id=str(b.issuer_node_id),
                    bundle_hash=hashed,
                    state="pending",
                    expected_peer_epoch=peer.epoch,
                    approved_by=actor,
                )
            )
            self.audit(db, actor, peer.node_id, "invite.accept_intent")
            return None

    async def finish_accept(self, actor: str, body: InviteAccept, receipt: dict):
        from pydantic import ValidationError

        from anygarden.federation.schemas import RedemptionReceipt

        b = body.bundle
        try:
            parsed = RedemptionReceipt.model_validate(receipt)
            channels = [g.channel_id for g in parsed.grants]
            if len(set(channels)) != len(channels) or set(channels) != {
                scope.channel_id for scope in b.scopes
            }:
                raise ValueError
        except (ValidationError, ValueError):
            raise PeerError("RECEIPT_DENIED", 409) from None
        receipt = parsed.model_dump(mode="json")
        # Remote ACK is only meaningful for this locally approved invitation.
        if (
            receipt.get("protocol_version") != 1
            or receipt.get("state") != "accepted"
            or receipt.get("invite_id") != str(b.invite_id)
            or receipt.get("issuer_node_id") != str(b.issuer_node_id)
            or receipt.get("intended_node_id") != self.node_id
        ):
            raise PeerError("RECEIPT_DENIED", 409)
        async with self.sessions.begin() as db:
            await self.admin(db, actor)
            peer = await self.lock_peer(db, str(b.issuer_node_id))
            row = await db.get(PeerAcceptance, str(b.invite_id))
            if (
                row is None
                or row.bundle_hash != bundle_digest(b)
                or row.expected_peer_epoch != peer.epoch
            ):
                raise PeerError("ACCEPTANCE_CONFLICT", 409)
            if b.grant_expires_at <= now():
                raise PeerError("GRANT_DENIED")
            await self.check_received_grants(db, str(b.issuer_node_id), receipt)
            if row.state == "confirmed":
                return row.receipt
            # This mirror grant permits delivery from the authority only; it
            # never exposes a channel owned by this node or any local credential.
            epochs = {g["channel_id"]: g["grant_epoch"] for g in receipt["grants"]}
            for scope in b.scopes:
                key = (
                    str(b.issuer_node_id),
                    str(b.issuer_node_id),
                    str(scope.channel_id),
                )
                grant = await db.get(PeerGrant, key)
                if grant is not None and grant.epoch >= epochs[str(scope.channel_id)]:
                    raise PeerError("GRANT_DENIED")
                if grant is None:
                    grant = PeerGrant(
                        peer_node_id=key[0], authority_node_id=key[1], channel_id=key[2]
                    )
                    db.add(grant)
                grant.epoch, grant.active = epochs[str(scope.channel_id)], True
                grant.actors = [p.model_dump(mode="json") for p in scope.actors]
                grant.capabilities, grant.role = scope.capabilities, scope.role
                grant.expires_at, grant.approved_by = b.grant_expires_at, actor
                consent = await db.get(PeerConsent, key)
                if consent is None:
                    consent = PeerConsent(
                        peer_node_id=key[0],
                        authority_node_id=key[1],
                        channel_id=key[2],
                        policy_epoch=1,
                        active=True,
                    )
                    db.add(consent)
                else:
                    consent.active, consent.policy_epoch = (
                        True,
                        consent.policy_epoch + 1,
                    )
            row.state, row.receipt = "confirmed", receipt
            peer.state, peer.updated_at = "active", now()
            self.audit(db, actor, peer.node_id, "invite.accept_confirmed")
            return receipt

    async def revoke_invite(self, actor, invite_id):
        async with self.sessions.begin() as db:
            await self.admin(db, actor)
            row = await db.get(PeerInvite, invite_id)
            if row is None:
                raise PeerError("INVITE_NOT_FOUND", 404)
            await self.lock_peer(db, row.intended_node_id)
            await db.refresh(row)
            if row.state != "pending":
                raise PeerError("INVITE_CONSUMED_OR_EXPIRED", 409)
            row.state = "revoked"
            self.audit(db, actor, row.intended_node_id, "invite.revoke")

    async def _disable_grants(self, db, node_id):
        await db.execute(
            update(PeerGrant)
            .where(PeerGrant.peer_node_id == node_id)
            .values(active=False, epoch=PeerGrant.epoch + 1)
        )
        await db.execute(
            update(PeerConsent)
            .where(PeerConsent.peer_node_id == node_id)
            .values(active=False, policy_epoch=PeerConsent.policy_epoch + 1)
        )
        await db.execute(
            update(PeerInvite)
            .where(
                PeerInvite.intended_node_id == node_id, PeerInvite.state == "pending"
            )
            .values(state="revoked")
        )

    def close_connections(self, node_id):
        for closer in tuple(self._closers.get(node_id, ())):
            closer()

    async def revoke(self, actor, node_id):
        async with self.sessions.begin() as db:
            await self.admin(db, actor)
            peer = await self.lock_peer(db, node_id)
            if peer.state != "revoked":
                peer.state, peer.epoch, peer.updated_at = (
                    "revoked",
                    peer.epoch + 1,
                    now(),
                )
                await self._disable_grants(db, node_id)
                event = PeerControlEvent(
                    id=str(uuid4()),
                    peer_node_id=node_id,
                    kind="peer.revoke",
                    peer_epoch=peer.epoch,
                    created_at=now(),
                    delivered=False,
                )
                db.add(event)
                self.audit(db, actor, node_id, "peer.revoke")
            epoch = peer.epoch
        self.close_connections(node_id)
        return {"state": "revoked", "peer_epoch": epoch}

    async def rotate(self, actor, node_id, pem, expected_epoch):
        cert = inspect_certificate(pem)
        if cert.node_id != node_id:
            raise PeerError("IDENTITY_MISMATCH", 400)
        async with self.sessions.begin() as db:
            await self.admin(db, actor)
            peer = await self.lock_peer(db, node_id)
            if peer.epoch != expected_epoch:
                raise PeerError("PEER_EPOCH_CONFLICT", 409)
            if peer.fingerprint == cert.fingerprint:
                raise PeerError("PIN_UNCHANGED", 409)
            peer.certificate_pem, peer.fingerprint = pem, cert.fingerprint
            peer.epoch += 1
            peer.updated_at = now()
            # Rotation conservatively invalidates every old grant/session binding.
            await self._disable_grants(db, node_id)
            self.audit(db, actor, node_id, "peer.rotate")
            epoch = peer.epoch
        self.close_connections(node_id)
        return {"peer_epoch": epoch, "fingerprint": cert.fingerprint}

    async def receive_revoke(self, tls, sender, event_id, peer_epoch):
        async with self.sessions.begin() as db:
            peer = await self.authenticate(db, tls, sender, control=True)
            old = await db.get(PeerControlEvent, event_id)
            if old:
                if (
                    old.peer_node_id != sender
                    or old.kind != "peer.remote_revoke"
                    or old.peer_epoch != peer_epoch
                ):
                    raise PeerError("ID_CONFLICT", 409)
            else:
                # Remote certificate epoch is not our local peer epoch namespace.
                peer.state, peer.epoch = "revoked", peer.epoch + 1
                await self._disable_grants(db, sender)
                db.add(
                    PeerControlEvent(
                        id=event_id,
                        peer_node_id=sender,
                        kind="peer.remote_revoke",
                        peer_epoch=peer_epoch,
                        created_at=now(),
                        delivered=True,
                    )
                )
            return {"event_id": event_id, "state": "revoked"}

    async def authorize(
        self,
        db: AsyncSession,
        tls: CertificateIdentity,
        *,
        sender_node_id: str,
        authority_node_id: str,
        channel_id: str,
        principal: Principal,
        action: str,
        grant_epoch: int,
        policy_epoch: int | None = None,
    ) -> AuthorizedPeer:
        peer = await self.authenticate(db, tls, sender_node_id)
        if str(principal.node_id) != sender_node_id:
            raise PeerError("IDENTITY_MISMATCH", 401)
        if authority_node_id != self.node_id:
            raise PeerError("AUTHORITY_UNAVAILABLE", 503)
        key = (sender_node_id, authority_node_id, channel_id)
        grant = await db.get(PeerGrant, key, populate_existing=True)
        if (
            grant is None
            or not grant.active
            or grant.epoch != grant_epoch
            or grant.expires_at <= now()
        ):
            raise PeerError("GRANT_DENIED")
        if principal.model_dump(mode="json") not in grant.actors:
            raise PeerError("PRINCIPAL_DENIED")
        if action not in grant.capabilities:
            raise PeerError("SCOPE_DENIED")
        room = await db.get(Room, channel_id, populate_existing=True)
        if room is None or room.archived_at or room.is_dm:
            raise PeerError("CHANNEL_DENIED")
        if grant.role == "observer" and action != "channel.read":
            raise PeerError("SCOPE_DENIED")
        await self.admin(db, grant.approved_by)
        consent = await db.get(PeerConsent, key, populate_existing=True)
        if (
            consent is None
            or not consent.active
            or (policy_epoch is not None and policy_epoch != consent.policy_epoch)
        ):
            raise PeerError("LOCAL_POLICY_DENIED")
        auth = AuthorizedPeer(
            sender_node_id,
            authority_node_id,
            channel_id,
            peer.epoch,
            grant.epoch,
            consent.policy_epoch,
            principal,
            action,
        )
        if action == "task.execute" and (
            self.local_policy is None or not await self.local_policy(db, auth)
        ):
            raise PeerError("LOCAL_POLICY_DENIED")
        return auth

    async def trusted_certificates(self) -> list[str]:
        # Revoked peers remain cryptographically authenticated for revoke/ACK ONLY.
        # Route-level state/pin checks remain mandatory, including on existing sockets.
        async with self.sessions() as db:
            pems = list((await db.execute(select(Peer.certificate_pem))).scalars())
        valid = []
        for pem in pems:
            try:
                inspect_certificate(pem)
                valid.append(pem)
            except PeerError:
                continue
        return valid

    async def replace_grant(self, actor, node_id, channel_id, body):
        if str(body.scope.channel_id) != channel_id:
            raise PeerError("CHANNEL_DENIED")
        async with self.sessions.begin() as db:
            await self.admin(db, actor)
            peer = await self.lock_peer(db, node_id)
            if peer.state != "active":
                raise PeerError("PEER_DENIED")
            room = await db.get(Room, channel_id)
            if room is None or room.archived_at or room.is_dm:
                raise PeerError("CHANNEL_DENIED")
            if any(str(p.node_id) != node_id for p in body.scope.actors):
                raise PeerError("IDENTITY_MISMATCH", 400)
            key = (node_id, self.node_id, channel_id)
            grant = await db.get(PeerGrant, key, populate_existing=True)
            if (
                grant is None
                or not grant.active
                or grant.epoch != body.expected_grant_epoch
            ):
                # Revoked grants require a new invitation, never PATCH resurrection.
                raise PeerError("GRANT_DENIED")
            grant.epoch += 1
            grant.actors = [p.model_dump(mode="json") for p in body.scope.actors]
            grant.capabilities = body.scope.capabilities
            grant.role = body.scope.role
            grant.expires_at = now() + timedelta(seconds=body.expires_in_seconds)
            grant.approved_by = actor
            consent = await db.get(PeerConsent, key)
            consent.policy_epoch += 1
            self.audit(db, actor, node_id, "grant.replace")
            return {"grant_epoch": grant.epoch, "policy_epoch": consent.policy_epoch}

    async def revoke_grant(self, actor, node_id, channel_id):
        async with self.sessions.begin() as db:
            await self.admin(db, actor)
            peer = await self.lock_peer(db, node_id)
            key = (node_id, self.node_id, channel_id)
            grant = await db.get(PeerGrant, key, populate_existing=True)
            if grant is None:
                raise PeerError("GRANT_DENIED")
            if grant.active:
                grant.active, grant.epoch = False, grant.epoch + 1
                consent = await db.get(PeerConsent, key)
                consent.active, consent.policy_epoch = False, consent.policy_epoch + 1
                db.add(
                    PeerControlEvent(
                        id=str(uuid4()),
                        peer_node_id=node_id,
                        kind="grant.revoke",
                        channel_id=channel_id,
                        grant_epoch=grant.epoch,
                        peer_epoch=peer.epoch,
                        created_at=now(),
                        delivered=False,
                    )
                )
                self.audit(db, actor, node_id, "grant.revoke")
            return {"grant_epoch": grant.epoch, "state": "revoked"}

    async def deliver_controls(self) -> int:
        """One bounded outbox pass. Network failure keeps durable events pending."""
        from anygarden.federation.schemas import Endpoint
        from anygarden.federation.transport import post

        async with self.sessions() as db:
            ids = list(
                (
                    await db.execute(
                        select(PeerControlEvent.id)
                        .where(
                            PeerControlEvent.delivered.is_(False),
                            PeerControlEvent.kind.in_(("peer.revoke", "grant.revoke")),
                        )
                        .order_by(PeerControlEvent.created_at)
                        .limit(100)
                    )
                ).scalars()
            )
        delivered = 0
        for event_id in ids:
            async with self.sessions.begin() as db:
                event = await db.get(PeerControlEvent, event_id)
                peer = await self.lock_peer(db, event.peer_node_id)
                if peer.state not in ("active", "revoked"):
                    continue
                endpoint, pem, node, epoch = (
                    Endpoint(**peer.endpoint),
                    peer.certificate_pem,
                    peer.node_id,
                    peer.epoch,
                )
                payload = {
                    "protocol_version": 1,
                    "sender_node_id": self.node_id,
                    "event_id": event.id,
                    "peer_epoch": event.peer_epoch,
                }
                path = "/api/v1/federation/revoke"
                if event.kind == "grant.revoke":
                    payload.update(
                        channel_id=event.channel_id, grant_epoch=event.grant_epoch
                    )
                    path = "/api/v1/federation/grants/revoke"
            try:
                receipt = await post(self, endpoint, pem, path, payload)
                if receipt != {"event_id": event_id, "state": "revoked"}:
                    continue
            except PeerError:
                continue
            async with self.sessions.begin() as db:
                peer = await self.lock_peer(db, node)
                if peer.epoch != epoch:
                    continue
                await db.execute(
                    update(PeerControlEvent)
                    .where(PeerControlEvent.id == event_id)
                    .values(delivered=True)
                )
            delivered += 1
        return delivered

    async def check_received_grants(self, db, issuer_node_id, receipt):
        """#591 delivery hook: inspect authenticated revocation tombstones.

        This is necessary but not sufficient for shared dispatch: fresh authority
        confirmation and execution-node local policy are still mandatory.
        """
        for grant in receipt.get("grants", []):
            if (
                not isinstance(grant, dict)
                or not isinstance(grant.get("grant_epoch"), int)
                or isinstance(grant["grant_epoch"], bool)
                or grant["grant_epoch"] < 1
                or not isinstance(grant.get("channel_id"), str)
            ):
                raise PeerError("RECEIPT_DENIED", 409)
            revoked = await db.scalar(
                select(PeerControlEvent.id)
                .where(
                    PeerControlEvent.peer_node_id == issuer_node_id,
                    PeerControlEvent.kind == "grant.remote_revoke",
                    PeerControlEvent.channel_id == grant["channel_id"],
                    PeerControlEvent.grant_epoch >= grant["grant_epoch"],
                )
                .limit(1)
            )
            if revoked:
                raise PeerError("GRANT_DENIED")

    async def receive_grant_revoke(
        self, tls, sender, event_id, peer_epoch, channel_id, grant_epoch
    ):
        async with self.sessions.begin() as db:
            await self.authenticate(db, tls, sender, control=True)
            old = await db.get(PeerControlEvent, event_id)
            if old:
                if (
                    old.peer_node_id != sender
                    or old.kind != "grant.remote_revoke"
                    or old.peer_epoch != peer_epoch
                    or old.channel_id != channel_id
                    or old.grant_epoch != grant_epoch
                ):
                    raise PeerError("ID_CONFLICT", 409)
            else:
                db.add(
                    PeerControlEvent(
                        id=event_id,
                        peer_node_id=sender,
                        kind="grant.remote_revoke",
                        peer_epoch=peer_epoch,
                        channel_id=channel_id,
                        grant_epoch=grant_epoch,
                        created_at=now(),
                        delivered=True,
                    )
                )
                key = (sender, sender, channel_id)
                grant = await db.get(PeerGrant, key)
                if grant is not None and grant.epoch <= grant_epoch:
                    grant.active, grant.epoch = False, grant_epoch
                    consent = await db.get(PeerConsent, key)
                    if consent is not None:
                        consent.active, consent.policy_epoch = (
                            False,
                            consent.policy_epoch + 1,
                        )
            # Does not revoke unrelated channels or mutate our local epoch namespace.
            return {"event_id": event_id, "state": "revoked"}

    async def authorize_delivery(
        self, db, tls, *, authority_node_id, channel_id, grant_epoch, policy_epoch=None
    ):
        """Mirror-side check before event dedup/projection, in caller transaction.

        The caller also checks its channel-to-local-Room mapping and archive
        state. This does not authorize starting work during an authority outage.
        """
        if authority_node_id == self.node_id:
            raise PeerError("WRONG_AUTHORITY")
        peer = await self.authenticate(db, tls, authority_node_id)
        key = (authority_node_id, authority_node_id, channel_id)
        grant = await db.get(PeerGrant, key, populate_existing=True)
        if (
            grant is None
            or not grant.active
            or grant.epoch != grant_epoch
            or grant.expires_at <= now()
            or "channel.read" not in grant.capabilities
        ):
            raise PeerError("GRANT_DENIED")
        consent = await db.get(PeerConsent, key, populate_existing=True)
        if (
            consent is None
            or not consent.active
            or (policy_epoch is not None and consent.policy_epoch != policy_epoch)
        ):
            raise PeerError("LOCAL_POLICY_DENIED")
        await self.admin(db, grant.approved_by)
        await self.check_received_grants(
            db,
            authority_node_id,
            {"grants": [{"channel_id": channel_id, "grant_epoch": grant_epoch}]},
        )
        return {
            "certificate_epoch": peer.epoch,
            "grant_epoch": grant.epoch,
            "policy_epoch": consent.policy_epoch,
        }
