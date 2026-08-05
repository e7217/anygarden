"""Phase 3 contract regressions for message-linked tasks and CAS claims.

The scenarios in this file deliberately stay at the public REST/WS boundary.
They are authored against the Phase 3 contract, rather than the pre-Phase-3
``main`` baseline: task creation from a message is one-to-one, claim is an
atomic compare-and-swap, and an assignment wake is a reply on the source
thread.  Unit coverage for the service's SQL predicate belongs beside the
service; these cases keep API, lifecycle, and replay callers honest.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

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
    AgentTurnTask,
    Base,
    Message,
    Participant,
    Project,
    Room,
    Task,
    User,
)
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture()
async def claim_env(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    """A file-backed app with two human claimers and two agent claimers.

    File storage is intentional: the concurrent REST clients and the
    synchronous WebSocket portal below must share one durable database.
    """

    config = AnygardenSettings(
        db_url=f"sqlite+aiosqlite:///{tmp_path / 'task-claims.db'}",
        jwt_secret=secrets.token_urlsafe(32),
        log_level="DEBUG",
    )
    engine = build_engine(config.db_url)
    factory = build_session_factory(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await create_message_fts(conn)

    async with factory() as db:
        project = Project(name="task-claim-regression-project")
        room = Room(
            project=project,
            name="task-claim-room",
            # The contract permits a member to self-claim only in rooms
            # explicitly opting into human assignment.
            allow_human_assignment=True,
        )
        other_room = Room(project=project, name="other-task-claim-room")
        owner = User(email="task-owner@example.test", password_hash="x")
        member = User(email="task-member@example.test", password_hash="x")
        outsider = User(email="task-outsider@example.test", password_hash="x")
        agent_a = Agent(name="claim-agent-a", engine="echo")
        agent_b = Agent(name="claim-agent-b", engine="echo")
        db.add_all([project, room, other_room, owner, member, outsider, agent_a, agent_b])
        await db.flush()

        participants = {
            "owner": Participant(room_id=room.id, user_id=owner.id, role="owner"),
            "member": Participant(room_id=room.id, user_id=member.id, role="member"),
            "member_other": Participant(
                room_id=other_room.id, user_id=member.id, role="member"
            ),
            "agent_a": Participant(room_id=room.id, agent_id=agent_a.id, role="member"),
            "agent_b": Participant(room_id=room.id, agent_id=agent_b.id, role="member"),
        }
        db.add_all(participants.values())
        await db.flush()

        agent_tokens: dict[str, str] = {}
        for name, agent in (("agent_a", agent_a), ("agent_b", agent_b)):
            plain = generate_token()
            token_hash, hint = hash_agent_token(plain)
            db.add(AgentToken(agent_id=agent.id, token_hash=token_hash, lookup_hint=hint))
            agent_tokens[name] = plain
        await db.commit()

        def user_token(user: User) -> str:
            return create_user_token(
                user.id,
                user.email or "",
                user.is_admin,
                secret=config.jwt_secret,
            )

        app = create_app(config)
        app.state.engine = engine
        app.state.session_factory = factory
        yield {
            "app": app,
            "factory": factory,
            "room_id": room.id,
            "other_room_id": other_room.id,
            "participants": {name: participant.id for name, participant in participants.items()},
            "tokens": {
                "owner": user_token(owner),
                "member": user_token(member),
                "outsider": user_token(outsider),
                **agent_tokens,
            },
        }

    await engine.dispose()


async def _root(
    client: AsyncClient, room_id: str, token: str, content: str
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/rooms/{room_id}/messages",
        json={"content": content},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _reply(
    client: AsyncClient, room_id: str, root_id: str, token: str, content: str
) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/rooms/{room_id}/threads/{root_id}/messages",
        json={"content": content},
        headers=_auth(token),
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _source_task(
    client: AsyncClient,
    room_id: str,
    source_message_id: str,
    token: str,
    *,
    title: str = "linked task",
    assignee_participant_id: str | None = None,
) -> Any:
    body: dict[str, Any] = {"title": title}
    if assignee_participant_id is not None:
        body["assignee_participant_id"] = assignee_participant_id
    return await client.post(
        f"/api/v1/rooms/{room_id}/messages/{source_message_id}/task",
        json=body,
        headers=_auth(token),
    )


def _error_code(response: Any) -> str | None:
    """Return a contract error code regardless of the standard error shape."""

    try:
        payload = response.json()
    except ValueError:
        return None

    def walk(value: Any) -> str | None:
        if isinstance(value, dict):
            code = value.get("code")
            if isinstance(code, str):
                return code
            for child in value.values():
                found = walk(child)
                if found:
                    return found
        if isinstance(value, list):
            for child in value:
                found = walk(child)
                if found:
                    return found
        return None

    return walk(payload)


async def _task_count(factory, room_id: str) -> int:
    async with factory() as db:
        return int(
            await db.scalar(
                select(func.count()).select_from(Task).where(Task.room_id == room_id)
            )
            or 0
        )


async def _task_assignments(factory, room_id: str, task_id: str) -> list[Message]:
    async with factory() as db:
        messages = (
            await db.scalars(
                select(Message)
                .where(Message.room_id == room_id)
                .order_by(Message.seq)
            )
        ).all()
        return [
            message
            for message in messages
            if (message.extra_metadata or {}).get("task_assignment", {}).get("task_id")
            == task_id
        ]


async def _remove_participant(factory, participant_id: str) -> None:
    async with factory() as db:
        participant = await db.get(Participant, participant_id)
        assert participant is not None
        await db.delete(participant)
        await db.commit()


@pytest.mark.asyncio
async def test_source_message_task_is_room_scoped_one_to_one_and_thread_anchored(
    claim_env: dict[str, Any],
) -> None:
    """A root/reply source has one canonical task and no cross-room oracle."""

    transport = ASGITransport(app=claim_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        root = await _root(
            client,
            claim_env["room_id"],
            claim_env["tokens"]["member"],
            "source root",
        )
        reply = await _reply(
            client,
            claim_env["room_id"],
            root["id"],
            claim_env["tokens"]["member"],
            "source reply",
        )
        created = await _source_task(
            client,
            claim_env["room_id"],
            reply["id"],
            claim_env["tokens"]["member"],
            title="reply-derived task",
        )
        assert created.status_code == 201, created.text
        task = created.json()
        assert task["source_message_id"] == reply["id"]
        assert task["source_thread_root_id"] == root["id"]

        listed = await client.get(
            f"/api/v1/rooms/{claim_env['room_id']}/tasks",
            headers=_auth(claim_env["tokens"]["member"]),
        )
        assert listed.status_code == 200, listed.text
        assert next(item for item in listed.json() if item["id"] == task["id"])[
            "source_message_id"
        ] == reply["id"]

        duplicate = await _source_task(
            client,
            claim_env["room_id"],
            reply["id"],
            claim_env["tokens"]["member"],
        )
        assert duplicate.status_code == 409, duplicate.text
        assert _error_code(duplicate) == "TASK_SOURCE_ALREADY_LINKED"
        # A retrier gets the canonical object id, not an accidentally-created
        # second task or a source-id existence oracle.
        assert task["id"] in duplicate.text

        other_root = await _root(
            client,
            claim_env["other_room_id"],
            claim_env["tokens"]["member"],
            "other room source",
        )
        cross_room = await _source_task(
            client,
            claim_env["room_id"],
            other_root["id"],
            claim_env["tokens"]["member"],
        )
        assert cross_room.status_code == 404, cross_room.text

    assert await _task_count(claim_env["factory"], claim_env["room_id"]) == 1


@pytest.mark.asyncio
async def test_concurrent_human_claim_has_exactly_one_cas_winner_and_is_quiet(
    claim_env: dict[str, Any],
) -> None:
    """Two eligible people racing a todo task cannot both claim or wake agents."""

    transport = ASGITransport(app=claim_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        source = await _root(
            client,
            claim_env["room_id"],
            claim_env["tokens"]["owner"],
            "race source",
        )
        created = await _source_task(
            client,
            claim_env["room_id"],
            source["id"],
            claim_env["tokens"]["owner"],
            title="race me",
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["id"]

    start = asyncio.Event()

    async def claim(token: str):
        async with AsyncClient(transport=transport, base_url="http://test") as claimant:
            await start.wait()
            return await claimant.post(
                f"/api/v1/tasks/{task_id}/claim", headers=_auth(token)
            )

    owner_waiter = asyncio.create_task(claim(claim_env["tokens"]["owner"]))
    member_waiter = asyncio.create_task(claim(claim_env["tokens"]["member"]))
    await asyncio.sleep(0)
    start.set()
    owner_claim, member_claim = await asyncio.gather(owner_waiter, member_waiter)
    results = [owner_claim, member_claim]
    assert sorted(response.status_code for response in results) == [200, 409]

    winner = next(response.json() for response in results if response.status_code == 200)
    loser = next(response for response in results if response.status_code == 409)
    assert winner["status"] == "in_progress"
    assert winner["assignee_participant_id"] in {
        claim_env["participants"]["owner"],
        claim_env["participants"]["member"],
    }
    assert _error_code(loser) == "TASK_CLAIM_CONFLICT"
    assert winner["assignee_participant_id"] in loser.text

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        retry = await client.post(
            f"/api/v1/tasks/{task_id}/claim",
            headers=_auth(claim_env["tokens"]["owner"]),
        )
    assert retry.status_code == 409, retry.text
    assert _error_code(retry) == "TASK_CLAIM_CONFLICT"
    # Creation was unassigned; both a successful self-claim and an idempotent
    # retry must stay chat-quiet.
    assert await _task_assignments(claim_env["factory"], claim_env["room_id"], task_id) == []


@pytest.mark.asyncio
async def test_agent_reservation_claim_transition_and_assignment_wake_are_scoped(
    claim_env: dict[str, Any],
) -> None:
    """Only the reserved agent can claim/finish; initial assignment wakes in source thread."""

    transport = ASGITransport(app=claim_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        source = await _root(
            client,
            claim_env["room_id"],
            claim_env["tokens"]["owner"],
            "agent assignment source",
        )
        created = await _source_task(
            client,
            claim_env["room_id"],
            source["id"],
            claim_env["tokens"]["owner"],
            title="reserved agent task",
            assignee_participant_id=claim_env["participants"]["agent_a"],
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["id"]

        # A member may convert a message, but cannot convert that capability
        # into assigning a different participant.
        forbidden_source = await _root(
            client,
            claim_env["room_id"],
            claim_env["tokens"]["member"],
            "member cannot delegate",
        )
        arbitrary_assignment = await _source_task(
            client,
            claim_env["room_id"],
            forbidden_source["id"],
            claim_env["tokens"]["member"],
            assignee_participant_id=claim_env["participants"]["agent_b"],
        )
        assert arbitrary_assignment.status_code == 403, arbitrary_assignment.text

        wrong_agent = await client.post(
            f"/api/v1/tasks/{task_id}/claim",
            headers=_auth(claim_env["tokens"]["agent_b"]),
        )
        assert wrong_agent.status_code == 409, wrong_agent.text
        assert _error_code(wrong_agent) == "TASK_CLAIM_CONFLICT"

        claimed = await client.post(
            f"/api/v1/tasks/{task_id}/claim",
            headers=_auth(claim_env["tokens"]["agent_a"]),
        )
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["status"] == "in_progress"

        foreign_update = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"status": "done"},
            headers=_auth(claim_env["tokens"]["agent_b"]),
        )
        assert foreign_update.status_code == 403, foreign_update.text
        done = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"status": "done"},
            headers=_auth(claim_env["tokens"]["agent_a"]),
        )
        assert done.status_code == 200, done.text
        reopened_by_agent = await client.put(
            f"/api/v1/tasks/{task_id}",
            json={"status": "in_progress"},
            headers=_auth(claim_env["tokens"]["agent_a"]),
        )
        assert reopened_by_agent.status_code == 409, reopened_by_agent.text
        assert _error_code(reopened_by_agent) == "TASK_CLAIM_REQUIRED"

    assignments = await _task_assignments(
        claim_env["factory"], claim_env["room_id"], task_id
    )
    assert len(assignments) == 1
    assignment = assignments[0]
    assert assignment.parent_message_id == source["id"]
    assert assignment.root_message_id == source["id"]
    assignment_meta = assignment.extra_metadata["task_assignment"]
    assert assignment_meta["task_id"] == task_id
    assert assignment_meta["assignee_pid"] == claim_env["participants"]["agent_a"]
    assert assignment_meta["event"] == "assigned"
    async with claim_env["factory"]() as db:
        turn = await db.scalar(select(AgentTurnTask).where(AgentTurnTask.task_id == task_id))
        assert turn is not None


@pytest.mark.asyncio
async def test_archived_room_rejects_source_conversion_and_claim_without_mutation(
    claim_env: dict[str, Any],
) -> None:
    """Archive is a current-state gate for already-open task claim flows."""

    transport = ASGITransport(app=claim_env["app"])
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        source = await _root(
            client,
            claim_env["room_id"],
            claim_env["tokens"]["owner"],
            "archive source",
        )
        created = await _source_task(
            client,
            claim_env["room_id"],
            source["id"],
            claim_env["tokens"]["owner"],
        )
        assert created.status_code == 201, created.text
        task_id = created.json()["id"]

        archive = await client.post(
            f"/api/v1/rooms/{claim_env['room_id']}/archive",
            headers=_auth(claim_env["tokens"]["owner"]),
        )
        assert archive.status_code == 200, archive.text

        denied_claim = await client.post(
            f"/api/v1/tasks/{task_id}/claim",
            headers=_auth(claim_env["tokens"]["member"]),
        )
        assert denied_claim.status_code == 409, denied_claim.text
        denied_convert = await _source_task(
            client,
            claim_env["room_id"],
            source["id"],
            claim_env["tokens"]["owner"],
            title="must not create after archive",
        )
        assert denied_convert.status_code == 409, denied_convert.text

        listed = await client.get(
            f"/api/v1/rooms/{claim_env['room_id']}/tasks",
            headers=_auth(claim_env["tokens"]["member"]),
        )
        assert listed.status_code == 200, listed.text
        task = next(item for item in listed.json() if item["id"] == task_id)
        assert task["status"] == "todo"
        assert task["assignee_participant_id"] is None


def test_assignment_replay_is_thread_bound_and_removed_member_socket_cannot_claim(
    claim_env: dict[str, Any],
) -> None:
    """Replay sends only durable assignment messages; stale members are revoked.

    ``task.updated`` is a live hint, so reconnect restores task state through
    normal message replay plus the REST task bootstrap rather than replaying a
    second claim/update or creating another synthetic assignment.
    """

    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    async def setup() -> tuple[dict[str, Any], str]:
        transport = ASGITransport(app=claim_env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            root = await _root(
                client,
                claim_env["room_id"],
                claim_env["tokens"]["owner"],
                "replay source",
            )
            created = await _source_task(
                client,
                claim_env["room_id"],
                root["id"],
                claim_env["tokens"]["owner"],
                title="replay task",
                assignee_participant_id=claim_env["participants"]["agent_a"],
            )
            assert created.status_code == 201, created.text
            return root, created.json()["id"]

    root, task_id = asyncio.run(setup())
    before = asyncio.run(
        _task_assignments(claim_env["factory"], claim_env["room_id"], task_id)
    )
    assert len(before) == 1

    with TestClient(claim_env["app"]) as client:
        with client.websocket_connect(
            f"/ws/rooms/{claim_env['room_id']}?since_seq={root['seq']}",
            subprotocols=["anygarden.v1", f"bearer.{claim_env['tokens']['agent_a']}"],
        ) as replay_ws:
            assert json.loads(replay_ws.receive_text())["type"] == "welcome"
            replay = json.loads(replay_ws.receive_text())
            assert replay["type"] == "message"
            assert replay["id"] == before[0].id
            assert replay["parent_message_id"] == root["id"]
            assert replay["root_message_id"] == root["id"]
            assert replay["metadata"]["task_assignment"]["task_id"] == task_id

        with client.websocket_connect(
            f"/ws/rooms/{claim_env['room_id']}",
            subprotocols=["anygarden.v1", f"bearer.{claim_env['tokens']['member']}"],
        ) as stale_member_ws:
            assert json.loads(stale_member_ws.receive_text())["type"] == "welcome"
            asyncio.run(
                _remove_participant(
                    claim_env["factory"], claim_env["participants"]["member"]
                )
            )
            stale_member_ws.send_text(
                json.dumps({"type": "send", "content": "must not persist"})
            )
            with pytest.raises(WebSocketDisconnect) as exc:
                stale_member_ws.receive_text()
            assert exc.value.code == 4003

    async def assert_postconditions() -> None:
        transport = ASGITransport(app=claim_env["app"])
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            stale_claim = await client.post(
                f"/api/v1/tasks/{task_id}/claim",
                headers=_auth(claim_env["tokens"]["member"]),
            )
            assert stale_claim.status_code == 403, stale_claim.text
            bootstrap = await client.get(
                f"/api/v1/rooms/{claim_env['room_id']}/tasks",
                headers=_auth(claim_env["tokens"]["owner"]),
            )
            assert bootstrap.status_code == 200, bootstrap.text
            restored = next(item for item in bootstrap.json() if item["id"] == task_id)
            assert restored["source_message_id"] == root["id"]
            assert restored["source_thread_root_id"] == root["id"]
            assert restored["status"] == "todo"
            assert restored["assignee_participant_id"] == claim_env["participants"]["agent_a"]

    asyncio.run(assert_postconditions())
    after = asyncio.run(
        _task_assignments(claim_env["factory"], claim_env["room_id"], task_id)
    )
    assert [message.id for message in after] == [message.id for message in before]
