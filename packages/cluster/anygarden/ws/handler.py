"""WebSocket route handler for ``/ws/rooms/{room_id}``."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from anygarden.auth.dependencies import Identity, get_identity, require_room_member
from anygarden.config import AnygardenSettings
from anygarden.db.models import ActivityLog, Agent, Participant, Room, User
from anygarden.db.repository import append_message, replay_since_seq
from anygarden.rooms.membership import ensure_agent_in_room
from anygarden.messages.references import (
    InvalidSharedFileReference,
    canonicalize_shared_file_references,
)
from anygarden.observability.metrics import (
    guest_active,
    guest_rate_limited_total,
)
from anygarden.ws.manager import ConnectionManager
from anygarden.orchestration.rules import (
    MAX_PEER_DEPTH,
    MAX_TOTAL_PEER_HANDOFFS_PER_USER_TURN,
    CooldownManager,
    GuestRoomAggregateLimiter,
    TypingTracker,
    is_peer_mention,
    parse_mentions,
    strip_peer_mentions_from_content,
)
from anygarden.ws.protocol import (
    ErrorOut,
    LifecycleFrame,
    MessageOut,
    ParticipantBrief,
    TypingOut,
    WelcomeOut,
    parse_incoming,
    SendFrame,
    TypingFrame,
)

logger = structlog.get_logger(__name__)

router = APIRouter()


def _lifecycle_details(frame: LifecycleFrame) -> dict[str, Any]:
    """Extract the JSON details payload from a LifecycleFrame.

    All optional fields are included only when set (mirrors the wire
    format's ``exclude_none`` dump). ``room_id`` is always present.
    """
    out: dict[str, Any] = {"room_id": frame.room_id}
    if frame.engine is not None:
        out["engine"] = frame.engine
    if frame.outcome is not None:
        out["outcome"] = frame.outcome
    if frame.duration_ms is not None:
        out["duration_ms"] = frame.duration_ms
    if frame.error is not None:
        out["error"] = frame.error
    return out


async def _persist_lifecycle_event(
    db: AsyncSession, *, agent_id: str, frame: LifecycleFrame
) -> None:
    """Write a LifecycleFrame as an ActivityLog row.

    Commit is the caller's responsibility.
    """
    db.add(ActivityLog(
        agent_id=agent_id,
        event_type=frame.event,
        request_id=frame.request_id,
        details=_lifecycle_details(frame),
    ))


def _is_ambient_candidate(
    content: str,
    metadata: dict[str, Any],
    *,
    sender_is_agent: bool,
) -> bool:
    """Decide whether a new message is a candidate for the ambient
    context window (#148 Part 3).

    A message is "ambient" when it's agent-to-agent chatter that
    isn't directly aimed at anyone specific and isn't carrying a
    task-initiation payload. Rules:

    0. Sender is an agent (#233). Human and guest messages are
       never ambient — users always expect their input to be
       actionable. The stamp was originally designed to dampen
       agent-to-agent chatter, and missing this gate caused
       orchestrator rooms to go silent once #225 flipped
       ``context_window_enabled`` on by default.
    1. No user / legacy mention already parsed into metadata.
       ``parse_mentions`` runs upstream, so seeing an addressable
       mention means "someone is being targeted" — not ambient.
    2. Not a delegated / room-routed task. These prefixes are the
       adapter-side "always respond" short-circuits (``decide_policy``
       rule 2); stamping ingest_only on them would silently convert
       an active task into passive ingestion.
    3. No ``room_query`` metadata — that marks the representative-
       agent forwarding path and must reach the representative as a
       real response.

    Intentionally mirrors ``decide_policy``'s rule 5/7 surface in
    agents/integrations/base.py — the two branches must stay
    symmetric so the server's "should I stamp it?" and the agent's
    "should I act on the stamp?" never drift. A contract test lives
    alongside the migration work plan (#148 §6).
    """
    if not sender_is_agent:
        return False
    if (
        content.startswith("[DELEGATED]")
        or content.startswith("[ROOM_QUERY]")
        or content.startswith("[HANDOFF]")
    ):
        return False
    if metadata.get("room_query"):
        return False
    mentions = metadata.get("mentions") or []
    for m in mentions:
        if isinstance(m, dict) and m.get("type") in ("user", "legacy"):
            return False
    return True


async def _apply_orchestrator_handoff(
    db: AsyncSession,
    *,
    room_id: str,
    content: str,
    metadata: dict[str, Any],
    orchestrator_agent_id: str | None,
    sender_agent_id: str | None,
) -> str | None:
    """Parse a ``[HANDOFF]`` message and flip the room's next-speaker
    pointer (#159 Phase C).

    Returns the new ``next_speaker_participant_id`` when the handoff
    was accepted, or ``None`` when it was ignored.

    Acceptance rules (every one must hold):

    1. ``content`` starts with ``[HANDOFF]``. Other prefixes short-
       circuit immediately — this helper is a no-op on ordinary
       messages so the caller can always invoke it.
    2. The sender is this room's orchestrator. Workers can't hijack
       turn order even if they emit the prefix.
    3. The message carries a ``type: user`` mention in its metadata
       whose ``id`` is an existing participant of this room.

    On success the helper:

    - Updates ``Room.next_speaker_participant_id`` in the same DB
      transaction as the message persist (caller owns the commit).
    - Mutates ``metadata`` in place to add
      ``next_speaker_participant_id``. The broadcast carries this
      stamp so agent-side ``decide_policy``'s O2 rule fires on the
      target.

    Structural mirror of ``_compute_round_robin_next`` — both are
    strategy-specific hooks that the main SendFrame pipeline
    delegates to, so each strategy owns its next-speaker logic in
    one place.
    """
    if not content.startswith("[HANDOFF]"):
        return None
    if not orchestrator_agent_id:
        return None
    if sender_agent_id != orchestrator_agent_id:
        return None

    # Pull the first ``type=user`` mention as the target. We trust
    # the server's ``parse_mentions`` output here because it's
    # already been run upstream of this helper in the SendFrame
    # path (see ``ws/handler.py`` where ``metadata["mentions"]`` is
    # populated).
    mentions = metadata.get("mentions") or []
    target_pid: str | None = None
    for m in mentions:
        if isinstance(m, dict) and m.get("type") == "user":
            candidate = m.get("id")
            if isinstance(candidate, str) and candidate:
                target_pid = candidate
                break
    if target_pid is None:
        return None

    # Confirm the target is actually a participant of this room.
    # Catches LLM hallucinations before they poison the pointer.
    participant_id = (
        await db.execute(
            select(Participant.id)
            .where(Participant.room_id == room_id)
            .where(Participant.id == target_pid)
        )
    ).scalar_one_or_none()
    if participant_id is None:
        return None

    await db.execute(
        sa_update(Room)
        .where(Room.id == room_id)
        .values(next_speaker_participant_id=target_pid)
    )
    metadata["next_speaker_participant_id"] = target_pid
    return target_pid


async def _apply_orchestrator_fallback_nominate(
    db: AsyncSession,
    *,
    room_id: str,
    content: str,
    metadata: dict[str, Any],
    orchestrator_agent_id: str | None,
    sender_agent_id: str | None,
    current_speaker_index: int,
) -> tuple[int, str] | None:
    """Server-side safety net for ``orchestrator`` strategy rooms when
    the moderator LLM emits a message *without* a valid handoff
    (no ``[HANDOFF]`` prefix that ``_apply_orchestrator_handoff``
    accepts, and no addressable mention parsed into metadata).

    Returns ``(new_index, next_speaker_participant_id)`` when a
    fallback nomination was applied, or ``None`` when no action was
    taken.

    Background: docs/research/2026-05-12-multi-agent-turn-taking-
    mediator-failure.md documents an LLM failure pattern where the
    orchestrator nails the first handoff but omits the mention token
    from the second onward (instruction-following decay + format-task
    interference). Without this fallback, the room silently stalls —
    every participant sees the message as ``ingest_only`` and no one
    is triggered to reply. The fallback rotates to the next non-
    orchestrator participant so the conversation keeps moving.

    Acceptance rules (every one must hold):

    1. The room has a valid ``orchestrator_agent_id``.
    2. The sender is the orchestrator agent. Worker messages don't
       trigger the safety net — they're routed via mention parsing.
    3. ``content`` does not start with ``[종료]``. The orchestrator's
       explicit termination marker is respected; no nominate is made
       so the room comes to rest. Other prefixes like ``[HANDOFF]``,
       ``[DELEGATED]``, ``[ROOM_QUERY]`` are handled upstream — if
       they succeed they stamp ``next_speaker_participant_id`` which
       rule 4 below short-circuits on, and if they fail the room
       genuinely needs the fallback.
    4. ``metadata.next_speaker_participant_id`` is not already set.
       A successful ``_apply_orchestrator_handoff`` upstream stamps
       this; we never override an explicit nomination.
    5. ``metadata.mentions`` contains no ``type=user`` or
       ``type=legacy`` entry. An addressable mention means the
       moderator did address someone and the agent-side rule 3 will
       route normally — no fallback needed.

    On success the helper:

    - Updates ``Room.current_speaker_index`` and
      ``next_speaker_participant_id`` in the caller's DB transaction.
    - Mutates ``metadata`` in place to add
      ``next_speaker_participant_id``. The broadcast carries this
      stamp so agent-side ``decide_policy`` rule 4a (O2) wakes the
      nominated participant.

    Round-robin pool excludes the orchestrator itself — the moderator
    role is "distribute speaking turns", and nominating yourself
    would loop on the same failure. If the pool is empty (only the
    orchestrator is present, or no agent participants beyond the
    orchestrator), the helper returns ``None`` and the message just
    flows as ingest_only.
    """
    if not orchestrator_agent_id:
        return None
    if sender_agent_id != orchestrator_agent_id:
        return None
    # Explicit termination — respect the orchestrator's wrap-up.
    if content.startswith("[종료]"):
        return None
    # Upstream handoff already nominated — never override.
    if metadata.get("next_speaker_participant_id"):
        return None
    # Addressable mention exists — mention routing will handle it.
    mentions = metadata.get("mentions") or []
    for m in mentions:
        if isinstance(m, dict) and m.get("type") in ("user", "legacy"):
            return None

    # Round-robin among non-orchestrator agent participants. Stable
    # order mirrors ``_compute_round_robin_next`` (joined_at, id) so
    # the rotation matches user expectations from the standard
    # round_robin strategy.
    rows = (
        await db.execute(
            select(Participant.id, Participant.agent_id)
            .where(Participant.room_id == room_id)
            .where(Participant.agent_id.isnot(None))
            .where(Participant.agent_id != orchestrator_agent_id)
            .order_by(Participant.joined_at.asc(), Participant.id.asc())
        )
    ).all()
    if not rows:
        return None

    new_index = (current_speaker_index + 1) % len(rows)
    next_pid: str = rows[new_index][0]

    await db.execute(
        sa_update(Room)
        .where(Room.id == room_id)
        .values(
            current_speaker_index=new_index,
            next_speaker_participant_id=next_pid,
        )
    )
    metadata["next_speaker_participant_id"] = next_pid
    return new_index, next_pid


async def _compute_round_robin_next(
    db: AsyncSession,
    *,
    room_id: str,
    current_index: int,
    sender_is_human: bool,
) -> tuple[int, str] | None:
    """Return ``(new_index, next_speaker_participant_id)`` for the
    round_robin strategy, or ``None`` if the room has no agents yet.

    Rules (Issue #159 Phase B):

    - Agent participants are ordered by ``joined_at`` then ``id`` for
      a stable rotation across connects.
    - Human messages reset rotation to index 0 — right after a user
      speaks we want *some* agent to respond immediately, not whoever
      the cursor happened to point at.
    - Agent messages advance one step (modulo the agent count).
    """
    agent_participant_ids: list[str] = (
        (
            await db.execute(
                select(Participant.id)
                .where(Participant.room_id == room_id)
                .where(Participant.agent_id.isnot(None))
                .order_by(Participant.joined_at.asc(), Participant.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if not agent_participant_ids:
        return None
    if sender_is_human:
        new_index = 0
    else:
        new_index = (current_index + 1) % len(agent_participant_ids)
    return new_index, agent_participant_ids[new_index]


async def _build_participants_brief(
    db: AsyncSession, *, room_id: str
) -> list[ParticipantBrief]:
    """Collect a room's roster for the welcome frame (#221).

    Orchestrator agents inject this list into their LLM system prompt
    so the model can call ``handoff_to`` with a valid ``participant_id``
    (UUID) instead of guessing a display name. Ordered by ``joined_at``
    then ``id`` to match ``_compute_round_robin_next``'s stable order.

    ``selectinload`` keeps this to a small fixed number of queries
    regardless of roster size. Orphaned participants (both FK relations
    empty — the transient state between a user deletion and the
    cascaded participant row cleanup) fall back to a generic label
    rather than raising, because welcome must succeed even for a
    temporarily inconsistent row.
    """
    stmt = (
        select(Participant)
        .where(Participant.room_id == room_id)
        .options(
            selectinload(Participant.user),
            selectinload(Participant.agent),
        )
        .order_by(Participant.joined_at.asc(), Participant.id.asc())
    )
    rows = (await db.execute(stmt)).scalars().all()
    briefs: list[ParticipantBrief] = []
    for p in rows:
        if p.user is not None:
            user = p.user
            if user.display_name:
                name = user.display_name
            elif user.email:
                name = user.email.split("@")[0]
            else:
                name = "Guest"
            kind = "guest" if user.is_anonymous else "user"
            briefs.append(ParticipantBrief(id=p.id, display_name=name, kind=kind))
        elif p.agent is not None:
            briefs.append(
                ParticipantBrief(
                    id=p.id,
                    display_name=p.agent.name or "",
                    kind="agent",
                    agent_id=p.agent_id,
                    description=p.agent.description,
                )
            )
        else:
            briefs.append(
                ParticipantBrief(id=p.id, display_name="Unknown", kind="user")
            )
    return briefs


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
    config: AnygardenSettings = app.state.config
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
    # Issue #279 — peer-mention budget; tests that bypass the
    # lifespan can leave this None and the safety net falls back to
    # depth-only enforcement.
    peer_handoff_budget = getattr(app.state, "peer_handoff_budget", None)

    # -- Authentication via Sec-WebSocket-Protocol --
    raw_protocols = websocket.headers.get("sec-websocket-protocol", "")
    selected_subprotocol: str | None = None
    if raw_protocols:
        selected_subprotocol = "anygarden.v1"

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
    agent_opt_out = False
    # Issue #237 — per-agent memory_md snapshot stamped into the welcome
    # frame so the SDK can inject it into the engine's system prompt.
    # None for user/guest connections.
    agent_memory_md: str | None = None
    # Issue #279 — the welcomed agent's own collaboration policy.
    # Default ``solo`` is the safe pre-#279 value; it stays ``solo``
    # for user/guest welcomes since they don't run an LLM that would
    # consume a peer-mention hint.
    agent_collaboration_mode: str = "solo"
    # Issue #159 Phase A — speaker strategy fields cached from the
    # Room row so the SDK can dispatch in ``decide_policy``. Defaults
    # here reproduce the pre-#159 behaviour for welcome flows that
    # skip the room lookup (guests, tests).
    speaker_strategy = "mentioned_only"
    orchestrator_agent_id: str | None = None
    next_speaker_participant_id: str | None = None
    if identity and identity.kind == "agent":
        async with session_factory() as db:
            result = await db.execute(
                select(Participant.id, Participant.room_id).where(
                    Participant.agent_id == identity.id,
                )
            )
            pid_to_room = {row[0]: row[1] for row in result.all()}
            # Issue #148 Part 3 — read the agent's opt-out flag at
            # welcome time so the SDK can cache it for ``decide_policy``.
            # Bundled in the same session so we don't pay a second
            # round-trip. ``scalar_one_or_none`` guards the (unlikely)
            # case where the agent row was deleted between auth and
            # welcome.
            #
            # Issue #279 — pull ``collaboration_mode`` in the same
            # round-trip so the SDK can decide whether to append the
            # peer-mention hint when composing the LLM system prompt.
            opt_out_row = (
                await db.execute(
                    select(
                        Agent.context_window_opt_out,
                        Agent.memory_md,
                        Agent.collaboration_mode,
                    ).where(Agent.id == identity.id)
                )
            ).first()
            if opt_out_row is not None:
                agent_opt_out = bool(opt_out_row[0])
                agent_memory_md = opt_out_row[1]
                agent_collaboration_mode = opt_out_row[2] or "solo"
        connected_pids = await manager.connected_participant_ids()
        connected_room_ids = {
            pid_to_room[pid] for pid in pid_to_room if pid in connected_pids
        }
        connected_room_ids.add(room_id)  # about to subscribe
        pending_rooms = sorted(set(pid_to_room.values()) - connected_room_ids)

    # Issue #159 Phase A — propagate the room's speaker-strategy
    # fields in every welcome frame so both agents and UIs know how
    # the room dispatches turns. Separate session so any failure
    # stays out of the opt-out read path above.
    # Issue #221 — collect the participant roster here too so
    # orchestrator agents can inject it into their LLM system prompt
    # (see ``claude_code.py``). Same session as the Room row to keep
    # welcome to a single round-trip pair.
    participants_brief: list[ParticipantBrief] = []
    # Issue #237 — ephemeral flag from the Room row.
    room_ephemeral = False
    async with session_factory() as db:
        row = (
            await db.execute(
                select(
                    Room.speaker_strategy,
                    Room.orchestrator_agent_id,
                    Room.next_speaker_participant_id,
                    Room.ephemeral,
                ).where(Room.id == room_id)
            )
        ).first()
        if row is not None:
            (
                speaker_strategy,
                orchestrator_agent_id,
                next_speaker_participant_id,
                room_ephemeral,
            ) = row
        participants_brief = await _build_participants_brief(db, room_id=room_id)

    welcome = WelcomeOut(
        participant_id=participant.id,
        pending_rooms=pending_rooms,
        # Issue #61 — tell the agent SDK which agent identity this
        # connection is bound to so it can gate ``room_query``
        # forwarding to the representative agent only.
        agent_id=identity.id if identity and identity.kind == "agent" else None,
        context_window_opt_out=agent_opt_out,
        speaker_strategy=speaker_strategy,
        orchestrator_agent_id=orchestrator_agent_id,
        next_speaker_participant_id=next_speaker_participant_id,
        participants=participants_brief,
        ephemeral=bool(room_ephemeral),
        memory_md=agent_memory_md,
        my_collaboration_mode=agent_collaboration_mode,
    )
    # Issue #176 — the welcome send sits OUTSIDE the main receive-loop
    # try/except (which starts at the ``try:`` on the Subscribe block
    # further down). In dev, Vite HMR + React StrictMode routinely
    # close the client socket in the microseconds between accept and
    # the first server write; uvicorn then raises
    # ``ClientDisconnected`` which surfaces here as
    # ``WebSocketDisconnect``. Nothing is subscribed yet and no gauge
    # has been bumped, so we log the race at info-level and bail — no
    # cleanup needed, no traceback noise in the dev log.
    try:
        await websocket.send_text(welcome.model_dump_json())
    except WebSocketDisconnect:
        logger.info(
            "ws.disconnected_before_welcome",
            room_id=room_id,
            participant_id=participant.id,
        )
        return

    # Decide guest-ness BEFORE subscribe so the ``finally`` block can
    # safely test ``is_guest_session`` even if an exception fires
    # during (or immediately after) ``subscribe``. We also flip a
    # separate ``guest_gauge_incremented`` the moment we actually
    # bump the gauge — the finally block decrements only when that
    # happened, so an exception between subscribe and inc() can't
    # leave the gauge underwater.
    is_guest_session = identity is not None and identity.kind == "guest"
    guest_gauge_incremented = False

    # Subscribe — pass ``user_id`` for logged-in user sessions so the
    # per-user task-update fanout (#266) can find them. Agent and
    # guest connections leave the user_id index empty, which is the
    # correct behaviour: those targets never receive a task.updated
    # frame.
    user_id = (
        identity.id if identity is not None and identity.kind == "user" else None
    )
    await manager.subscribe(
        room_id, participant.id, websocket, user_id=user_id
    )
    logger.info("ws.connected", room_id=room_id, participant_id=participant.id)

    # Track guest connections separately from the overall WS gauge so
    # operators can spot a guest-session spike (e.g. leaked invite
    # link) without cross-contaminating ws_connections_active.
    if is_guest_session:
        guest_active.inc()
        guest_gauge_incremented = True

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
                    if is_guest:
                        guest_rate_limited_total.labels(scope="cooldown").inc()
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
                            guest_rate_limited_total.labels(
                                scope="room_aggregate"
                            ).inc()
                            await websocket.send_text(
                                ErrorOut(
                                    detail="Rate limited (room aggregate)"
                                ).model_dump_json()
                            )
                            continue

                metadata = dict(frame_in.metadata) if frame_in.metadata else {}
                async with session_factory() as ref_db:
                    try:
                        metadata = await canonicalize_shared_file_references(
                            ref_db,
                            room_id=room_id,
                            metadata=metadata,
                            allow_shared_files=not is_guest,
                        )
                    except InvalidSharedFileReference:
                        await websocket.send_text(
                            ErrorOut(
                                detail="Invalid shared file reference"
                            ).model_dump_json()
                        )
                        continue
                if mentions:
                    metadata["mentions"] = mentions

                # Issue #279 — peer-mention safety net.
                #
                # Two caps stack on top of one another inside a single
                # user turn (defined as: from a human/guest send up to
                # the next one):
                #
                # - ``MAX_PEER_DEPTH`` — how many sequential layers of
                #   agent-to-agent fanout are allowed (1 = original
                #   agent may peer-ask once; the peer's reply must NOT
                #   contain another peer mention).
                # - ``MAX_TOTAL_PEER_HANDOFFS_PER_USER_TURN`` — total
                #   peer-mention events allowed in the room across
                #   the whole turn, regardless of layer.
                #
                # Both express depth as ``budget.consume()``-derived
                # used count: 1st peer-ask of the turn yields used=1,
                # which is layer 1. Triggering both caps simultaneously
                # is the same write so we keep one budget object.
                #
                # Human/guest sends open a fresh turn → budget reset
                # below. Agent sends with peer mentions trigger the
                # consume-and-check. Pre-spawn-of-budget tests skip the
                # safety net entirely so legacy fixtures don't break.
                is_agent_for_peer = (
                    identity is not None and identity.kind == "agent"
                )
                if is_agent_for_peer and mentions and peer_handoff_budget is not None:
                    async with session_factory() as peer_db:
                        peer_rows = (
                            await peer_db.execute(
                                select(
                                    Participant.id, Participant.agent_id
                                ).where(
                                    Participant.room_id == room_id,
                                    Participant.agent_id.isnot(None),
                                )
                            )
                        ).all()
                    agent_participants = {pid: aid for pid, aid in peer_rows}
                    sender_agent_id = (
                        identity.id if identity is not None else None
                    )
                    peer_mentions = [
                        m
                        for m in mentions
                        if is_peer_mention(
                            m,
                            sender_agent_id=sender_agent_id,
                            agent_participants=agent_participants,
                        )
                    ]
                    if peer_mentions:
                        ok = peer_handoff_budget.consume(room_id)
                        used = (
                            MAX_TOTAL_PEER_HANDOFFS_PER_USER_TURN
                            - peer_handoff_budget.remaining(room_id)
                        )
                        block = (not ok) or (used > MAX_PEER_DEPTH)
                        if block:
                            peer_pids = {
                                str(m["id"])
                                for m in peer_mentions
                                if m.get("type") == "user"
                            }
                            frame_in.content = strip_peer_mentions_from_content(
                                frame_in.content, peer_pids=peer_pids
                            )
                            mentions = [m for m in mentions if m not in peer_mentions]
                            if mentions:
                                metadata["mentions"] = mentions
                            else:
                                metadata.pop("mentions", None)
                            # Stamp the (would-be) depth so observability
                            # can tell the blocked event apart from a
                            # successful pass-through.
                            metadata["peer_depth"] = (
                                MAX_TOTAL_PEER_HANDOFFS_PER_USER_TURN + 1
                                if not ok
                                else used
                            )
                            metadata["peer_blocked"] = True
                            logger.warning(
                                "ws.peer_mention_blocked",
                                room_id=room_id,
                                sender_agent_id=sender_agent_id,
                                used=used,
                                budget_ok=ok,
                            )
                        else:
                            metadata["peer_depth"] = used
                            # First peer-ask in the turn → ``peer_query``;
                            # everything after → ``peer_response`` (the
                            # initiating agent reading a reply that itself
                            # carried mentions, which is the depth-2
                            # territory we strip; in practice this branch
                            # only fires when ``MAX_PEER_DEPTH`` is bumped
                            # above 1 by an admin override).
                            metadata["kind"] = (
                                "peer_query" if used == 1 else "peer_response"
                            )
                elif (
                    not is_agent_for_peer
                    and not is_guest
                    and peer_handoff_budget is not None
                ):
                    # Human send opens a fresh user turn — restore the
                    # peer-mention budget so the first agent in the new
                    # turn starts with a clean slate.
                    peer_handoff_budget.reset(room_id)

                # Room mention → representative agent routing.
                # Guests can't reach this block — their mentions had
                # ``type == "room"`` filtered out above — but the
                # explicit ``not is_guest`` check is defence in depth
                # in case the guest filter ever loosens: membership
                # changes are admin-only and the auto-join below
                # creates a Participant row.
                #
                # ``not is_agent`` breaks the ``[ROOM_QUERY]``
                # forwarding loop: ``room_query`` adapters forward
                # queries on behalf of users by issuing a fresh
                # ``send`` from the representative's agent identity.
                # If the server re-attached ``room_query`` metadata
                # to that forward, every recipient agent in the
                # target room would forward again, ad infinitum.
                # Agents never originate ``#room`` queries — humans
                # do — so silencing the routing path for agent
                # senders is both sufficient and safe.
                #
                # We deliberately do NOT use a ``content.startswith
                # ("[ROOM_QUERY]")`` guard here. That looked tempting
                # as belt-and-suspenders but creates a UX trap: a
                # human user typing ``[ROOM_QUERY]`` literally in
                # their message would silently lose room routing
                # with no error feedback. The agent-identity check
                # already closes the loop at the source.
                #
                # The agent SDK additionally strips the
                # ``<#room:...>`` token before forwarding
                # (see ``room_query._strip_room_mention``); this
                # server guard is the safety net for that strip
                # ever regressing.
                is_agent = identity is not None and identity.kind == "agent"
                room_mentions = (
                    [m for m in mentions if m.get("type") == "room"]
                    if not is_guest and not is_agent
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
                                # Auto-join representative to this room.
                                # ``ensure_agent_in_room`` is idempotent at
                                # the DB layer but always pushes a
                                # ``JoinRoomOut`` through the agent's other
                                # WS sessions — the frame is what triggers
                                # the SDK to actually subscribe to this
                                # room in time for the upcoming broadcast
                                # (issue #50).
                                await ensure_agent_in_room(
                                    rq_db,
                                    manager,
                                    room_id=room_id,
                                    agent_id=rep_agent_id,
                                )
                                # ``query_id`` pairs the question
                                # message with the eventual ``room_
                                # query_result`` broadcast so the
                                # source-room banner (issue #55) can
                                # transition pending → completed/
                                # timeout/solo without needing a new
                                # WS event type. ``role`` lets the
                                # client cheaply distinguish the
                                # originating question from forwarded
                                # / result messages — all three
                                # share the ``room_query*`` metadata
                                # family. ``source_participant_id``
                                # is what the target-room forward
                                # badge renders as ``↪ #room ·
                                # @user`` (we don't have the source
                                # participant on the agent side
                                # otherwise — the agent SDK only
                                # sees the routing token, not the
                                # human author).
                                # Issue #155 — resolve the source user's
                                # human-readable name so the target-room
                                # forward badge can render ``↪ #room ·
                                # @Alice``. The target room's participant
                                # map never contains the source-room user,
                                # so ``MessageBubble.resolveUser`` always
                                # misses and falls back to the last-6-hex
                                # of the UUID without this value. Mirror
                                # of #153's responder-name snapshot, but
                                # for the forward direction.
                                #
                                # Rule must stay in sync with
                                # ``rooms/router.py:290-302`` — if that
                                # fallback chain changes, update here too.
                                source_participant_name = ""
                                if participant.user_id:
                                    source_user = (
                                        await rq_db.execute(
                                            select(User).where(
                                                User.id == participant.user_id
                                            )
                                        )
                                    ).scalar_one_or_none()
                                    if source_user:
                                        if source_user.display_name:
                                            source_participant_name = (
                                                source_user.display_name
                                            )
                                        elif source_user.email:
                                            source_participant_name = (
                                                source_user.email.split("@")[0]
                                            )
                                        else:
                                            source_participant_name = "Guest"
                                metadata["room_query"] = {
                                    "target_room_id": target_room_id,
                                    "source_room_id": room_id,
                                    "role": "question",
                                    "query_id": str(uuid4()),
                                    "source_participant_id": participant.id,
                                    "source_participant_name": source_participant_name,
                                    # Issue #61 — identify which agent
                                    # is responsible for forwarding the
                                    # [ROOM_QUERY]. When multiple agents
                                    # are in the source room they all
                                    # receive this broadcast; only the
                                    # matching ``representative_agent_id``
                                    # should call forward, otherwise the
                                    # target room receives N duplicates.
                                    "representative_agent_id": rep_agent_id,
                                }

                # #148 Part 3 — stamp ``ingest_only`` on ambient broadcasts
                # so peer agents absorb the text as context instead of
                # treating it as an actionable message. Opt-in per room
                # via ``rooms.context_window_enabled`` (Part 1). Scoped
                # BEFORE the commit-and-broadcast so the stamp persists on
                # the stored row and replays correctly on reconnect.
                async with session_factory() as db:
                    room_row = (
                        await db.execute(
                            select(
                                Room.context_window_enabled,
                                Room.speaker_strategy,
                                Room.current_speaker_index,
                                Room.orchestrator_agent_id,
                            ).where(Room.id == room_id)
                        )
                    ).first()
                    context_window_enabled = bool(room_row[0]) if room_row else False
                    speaker_strategy = (
                        room_row[1] if room_row else "mentioned_only"
                    )
                    current_speaker_index = int(room_row[2]) if room_row else 0
                    orchestrator_agent_id = (
                        room_row[3] if room_row else None
                    )

                    # #233 — only stamp agent-to-agent chatter. Human
                    # sends always reach peers as actionable even when
                    # ``context_window_enabled`` is on; without this
                    # guard orchestrator rooms short-circuit to
                    # ``INGEST_ONLY`` before rule 5a in
                    # ``decide_policy`` can let the orchestrator reply.
                    sender_is_agent = (
                        identity is not None and identity.kind == "agent"
                    )
                    if (
                        context_window_enabled
                        and _is_ambient_candidate(
                            frame_in.content,
                            metadata,
                            sender_is_agent=sender_is_agent,
                        )
                        and "ingest_only" not in metadata
                    ):
                        metadata["ingest_only"] = True

                    # Issue #159 Phase B — round_robin dispatcher.
                    # Server picks the next speaker; agents just check
                    # whether they match. Human senders reset rotation
                    # to the first agent so the next turn responds
                    # immediately, rather than wherever the cursor
                    # happened to stop before. See
                    # ``_compute_round_robin_next`` for the details.
                    if speaker_strategy == "round_robin":
                        sender_is_human = (
                            identity is not None and identity.kind == "user"
                        )
                        next_info = await _compute_round_robin_next(
                            db,
                            room_id=room_id,
                            current_index=current_speaker_index,
                            sender_is_human=sender_is_human,
                        )
                        if next_info is not None:
                            new_index, next_pid = next_info
                            metadata["next_speaker_participant_id"] = next_pid
                            await db.execute(
                                sa_update(Room)
                                .where(Room.id == room_id)
                                .values(
                                    current_speaker_index=new_index,
                                    next_speaker_participant_id=next_pid,
                                )
                            )

                    # Issue #159 Phase C — orchestrator handoff.
                    # When the orchestrator emits a ``[HANDOFF]``
                    # message, flip ``Room.next_speaker_participant_id``
                    # and stamp the outgoing metadata so the target
                    # agent wakes up under ``decide_policy`` rule O2.
                    # Non-orchestrator senders and messages that don't
                    # parse cleanly are silently ignored — see
                    # ``_apply_orchestrator_handoff`` for the trust
                    # rules. Runs regardless of the strategy so a room
                    # that was just flipped back to ``mentioned_only``
                    # still processes in-flight handoffs consistently.
                    sender_agent_id = (
                        identity.id
                        if identity is not None and identity.kind == "agent"
                        else None
                    )
                    await _apply_orchestrator_handoff(
                        db,
                        room_id=room_id,
                        content=frame_in.content,
                        metadata=metadata,
                        orchestrator_agent_id=orchestrator_agent_id,
                        sender_agent_id=sender_agent_id,
                    )

                    # Orchestrator fallback nominate — when the
                    # moderator emits a non-terminal message without
                    # a valid handoff or addressable mention, the
                    # server rotates to the next non-orchestrator
                    # participant via round-robin so the room never
                    # silently stalls on LLM instruction-following
                    # decay. See
                    # ``_apply_orchestrator_fallback_nominate`` and
                    # docs/research/2026-05-12-multi-agent-turn-
                    # taking-mediator-failure.md for the failure
                    # mode this defends against (V1-V5 PoC observed
                    # the orchestrator omit the mention token from
                    # the second handoff onward in 5/5 trials, even
                    # with persona reinforcement).
                    if speaker_strategy == "orchestrator":
                        fallback_info = (
                            await _apply_orchestrator_fallback_nominate(
                                db,
                                room_id=room_id,
                                content=frame_in.content,
                                metadata=metadata,
                                orchestrator_agent_id=orchestrator_agent_id,
                                sender_agent_id=sender_agent_id,
                                current_speaker_index=current_speaker_index,
                            )
                        )
                        if fallback_info is not None:
                            new_index, next_pid = fallback_info
                            logger.warning(
                                "orchestrator_fallback_nominate",
                                room_id=room_id,
                                next_participant_id=next_pid,
                                new_index=new_index,
                            )

                    # #313 — auto-route response detection. If this
                    # message carries the rep agent's reply to an
                    # earlier ``POST /auto-route-unassigned`` request
                    # we resolve the matching Future + tag the
                    # message so the frontend can hide it from the
                    # chat thread (it's an internal protocol echo,
                    # not user-facing). Non-routing messages walk
                    # through unchanged.
                    if identity is not None and identity.kind == "agent":
                        from anygarden.routing.protocol import (
                            try_parse_routing_response,
                        )

                        parsed = try_parse_routing_response(frame_in.content)
                        if parsed is not None:
                            request_id, result = parsed
                            futures = getattr(
                                websocket.app.state, "routing_futures", None
                            )
                            if futures is not None:
                                fut = futures.pop(request_id, None)
                                if fut is not None and not fut.done():
                                    fut.set_result(result)
                            if metadata is None:
                                metadata = {}
                            metadata["system_origin"] = "auto_route_response"
                            metadata["routing_request_id"] = request_id

                    msg = await append_message(
                        db,
                        room_id=room_id,
                        participant_id=participant.id,
                        content=frame_in.content,
                        metadata=metadata or None,
                    )

                    # Log message events for agents (same transaction).
                    # On user sends we mint a fresh ``request_id`` per
                    # target agent (keyed by that agent's participant_id
                    # so the tailored broadcast below can look it up).
                    # On agent sends we echo the request_id back onto
                    # ``response_sent`` so the full lifecycle chain
                    # resolves under one identifier.
                    request_id_by_participant: dict[str, str] = {}
                    if identity and identity.kind == "agent":
                        echoed_rid = None
                        if isinstance(metadata, dict):
                            raw = metadata.get("request_id")
                            if isinstance(raw, str):
                                echoed_rid = raw
                        db.add(ActivityLog(
                            agent_id=identity.id,
                            event_type="response_sent",
                            request_id=echoed_rid,
                            details={"room_id": room_id},
                        ))
                    elif identity and identity.kind == "user":
                        agent_parts = (await db.execute(
                            select(Participant.id, Participant.agent_id).where(
                                Participant.room_id == room_id,
                                Participant.agent_id.isnot(None),
                            )
                        )).all()
                        for pid, aid in agent_parts:
                            rid = str(uuid4())
                            request_id_by_participant[pid] = rid
                            db.add(ActivityLog(
                                agent_id=aid,
                                event_type="message_received",
                                request_id=rid,
                                details={
                                    "room_id": room_id,
                                    "from_participant_id": participant.id,
                                    # #222 — tie the turn back to the
                                    # Message row that woke the agent
                                    # up so ActivityPanel can render
                                    # "responding to <msg>" without a
                                    # second timestamp-based lookup.
                                    "trigger_message_id": msg.id,
                                },
                            ))

                    await db.commit()
                    base_metadata = msg.extra_metadata

                def _make_out(pid: str) -> MessageOut:
                    """Per-recipient MessageOut.

                    Agents receive ``metadata.request_id`` so they can
                    thread their LifecycleFrame emissions back to this
                    particular invocation. Non-agent subscribers see
                    the stored metadata unchanged — ``request_id``
                    never persists on the message row itself.
                    """
                    rid = request_id_by_participant.get(pid)
                    meta = dict(base_metadata) if base_metadata else {}
                    if rid is not None:
                        meta["request_id"] = rid
                    return MessageOut(
                        id=msg.id,
                        room_id=msg.room_id,
                        participant_id=msg.participant_id,
                        content=msg.content,
                        seq=msg.seq,
                        created_at=msg.created_at,
                        metadata=meta or None,
                    )

                await manager.broadcast_tailored(room_id, _make_out)

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

            elif isinstance(frame_in, LifecycleFrame):
                # Agents only. Other identity kinds can't produce valid
                # lifecycle events — drop silently rather than crash
                # the session.
                if identity and identity.kind == "agent":
                    async with session_factory() as db:
                        await _persist_lifecycle_event(
                            db, agent_id=identity.id, frame=frame_in
                        )
                        await db.commit()
                else:
                    logger.warning(
                        "ws.lifecycle.dropped",
                        reason="non-agent identity",
                        room_id=room_id,
                        identity_kind=(identity.kind if identity else None),
                    )

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
        if guest_gauge_incremented:
            guest_active.dec()
