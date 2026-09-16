"""Explicit bounded retries: SQL intent commits before pinned network I/O.

A failed/lost response keeps the SAME request ID unconfirmed. No local confirmed
message, task execution or fallback authority is manufactured during outage.
"""

import json

from anygarden.federation.certificates import inspect_certificate
from anygarden.federation.errors import PeerError
from anygarden.federation.schemas import Endpoint
from anygarden.federation.transport import post
from anygarden.shared_channels.models import ChannelSubmission
from anygarden.shared_channels.schemas import ChannelError, validate


async def retry_submission(service, identity, authority, channel, request_id):
    key = (authority, channel, request_id)
    async with service.sessions.begin() as db:
        await service.local_access(
            db, identity=identity, authority=authority, channel=channel, write=True
        )
        row = await db.get(ChannelSubmission, key)
        if row is None:
            raise ChannelError("SUBMISSION_NOT_FOUND", 404)
        envelope = json.loads(row.body)
        # Recheck actor/capability/epoch before returning an old receipt too.
        await service.queue(db, envelope, identity=identity)
        if row.receipt:
            return service.submission_view(row)
        peer, _ = await service.mirror_policy(
            db, authority, channel, envelope["grant_epoch"]
        )
        endpoint, pem = Endpoint(**peer.endpoint), peer.certificate_pem
    try:
        receipt = await post(
            service.peers,
            endpoint,
            pem,
            "/api/v1/federation/channels/commands",
            envelope,
        )
        validate("receipt", receipt)
        for field in ("request_id", "authority_node_id", "channel_id"):
            if receipt[field] != envelope[field]:
                raise ChannelError("RECEIPT_INTEGRITY")
    except (PeerError, ChannelError) as error:
        async with service.sessions.begin() as db:
            await service.queue(db, envelope, identity=identity)
            row = await db.get(ChannelSubmission, key)
            row.error_code = error.code
            return service.submission_view(row)
    async with service.sessions.begin() as db:
        await service.queue(db, envelope, identity=identity)
        # Pin may have rotated while the request was in flight.
        await service.peers.authorize_delivery(
            db,
            inspect_certificate(pem),
            authority_node_id=authority,
            channel_id=channel,
            grant_epoch=envelope["grant_epoch"],
        )
        row = await db.get(ChannelSubmission, key)
        if row.receipt and row.receipt != receipt:
            raise ChannelError("RECEIPT_INTEGRITY")
        row.receipt, row.state, row.error_code = receipt, "confirmed", None
        return service.submission_view(row)


async def pull(service, identity, scope):
    authority, channel = scope["authority_node_id"], scope["channel_id"]
    if scope["sender_node_id"] != service.node_id or scope[
        "actor"
    ] != service.local_principal(identity):
        raise ChannelError("PRINCIPAL_DENIED", 403)
    async with service.sessions.begin() as db:
        stream = await service.local_access(
            db, identity=identity, authority=authority, channel=channel
        )
        peer, grant = await service.mirror_policy(
            db, authority, channel, scope["grant_epoch"]
        )
        if scope["actor"] not in grant.actors:
            raise ChannelError("PRINCIPAL_DENIED", 403)
        endpoint, pem, after = (
            Endpoint(**peer.endpoint),
            peer.certificate_pem,
            stream.applied_seq,
        )
    result = await post(
        service.peers,
        endpoint,
        pem,
        "/api/v1/federation/channels/replay",
        {**scope, "after_seq": after, "limit": 100},
    )
    if set(result) != {"events"}:
        raise ChannelError("EVENT_INTEGRITY")
    async with service.sessions.begin() as db:
        await service.local_access(
            db, identity=identity, authority=authority, channel=channel
        )
        received = await service.receive(
            db,
            result["events"],
            tls=inspect_certificate(pem),
            authority_node_id=authority,
            channel_id=channel,
            grant_epoch=scope["grant_epoch"],
        )
    # Cursor + inbox + projections are already durable if this ACK is lost.
    await post(
        service.peers,
        endpoint,
        pem,
        "/api/v1/federation/channels/ack",
        {**scope, "seq": received["ack_seq"]},
    )
    return received
