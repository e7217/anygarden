"""PresenceService — unified participant liveness source of truth.

Before this module the question "is this agent currently reachable?"
had three different answers depending on who was asking:

- ``ConnectionManager`` (WS layer) knew who had an open socket,
- ``Agent.last_heartbeat_at`` (DB) was updated by the machine
  layer on agent process heartbeat,
- the REST ``GET /rooms/{id}`` / ``[ROOM_QUERY]`` / frontend
  participant popovers just looked at the ``Participant`` row and
  had no liveness signal at all.

``PresenceService`` consolidates those signals into a single API
(``status``/``room_snapshot``/``publish``). Consumers — REST
serializers, cross-room query expected-count logic, and the
frontend presence dot — now funnel through here instead of
inventing their own liveness heuristic.

Design notes
-------------

- **WS subscription is the primary source of truth.** An active
  subscription in ``ConnectionManager`` means ``online=True``, full
  stop. Heartbeat is only a tie-breaker for "probably reconnecting
  right now" cases.
- **No DB writes.** ``last_seen_at`` is kept in
  ``ConnectionManager._last_seen`` (memory) and recomputed on each
  ``status`` call. Prefer losing the last-seen timestamp across
  process restarts over paying a write on every connect/disconnect.
- **Batching.** ``room_snapshot`` issues a single ``SELECT`` for
  agent heartbeats (``agent_id IN (...)``) — not N individual
  queries — because rooms with dozens of agents are a normal case.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from doorae.db.models import Agent, Participant


def _as_aware(dt: datetime | None) -> datetime | None:
    """Coerce a DB-returned naive datetime into UTC-aware.

    SQLite strips timezone info on roundtrip even when the column is
    declared ``DateTime(timezone=True)``; Postgres preserves it. We
    normalise at the edge so downstream arithmetic (``now - hb``)
    stays consistent regardless of backend.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass(frozen=True, slots=True)
class ParticipantStatus:
    """Snapshot of a single participant's liveness state.

    ``source`` is diagnostic — tests and log lines can see which
    signal won. Consumers should only branch on ``online`` and
    ``last_seen_at``.
    """

    participant_id: str
    online: bool
    last_seen_at: datetime | None
    source: Literal["ws", "heartbeat", "db"]


class PresenceService:
    """Compute and broadcast participant presence.

    Constructed with a ``ConnectionManager`` reference; the manager
    is the primary signal for "subscribed right now?". A
    ``set_presence_service`` setter on the manager carries the
    reverse wiring so subscribe/unsubscribe can call ``publish``
    without importing this module (avoids circular imports).
    """

    def __init__(self, conn_mgr) -> None:  # type: ignore[no-untyped-def]
        # ``conn_mgr`` is ``doorae.ws.manager.ConnectionManager`` but
        # typing it eagerly would force a circular import at module
        # load — the manager imports this module's frame type.
        self._conn_mgr = conn_mgr

    async def status(
        self,
        participant_id: str,
        *,
        db: AsyncSession,
        now: datetime | None = None,
    ) -> ParticipantStatus:
        """Compute a fresh ``ParticipantStatus`` for ``participant_id``.

        Resolution order:

        1. If the participant has a live WS subscription → online,
           ``last_seen_at`` = current time (source=``ws``).
        2. Otherwise, if the participant is an agent and
           ``Agent.last_heartbeat_at`` is within the recent window →
           still offline from the WS side, but expose
           ``last_seen_at`` so the UI can show "last seen 12s ago"
           (source=``heartbeat``).
        3. Otherwise → offline with a best-effort ``last_seen_at``
           pulled from the manager's memo (source=``db``; the DB
           itself holds no presence row today, the name just reflects
           "we fell through to whatever scraps we have").
        """
        now = now or datetime.now(timezone.utc)
        connected = await self._conn_mgr.connected_participant_ids()
        if participant_id in connected:
            return ParticipantStatus(
                participant_id=participant_id,
                online=True,
                last_seen_at=now,
                source="ws",
            )

        # Fallback: heartbeat-based check for agent participants.
        last_seen_memo = self._conn_mgr.last_seen_at(participant_id)

        # Look up participant → agent_id (single row).
        part = (
            await db.execute(
                select(Participant).where(Participant.id == participant_id)
            )
        ).scalar_one_or_none()

        if part is not None and part.agent_id is not None:
            agent = (
                await db.execute(
                    select(Agent).where(Agent.id == part.agent_id)
                )
            ).scalar_one_or_none()
            hb = _as_aware(
                getattr(agent, "last_heartbeat_at", None) if agent else None
            )
            if hb is not None:
                # Use whichever is more recent: WS disconnect memo or heartbeat.
                latest = (
                    hb
                    if last_seen_memo is None or hb > last_seen_memo
                    else last_seen_memo
                )
                return ParticipantStatus(
                    participant_id=participant_id,
                    online=False,
                    last_seen_at=latest,
                    source="heartbeat",
                )

        return ParticipantStatus(
            participant_id=participant_id,
            online=False,
            last_seen_at=last_seen_memo,
            source="db",
        )

    async def room_snapshot(
        self,
        room_id: str,
        *,
        db: AsyncSession,
        now: datetime | None = None,
    ) -> list[ParticipantStatus]:
        """Batch-compute presence for every participant in ``room_id``.

        Uses a single ``IN``-query for agent heartbeats to avoid
        N+1 round-trips when a room holds many agents.
        """
        now = now or datetime.now(timezone.utc)
        connected = await self._conn_mgr.connected_participant_ids()

        rows = (
            await db.execute(
                select(Participant.id, Participant.agent_id).where(
                    Participant.room_id == room_id
                )
            )
        ).all()

        agent_ids = {row[1] for row in rows if row[1] is not None}
        heartbeats: dict[str, datetime | None] = {}
        if agent_ids:
            hb_rows = (
                await db.execute(
                    select(Agent.id, Agent.last_heartbeat_at).where(
                        Agent.id.in_(agent_ids)
                    )
                )
            ).all()
            heartbeats = {row[0]: _as_aware(row[1]) for row in hb_rows}

        statuses: list[ParticipantStatus] = []
        for pid, agent_id in rows:
            if pid in connected:
                statuses.append(
                    ParticipantStatus(
                        participant_id=pid,
                        online=True,
                        last_seen_at=now,
                        source="ws",
                    )
                )
                continue

            last_seen_memo = self._conn_mgr.last_seen_at(pid)
            if agent_id is not None:
                hb = heartbeats.get(agent_id)
                if hb is not None:
                    latest = (
                        hb
                        if last_seen_memo is None or hb > last_seen_memo
                        else last_seen_memo
                    )
                    statuses.append(
                        ParticipantStatus(
                            participant_id=pid,
                            online=False,
                            last_seen_at=latest,
                            source="heartbeat",
                        )
                    )
                    continue

            statuses.append(
                ParticipantStatus(
                    participant_id=pid,
                    online=False,
                    last_seen_at=last_seen_memo,
                    source="db",
                )
            )
        return statuses

    async def publish(
        self,
        room_id: str,
        participant_id: str,
        *,
        online: bool,
        last_seen_at: datetime | None,
    ) -> None:
        """Broadcast a ``PresenceUpdateOut`` frame to ``room_id``.

        Best-effort: failures to deliver to individual sockets are
        absorbed inside ``ConnectionManager.broadcast`` already.
        """
        # Late import to avoid a cycle — ``ws.protocol`` imports
        # nothing from presence, but presence-module users often
        # haven't finished importing ws.protocol yet.
        from doorae.ws.protocol import PresenceUpdateOut

        frame = PresenceUpdateOut(
            room_id=room_id,
            participant_id=participant_id,
            online=online,
            last_seen_at=last_seen_at,
        )
        # The subject participant doesn't need to learn about their
        # own presence state — it would just confuse client-side
        # message loops that expect e.g. the next frame after
        # subscribe to be replayed history or a message.
        await self._conn_mgr.broadcast(
            room_id, frame, exclude_participant_id=participant_id
        )
