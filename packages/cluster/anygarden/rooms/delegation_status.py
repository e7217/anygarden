"""Read-only delegation status for UI polling (#593 support, task #35).

Serves the follower-side ``DelegationMirror`` rows of the shared channel
bound to a local mirror room. The mirror is authority-confirmed state only:
this view never mutates Tasks, delegations or execution state and never
triggers execution. Room visibility stays governed by the ordinary rooms
authorization path — shared rooms opt in explicitly through ``allow_shared``,
so every other rooms endpoint keeps rejecting them with the shared-channel
redirect.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from anygarden.auth.dependencies import Identity
from anygarden.dependencies import get_current_identity, get_db
from anygarden.federation.delegation_models import DelegationMirror
from anygarden.rooms.authorization import Capability, require_capability
from anygarden.shared_channels.models import ChannelStream

router = APIRouter(tags=["rooms"])


class DelegationStatusOut(BaseModel):
    """One authority-confirmed delegation, exactly as mirrored.

    ``state``/``process_state``/``task_status`` use the wire vocabulary the
    channel events already carry (accepted/running, cancel_requested,
    unknown, …) so the UI polls one source of truth instead of re-deriving
    transitions.
    """

    authority_node_id: str
    channel_id: str
    delegation_id: str
    task_id: str
    source_message_id: str
    requester: dict
    executor: dict
    execution_id: str | None
    revision: int
    state: str
    process_state: str
    task_status: str
    result_markdown: str | None = None
    error: str | None = None
    created_at: str | None = None
    finished_at: str | None = None
    can_cancel: bool = False


@router.get("/{room_id}/delegations", response_model=list[DelegationStatusOut])
async def list_room_delegations(
    room_id: str,
    request: Request,
    identity: Identity = Depends(get_current_identity),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
):
    """List delegation mirrors for the channel bound to this room.

    Requires ordinary room read capability; the shared-room opt-in keeps
    membership, archive and role checks intact. Rooms without a bound
    channel simply have no delegations.

    Guests are rejected after the room gate with the same error outsiders
    see: channel membership authority is the federated roster, and a local
    guest binding must not bypass it (#34 B-6 fail-closed parity with the
    shared-channel snapshot API).
    """
    await require_capability(
        db,
        room_id=room_id,
        identity=identity,
        capability=Capability.ROOM_READ,
        allow_shared=True,
    )
    if identity.kind not in {"user", "agent"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not a member of this room",
        )
    service = getattr(request.app.state, "channel_service", None)
    if service is not None:
        stream = await db.scalar(
            select(ChannelStream).where(ChannelStream.local_room_id == room_id)
        )
        if stream:
            from anygarden.shared_channels.product import actor_policy, delegation_views

            await service.local_access(
                db,
                identity=identity,
                authority=stream.authority_node_id,
                channel=stream.channel_id,
            )
            policy = await actor_policy(service, db, identity, stream)
            return await delegation_views(service, db, stream, policy)
    rows = (
        await db.scalars(
            select(DelegationMirror)
            .join(
                ChannelStream,
                (ChannelStream.authority_node_id == DelegationMirror.authority_node_id)
                & (ChannelStream.channel_id == DelegationMirror.channel_id),
            )
            .where(ChannelStream.local_room_id == room_id)
            .order_by(DelegationMirror.delegation_id)
        )
    ).all()
    return [
        DelegationStatusOut.model_validate(row, from_attributes=True) for row in rows
    ]
