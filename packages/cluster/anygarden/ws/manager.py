"""In-process WebSocket connection manager."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

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


class ConnectionManager:
    """Manages active WebSocket connections grouped by room."""

    def __init__(self) -> None:
        # room_id -> list of subscriptions
        self._rooms: dict[str, list[_Subscription]] = {}
        # participant_id -> subscription (for direct sends)
        self._by_participant: dict[str, _Subscription] = {}
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
        return len(self._by_participant)

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
    ) -> None:
        """Register *ws* as listening on *room_id*.

        Issue #79 — single-session policy. If *participant_id* already
        has an active subscription, the older socket is evicted and
        closed with code 4040 ("superseded"). Without this guard two
        clients sharing an agent token (e.g. ``anygarden-machine`` reconcile
        racing a manual launch) would both stay in ``_rooms[room_id]``
        and every broadcast would fan out to both — doubling LLM calls,
        ``[ROOM_QUERY]`` forwards, and direct replies.

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
        )
        superseded: _Subscription | None = None
        async with self._lock:
            old = self._by_participant.get(participant_id)
            if old is not None:
                superseded = old
                old_subs = self._rooms.get(old.room_id, [])
                self._rooms[old.room_id] = [
                    s for s in old_subs if s.participant_id != participant_id
                ]
                if not self._rooms[old.room_id]:
                    del self._rooms[old.room_id]
                # Drop the old participant's entry from the user index
                # too — the same participant may carry a different
                # user_id on the new subscription (rare but defensible).
                if old.user_id is not None:
                    bucket = self._by_user.get(old.user_id)
                    if bucket is not None:
                        bucket.discard(participant_id)
                        if not bucket:
                            del self._by_user[old.user_id]
            self._rooms.setdefault(room_id, []).append(sub)
            self._by_participant[participant_id] = sub
            if user_id is not None:
                self._by_user.setdefault(user_id, set()).add(participant_id)

        # Close the superseded socket OUTSIDE the lock — ws.close awaits
        # the underlying ASGI send and we must not block other ops.
        # Best-effort: a socket that's already half-dead can throw on
        # close; we just need it to stop receiving frames.
        if superseded is not None:
            try:
                await superseded.ws.close(code=4040, reason="superseded")
            except Exception:
                pass

        # Publish AFTER releasing the lock so the broadcast path's own
        # lock acquisition doesn't deadlock with ours.
        if self._presence is not None:
            now = datetime.now(timezone.utc)
            await self._presence.publish(
                room_id,
                participant_id,
                online=True,
                last_seen_at=now,
            )

    async def unsubscribe(self, participant_id: str) -> None:
        """Remove the subscription for *participant_id*."""
        async with self._lock:
            sub = self._by_participant.pop(participant_id, None)
            if sub is None:
                return
            subs = self._rooms.get(sub.room_id, [])
            self._rooms[sub.room_id] = [
                s for s in subs if s.participant_id != participant_id
            ]
            if not self._rooms[sub.room_id]:
                del self._rooms[sub.room_id]
            if sub.user_id is not None:
                bucket = self._by_user.get(sub.user_id)
                if bucket is not None:
                    bucket.discard(participant_id)
                    if not bucket:
                        del self._by_user[sub.user_id]
            now = datetime.now(timezone.utc)
            self._last_seen[participant_id] = now
            room_id = sub.room_id

        if self._presence is not None:
            await self._presence.publish(
                room_id,
                participant_id,
                online=False,
                last_seen_at=now,
            )

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
    ) -> None:
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
        for sub in subs:
            try:
                frame = make_frame(sub.participant_id)
                await sub.ws.send_text(frame.model_dump_json())
            except Exception:
                pass

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

        Multiple subscriptions per user are *each* notified — that
        matches existing semantics for room broadcasts (a user with two
        tabs hears the same message twice). Per-recipient errors are
        swallowed; dead connections are cleaned up on next unsubscribe.
        """
        if not user_ids:
            return
        payload = frame.model_dump_json()
        async with self._lock:
            targets: list[_Subscription] = []
            for uid in user_ids:
                for pid in self._by_user.get(uid, set()):
                    sub = self._by_participant.get(pid)
                    if sub is not None:
                        targets.append(sub)
        for sub in targets:
            try:
                await sub.ws.send_text(payload)
            except Exception:
                pass

    async def connected_participant_ids(self) -> set[str]:
        """Return the set of participant IDs that have an active subscription."""
        async with self._lock:
            return set(self._by_participant.keys())

    async def send_to(self, participant_id: str, frame: OutgoingFrame) -> None:
        """Send *frame* directly to a single participant."""
        async with self._lock:
            sub = self._by_participant.get(participant_id)
        if sub is not None:
            try:
                await sub.ws.send_text(frame.model_dump_json())
            except Exception:
                pass
