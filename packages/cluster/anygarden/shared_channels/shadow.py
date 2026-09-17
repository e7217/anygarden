"""Shadow participants for remote shared-channel principals (task #51).

The delegation contract (`federation.delegation.DelegationService`) resolves
every command actor to a local ``Participant`` row to fence current roles
before dedup. Remote principals have no local identity — these shadow rows
carry **no user_id/agent_id** (NULL), so no local identity predicate can ever
match them: they confer no credentials, no visibility, and no membership in
any legacy room path. They are execution-role markers only.

Row identity is deterministic — ``uuid5`` over a dedicated namespace,
deliberately distinct from the delegation execution-ID namespace
(``NAMESPACE_URL``-based, ``federation.delegation``) — so the resolver can
compute the same id without a linkage column or migration.
"""

from __future__ import annotations

from uuid import NAMESPACE_OID, uuid5

SHADOW_NAMESPACE = uuid5(NAMESPACE_OID, "anygarden:shared-shadow-participant")

#: Roles that pass the delegation participant fence (member/admin/owner).
#: Anything else — notably ``observer`` — fails closed.
FENCED_ROLES = ("member", "admin", "owner")


def shadow_participant_id(
    authority_node_id: str, channel_id: str, node_id: str, kind: str, principal_id: str
) -> str:
    return str(
        uuid5(
            SHADOW_NAMESPACE,
            f"{authority_node_id}:{channel_id}:{node_id}:{kind}:{principal_id}",
        )
    )


async def sync_shadow(db, node_id: str, stream, event: dict) -> None:
    """Mirror a participant-changed event into the authority-side shadow row.

    Called from the single roster projection choke point with the composing
    service's node id. Only the authority side creates shadows (mirror-side
    guards never resolve remote actors), and only for principals from other
    nodes — the authority's own principals already have real Participant
    rows. Removal (``active=False``) demotes the shadow below the fenced
    roles immediately, so post-revocation replays are rejected before dedup,
    independently of peer grants.
    """
    from anygarden.db.models import Participant

    principal = event["principal"]
    if node_id != stream.authority_node_id or principal["node_id"] == node_id:
        return
    shadow_id = shadow_participant_id(
        stream.authority_node_id,
        stream.channel_id,
        principal["node_id"],
        principal["kind"],
        principal["principal_id"],
    )
    shadow = await db.get(Participant, shadow_id)
    role = event["role"] if event["active"] else "observer"
    fenced = role if role in FENCED_ROLES else "observer"
    if shadow is None:
        if not event["active"]:
            # Removal before any shadow existed: nothing to demote.
            return
        db.add(Participant(id=shadow_id, room_id=stream.local_room_id, role=fenced))
    else:
        shadow.role = fenced
