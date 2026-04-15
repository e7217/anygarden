"""In-process WebSocket connection manager."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from fastapi import WebSocket

from doorae.ws.protocol import OutgoingFrame

if TYPE_CHECKING:
    from doorae.presence.service import PresenceService


@dataclass(slots=True)
class _Subscription:
    room_id: str
    participant_id: str
    ws: WebSocket


class ConnectionManager:
    """Manages active WebSocket connections grouped by room."""

    def __init__(self) -> None:
        # room_id -> list of subscriptions
        self._rooms: dict[str, list[_Subscription]] = {}
        # participant_id -> subscription (for direct sends)
        self._by_participant: dict[str, _Subscription] = {}
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
        self, room_id: str, participant_id: str, ws: WebSocket
    ) -> None:
        """Register *ws* as listening on *room_id*."""
        sub = _Subscription(room_id=room_id, participant_id=participant_id, ws=ws)
        async with self._lock:
            self._rooms.setdefault(room_id, []).append(sub)
            self._by_participant[participant_id] = sub

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
