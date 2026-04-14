"""In-process WebSocket connection manager."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from doorae.ws.protocol import OutgoingFrame


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
        self._lock = asyncio.Lock()

    @property
    def active_connections(self) -> int:
        return len(self._by_participant)

    async def subscribe(
        self, room_id: str, participant_id: str, ws: WebSocket
    ) -> None:
        """Register *ws* as listening on *room_id*."""
        sub = _Subscription(room_id=room_id, participant_id=participant_id, ws=ws)
        async with self._lock:
            self._rooms.setdefault(room_id, []).append(sub)
            self._by_participant[participant_id] = sub

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

    async def broadcast(self, room_id: str, frame: OutgoingFrame) -> None:
        """Send *frame* to every subscriber in *room_id*."""
        payload = frame.model_dump_json()
        async with self._lock:
            subs = list(self._rooms.get(room_id, []))
        for sub in subs:
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
