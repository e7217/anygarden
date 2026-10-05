"""In-process WebSocket connection manager."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
from uuid import uuid4

from fastapi import WebSocket

from anygarden.ws.protocol import OutgoingFrame

if TYPE_CHECKING:
    from anygarden.presence.service import PresenceService


@dataclass(slots=True)
class _Subscription:
    room_id: str
    participant_id: str
    ws: WebSocket
    # Issue #266 — populated when the subscriber is a logged-in user
    # (vs. agent / anonymous guest). Drives ``push_to_users`` for the
    # agent-profile 2차 view so admins receive ``task.updated`` frames
    # on whichever room they happen to be looking at.
    user_id: Optional[str] = None
    generation: Optional[int] = None
    execution_control: bool = False
    turn_control: bool = False
    socket_epoch: str = field(default_factory=lambda: str(uuid4()))
    # #782 — the invite a guest session was admitted with, so revoking
    # the invite can close the sockets it already let in.
    invite_id: Optional[str] = None


class ConnectionManager:
    """Manages active WebSocket connections grouped by room."""

    def __init__(self) -> None:
        # room_id -> list of subscriptions
        self._rooms: dict[str, list[_Subscription]] = {}
        # participant_id -> live subscriptions, oldest first (for direct
        # sends). Agents hold at most one (#79); a human participant holds
        # one per open tab/device (#731).
        self._by_participant: dict[str, list[_Subscription]] = {}
        # Issue #266 — user_id -> set of participant_ids. Reverse index
        # for the per-user fanout used by ``push_to_users``. Populated
        # only when the caller hands ``subscribe`` a ``user_id``; agent
        # and anonymous-guest subscriptions skip the index since they
        # never receive a user-targeted frame.
        self._by_user: dict[str, set[str]] = {}
        # participant_id -> last disconnect timestamp. Populated on
        # ``unsubscribe`` so ``PresenceService`` can expose a
        # best-effort "last seen" for the UI even after the socket is
        # gone. Memory-only: a process restart resets it, and
        # ``PresenceService`` falls back to ``Agent.last_heartbeat_at``.
        self._last_seen: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        # Optional PresenceService, wired in by the app factory.
        # See PresenceService docstring for the rationale of the
        # setter pattern (avoids circular imports).
        self._presence: Optional["PresenceService"] = None

    @property
    def active_connections(self) -> int:
        return sum(len(subs) for subs in self._by_participant.values())

    def _attach_locked(self, sub: _Subscription) -> None:
        self._rooms.setdefault(sub.room_id, []).append(sub)
        self._by_participant.setdefault(sub.participant_id, []).append(sub)
        if sub.user_id is not None:
            self._by_user.setdefault(sub.user_id, set()).add(sub.participant_id)

    def _detach_locked(self, sub: _Subscription) -> None:
        """Drop exactly *sub* from every index. Caller holds ``_lock``."""
        room_subs = [s for s in self._rooms.get(sub.room_id, []) if s is not sub]
        if room_subs:
            self._rooms[sub.room_id] = room_subs
        else:
            self._rooms.pop(sub.room_id, None)
        remaining = [
            s for s in self._by_participant.get(sub.participant_id, []) if s is not sub
        ]
        if remaining:
            self._by_participant[sub.participant_id] = remaining
        else:
            self._by_participant.pop(sub.participant_id, None)
        if sub.user_id is not None and not any(
            s.user_id == sub.user_id for s in remaining
        ):
            bucket = self._by_user.get(sub.user_id)
            if bucket is not None:
                bucket.discard(sub.participant_id)
                if not bucket:
                    del self._by_user[sub.user_id]

    def set_presence_service(self, presence: "PresenceService") -> None:
        """Inject the PresenceService used for publish-on-subscribe.

        Called once from the app factory. Optional: tests that don't
        care about presence broadcasts can leave it unset and the
        subscribe/unsubscribe hooks simply no-op.
        """
        self._presence = presence

    def last_seen_at(self, participant_id: str) -> datetime | None:
        """Return the memo'd last-seen timestamp, or ``None``.

        Intentionally not async: ``_last_seen`` is a plain dict and
        ``PresenceService`` batches this call inside its own lookup
        loops.
        """
        return self._last_seen.get(participant_id)

    async def subscribe(
        self,
        room_id: str,
        participant_id: str,
        ws: WebSocket,
        *,
        user_id: str | None = None,
        generation: int | None = None,
        execution_control: bool = False,
        turn_control: bool = False,
        exclusive: bool = True,
        invite_id: str | None = None,
    ) -> None:
        """Register *ws* as listening on *room_id*.

        Issue #79 — single-session policy. When *exclusive* (the default,
        and what agent connections use), every active subscription of
        *participant_id* is evicted and closed with code 4040
        ("superseded"). Without this guard two
        clients sharing an agent token (e.g. the machine daemon's reconcile
        racing a manual launch) would both stay in ``_rooms[room_id]``
        and every broadcast would fan out to both — doubling LLM calls,
        ``[ROOM_QUERY]`` forwards, and direct replies.

        Issue #731 — human sessions pass ``exclusive=False``: a user who
        opens the same room in several tabs shares one room-scoped
        participant, and evicting would make the tabs knock each other
        off in an endless reconnect loop. Those sockets coexist; the
        participant is online while at least one of them is open.

        ``user_id`` (#266) — when supplied, the subscription is also
        added to the per-user reverse index that backs
        ``push_to_users``. Pass it for logged-in user sessions so
        admin/owner targets reach them; leave it ``None`` for agent
        and anonymous-guest connections.
        """
        sub = _Subscription(
            room_id=room_id,
            participant_id=participant_id,
            ws=ws,
            user_id=user_id,
            generation=generation,
            execution_control=execution_control,
            turn_control=turn_control,
            invite_id=invite_id,
        )
        async with self._lock:
            existing = list(self._by_participant.get(participant_id, []))
            superseded = existing if exclusive else []
            for old in superseded:
                self._detach_locked(old)
            self._attach_locked(sub)
        came_online = exclusive or not existing

        # Close the superseded socket OUTSIDE the lock — ws.close awaits
        # the underlying ASGI send and we must not block other ops.
        # Best-effort: a socket that's already half-dead can throw on
        # close; we just need it to stop receiving frames.
        transport = getattr(self, "execution_transport", None)
        for old in superseded:
            if transport is not None:
                transport.disconnected(participant_id, old.socket_epoch)
            try:
                await old.ws.close(code=4040, reason="superseded")
            except Exception:
                pass

        # Publish AFTER releasing the lock so the broadcast path's own
        # lock acquisition doesn't deadlock with ours. A second tab of an
        # already-online participant changes nothing observable.
        if self._presence is not None and came_online:
            now = datetime.now(timezone.utc)
            await self._presence.publish(
                room_id,
                participant_id,
                online=True,
                last_seen_at=now,
            )

    async def unsubscribe(self, participant_id: str, *, websocket: WebSocket | None = None) -> None:
        """Remove *websocket*'s subscription, or every subscription of
        *participant_id* when no socket is given.

        The participant goes offline (last-seen memo + presence) only
        once its last socket is gone.
        """
        async with self._lock:
            subs = self._by_participant.get(participant_id, [])
            if websocket is not None:
                subs = [s for s in subs if s.ws is websocket]
            if not subs:
                return
            transport = getattr(self, "execution_transport", None)
            for sub in subs:
                if transport is not None:
                    transport.disconnected(participant_id, sub.socket_epoch)
                self._detach_locked(sub)
            if participant_id in self._by_participant:
                return
            now = datetime.now(timezone.utc)
            self._last_seen[participant_id] = now
            room_id = subs[-1].room_id

        if self._presence is not None:
            await self._presence.publish(
                room_id,
                participant_id,
                online=False,
                last_seen_at=now,
            )

    async def revoke_room(
        self,
        room_id: str,
        *,
        code: int = 4003,
        reason: str = "Room access revoked",
    ) -> int:
        """Close every current subscription in *room_id*.

        Used when a room is archived. New read-only connections may still be
        established afterwards, but sockets that were accepted while the room
        was active must not retain a stale assumption that writes are allowed.
        The per-frame authorization gate remains the backstop for races and
        multi-worker deployments.
        """

        async with self._lock:
            subs = list(self._rooms.get(room_id, []))

        for sub in subs:
            try:
                await sub.ws.close(code=code, reason=reason)
            except Exception:  # noqa: BLE001 — best-effort socket revocation
                pass
        for sub in subs:
            await self.unsubscribe(sub.participant_id, websocket=sub.ws)
        return len({sub.participant_id for sub in subs})

    async def revoke_invite(
        self,
        invite_id: str,
        *,
        code: int = 4001,
        reason: str = "Invite revoked",
    ) -> int:
        """Close every guest socket admitted with *invite_id* (#782).

        Token checks stop new requests and reconnects; this closes the
        sockets that were already open. Returns the number of guest
        participants disconnected.
        """

        async with self._lock:
            subs = [
                sub
                for room_subs in self._rooms.values()
                for sub in room_subs
                if sub.invite_id == invite_id
            ]

        for sub in subs:
            try:
                await sub.ws.close(code=code, reason=reason)
            except Exception:  # noqa: BLE001 — best-effort socket revocation
                pass
        for sub in subs:
            await self.unsubscribe(sub.participant_id, websocket=sub.ws)
        return len({sub.participant_id for sub in subs})

    async def broadcast(
        self,
        room_id: str,
        frame: OutgoingFrame,
        *,
        exclude_participant_id: str | None = None,
    ) -> None:
        """Send *frame* to every subscriber in *room_id*.

        ``exclude_participant_id`` — when set, the named participant's
        own WS is skipped. Used by ``PresenceService`` so an arriving
        participant doesn't receive a presence_update event for
        itself (other subscribers still get it).
        """
        payload = frame.model_dump_json()
        async with self._lock:
            subs = list(self._rooms.get(room_id, []))
        for sub in subs:
            if (
                exclude_participant_id is not None
                and sub.participant_id == exclude_participant_id
            ):
                continue
            try:
                await sub.ws.send_text(payload)
            except Exception:
                # Connection already closed — will be cleaned up on next unsubscribe.
                pass

    async def broadcast_tailored(
        self,
        room_id: str,
        make_frame,
    ) -> set[str]:
        """Broadcast to a room with a per-recipient frame factory.

        ``make_frame(participant_id) -> OutgoingFrame`` is invoked
        once per subscriber. Used when the outgoing payload must
        vary per recipient — specifically, fan-out of a user
        message where each agent gets its own
        ``metadata.request_id`` so subsequent lifecycle events can
        be linked to this particular invocation.

        Per-subscriber errors are swallowed (same semantics as
        ``broadcast``); dead connections are cleaned up on their
        next unsubscribe.
        """
        async with self._lock:
            subs = list(self._rooms.get(room_id, []))
        delivered: set[str] = set()
        for sub in subs:
            try:
                frame = make_frame(sub.participant_id)
                if frame is None:
                    continue
                await sub.ws.send_text(frame.model_dump_json())
                delivered.add(sub.participant_id)
            except Exception:
                pass
        return delivered

    async def push_to_users(
        self,
        user_ids: set[str] | frozenset[str],
        frame: OutgoingFrame,
    ) -> None:
        """Send *frame* to every active subscription owned by *user_ids*.

        Backs the agent-profile 2차 view fanout (#266 Step 6): when a
        task is created/updated/deleted, the server pushes a
        ``task.updated`` frame to every admin user (and, in a future
        revision, every agent owner) so their UI updates without
        polling.

        Every subscription of every matching participant is notified —
        one frame per open tab or room. Per-recipient errors are
        swallowed; dead connections are cleaned up on next unsubscribe.
        """
        if not user_ids:
            return
        payload = frame.model_dump_json()
        async with self._lock:
            targets: list[_Subscription] = []
            for uid in user_ids:
                for pid in self._by_user.get(uid, set()):
                    targets.extend(self._by_participant.get(pid, []))
        for sub in targets:
            try:
                await sub.ws.send_text(payload)
            except Exception:
                pass

    async def connected_participant_ids(self) -> set[str]:
        """Return the set of participant IDs that have an active subscription."""
        async with self._lock:
            return set(self._by_participant.keys())

    async def is_connected(self, participant_id: str) -> bool:
        async with self._lock:
            return participant_id in self._by_participant

    async def participant_generation(self, participant_id: str) -> int | None:
        async with self._lock:
            subs = self._by_participant.get(participant_id)
            return subs[-1].generation if subs else None

    async def participant_turn_control(self, participant_id: str) -> bool:
        async with self._lock:
            subs = self._by_participant.get(participant_id, [])
            return bool(subs and subs[-1].turn_control)

    async def execution_connection(self, participant_id: str, *, websocket=None) -> tuple[int, str] | None:
        """Return the advertised control fence for exactly this live socket."""
        async with self._lock:
            subs = self._by_participant.get(participant_id)
            sub = subs[-1] if subs else None
            if (sub is None or not sub.execution_control or sub.generation is None
                    or (websocket is not None and sub.ws is not websocket)):
                return None
            return sub.generation, sub.socket_epoch

    async def send_to(
        self,
        participant_id: str,
        frame: OutgoingFrame,
        *,
        expected_generation: int | None = None,
        expected_socket_epoch: str | None = None,
    ) -> bool:
        """Send to every socket of *participant_id*, optionally fencing
        against a process generation / socket epoch.

        Returns ``True`` when at least one socket accepted the frame.
        """
        async with self._lock:
            subs = list(self._by_participant.get(participant_id, []))
        payload: str | None = None
        sent = False
        for sub in subs:
            if expected_generation is not None and sub.generation != expected_generation:
                continue
            if expected_socket_epoch is not None and sub.socket_epoch != expected_socket_epoch:
                continue
            if payload is None:
                payload = frame.model_dump_json()
            try:
                await sub.ws.send_text(payload)
            except Exception:  # noqa: S112 — dead socket; dropped on its unsubscribe
                continue
            sent = True
        return sent
