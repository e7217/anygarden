"""ConnectionManager single-session policy (#79).

When two clients share the same agent token they connect to the same
``participant_id``. Without the fix both subscriptions stay alive in
``_rooms`` and every broadcast doubles up. These tests pin the
"newest connection wins" contract that prevents duplicate forwards
and replies.
"""

from __future__ import annotations

import pytest

from anygarden.ws.manager import ConnectionManager


class _RecordingWS:
    """Minimal WS double — records ``send_text``/``close`` calls."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.closed: tuple[int, str] | None = None

    async def send_text(self, data: str) -> None:
        if self.closed is not None:
            raise RuntimeError("socket closed")
        self.sent.append(data)

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)


class _RaisingCloseWS(_RecordingWS):
    """Variant whose ``close`` raises — verifies the supersede path
    swallows the exception so a half-dead old socket can't break the
    new connection's subscribe."""

    async def close(self, *, code: int, reason: str) -> None:
        self.closed = (code, reason)
        raise RuntimeError("socket already half-closed")


@pytest.mark.asyncio
async def test_second_subscribe_supersedes_first() -> None:
    mgr = ConnectionManager()
    old = _RecordingWS()
    new = _RecordingWS()

    await mgr.subscribe("room-1", "p-1", old)  # type: ignore[arg-type]
    await mgr.subscribe("room-1", "p-1", new)  # type: ignore[arg-type]

    assert old.closed == (4040, "superseded")
    # Only the new socket should receive subsequent broadcasts.
    from anygarden.ws.protocol import MessageOut
    from datetime import datetime, timezone

    frame = MessageOut(
        id="m-1",
        room_id="room-1",
        participant_id="other",
        content="hello",
        seq=1,
        created_at=datetime.now(timezone.utc),
    )
    await mgr.broadcast("room-1", frame)
    assert new.sent, "new socket must receive the broadcast"
    assert old.sent == [], "old socket must not receive broadcasts after supersede"


@pytest.mark.asyncio
async def test_supersede_across_different_rooms() -> None:
    """An agent reconnecting from a different room still supersedes
    its prior single-room subscription. Without this, the prior
    subscription would linger in its old room and keep doubling
    broadcasts there."""
    mgr = ConnectionManager()
    old = _RecordingWS()
    new = _RecordingWS()

    await mgr.subscribe("room-A", "p-1", old)  # type: ignore[arg-type]
    await mgr.subscribe("room-B", "p-1", new)  # type: ignore[arg-type]

    assert old.closed == (4040, "superseded")

    # Old room should be empty (and pruned).
    connected = await mgr.connected_participant_ids()
    assert connected == {"p-1"}

    # Broadcasting to the old room should be a no-op now.
    from anygarden.ws.protocol import MessageOut
    from datetime import datetime, timezone

    frame = MessageOut(
        id="m-1",
        room_id="room-A",
        participant_id="other",
        content="x",
        seq=1,
        created_at=datetime.now(timezone.utc),
    )
    await mgr.broadcast("room-A", frame)
    assert old.sent == []
    assert new.sent == []


@pytest.mark.asyncio
async def test_supersede_swallows_close_error() -> None:
    """A flaky close on the old socket must not abort the new
    subscribe — the new socket has to become live regardless."""
    mgr = ConnectionManager()
    old = _RaisingCloseWS()
    new = _RecordingWS()

    await mgr.subscribe("room-1", "p-1", old)  # type: ignore[arg-type]
    await mgr.subscribe("room-1", "p-1", new)  # type: ignore[arg-type]

    # close was attempted (raised internally, swallowed).
    assert old.closed == (4040, "superseded")
    # New socket is live and the manager state is consistent.
    assert (await mgr.connected_participant_ids()) == {"p-1"}


@pytest.mark.asyncio
async def test_distinct_participants_coexist() -> None:
    """Different ``participant_id``s must not interfere — sanity
    check that the supersede path is keyed on participant only."""
    mgr = ConnectionManager()
    a = _RecordingWS()
    b = _RecordingWS()

    await mgr.subscribe("room-1", "p-A", a)  # type: ignore[arg-type]
    await mgr.subscribe("room-1", "p-B", b)  # type: ignore[arg-type]

    assert a.closed is None
    assert b.closed is None

    from anygarden.ws.protocol import MessageOut
    from datetime import datetime, timezone

    frame = MessageOut(
        id="m-1",
        room_id="room-1",
        participant_id="other",
        content="ping",
        seq=1,
        created_at=datetime.now(timezone.utc),
    )
    await mgr.broadcast("room-1", frame)
    assert a.sent and b.sent
