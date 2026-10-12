"""MSG-03 — concurrent sends keep each room's ``seq`` unique and gapless.

Uses a file-backed SQLite DB so the concurrent sessions really contend for
the writer, exactly like simultaneous WS senders.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from anygarden.db.engine import (
    begin_write_transaction,
    build_engine,
    build_session_factory,
)
from anygarden.db.models import Base, Message, Project, Room
from anygarden.messages.service import append_message

pytestmark = pytest.mark.req("MSG-03")

SENDERS = 30


async def _setup(tmp_path: Path):
    engine = build_engine(f"sqlite+aiosqlite:///{tmp_path / 'seq.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = build_session_factory(engine)
    async with sessions() as db:
        project = Project(name="p")
        db.add(project)
        await db.flush()
        rooms = [Room(project_id=project.id, name=n) for n in ("a", "b")]
        db.add_all(rooms)
        await db.commit()
        room_ids = [r.id for r in rooms]
    return engine, sessions, room_ids


async def _seqs(sessions, room_id: str) -> list[int]:
    async with sessions() as db:
        rows = await db.execute(
            select(Message.seq).where(Message.room_id == room_id).order_by(Message.seq)
        )
        return list(rows.scalars().all())


async def test_concurrent_ws_sends_get_gapless_unique_seq(tmp_path: Path) -> None:
    """The WS send path (``begin_write_transaction`` + ``append_message``)
    never fails or collides under contention, in two rooms at once."""
    engine, sessions, room_ids = await _setup(tmp_path)

    async def send(room_id: str, i: int) -> int:
        async with sessions() as db:
            await begin_write_transaction(db)
            msg = await append_message(db, room_id, None, f"m{i}")
            await db.commit()
            return msg.seq

    try:
        results = await asyncio.gather(
            *(send(rid, i) for i in range(SENDERS) for rid in room_ids)
        )
        assert len(results) == SENDERS * len(room_ids)
        for rid in room_ids:
            assert await _seqs(sessions, rid) == list(range(1, SENDERS + 1))
    finally:
        await engine.dispose()


async def test_concurrent_unserialized_appends_never_persist_dupes_or_gaps(
    tmp_path: Path,
) -> None:
    """Even without the writer lock, whatever is stored stays 1..N with no
    duplicate (the ``uq_room_seq`` constraint + retry loop)."""
    engine, sessions, room_ids = await _setup(tmp_path)
    room_id = room_ids[0]

    async def send(i: int) -> None:
        async with sessions() as db:
            await append_message(db, room_id, None, f"m{i}")
            await db.commit()

    try:
        await asyncio.gather(*(send(i) for i in range(SENDERS)), return_exceptions=True)
        seqs = await _seqs(sessions, room_id)
        assert seqs, "at least one append must land"
        assert seqs == list(range(1, len(seqs) + 1))
    finally:
        await engine.dispose()


async def test_concurrent_rest_sends_all_succeed_with_gapless_seq(tmp_path: Path) -> None:
    """The REST send path takes the same writer lock as the WS path, so a
    burst of concurrent posts never surfaces SQLITE_BUSY as a 500."""
    import secrets

    from anygarden.app import create_app
    from anygarden.config import AnygardenSettings
    from httpx import ASGITransport, AsyncClient

    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'rest.db'}",
        jwt_secret=secrets.token_urlsafe(32),
    )
    engine = build_engine(config.db_url)
    sessions = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            reg = await client.post(
                "/api/v1/auth/register",
                json={"email": "seq@test.dev", "password": "correct-horse"},
            )
            headers = {"Authorization": f"Bearer {reg.json()['token']}"}
            project = (
                await client.post("/api/v1/projects", json={"name": "p"}, headers=headers)
            ).json()
            room = (
                await client.post(
                    "/api/v1/rooms",
                    json={"project_id": project["id"], "name": "r"},
                    headers=headers,
                )
            ).json()

            responses = await asyncio.gather(
                *(
                    client.post(
                        f"/api/v1/rooms/{room['id']}/messages",
                        json={"content": f"m{i}"},
                        headers=headers,
                    )
                    for i in range(SENDERS)
                )
            )

        assert [r.status_code for r in responses] == [201] * SENDERS
        assert await _seqs(sessions, room["id"]) == list(range(1, SENDERS + 1))
    finally:
        await engine.dispose()
