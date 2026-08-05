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
from anygarden.auth.dependencies import Identity
from anygarden.auth.jwt import UserClaims, create_user_token
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
    RoomAuthorizationAudit,
    SavedMessage,
    Task,
    User,
)
from anygarden.dependencies import forbid_guest, get_db
from anygarden.rooms.authorization import (
    Capability,
    require_capability,
    resolve_access,
)
from anygarden.ws.handler import _require_fresh_frame_access
from anygarden.ws.protocol import SendFrame
from fastapi import Depends, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine


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
            "global_admin_id": global_admin.id,
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


async def _authorization_audits(factory) -> list[RoomAuthorizationAudit]:
    async with factory() as db:
        return (
            await db.scalars(
                select(RoomAuthorizationAudit).order_by(
                    RoomAuthorizationAudit.created_at,
                    RoomAuthorizationAudit.id,
                )
            )
        ).all()


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
async def test_global_admin_bypasses_are_audited_but_role_grants_are_not(
    authorization_env: dict,
) -> None:
    """Single-room and collection/search bypasses leave durable evidence."""

    token = authorization_env["tokens"]["global_admin"]
    headers = _auth(token)
    transport = ASGITransport(app=authorization_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        room = await client.get(
            f"/api/v1/rooms/{authorization_env['room_id']}",
            headers=headers,
        )
        rooms = await client.get(
            "/api/v1/rooms",
            params={"project_id": authorization_env["project_id"]},
            headers=headers,
        )
        search = await client.get(
            "/api/v1/search",
            params={"q": "authorizationneedle"},
            headers=headers,
        )
    assert room.status_code == 200
    assert rooms.status_code == 200
    assert search.status_code == 200

    async with authorization_env["factory"]() as db:
        audits = (
            await db.scalars(
                select(RoomAuthorizationAudit).order_by(
                    RoomAuthorizationAudit.created_at,
                    RoomAuthorizationAudit.id,
                )
            )
        ).all()
        assert len(audits) == 3
        by_scope = {audit.scope: audit for audit in audits}
        assert set(by_scope) == {"room", "rooms.collection", "search.messages"}
        assert all(
            audit.actor_user_id == authorization_env["global_admin_id"]
            for audit in audits
        )
        assert all(audit.capability == "room.read" for audit in audits)
        assert all(audit.outcome == "allowed" for audit in audits)
        assert by_scope["room"].room_id == authorization_env["room_id"]
        assert by_scope["rooms.collection"].room_id is None
        assert by_scope["search.messages"].room_id is None
        assert by_scope["rooms.collection"].details == {
            "bypassed_room_count": 2,
            "visible_room_count": 2,
        }

        # Membership in every room makes these ordinary allowed reads rather
        # than operator bypasses, so the audit stream must stay unchanged.
        db.add_all(
            [
                Participant(
                    room_id=authorization_env["room_id"],
                    user_id=authorization_env["global_admin_id"],
                    role="member",
                ),
                Participant(
                    room_id=authorization_env["child_room_id"],
                    user_id=authorization_env["global_admin_id"],
                    role="member",
                ),
            ]
        )
        await db.commit()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (
            await client.get(
                f"/api/v1/rooms/{authorization_env['room_id']}",
                headers=headers,
            )
        ).status_code == 200
        assert (
            await client.get(
                "/api/v1/rooms",
                params={"project_id": authorization_env["project_id"]},
                headers=headers,
            )
        ).status_code == 200
        assert (
            await client.get(
                "/api/v1/search",
                params={"q": "authorizationneedle"},
                headers=headers,
            )
        ).status_code == 200

    async with authorization_env["factory"]() as db:
        assert len((await db.scalars(select(RoomAuthorizationAudit))).all()) == 3


@pytest.mark.asyncio
async def test_failed_global_admin_write_audits_nonmember_and_role_delta(
    authorization_env: dict,
) -> None:
    """A downstream 404 cannot roll back either kind of operator grant."""

    app = authorization_env["app"]
    app.state.machine_bus = object()
    headers = _auth(authorization_env["tokens"]["global_admin"])
    room_id = authorization_env["room_id"]
    failed_task_id = "failed-business-task"

    @app.post("/_test/authorization/audit-rollback")
    async def fail_after_bypass(
        identity: Identity = Depends(forbid_guest),
        db: AsyncSession = Depends(get_db),
    ) -> None:
        await require_capability(
            db,
            room_id=room_id,
            identity=identity,
            capability=Capability.FILE_MANAGE,
        )
        db.add(Task(id=failed_task_id, room_id=room_id, title="must rollback"))
        await db.flush()
        raise HTTPException(status_code=404, detail="downstream failure")

    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        nonmember_failure = await client.post(
            "/_test/authorization/audit-rollback",
            headers=headers,
        )
    assert nonmember_failure.status_code == 404

    async with authorization_env["factory"]() as db:
        db.add(
            Participant(
                room_id=room_id,
                user_id=authorization_env["global_admin_id"],
                role="member",
            )
        )
        await db.commit()

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        role_delta_failure = await client.delete(
            f"/api/v1/rooms/{room_id}/files/missing-role-delta",
            headers=headers,
        )
        role_granted_read = await client.get(
            f"/api/v1/rooms/{room_id}",
            headers=headers,
        )
    assert role_delta_failure.status_code == 404
    assert role_granted_read.status_code == 200

    async with authorization_env["factory"]() as db:
        audits = (
            await db.scalars(
                select(RoomAuthorizationAudit).order_by(
                    RoomAuthorizationAudit.created_at,
                    RoomAuthorizationAudit.id,
                )
            )
        ).all()
        assert len(audits) == 2
        assert all(
            audit.actor_user_id == authorization_env["global_admin_id"]
            for audit in audits
        )
        assert all(audit.room_id == room_id for audit in audits)
        assert all(audit.scope == "room" for audit in audits)
        assert all(audit.capability == "file.manage" for audit in audits)
        assert all(audit.outcome == "allowed" for audit in audits)
        assert await db.scalar(select(Task).where(Task.id == failed_task_id)) is None


@pytest.mark.asyncio
async def test_ws_fresh_gate_persists_global_admin_role_delta_audit(
    authorization_env: dict,
) -> None:
    """Raw WS authorization sessions share the durable audit teardown."""

    room_id = authorization_env["room_id"]
    admin_id = authorization_env["global_admin_id"]
    async with authorization_env["factory"]() as db:
        db.add(Participant(room_id=room_id, user_id=admin_id, role="observer"))
        await db.commit()

    identity = Identity(
        kind="user",
        id=admin_id,
        claims=UserClaims(
            user_id=admin_id,
            email="global-admin@authorization.test",
            is_admin=True,
        ),
    )
    access = await _require_fresh_frame_access(
        authorization_env["factory"],
        room_id=room_id,
        identity=identity,
        frame=SendFrame(content="operator role delta"),
    )
    assert access.effective_role == "observer"

    async with authorization_env["factory"]() as db:
        audit = (await db.scalars(select(RoomAuthorizationAudit))).one()
        assert audit.actor_user_id == admin_id
        assert audit.room_id == room_id
        assert audit.scope == "room"
        assert audit.capability == "message.send"
        assert audit.outcome == "allowed"


@pytest.mark.asyncio
async def test_audit_flush_releases_callers_before_second_pool_checkout(
    tmp_path,
) -> None:
    """Pool-sized concurrent bypasses cannot deadlock on nested checkouts."""

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'authorization-pool.db'}"
    config = AnygardenSettings(
        db_url=db_url,
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = create_async_engine(
        db_url,
        connect_args={"check_same_thread": False},
        pool_size=2,
        max_overflow=0,
        pool_timeout=1,
    )
    factory = build_session_factory(engine)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async with factory() as db:
            project = Project(name="audit-pool-project")
            room = Room(project=project, name="audit-pool-room")
            admin = User(
                email="audit-pool-admin@example.test",
                password_hash="x",
                is_admin=True,
            )
            db.add_all([project, room, admin])
            await db.commit()
            room_id = room.id
            token = create_user_token(
                admin.id,
                admin.email or "",
                True,
                secret=config.jwt_secret,
            )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = factory
        entered = 0
        entered_lock = asyncio.Lock()
        all_callers_hold_connections = asyncio.Event()

        @app.get("/_test/authorization/pool-capacity")
        async def bypass_at_pool_capacity(
            identity: Identity = Depends(forbid_guest),
            db: AsyncSession = Depends(get_db),
        ) -> dict[str, bool]:
            nonlocal entered
            await resolve_access(db, room_id=room_id, identity=identity)
            async with entered_lock:
                entered += 1
                if entered == 2:
                    all_callers_hold_connections.set()
            await asyncio.wait_for(all_callers_hold_connections.wait(), timeout=2)
            await require_capability(
                db,
                room_id=room_id,
                identity=identity,
                capability=Capability.ROOM_READ,
            )
            return {"ok": True}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            responses = await asyncio.wait_for(
                asyncio.gather(
                    client.get(
                        "/_test/authorization/pool-capacity",
                        headers=_auth(token),
                    ),
                    client.get(
                        "/_test/authorization/pool-capacity",
                        headers=_auth(token),
                    ),
                ),
                timeout=5,
            )
        assert [response.status_code for response in responses] == [200, 200]

        async with factory() as db:
            audits = (await db.scalars(select(RoomAuthorizationAudit))).all()
            assert len(audits) == 2
            assert all(audit.actor_user_id == admin.id for audit in audits)
            assert all(audit.room_id == room_id for audit in audits)
            assert all(audit.scope == "room" for audit in audits)
            assert all(audit.capability == "room.read" for audit in audits)
            assert all(audit.outcome == "allowed" for audit in audits)
    finally:
        await engine.dispose()


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

    audits = asyncio.run(_authorization_audits(authorization_env["factory"]))
    assert len(audits) == 2
    assert all(
        audit.actor_user_id == authorization_env["global_admin_id"] for audit in audits
    )
    assert all(audit.room_id == room_id for audit in audits)
    assert all(audit.capability == "room.read" for audit in audits)
    assert all(audit.outcome == "allowed" for audit in audits)


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
