"""Shared-channel room visibility for legacy room listings (#593 task #34).

Read-parity companion to ``ChannelService.local_access``: decides which
shared-channel-bound Rooms a local identity may currently *see* in rooms API
metadata (listings and room detail). It deliberately does not mutate, does not
take write locks, and never creates local credentials for remote principals.
Message/thread content remains exclusive to the shared-channel snapshot API.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.federation.models import PeerGrant, Peer
from anygarden.shared_channels.models import ChannelStream, SharedParticipant


def _principal(channel_service, identity) -> dict | None:
    if identity.kind not in {"user", "agent"}:
        return None
    try:
        return channel_service.local_principal(identity)
    except Exception:
        return None


async def visible_shared_room_ids(
    db: AsyncSession,
    *,
    identity,
    room_ids: frozenset[str],
    channel_service,
) -> frozenset[str]:
    """Return the subset of ``room_ids`` currently visible via sharing.

    Visibility predicate (read parity with ``local_access``):

    - identity must be a local user or agent principal (guests fail closed);
    - a Participant row on the bound Room is checked by the caller
      (``require_capability``/``accessible_room_ids``), not here;
    - authority-side bindings additionally require an **active** roster row
      for the principal (removal tombstone hides the room immediately);
    - mirror-side bindings additionally require the current inbound grant to
      be active, unexpired, and to list the principal among its actors.

    All checks read the caller's transaction — no cache, per-request
    re-evaluation. A ``None`` channel service (sharing disabled) hides every
    shared room. This is a listing/detail read path: unlike
    ``local_access``/``mirror_policy`` it takes no write locks.
    """
    if channel_service is None or not room_ids:
        return frozenset()
    principal = _principal(channel_service, identity)
    if principal is None:
        return frozenset()
    streams = (
        (
            await db.scalars(
                select(ChannelStream).where(ChannelStream.local_room_id.in_(room_ids))
            )
        )
        .unique()
        .all()
    )
    visible: set[str] = set()
    for stream in streams:
        if stream.authority_node_id == channel_service.node_id:
            row = await db.get(
                SharedParticipant,
                (
                    stream.authority_node_id,
                    stream.channel_id,
                    principal["node_id"],
                    principal["kind"],
                    principal["principal_id"],
                ),
            )
            if row is not None and row.active:
                visible.add(stream.local_room_id)
        else:
            grant = await db.get(
                PeerGrant,
                (
                    stream.authority_node_id,
                    stream.authority_node_id,
                    stream.channel_id,
                ),
            )
            peer = await db.get(Peer, stream.authority_node_id)
            if (
                grant is not None
                and grant.active
                and peer is not None
                and _unexpired(grant)
                and principal in (grant.actors or [])
            ):
                visible.add(stream.local_room_id)
    return frozenset(visible)


def _unexpired(grant) -> bool:
    from anygarden.federation.service import now

    return grant.expires_at is not None and grant.expires_at > now()
