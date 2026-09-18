"""D-1 (#624) message reactions — low-cost receipts, never agent wakes."""

from __future__ import annotations

import pytest
from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.models import Base, Message, Room, User
from httpx import ASGITransport, AsyncClient


@pytest.fixture()
async def react_env(tmp_path):
    db_path = tmp_path / "react.db"
    engine = build_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = build_session_factory(engine)
    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{db_path}",
        jwt_secret="synthetic-local-test-secret-not-a-real-credential",
    )
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    admin, member = str(uuid4()), str(uuid4())
    room_id = str(uuid4())
    message_id = str(uuid4())
    from anygarden.db.models import Message, Participant

    member_pid = str(uuid4())
    async with factory.begin() as db:
        db.add(User(id=admin, email="a@x.test", password_hash="x", is_admin=True))
        db.add(User(id=member, email="m@x.test", password_hash="x"))
        db.add(Room(id=room_id, name="react", visibility="private"))
        db.add(
            Participant(id=member_pid, room_id=room_id, user_id=member, role="member")
        )
        db.add(
            Message(
                id=message_id,
                room_id=room_id,
                participant_id=member_pid,
                seq=1,
                content="hello",
            )
        )
    return {
        "app": app,
        "engine": engine,
        "factory": factory,
        "room": room_id,
        "message": message_id,
        "admin": admin,
        "member": member,
        "member_pid": member_pid,
        "config": config,
    }


def _token(config, uid_, email, admin=False):
    return create_user_token(uid_, email, admin, secret=config.jwt_secret)


async def test_reaction_add_list_remove_and_conflict(react_env):
    from anygarden.db.models import MessageReaction

    env = react_env
    app, config = env["app"], env["config"]
    member_token = _token(config, env["member"], "m@x.test")
    headers = {"Authorization": f"Bearer {member_token}"}
    url = f"/api/v1/rooms/{env['room']}/messages/{env['message']}/reactions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        created = await c.post(url, headers=headers, json={"emoji": "👍"})
        assert created.status_code == 201, created.text
        duplicate = await c.post(url, headers=headers, json={"emoji": "👍"})
        assert duplicate.status_code == 409
        removed = await c.delete(f"{url}/👍", headers=headers)
        assert removed.status_code == 204
        re_added = await c.post(url, headers=headers, json={"emoji": "👍"})
        assert re_added.status_code == 201
    async with env["factory"]() as db:
        rows = (
            await db.scalars(
                select(MessageReaction).where(MessageReaction.emoji == "👍")
            )
        ).all()
        assert len(rows) == 1


async def test_reaction_requires_membership(react_env):
    env = react_env
    app, config = env["app"], env["config"]
    outsider, outsider_email = str(uuid4()), "o@x.test"
    token = _token(config, outsider, outsider_email)
    url = f"/api/v1/rooms/{env['room']}/messages/{env['message']}/reactions"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        response = await c.post(
            url, headers={"Authorization": f"Bearer {token}"}, json={"emoji": "👍"}
        )
        assert response.status_code == 403


from uuid import uuid4

from sqlalchemy import select, update


async def test_reminder_wake_respects_room_policy(react_env):
    """D-1 condition 3: reminder wake frames honor the room's policy —
    rooms without ``reminder`` in wake_triggers never emit the frame,
    and emitted frames carry the reminder stamp for agent classification."""
    from types import SimpleNamespace

    from anygarden.messages.service import broadcast_reminder_wake
    from anygarden.db.models import Room

    env = react_env
    app, config = env["app"], env["config"]
    member_token = _token(config, env["member"], "m@x.test")

    class RecordingManager:
        def __init__(self):
            self.frames = []

        async def broadcast(self, room_id, frame):
            self.frames.append(frame)

    manager = RecordingManager()
    app.state.connection_manager = manager

    # Opt the room into reminder wakes and emit one.
    async with env["factory"].begin() as db:
        await db.execute(
            update(Room)
            .where(Room.id == env["room"])
            .values(wake_triggers=["reminder"])
        )
    async with env["factory"]() as db:
        published = await broadcast_reminder_wake(
            db,
            env["room"],
            text="정기 리마인더",
            manager=manager,
        )
    assert published is True
    assert len(manager.frames) == 1
    frame = manager.frames[0]
    payload = frame.model_dump_json()
    import json as _json

    assert _json.loads(payload)["metadata"]["wake_trigger"] == "reminder"

    # Opt out: the emitter must not publish (condition 3).
    async with env["factory"].begin() as db:
        await db.execute(
            update(Room).where(Room.id == env["room"]).values(wake_triggers=["mention"])
        )
    manager.frames.clear()
    async with env["factory"]() as db:
        published = await broadcast_reminder_wake(
            db,
            env["room"],
            text="정기 리마인더 2",
            manager=manager,
        )
    assert published is False
    assert manager.frames == []
    # The reminder message itself is not persisted for opted-out rooms —
    # emitting would silently inject content nobody agreed to wake for.
    async with env["factory"]() as db:
        count = len(
            (
                await db.execute(
                    select(Message).where(Message.content == "정기 리마인더 2")
                )
            ).all()
        )
    assert count == 0
