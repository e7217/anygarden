"""REST and WebSocket regression contract for Phase 2 message threads.

The tests deliberately use the public write/read/replay/search surfaces.  They
are authored against the Phase 2 contract, so this module is expected to fail
on the pre-thread ``main`` baseline and becomes executable when task #20's
implementation head is integrated.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from anygarden.app import create_app
from anygarden.auth.jwt import create_user_token
from anygarden.config import AnygardenSettings
from anygarden.db.engine import build_engine, build_session_factory
from anygarden.db.fts import create_message_fts
from anygarden.db.models import (
    ActivityLog,
    Agent,
    Base,
    Message,
    Participant,
    Project,
    Room,
    User,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture()
async def thread_env(tmp_path: Path) -> AsyncIterator[dict]:
    """Two private rooms with member, observer, and outsider identities."""

    config = AnygardenSettings(
        # The final regression mixes TestClient's portal thread with async
        # fixture helpers. A file-backed DB keeps both connections on the same
        # database; an in-memory URL can allocate one database per connection.
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'threads.db'}",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await create_message_fts(conn)

    async with factory() as db:
        project = Project(name="thread-regression-project")
        room_a = Room(project=project, name="thread-room-a")
        room_b = Room(project=project, name="thread-room-b")
        owner = User(email="thread-owner@example.test", password_hash="x")
        member = User(email="thread-member@example.test", password_hash="x")
        observer = User(email="thread-observer@example.test", password_hash="x")
        outsider = User(email="thread-outsider@example.test", password_hash="x")
        agent_a = Agent(name="thread-agent-a", engine="codex")
        agent_b = Agent(name="thread-agent-b", engine="codex")
        db.add_all(
            [
                project,
                room_a,
                room_b,
                owner,
                member,
                observer,
                outsider,
                agent_a,
                agent_b,
            ]
        )
        await db.flush()

        participants = {
            "owner": Participant(room_id=room_a.id, user_id=owner.id, role="owner"),
            "member_a": Participant(
                room_id=room_a.id,
                user_id=member.id,
                role="member",
            ),
            # The same member can demonstrate a cross-room root rejection
            # without turning the failure into a membership denial.
            "member_b": Participant(
                room_id=room_b.id,
                user_id=member.id,
                role="member",
            ),
            "observer": Participant(
                room_id=room_a.id,
                user_id=observer.id,
                role="observer",
            ),
            "agent_a": Participant(
                room_id=room_a.id,
                agent_id=agent_a.id,
                role="member",
            ),
            "agent_b": Participant(
                room_id=room_a.id,
                agent_id=agent_b.id,
                role="member",
            ),
        }
        db.add_all(participants.values())
        await db.commit()

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = factory

        def token(user: User) -> str:
            return create_user_token(
                user.id,
                user.email or "",
                user.is_admin,
                secret=config.jwt_secret,
            )

        yield {
            "app": app,
            "factory": factory,
            "room_a": room_a.id,
            "room_b": room_b.id,
            "member_a_participant": participants["member_a"].id,
            "agent_ids": {"a": agent_a.id, "b": agent_b.id},
            "agent_participant_ids": {
                "a": participants["agent_a"].id,
                "b": participants["agent_b"].id,
            },
            "tokens": {
                "owner": token(owner),
                "member": token(member),
                "observer": token(observer),
                "outsider": token(outsider),
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


async def _remove_participant(factory, participant_id: str) -> None:
    async with factory() as db:
        participant = await db.get(Participant, participant_id)
        assert participant is not None
        await db.delete(participant)
        await db.commit()


async def _message_received_events(factory, message_ids: set[str]) -> list[ActivityLog]:
    async with factory() as db:
        rows = await db.scalars(
            select(ActivityLog).where(ActivityLog.event_type == "message_received")
        )
        return [
            row
            for row in rows.all()
            if (row.details or {}).get("trigger_message_id") in message_ids
        ]


async def _create_root(
    client: AsyncClient, room_id: str, token: str, content: str
) -> dict:
    response = await client.post(
        f"/api/v1/rooms/{room_id}/messages",
        json={"content": content},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_reply(
    client: AsyncClient,
    room_id: str,
    root_id: str,
    token: str,
    content: str,
) -> dict:
    response = await client.post(
        f"/api/v1/rooms/{room_id}/threads/{root_id}/messages",
        json={"content": content},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.asyncio
async def test_thread_rest_history_pagination_and_search_identify_root(
    thread_env: dict,
) -> None:
    """All read surfaces retain canonical root identity across a page boundary."""

    transport = ASGITransport(app=thread_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        root = await _create_root(
            client,
            thread_env["room_a"],
            thread_env["tokens"]["member"],
            "thread root",
        )
        reply = await _create_reply(
            client,
            thread_env["room_a"],
            root["id"],
            thread_env["tokens"]["member"],
            "threadreplyneedle",
        )

        assert root["parent_message_id"] is None
        assert root["root_message_id"] is None
        assert reply["parent_message_id"] == root["id"]
        assert reply["root_message_id"] == root["id"]
        assert reply["seq"] == root["seq"] + 1

        legacy = await client.get(
            f"/api/v1/rooms/{thread_env['room_a']}/messages",
            headers=_auth(thread_env["tokens"]["member"]),
        )
        roots = await client.get(
            f"/api/v1/rooms/{thread_env['room_a']}/thread-roots",
            headers=_auth(thread_env["tokens"]["member"]),
        )
        replies = await client.get(
            f"/api/v1/rooms/{thread_env['room_a']}/threads/{root['id']}/messages",
            headers=_auth(thread_env["tokens"]["member"]),
        )
        cursor_page = await client.get(
            f"/api/v1/rooms/{thread_env['room_a']}/messages",
            params={"since_seq": root["seq"], "limit": 1},
            headers=_auth(thread_env["tokens"]["member"]),
        )
        search = await client.get(
            "/api/v1/search",
            params={"q": "threadreplyneedle"},
            headers=_auth(thread_env["tokens"]["member"]),
        )

    assert legacy.status_code == 200, legacy.text
    assert [item["id"] for item in legacy.json()] == [root["id"], reply["id"]]
    assert roots.status_code == 200, roots.text
    assert [item["id"] for item in roots.json()] == [root["id"]]
    assert replies.status_code == 200, replies.text
    assert [item["id"] for item in replies.json()] == [reply["id"]]
    assert cursor_page.status_code == 200, cursor_page.text
    assert cursor_page.json()[0]["id"] == reply["id"]
    assert cursor_page.json()[0]["root_message_id"] == root["id"]
    assert search.status_code == 200, search.text
    assert search.json()[0]["message_id"] == reply["id"]
    assert search.json()[0]["parent_message_id"] == root["id"]
    assert search.json()[0]["root_message_id"] == root["id"]
    assert search.json()[0]["seq"] == reply["seq"]


@pytest.mark.asyncio
async def test_cross_room_and_reply_parent_are_rejected_without_append(
    thread_env: dict,
) -> None:
    """A root is room-local and the public reply endpoint is single-depth."""

    transport = ASGITransport(app=thread_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        root_a = await _create_root(
            client,
            thread_env["room_a"],
            thread_env["tokens"]["member"],
            "root a",
        )
        root_b = await _create_root(
            client,
            thread_env["room_b"],
            thread_env["tokens"]["member"],
            "root b",
        )
        before = await _message_count(thread_env["factory"], thread_env["room_a"])

        cross_room = await client.post(
            f"/api/v1/rooms/{thread_env['room_a']}/threads/{root_b['id']}/messages",
            json={"content": "must not attach"},
            headers=_auth(thread_env["tokens"]["member"]),
        )
        reply = await _create_reply(
            client,
            thread_env["room_a"],
            root_a["id"],
            thread_env["tokens"]["member"],
            "valid first-level reply",
        )
        nested = await client.post(
            f"/api/v1/rooms/{thread_env['room_a']}/threads/{reply['id']}/messages",
            json={"content": "must not nest"},
            headers=_auth(thread_env["tokens"]["member"]),
        )

    # The Phase 2 contract permits 400 (known malformed shape) or 404 (a
    # non-root/cross-room parent that must not be resolved as a usable root),
    # but neither path may create a room-A message.
    assert cross_room.status_code in {400, 404}, cross_room.text
    assert nested.status_code in {400, 404}, nested.text
    assert (
        await _message_count(thread_env["factory"], thread_env["room_a"]) == before + 1
    )


@pytest.mark.asyncio
async def test_thread_reply_honors_private_observer_and_archive_boundaries(
    thread_env: dict,
) -> None:
    """Replies use the normal room send capability, not a weaker side door."""

    transport = ASGITransport(app=thread_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        root = await _create_root(
            client,
            thread_env["room_a"],
            thread_env["tokens"]["owner"],
            "owner root",
        )
        observer = await client.post(
            f"/api/v1/rooms/{thread_env['room_a']}/threads/{root['id']}/messages",
            json={"content": "observer reply"},
            headers=_auth(thread_env["tokens"]["observer"]),
        )
        outsider_read = await client.get(
            f"/api/v1/rooms/{thread_env['room_a']}/threads/{root['id']}/messages",
            headers=_auth(thread_env["tokens"]["outsider"]),
        )
        archived = await client.post(
            f"/api/v1/rooms/{thread_env['room_a']}/archive",
            headers=_auth(thread_env["tokens"]["owner"]),
        )
        archived_reply = await client.post(
            f"/api/v1/rooms/{thread_env['room_a']}/threads/{root['id']}/messages",
            json={"content": "archived reply"},
            headers=_auth(thread_env["tokens"]["member"]),
        )

    assert observer.status_code == 403, observer.text
    assert outsider_read.status_code == 403, outsider_read.text
    assert archived.status_code == 200, archived.text
    assert archived_reply.status_code == 409, archived_reply.text
    assert await _message_count(thread_env["factory"], thread_env["room_a"]) == 1


def test_thread_reply_agent_scheduling_is_mention_targeted(thread_env: dict) -> None:
    """Room-wide reply fanout creates a turn only for mentioned agents."""

    from starlette.testclient import TestClient

    room_id = thread_env["room_a"]
    member_token = thread_env["tokens"]["member"]
    owner_token = thread_env["tokens"]["owner"]
    target_pid = thread_env["agent_participant_ids"]["b"]

    with TestClient(thread_env["app"]) as client:
        root_response = client.post(
            f"/api/v1/rooms/{room_id}/messages",
            json={"content": "scheduling root"},
            headers=_auth(owner_token),
        )
        assert root_response.status_code == 201, root_response.text
        root = root_response.json()

        with client.websocket_connect(
            f"/ws/rooms/{room_id}",
            subprotocols=["anygarden.v1", f"bearer.{member_token}"],
        ) as ws:
            assert json.loads(ws.receive_text())["type"] == "welcome"
            ws.send_text(
                json.dumps(
                    {
                        "type": "send",
                        "content": "passive thread context",
                        "thread_root_id": root["id"],
                    }
                )
            )
            passive_reply = json.loads(ws.receive_text())
            assert passive_reply["type"] == "message"

            ws.send_text(
                json.dumps(
                    {
                        "type": "send",
                        "content": f"<@user:{target_pid}> please inspect",
                        "thread_root_id": root["id"],
                    }
                )
            )
            targeted_reply = json.loads(ws.receive_text())
            assert targeted_reply["type"] == "message"

    events = asyncio.run(
        _message_received_events(
            thread_env["factory"],
            {passive_reply["id"], targeted_reply["id"]},
        )
    )
    assert len(events) == 1
    assert events[0].agent_id == thread_env["agent_ids"]["b"]
    assert events[0].details["trigger_message_id"] == targeted_reply["id"]


def test_websocket_reply_replay_and_removed_member_fresh_gate(thread_env: dict) -> None:
    """WS uses the same root identity and revokes a removed sender per frame."""

    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    room_id = thread_env["room_a"]
    member_token = thread_env["tokens"]["member"]
    owner_token = thread_env["tokens"]["owner"]

    with TestClient(thread_env["app"]) as client:
        root_response = client.post(
            f"/api/v1/rooms/{room_id}/messages",
            json={"content": "ws root"},
            headers=_auth(owner_token),
        )
        assert root_response.status_code == 201, root_response.text
        root = root_response.json()

        with client.websocket_connect(
            f"/ws/rooms/{room_id}",
            subprotocols=["anygarden.v1", f"bearer.{member_token}"],
        ) as ws:
            assert json.loads(ws.receive_text())["type"] == "welcome"
            ws.send_text(
                json.dumps(
                    {
                        "type": "send",
                        "content": "ws thread reply",
                        "thread_root_id": root["id"],
                    }
                )
            )
            reply = json.loads(ws.receive_text())
            assert reply["type"] == "message"
            assert reply["parent_message_id"] == root["id"]
            assert reply["root_message_id"] == root["id"]
            assert reply["seq"] == root["seq"] + 1

        # Legacy replay remains a room-wide log. A cursor between root and
        # reply must still give the client the reply's root identity.
        with client.websocket_connect(
            f"/ws/rooms/{room_id}?since_seq={root['seq']}",
            subprotocols=["anygarden.v1", f"bearer.{member_token}"],
        ) as replay_ws:
            assert json.loads(replay_ws.receive_text())["type"] == "welcome"
            replayed_reply = json.loads(replay_ws.receive_text())
            assert replayed_reply["id"] == reply["id"]
            assert replayed_reply["parent_message_id"] == root["id"]
            assert replayed_reply["root_message_id"] == root["id"]

        with client.websocket_connect(
            f"/ws/rooms/{room_id}",
            subprotocols=["anygarden.v1", f"bearer.{member_token}"],
        ) as removed_ws:
            assert json.loads(removed_ws.receive_text())["type"] == "welcome"
            asyncio.run(
                _remove_participant(
                    thread_env["factory"],
                    thread_env["member_a_participant"],
                )
            )
            removed_ws.send_text(
                json.dumps(
                    {
                        "type": "send",
                        "content": "must not persist after removal",
                        "thread_root_id": root["id"],
                    }
                )
            )
            with pytest.raises(WebSocketDisconnect) as exc:
                removed_ws.receive_text()
            assert exc.value.code == 4003

    assert asyncio.run(_message_count(thread_env["factory"], room_id)) == 2
