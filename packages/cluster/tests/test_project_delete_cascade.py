"""ROOM-02 — deleting a project removes its rooms and everything hanging off
them (participants, messages, shared-file rows, invites) while the user's
project-less DM room survives untouched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import (
    Base,
    Message,
    Participant,
    Project,
    Room,
    RoomInviteLink,
    RoomSharedFile,
    User,
)

pytestmark = pytest.mark.req("ROOM-02")


@pytest_asyncio.fixture()
async def env(config: AnygardenSettings) -> AsyncIterator[dict]:
    engine = build_engine(config.db_url)
    sessions = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with sessions() as db:
        owner = User(email="owner@anygarden.io", password_hash="x")
        db.add(owner)
        await db.flush()
        project = Project(name="doomed", created_by=owner.id)
        db.add(project)
        await db.flush()
        room = Room(project_id=project.id, name="main")
        dm = Room(project_id=None, name="DM", is_dm=True)
        db.add_all([room, dm])
        await db.flush()
        sub = Room(project_id=project.id, name="sub", parent_room_id=room.id)
        db.add(sub)
        await db.flush()

        for r in (room, sub, dm):
            part = Participant(room_id=r.id, user_id=owner.id, role="owner")
            db.add(part)
            await db.flush()
            db.add(Message(room_id=r.id, participant_id=part.id, content="hi", seq=1))
        db.add(
            RoomSharedFile(
                room_id=room.id,
                filename="notes.txt",
                storage_name="notes.txt",
                storage_path=f"{room.id}/f1",
                sha256="0" * 64,
                size_bytes=1,
                mime="text/plain",
                uploaded_by=owner.id,
            )
        )
        db.add(
            RoomInviteLink(
                room_id=room.id,
                created_by_user_id=owner.id,
                token_hash="h",
                lookup_hint="inv_cascade0",
            )
        )
        await db.commit()
        ids = {
            "owner": owner.id,
            "project": project.id,
            "rooms": [room.id, sub.id],
            "dm": dm.id,
        }

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "sessions": sessions,
            "ids": ids,
            "token": create_user_token(
                owner.id, "owner@anygarden.io", False, secret=config.jwt_secret
            ),
        }
    await engine.dispose()


async def _count(db, model, room_ids: list[str]) -> int:
    return await db.scalar(
        select(func.count()).select_from(model).where(model.room_id.in_(room_ids))
    )


async def test_project_delete_cascades_but_keeps_dm(env):
    ids = env["ids"]
    resp = await env["client"].delete(
        f"/api/v1/projects/{ids['project']}",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 204

    async with env["sessions"]() as db:
        assert await db.get(Project, ids["project"]) is None
        remaining_rooms = await db.scalar(
            select(func.count()).select_from(Room).where(Room.id.in_(ids["rooms"]))
        )
        assert remaining_rooms == 0
        for model in (Participant, Message, RoomSharedFile, RoomInviteLink):
            assert await _count(db, model, ids["rooms"]) == 0, model.__name__

        # The DM (no project) keeps its room, membership and history.
        assert await db.get(Room, ids["dm"]) is not None
        assert await _count(db, Participant, [ids["dm"]]) == 1
        assert await _count(db, Message, [ids["dm"]]) == 1
