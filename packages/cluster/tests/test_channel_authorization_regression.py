"""End-to-end regressions for the Phase 1 room authorization contract.

These tests deliberately exercise HTTP and WebSocket boundaries rather than
calling the authorization service directly.  Unit coverage for the policy
matrix lives in ``test_room_authorization.py``; this suite proves callers do
not leak a private room through a forgotten read/write path or retain stale
WebSocket authority.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.auth.token import generate_token, hash_agent_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.fts import create_message_fts
from anygarden.db.models import (
    Agent,
    AgentToken,
    Base,
    Message,
    Participant,
    Project,
    Room,
    SavedMessage,
    Task,
    User,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture()
async def authorization_env() -> AsyncIterator[dict]:
    """App plus a private room containing every supported role.

    The outsider deliberately owns a stale saved-message row for the private
    room.  It models a user removed from a room after bookmarking a message
    and keeps the saved-list disclosure check independent from UI flows.
    """

    config = AnygardenSettings(
        db_url="sqlite+aiosqlite://",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await create_message_fts(conn)

    async with factory() as db:
        project = Project(name="authorization-project")
        room = Room(project=project, name="private-room")
        child_room = Room(
            project=project,
            name="private-child",
            parent_room=room,
        )
        owner = User(email="owner@authorization.test", password_hash="x")
        room_admin = User(email="admin@authorization.test", password_hash="x")
        member = User(email="member@authorization.test", password_hash="x")
        observer = User(email="observer@authorization.test", password_hash="x")
        outsider = User(email="outsider@authorization.test", password_hash="x")
        global_admin = User(
            email="global-admin@authorization.test",
            password_hash="x",
            is_admin=True,
        )
        self_agent = Agent(name="self-agent", engine="echo")
        other_agent = Agent(name="other-agent", engine="echo")
        db.add_all(
            [
                project,
                room,
                child_room,
                owner,
                room_admin,
                member,
                observer,
                outsider,
                global_admin,
                self_agent,
                other_agent,
            ]
        )
        await db.flush()

        participants = {
            "owner": Participant(room_id=room.id, user_id=owner.id, role="owner"),
            "admin": Participant(room_id=room.id, user_id=room_admin.id, role="admin"),
            "member": Participant(room_id=room.id, user_id=member.id, role="member"),
            "observer": Participant(
                room_id=room.id,
                user_id=observer.id,
                role="observer",
            ),
            # An agent's stored role must not grant more than member access.
            "self_agent": Participant(
                room_id=room.id,
                agent_id=self_agent.id,
                role="owner",
            ),
            "other_agent": Participant(
                room_id=room.id,
                agent_id=other_agent.id,
                role="member",
            ),
            "child_member": Participant(
                room_id=child_room.id,
                user_id=member.id,
                role="member",
            ),
        }
        db.add_all(participants.values())
        await db.flush()

        message = Message(
            room_id=room.id,
            participant_id=participants["owner"].id,
            content="authorizationneedle",
            seq=1,
        )
        own_task = Task(
            room_id=room.id,
            title="self agent task",
            assignee_participant_id=participants["self_agent"].id,
        )
        other_task = Task(
            room_id=room.id,
            title="other agent task",
            assignee_participant_id=participants["other_agent"].id,
        )
        db.add_all([message, own_task, other_task])
        await db.flush()
        db.add(SavedMessage(user_id=outsider.id, message_id=message.id))

        self_agent_token = generate_token()
        self_hash, self_hint = hash_agent_token(self_agent_token)
        db.add(
            AgentToken(
                agent_id=self_agent.id,
                token_hash=self_hash,
                lookup_hint=self_hint,
            )
        )
        await db.commit()

        app = create_app(config)
        # The production lifespan reuses pre-populated test state.
        app.state.engine = engine
        app.state.session_factory = factory

        def user_token(user: User) -> str:
            return create_user_token(
                user.id,
                user.email or "",
                user.is_admin,
                secret=config.jwt_secret,
            )

        yield {
            "app": app,
            "factory": factory,
            "project_id": project.id,
            "room_id": room.id,
            "child_room_id": child_room.id,
            "message_id": message.id,
            "own_task_id": own_task.id,
            "other_task_id": other_task.id,
            "tokens": {
                "owner": user_token(owner),
                "admin": user_token(room_admin),
                "member": user_token(member),
                "observer": user_token(observer),
                "outsider": user_token(outsider),
                "global_admin": user_token(global_admin),
                "self_agent": self_agent_token,
            },
        }

    await engine.dispose()


async def _message_count(factory, room_id: str) -> int:
    async with factory() as db:
        return int(
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(Message.room_id == room_id)
            )
            or 0
        )


@pytest.mark.asyncio
async def test_private_room_is_hidden_from_all_nonmember_rest_paths(
    authorization_env: dict,
) -> None:
    """Direct IDs, collections, FTS, and stale saves must not disclose data."""

    app = authorization_env["app"]
    room_id = authorization_env["room_id"]
    project_id = authorization_env["project_id"]
    message_id = authorization_env["message_id"]
    outsider_headers = _auth(authorization_env["tokens"]["outsider"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        rooms = await client.get(
            "/api/v1/rooms",
            params={"project_id": project_id},
            headers=outsider_headers,
        )
        assert rooms.status_code == 200
        assert rooms.json() == []

        for path in (
            f"/api/v1/rooms/{room_id}",
            f"/api/v1/rooms/{room_id}/messages",
            f"/api/v1/rooms/{room_id}/tasks",
            f"/api/v1/rooms/{room_id}/sub-rooms",
        ):
            response = await client.get(path, headers=outsider_headers)
            assert response.status_code == 403, (path, response.text)

        search = await client.get(
            "/api/v1/search",
            params={"q": "authorizationneedle"},
            headers=outsider_headers,
        )
        assert search.status_code == 200, search.text
        assert search.json() == []

        saved = await client.get("/api/v1/saved", headers=outsider_headers)
        assert saved.status_code == 200
        assert saved.json() == []

        save = await client.post(
            "/api/v1/saved",
            json={"message_id": message_id},
            headers=outsider_headers,
        )
        assert save.status_code == 403

        # Operators are intentionally discoverable, so only this identity
        # gets the conventional missing-resource response.
        missing = await client.get(
            "/api/v1/rooms/not-a-real-room",
            headers=_auth(authorization_env["tokens"]["global_admin"]),
        )
        assert missing.status_code == 404


@pytest.mark.asyncio
async def test_roles_and_agent_task_update_scope_are_enforced_over_rest(
    authorization_env: dict,
) -> None:
    app = authorization_env["app"]
    room_id = authorization_env["room_id"]
    observer_headers = _auth(authorization_env["tokens"]["observer"])
    member_headers = _auth(authorization_env["tokens"]["member"])
    agent_headers = _auth(authorization_env["tokens"]["self_agent"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # An observer gets room content but cannot turn that read access into
        # work or room lifecycle authority.
        assert (
            await client.get(
                f"/api/v1/rooms/{room_id}/messages", headers=observer_headers
            )
        ).status_code == 200
        assert (
            await client.get(f"/api/v1/rooms/{room_id}/tasks", headers=observer_headers)
        ).status_code == 200
        assert (
            await client.post(
                f"/api/v1/rooms/{room_id}/tasks",
                json={"title": "observer must not create"},
                headers=observer_headers,
            )
        ).status_code == 403
        assert (
            await client.post(
                f"/api/v1/rooms/{room_id}/archive", headers=observer_headers
            )
        ).status_code == 403

        # A member can create ordinary work, but agent task updates are
        # intentionally narrower than the member's stored Participant role.
        created = await client.post(
            f"/api/v1/rooms/{room_id}/tasks",
            json={"title": "member may create"},
            headers=member_headers,
        )
        assert created.status_code == 201, created.text

        own_update = await client.put(
            f"/api/v1/tasks/{authorization_env['own_task_id']}",
            json={"status": "in_progress"},
            headers=agent_headers,
        )
        assert own_update.status_code == 200, own_update.text
        assert own_update.json()["status"] == "in_progress"

        for task_id, payload in (
            (authorization_env["own_task_id"], {"title": "not allowed"}),
            (authorization_env["other_task_id"], {"status": "in_progress"}),
        ):
            denied = await client.put(
                f"/api/v1/tasks/{task_id}",
                json=payload,
                headers=agent_headers,
            )
            assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
async def test_archive_cascades_to_child_and_converts_writes_to_409(
    authorization_env: dict,
) -> None:
    app = authorization_env["app"]
    room_id = authorization_env["room_id"]
    child_room_id = authorization_env["child_room_id"]
    owner_headers = _auth(authorization_env["tokens"]["owner"])
    member_headers = _auth(authorization_env["tokens"]["member"])

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        archived = await client.post(
            f"/api/v1/rooms/{room_id}/archive", headers=owner_headers
        )
        assert archived.status_code == 200, archived.text
        assert archived.json()["visibility"] == "private"
        assert archived.json()["archived_at"] is not None

        # Existing members retain read access, but every mutation on both
        # parent and child is rejected by the shared active-room gate.
        assert (
            await client.get(f"/api/v1/rooms/{room_id}", headers=member_headers)
        ).status_code == 200
        assert (
            await client.get(
                f"/api/v1/rooms/{child_room_id}/tasks", headers=member_headers
            )
        ).status_code == 200
        for target_room_id in (room_id, child_room_id):
            blocked = await client.post(
                f"/api/v1/rooms/{target_room_id}/tasks",
                json={"title": "archived write"},
                headers=member_headers,
            )
            assert blocked.status_code == 409, blocked.text

    async with authorization_env["factory"]() as db:
        child = await db.get(Room, child_room_id)
        assert child is not None
        assert child.archived_at is not None


def test_websocket_nonmember_and_observer_writes_do_not_leak_events(
    authorization_env: dict,
) -> None:
    """A denied socket gets no welcome/message frame and cannot persist a send."""

    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app = authorization_env["app"]
    room_id = authorization_env["room_id"]

    with TestClient(app) as client:
        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=[
                    "anygarden.v1",
                    f"bearer.{authorization_env['tokens']['outsider']}",
                ],
            ),
        ):
            pytest.fail("a nonmember must not receive a WS welcome frame")
        assert exc.value.code == 4003

        with client.websocket_connect(
            f"/ws/rooms/{room_id}",
            subprotocols=[
                "anygarden.v1",
                f"bearer.{authorization_env['tokens']['observer']}",
            ],
        ) as observer_ws:
            assert json.loads(observer_ws.receive_text())["type"] == "welcome"
            observer_ws.send_text(json.dumps({"type": "send", "content": "blocked"}))
            with pytest.raises(WebSocketDisconnect) as exc:
                observer_ws.receive_text()
            assert exc.value.code == 4003

    # The rejected observer frame created neither a message nor a broadcastable
    # event.  (One seeded message remains.)
    assert asyncio.run(_message_count(authorization_env["factory"], room_id)) == 1


def test_global_admin_rest_bypass_does_not_create_websocket_identity(
    authorization_env: dict,
) -> None:
    """REST inspection is privileged; WS still needs a Participant identity."""

    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    room_id = authorization_env["room_id"]
    token = authorization_env["tokens"]["global_admin"]
    with TestClient(authorization_env["app"]) as client:
        room = client.get(
            f"/api/v1/rooms/{room_id}",
            headers=_auth(token),
        )
        assert room.status_code == 200

        with (
            pytest.raises(WebSocketDisconnect) as exc,
            client.websocket_connect(
                f"/ws/rooms/{room_id}",
                subprotocols=["anygarden.v1", f"bearer.{token}"],
            ),
        ):
            pytest.fail("a nonmember global admin must not receive a WS welcome")
        assert exc.value.code == 4003


def test_archive_immediately_revokes_open_websocket(
    authorization_env: dict,
) -> None:
    """Archive closes an already-welcomed member socket with code 4003."""

    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app = authorization_env["app"]
    room_id = authorization_env["room_id"]

    with (
        TestClient(app) as client,
        client.websocket_connect(
            f"/ws/rooms/{room_id}",
            subprotocols=[
                "anygarden.v1",
                f"bearer.{authorization_env['tokens']['member']}",
            ],
        ) as member_ws,
    ):
        assert json.loads(member_ws.receive_text())["type"] == "welcome"
        archived = client.post(
            f"/api/v1/rooms/{room_id}/archive",
            headers=_auth(authorization_env["tokens"]["owner"]),
        )
        assert archived.status_code == 200, archived.text
        with pytest.raises(WebSocketDisconnect) as exc:
            member_ws.receive_text()
        assert exc.value.code == 4003
