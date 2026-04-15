"""WebSocket route handler for ``/ws/rooms/{room_id}``."""

from __future__ import annotations

import json
from typing import Any

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.auth.dependencies import Identity, get_identity, require_room_member
from doorae.config import DooraeSettings
from doorae.db.models import ActivityLog, Agent, Participant, Room
from doorae.db.repository import append_message, replay_since_seq
from doorae.ws.manager import ConnectionManager
from doorae.orchestration.rules import (
    CooldownManager,
    GuestRoomAggregateLimiter,
    TypingTracker,
    parse_mentions,
)
from doorae.ws.protocol import (
    ErrorOut,
    MessageOut,
    TypingOut,
    WelcomeOut,
    parse_incoming,
    SendFrame,
    TypingFrame,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


def _extract_since_seq(query_string: str | None) -> int:
    """Parse ``since_seq`` from raw query string."""
    if not query_string:
        return 0
    for part in query_string.split("&"):
        if part.startswith("since_seq="):
            try:
                return int(part.split("=", 1)[1])
            except (ValueError, IndexError):
                return 0
    return 0


@router.websocket("/ws/rooms/{room_id}")
async def ws_room(websocket: WebSocket, room_id: str) -> None:
    """Main WebSocket endpoint for room-scoped messaging."""
    # Retrieve app-level objects stashed on app.state by the lifespan.
    app = websocket.app
    config: DooraeSettings = app.state.config
    session_factory = app.state.session_factory

    # Get manager and orchestration objects from app.state (not module globals)
    manager: ConnectionManager = app.state.connection_manager
    cooldown_mgr: CooldownManager = app.state.cooldown_manager
    # Guests use a separate, stricter cooldown bucket (§11.7). A
    # missing guest manager in test setups is tolerated — we fall
    # back to the shared one so legacy tests don't break.
    guest_cooldown_mgr: CooldownManager = getattr(
        app.state, "guest_cooldown_manager", cooldown_mgr
    )
    guest_room_limiter: GuestRoomAggregateLimiter | None = getattr(
        app.state, "guest_room_limiter", None
    )
    typing_tracker: TypingTracker = app.state.typing_tracker

    # -- Authentication via Sec-WebSocket-Protocol --
    raw_protocols = websocket.headers.get("sec-websocket-protocol", "")
    selected_subprotocol: str | None = None
    if raw_protocols:
        selected_subprotocol = "doorae.v1"

    # Authenticate and resolve participant before accepting the connection.
    # We must finish the DB session cleanly before calling websocket.close()
    # to avoid aiosqlite connection cleanup races (especially in tests).
    identity: Identity | None = None
    participant: Participant | None = None
    auth_error: str | None = None

    async with session_factory() as db:
        try:
            identity = await get_identity(
                db,
                jwt_secret=config.jwt_secret,
                sec_websocket_protocol=raw_protocols or None,
            )
        except Exception as exc:
            logger.warning("ws.auth_failed", error=str(exc), protocols=raw_protocols[:100])
            auth_error = "Authentication failed"

        if auth_error is None and identity is not None:
            try:
                participant = await require_room_member(room_id, identity, db)
            except Exception as exc:
                logger.warning("ws.not_member", error=str(exc), identity_kind=identity.kind, identity_id=identity.id, room_id=room_id)
                auth_error = "Not a room member"

    if auth_error is not None or participant is None:
        code = 4001 if auth_error == "Authentication failed" else 4003
        await websocket.close(code=code, reason=auth_error or "Unauthorized")
        return

    await websocket.accept(subprotocol=selected_subprotocol)

    # Send welcome frame so the client knows its own participant_id.
    # For agents, include rooms that the agent is a member of but
    # hasn't connected to yet (e.g. sub-rooms created while the
    # agent was offline).  The SDK will auto-join them.
    pending_rooms: list[str] = []
    if identity and identity.kind == "agent":
        async with session_factory() as db:
            result = await db.execute(
                select(Participant.id, Participant.room_id).where(
                    Participant.agent_id == identity.id,
                )
            )
            pid_to_room = {row[0]: row[1] for row in result.all()}
        connected_pids = await manager.connected_participant_ids()
        connected_room_ids = {
            pid_to_room[pid] for pid in pid_to_room if pid in connected_pids
        }
        connected_room_ids.add(room_id)  # about to subscribe
        pending_rooms = sorted(set(pid_to_room.values()) - connected_room_ids)

    welcome = WelcomeOut(
        participant_id=participant.id,
        pending_rooms=pending_rooms,
    )
    await websocket.send_text(welcome.model_dump_json())

    # Subscribe
    await manager.subscribe(room_id, participant.id, websocket)
    logger.info("ws.connected", room_id=room_id, participant_id=participant.id)

    try:
        # -- Replay missed messages --
        since_seq = _extract_since_seq(websocket.scope.get("query_string", b"").decode())
        if since_seq > 0:
            async with session_factory() as db:
                missed = await replay_since_seq(db, room_id, since_seq)
                for msg in missed:
                    frame = MessageOut(
                        id=msg.id,
                        room_id=msg.room_id,
                        participant_id=msg.participant_id,
                        content=msg.content,
                        seq=msg.seq,
                        created_at=msg.created_at,
                        metadata=msg.extra_metadata,
                    )
                    await websocket.send_text(frame.model_dump_json())

        # -- Main receive loop --
        while True:
            raw = await websocket.receive_text()
            try:
                data: dict[str, Any] = json.loads(raw)
                frame_in = parse_incoming(data)
            except (json.JSONDecodeError, ValueError) as exc:
                await websocket.send_text(
                    ErrorOut(detail=f"Bad frame: {exc}").model_dump_json()
                )
                continue

            if isinstance(frame_in, SendFrame):
                is_guest = identity is not None and identity.kind == "guest"

                # Apply cooldown check. Guests run on a stricter
                # bucket so a shared invite with many people can't
                # drown out real users — §11.7. Error text is shared
                # with the registered-user path so it can't be used
                # as a guest/user oracle over the WS channel.
                active_cooldown = (
                    guest_cooldown_mgr if is_guest else cooldown_mgr
                )
                if not active_cooldown.check_cooldown(participant.id):
                    await websocket.send_text(
                        ErrorOut(
                            detail="Rate limited — please wait"
                        ).model_dump_json()
                    )
                    continue

                # Clear typing state on send
                typing_tracker.set_typing(room_id, participant.id, False)

                # Parse mentions and attach to metadata
                mentions = parse_mentions(frame_in.content)
                if is_guest:
                    # §11.6 — guest mentions are an *allowlist* of
                    # variants that cannot route across the guest's
                    # single-room boundary. ``user`` (incl.
                    # agents-as-users) and ``legacy`` name-style are
                    # kept; ``room`` is the documented cross-room
                    # trigger and is stripped. Using an allowlist
                    # (not ``type != "room"``) means future mention
                    # variants default to denied until we decide.
                    _guest_allowed_mentions = {"user", "legacy"}
                    mentions = [
                        m
                        for m in mentions
                        if m.get("type") in _guest_allowed_mentions
                    ]

                    # §11.7 room-aggregate cap on guest mentions.
                    # ``guest_room_limiter`` is populated in the
                    # lifespan; an absent limiter is an app-wiring
                    # bug, so we fail closed rather than silently
                    # skipping the cap.
                    if mentions:
                        if guest_room_limiter is None:
                            logger.error(
                                "ws.guest_room_limiter_missing", room_id=room_id
                            )
                            await websocket.send_text(
                                ErrorOut(
                                    detail="Server misconfiguration"
                                ).model_dump_json()
                            )
                            continue
                        if not guest_room_limiter.check(room_id):
                            await websocket.send_text(
                                ErrorOut(
                                    detail="Rate limited (room aggregate)"
                                ).model_dump_json()
                            )
                            continue

                metadata = dict(frame_in.metadata) if frame_in.metadata else {}
                if mentions:
                    metadata["mentions"] = mentions

                # Room mention → representative agent routing.
                # Guests can't reach this block — their mentions had
                # ``type == "room"`` filtered out above — but the
                # explicit ``not is_guest`` check is defence in depth
                # in case the guest filter ever loosens: membership
                # changes are admin-only and the auto-join below
                # creates a Participant row.
                room_mentions = (
                    [m for m in mentions if m.get("type") == "room"]
                    if not is_guest
                    else []
                )
                if room_mentions:
                    target_room_id = room_mentions[0]["id"]
                    async with session_factory() as rq_db:
                        target_room = (
                            await rq_db.execute(
                                select(Room).where(Room.id == target_room_id)
                            )
                        ).scalar_one_or_none()
                        if target_room and target_room.representative_agent_id:
                            rep_agent_id = target_room.representative_agent_id
                            # Check agent is online
                            rep_agent = (
                                await rq_db.execute(
                                    select(Agent).where(Agent.id == rep_agent_id)
                                )
                            ).scalar_one_or_none()
                            if rep_agent and rep_agent.actual_state not in ("running", "starting"):
                                # Offline — send system message after storing
                                metadata["_rep_offline"] = True
                            elif rep_agent:
                                # Auto-join representative to this room if not a participant
                                existing = (
                                    await rq_db.execute(
                                        select(Participant).where(
                                            Participant.room_id == room_id,
                                            Participant.agent_id == rep_agent_id,
                                        )
                                    )
                                ).scalar_one_or_none()
                                if existing is None:
                                    rq_db.add(Participant(
                                        room_id=room_id,
                                        agent_id=rep_agent_id,
                                        role="member",
                                    ))
                                    await rq_db.commit()
                                # Attach room_query metadata
                                metadata["room_query"] = {
                                    "target_room_id": target_room_id,
                                    "source_room_id": room_id,
                                }

                async with session_factory() as db:
                    msg = await append_message(
                        db,
                        room_id=room_id,
                        participant_id=participant.id,
                        content=frame_in.content,
                        metadata=metadata or None,
                    )

                    # Log message events for agents (same transaction)
                    if identity and identity.kind == "agent":
                        db.add(ActivityLog(
                            agent_id=identity.id,
                            event_type="response_sent",
                            details={"room_id": room_id},
                        ))
                    elif identity and identity.kind == "user":
                        agent_parts = (await db.execute(
                            select(Participant.agent_id).where(
                                Participant.room_id == room_id,
                                Participant.agent_id.isnot(None),
                            )
                        )).scalars().all()
                        for aid in agent_parts:
                            db.add(ActivityLog(
                                agent_id=aid,
                                event_type="message_received",
                                details={"room_id": room_id, "from_participant_id": participant.id},
                            ))

                    await db.commit()
                    out = MessageOut(
                        id=msg.id,
                        room_id=msg.room_id,
                        participant_id=msg.participant_id,
                        content=msg.content,
                        seq=msg.seq,
                        created_at=msg.created_at,
                        metadata=msg.extra_metadata,
                    )
                await manager.broadcast(room_id, out)

                # Send system message if representative agent is offline
                if metadata.get("_rep_offline"):
                    sys_out = ErrorOut(
                        detail="대표 에이전트가 오프라인입니다",
                    )
                    await websocket.send_text(sys_out.model_dump_json())

            elif isinstance(frame_in, TypingFrame):
                typing_tracker.set_typing(room_id, participant.id, frame_in.is_typing)
                out_typing = TypingOut(
                    room_id=room_id,
                    participant_id=participant.id,
                    is_typing=frame_in.is_typing,
                )
                await manager.broadcast(room_id, out_typing)

                # Agent typing → log processing_started
                if identity and identity.kind == "agent" and frame_in.is_typing:
                    async with session_factory() as db:
                        db.add(ActivityLog(
                            agent_id=identity.id,
                            event_type="processing_started",
                            details={"room_id": room_id},
                        ))
                        await db.commit()

            else:
                await websocket.send_text(
                    ErrorOut(detail="Frame type not supported on this endpoint").model_dump_json()
                )

    except WebSocketDisconnect:
        logger.info("ws.disconnected", room_id=room_id, participant_id=participant.id)
    except Exception as exc:
        logger.error("ws.error", room_id=room_id, error=str(exc))
    finally:
        await manager.unsubscribe(participant.id)
