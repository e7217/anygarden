"""MSG-08 — a human author can edit and delete their own message.

Edits and deletes are in-place changes of an existing row: the room seq
never moves, no new message frame is broadcast (so no agent wakes), and
other tabs learn about the change through a ``message_updated`` frame.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text

from anygarden.app import create_app
from anygarden.auth.dependencies import Identity
from anygarden.auth.jwt import GuestClaims, create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.fts import create_message_fts
from anygarden.db.models import (
    Agent,
    AgentTurn,
    Base,
    Message,
    Participant,
    Room,
    User,
)
from anygarden.dependencies import get_current_identity

pytestmark = pytest.mark.req("MSG-08")


class _RecordingManager:
    def __init__(self) -> None:
        self.frames: list[tuple[str, dict]] = []

    async def broadcast(self, room_id, frame, **_kwargs):
        payload = frame if isinstance(frame, dict) else frame.model_dump(mode="json")
        self.frames.append((room_id, payload))


@pytest.fixture()
async def env(tmp_path):
    db_path = tmp_path / "msg08.db"
    engine = build_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await create_message_fts(conn)
    factory = build_session_factory(engine)
    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{db_path}",
        jwt_secret="synthetic-local-test-secret-not-a-real-credential",
    )
    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = factory
    manager = _RecordingManager()
    app.state.connection_manager = manager

    ids = {
        "author": str(uuid4()),
        "other": str(uuid4()),
        "guest": str(uuid4()),
        "room": str(uuid4()),
        "author_pid": str(uuid4()),
        "other_pid": str(uuid4()),
        "guest_pid": str(uuid4()),
        "agent_pid": str(uuid4()),
        "agent": "agent-msg08",
        "mine": str(uuid4()),
        "theirs": str(uuid4()),
        "guest_msg": str(uuid4()),
        "agent_msg": str(uuid4()),
        "system_msg": str(uuid4()),
        "interaction_msg": str(uuid4()),
    }
    async with factory.begin() as db:
        db.add(User(id=ids["author"], email="author@x.test", password_hash="x"))
        db.add(User(id=ids["other"], email="other@x.test", password_hash="x"))
        db.add(User(id=ids["guest"], email="guest@x.test", password_hash="x"))
        db.add(Agent(id=ids["agent"], name="Agent", engine="claude-code"))
        db.add(Room(id=ids["room"], name="msg08", visibility="private"))
        await db.flush()
        db.add_all(
            [
                Participant(
                    id=ids["author_pid"], room_id=ids["room"],
                    user_id=ids["author"], role="member",
                ),
                Participant(
                    id=ids["other_pid"], room_id=ids["room"],
                    user_id=ids["other"], role="member",
                ),
                Participant(
                    id=ids["guest_pid"], room_id=ids["room"],
                    user_id=ids["guest"], role="member",
                ),
                Participant(
                    id=ids["agent_pid"], room_id=ids["room"],
                    agent_id=ids["agent"], role="member",
                ),
            ]
        )
        await db.flush()
        rows = [
            (ids["mine"], ids["author_pid"], "original zebra text",
             {"mentions": [{"type": "user", "participant_id": ids["agent_pid"]}]}),
            (ids["theirs"], ids["other_pid"], "someone else", None),
            (ids["guest_msg"], ids["guest_pid"], "guest says hi", None),
            (ids["agent_msg"], ids["agent_pid"], "agent reply", None),
            (ids["system_msg"], ids["author_pid"], "task created",
             {"system_origin": "task_assignment"}),
            (ids["interaction_msg"], ids["author_pid"], "pick one",
             {"interaction": {"kind": "choice"}}),
        ]
        for seq, (mid, pid, content, meta) in enumerate(rows, start=1):
            db.add(
                Message(
                    id=mid, room_id=ids["room"], participant_id=pid,
                    seq=seq, content=content, extra_metadata=meta,
                )
            )
    yield {"app": app, "config": config, "factory": factory, "manager": manager, **ids}
    await engine.dispose()


def _headers(env, user_key: str, email: str) -> dict[str, str]:
    token = create_user_token(
        env[user_key], email, False, secret=env["config"].jwt_secret
    )
    return {"Authorization": f"Bearer {token}"}


def _url(env, message_key: str) -> str:
    return f"/api/v1/rooms/{env['room']}/messages/{env[message_key]}"


async def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def _turn_count(env) -> int:
    async with env["factory"]() as db:
        return await db.scalar(select(func.count()).select_from(AgentTurn))


async def test_author_edits_own_message_in_place(env):
    headers = _headers(env, "author", "author@x.test")
    turns_before = await _turn_count(env)
    async with await _client(env["app"]) as c:
        res = await c.patch(
            _url(env, "mine"), headers=headers, json={"content": "edited giraffe text"}
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["content"] == "edited giraffe text"
    assert body["seq"] == 1
    assert body["metadata"]["edited_at"]
    # Routing data recorded at send time is left untouched: an edit never
    # re-derives or re-dispatches mentions.
    assert body["metadata"]["mentions"] == [
        {"type": "user", "participant_id": env["agent_pid"]}
    ]

    async with env["factory"]() as db:
        row = await db.get(Message, env["mine"])
        assert row.content == "edited giraffe text"
        assert row.seq == 1
        assert await db.scalar(select(func.max(Message.seq))) == 6
        assert await db.scalar(select(func.count()).select_from(Message)) == 6
        # FTS follows the edit.
        hits = (
            await db.execute(
                text("SELECT message_id FROM messages_fts WHERE messages_fts MATCH 'giraffe'")
            )
        ).scalars().all()
        assert hits == [env["mine"]]
        stale = (
            await db.execute(
                text("SELECT message_id FROM messages_fts WHERE messages_fts MATCH 'zebra'")
            )
        ).scalars().all()
        assert stale == []

    frames = env["manager"].frames
    assert [f["type"] for _, f in frames] == ["message_updated"]
    room_id, frame = frames[0]
    assert room_id == env["room"]
    assert frame["id"] == env["mine"]
    assert frame["seq"] == 1
    assert frame["content"] == "edited giraffe text"
    assert await _turn_count(env) == turns_before


async def test_author_deletes_own_message_as_tombstone(env):
    headers = _headers(env, "author", "author@x.test")
    async with await _client(env["app"]) as c:
        res = await c.delete(_url(env, "mine"), headers=headers)
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["content"] == ""
        assert body["seq"] == 1
        assert body["metadata"]["deleted_at"]

        # A deleted message can no longer be edited or deleted again.
        again = await c.delete(_url(env, "mine"), headers=headers)
        assert again.status_code == 409
        edit = await c.patch(_url(env, "mine"), headers=headers, json={"content": "x"})
        assert edit.status_code == 409

        history = await c.get(f"/api/v1/rooms/{env['room']}/messages", headers=headers)
    seqs = [m["seq"] for m in history.json()]
    assert seqs == [1, 2, 3, 4, 5, 6]

    async with env["factory"]() as db:
        hits = (
            await db.execute(
                text("SELECT message_id FROM messages_fts WHERE messages_fts MATCH 'zebra'")
            )
        ).scalars().all()
        assert hits == []

    frames = env["manager"].frames
    assert [f["type"] for _, f in frames] == ["message_updated"]
    assert frames[0][1]["metadata"]["deleted_at"]
    assert frames[0][1]["content"] == ""
    assert await _turn_count(env) == 0


async def test_other_member_cannot_edit_or_delete(env):
    headers = _headers(env, "other", "other@x.test")
    async with await _client(env["app"]) as c:
        edit = await c.patch(_url(env, "mine"), headers=headers, json={"content": "hijack"})
        delete = await c.delete(_url(env, "mine"), headers=headers)
    assert edit.status_code == 403
    assert delete.status_code == 403
    async with env["factory"]() as db:
        row = await db.get(Message, env["mine"])
        assert row.content == "original zebra text"
    assert env["manager"].frames == []


async def test_outsider_cannot_edit(env):
    outsider = str(uuid4())
    async with env["factory"].begin() as db:
        db.add(User(id=outsider, email="out@x.test", password_hash="x"))
    token = create_user_token(outsider, "out@x.test", False, secret=env["config"].jwt_secret)
    async with await _client(env["app"]) as c:
        res = await c.patch(
            _url(env, "mine"),
            headers={"Authorization": f"Bearer {token}"},
            json={"content": "nope"},
        )
    assert res.status_code == 403


@pytest.mark.parametrize("message_key", ["system_msg", "interaction_msg"])
async def test_structured_messages_are_not_editable(env, message_key):
    headers = _headers(env, "author", "author@x.test")
    async with await _client(env["app"]) as c:
        edit = await c.patch(_url(env, message_key), headers=headers, json={"content": "x"})
        delete = await c.delete(_url(env, message_key), headers=headers)
    assert edit.status_code == 403
    assert delete.status_code == 403
    assert env["manager"].frames == []


async def test_agent_cannot_edit_its_own_message(env):
    app = env["app"]
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        kind="agent", id=env["agent"]
    )
    try:
        async with await _client(app) as c:
            edit = await c.patch(_url(env, "agent_msg"), json={"content": "rewrite"})
            delete = await c.delete(_url(env, "agent_msg"))
    finally:
        app.dependency_overrides.pop(get_current_identity, None)
    assert edit.status_code == 403
    assert delete.status_code == 403


async def test_user_cannot_edit_agent_message(env):
    headers = _headers(env, "author", "author@x.test")
    async with await _client(env["app"]) as c:
        res = await c.patch(_url(env, "agent_msg"), headers=headers, json={"content": "x"})
    assert res.status_code == 403


async def test_guest_edits_own_message(env):
    app = env["app"]
    app.dependency_overrides[get_current_identity] = lambda: Identity(
        kind="guest",
        id=env["guest"],
        claims=GuestClaims(
            user_id=env["guest"], room_id=env["room"],
            invite_id="inv", display_name="Guest",
        ),
    )
    try:
        async with await _client(app) as c:
            own = await c.patch(_url(env, "guest_msg"), json={"content": "guest edit"})
            theirs = await c.patch(_url(env, "mine"), json={"content": "guest hijack"})
    finally:
        app.dependency_overrides.pop(get_current_identity, None)
    assert own.status_code == 200, own.text
    assert own.json()["content"] == "guest edit"
    assert theirs.status_code == 403


async def test_blank_edit_is_rejected(env):
    headers = _headers(env, "author", "author@x.test")
    async with await _client(env["app"]) as c:
        res = await c.patch(_url(env, "mine"), headers=headers, json={"content": "   "})
    assert res.status_code in (400, 422)


async def test_unknown_or_cross_room_message_is_404(env):
    headers = _headers(env, "author", "author@x.test")
    async with await _client(env["app"]) as c:
        res = await c.patch(
            f"/api/v1/rooms/{env['room']}/messages/{uuid4()}",
            headers=headers,
            json={"content": "x"},
        )
    assert res.status_code == 404
