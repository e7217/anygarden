"""Structured interactions: schema, routing stamps, no-home fallback (D-6)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from anygarden.interactions import (
    InteractionSchemaError,
    is_interaction_send,
    validate_request,
    validate_resolution,
)


def valid_request(kind: str = "question", **over):
    base = {
        "kind": kind,
        "id": str(uuid4()),
        "prompt": "어느 쪽으로 진행할까요?",
    }
    base.update(over)
    return base


def test_request_schema_is_closed_and_typed():
    out = validate_request(valid_request())
    assert out["kind"] == "question" and out["id"] == out["id"].lower()
    judgment = validate_request(
        valid_request("judgment", options=["A 안", "B 안"])
    )
    assert judgment["options"] == ["A 안", "B 안"]
    checklist = validate_request(
        valid_request("checklist", items=["항목1", "항목2"])
    )
    assert checklist["items"] == ["항목1", "항목2"]
    confirmation = validate_request(valid_request("confirmation"))
    assert "options" not in confirmation and "items" not in confirmation


def test_request_schema_rejects_malformed():
    with pytest.raises(InteractionSchemaError):
        validate_request({"kind": "brainstorm", "id": str(uuid4()), "prompt": "?"})
    with pytest.raises(InteractionSchemaError):
        validate_request(valid_request(extra_field="x"))
    with pytest.raises(InteractionSchemaError):
        validate_request(valid_request(id="not-a-uuid"))
    with pytest.raises(InteractionSchemaError):
        validate_request(valid_request(prompt=""))
    with pytest.raises(InteractionSchemaError):
        validate_request(valid_request("judgment", options=["하나"]))
    with pytest.raises(InteractionSchemaError):
        validate_request(valid_request("question", options=["A"]))
    with pytest.raises(InteractionSchemaError):
        validate_request(valid_request(timeout_seconds=1))


def test_resolution_schema_requires_a_payload_and_closed_shape():
    iid = str(uuid4())
    out = validate_resolution({"interaction_id": iid, "answer": "A 안으로"})
    assert out["interaction_id"] == iid
    assert validate_resolution(
        {"interaction_id": iid, "selections": [0, 2]}
    )["selections"] == [0, 2]
    assert validate_resolution(
        {"interaction_id": iid, "confirmed": True}
    )["confirmed"] is True
    with pytest.raises(InteractionSchemaError):
        validate_resolution({"interaction_id": iid})
    with pytest.raises(InteractionSchemaError):
        validate_resolution({"interaction_id": iid, "answer": "x", "bogus": 1})
    with pytest.raises(InteractionSchemaError):
        validate_resolution({"interaction_id": iid, "selections": [-1]})


def test_is_interaction_send():
    assert not is_interaction_send(None)
    assert not is_interaction_send({})
    assert not is_interaction_send({"mentions": []})
    assert is_interaction_send({"interaction": valid_request()})
    assert is_interaction_send({"interaction_resolution": {"interaction_id": "x"}})


@pytest.mark.asyncio
async def test_process_send_stamps_and_rejects(db):
    from anygarden.db.models import Message, Participant, Room, User
    from anygarden.interactions import process_send

    room_id, requester, target = str(uuid4()), str(uuid4()), str(uuid4())
    u1, u2 = User(id=str(uuid4()), email=f"{uuid4()}@t.test"), User(
        id=str(uuid4()), email=f"{uuid4()}@t.test"
    )
    db.add_all([u1, u2])
    await db.flush()
    db.add(Room(id=room_id, name="r"))
    await db.flush()
    db.add_all(
        [
            Participant(id=requester, room_id=room_id, user_id=u1.id, role="member"),
            Participant(id=target, room_id=room_id, user_id=u2.id, role="member"),
        ]
    )
    await db.flush()
    # Untargeted request: agents absorb (ingest_only), humans read.
    md = {"interaction": valid_request()}
    out = await process_send(db, md, thread_root_id=None, sender_participant_id=requester)
    assert out["ingest_only"] is True
    # Targeted request: explicit next speaker, no blanket ingest stamp.
    md = {"interaction": valid_request(target_participant_id=target)}
    out = await process_send(db, md, thread_root_id=None, sender_participant_id=requester)
    assert out["next_speaker_participant_id"] == target
    assert "ingest_only" not in out
    # Combined payloads rejected.
    with pytest.raises(InteractionSchemaError):
        await process_send(
            db,
            {"interaction": valid_request(), "interaction_resolution": {"interaction_id": str(uuid4()), "answer": "x"}},
            thread_root_id=None,
            sender_participant_id=requester,
        )
    # Resolution must thread-reply to the matching request.
    iid = str(uuid4())
    db.add(
        Message(
            id="root-1",
            room_id=room_id,
            participant_id=requester,
            content="?",
            seq=1,
            extra_metadata={"interaction": valid_request(id=iid)},
        )
    )
    await db.commit()
    # Wrong interaction id at the thread root: reference mismatch (LookupError).
    with pytest.raises(LookupError):
        await process_send(
            db,
            {"interaction_resolution": {"interaction_id": str(uuid4()), "answer": "x"}},
            thread_root_id="root-1",
            sender_participant_id=target,
        )
    # No thread root at all: shape-level refusal (SchemaError).
    with pytest.raises(InteractionSchemaError):
        await process_send(
            db,
            {"interaction_resolution": {"interaction_id": iid, "answer": "x"}},
            thread_root_id=None,
            sender_participant_id=target,
        )
    out = await process_send(
        db,
        {"interaction_resolution": {"interaction_id": iid, "answer": "A 안"}},
        thread_root_id="root-1",
        sender_participant_id=target,
    )
    assert out["next_speaker_participant_id"] == requester



@pytest.mark.req("MSG-07")
@pytest.mark.asyncio
async def test_second_resolution_of_same_interaction_is_rejected(engine, config):
    """Over the room WebSocket: the first answer to an interaction lands; a
    second answer gets an error frame and appends nothing."""
    import json

    from sqlalchemy import func, select
    from starlette.testclient import TestClient

    from anygarden.app import create_app
    from anygarden.auth.jwt import create_user_token
    from anygarden.db.engine import build_session_factory
    from anygarden.db.models import (
        InteractionResolution,
        Message,
        Participant,
        Project,
        Room,
        User,
    )

    iid = str(uuid4())
    sessions = build_session_factory(engine)
    async with sessions() as db:
        asker = User(email="asker@t.test", password_hash="x")
        answerer = User(email="answerer@t.test", password_hash="x")
        project = Project(name="p")
        db.add_all([asker, answerer, project])
        await db.flush()
        room = Room(project_id=project.id, name="r")
        db.add(room)
        await db.flush()
        asker_part = Participant(room_id=room.id, user_id=asker.id, role="owner")
        db.add_all(
            [asker_part, Participant(room_id=room.id, user_id=answerer.id, role="member")]
        )
        await db.flush()
        root = Message(
            room_id=room.id,
            participant_id=asker_part.id,
            content="pick one",
            seq=1,
            extra_metadata={"interaction": valid_request(id=iid)},
        )
        db.add(root)
        await db.commit()
        room_id, root_id = room.id, root.id
        token = create_user_token(
            answerer.id, answerer.email, False, secret=config.jwt_secret
        )

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions

    def answer(content: str) -> str:
        return json.dumps(
            {
                "type": "send",
                "content": content,
                "thread_root_id": root_id,
                "metadata": {
                    "interaction_resolution": {"interaction_id": iid, "answer": content}
                },
            }
        )

    def next_of(ws, kinds: set[str]) -> dict:
        while True:
            frame = json.loads(ws.receive_text())
            if frame.get("type") in kinds:
                return frame

    with TestClient(app) as client, client.websocket_connect(
        f"/ws/rooms/{room_id}", subprotocols=["anygarden.v1", f"bearer.{token}"]
    ) as ws:
        assert json.loads(ws.receive_text())["type"] == "welcome"
        ws.send_text(answer("A"))
        first = next_of(ws, {"message", "error"})
        assert first["type"] == "message", first
        ws.send_text(answer("B"))
        second = next_of(ws, {"message", "error"})
        assert second["type"] == "error", second
        assert "already resolved" in second["detail"]

    async with sessions() as db:
        resolutions = (await db.execute(select(InteractionResolution))).scalars().all()
        assert [r.interaction_id for r in resolutions] == [iid]
        assert resolutions[0].resolution_message_id == first["id"]
        count = await db.scalar(
            select(func.count()).select_from(Message).where(Message.room_id == room_id)
        )
        assert count == 2  # request + the single accepted answer


@pytest.mark.req("MSG-07")
@pytest.mark.asyncio
async def test_rest_answer_lands_once_and_mismatch_is_400(engine, config):
    """Over REST: a matching answer is accepted, a reference mismatch is a 400
    (not a 500), and a second answer to the same interaction is refused."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select

    from anygarden.app import create_app
    from anygarden.auth.jwt import create_user_token
    from anygarden.db.engine import build_session_factory
    from anygarden.db.models import InteractionResolution, Message, Participant, Project, Room, User

    iid = str(uuid4())
    sessions = build_session_factory(engine)
    async with sessions() as db:
        asker = User(email="asker@t.test", password_hash="x")
        answerer = User(email="answerer@t.test", password_hash="x")
        project = Project(name="p")
        db.add_all([asker, answerer, project])
        await db.flush()
        room = Room(project_id=project.id, name="r")
        db.add(room)
        await db.flush()
        asker_part = Participant(room_id=room.id, user_id=asker.id, role="owner")
        db.add_all(
            [asker_part, Participant(room_id=room.id, user_id=answerer.id, role="member")]
        )
        await db.flush()
        root = Message(
            room_id=room.id,
            participant_id=asker_part.id,
            content="pick one",
            seq=1,
            extra_metadata={"interaction": valid_request(id=iid)},
        )
        db.add(root)
        await db.commit()
        url = f"/api/v1/rooms/{room.id}/threads/{root.id}/messages"
        token = create_user_token(answerer.id, answerer.email, False, secret=config.jwt_secret)

    app = create_app(config)
    app.state.engine = engine
    app.state.session_factory = sessions

    def answer(interaction_id: str, content: str) -> dict:
        return {
            "content": content,
            "metadata": {
                "interaction_resolution": {"interaction_id": interaction_id, "answer": content}
            },
        }

    headers = {"Authorization": f"Bearer {token}"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        mismatch = await client.post(url, json=answer(str(uuid4()), "X"), headers=headers)
        assert mismatch.status_code == 400, mismatch.text
        first = await client.post(url, json=answer(iid, "A"), headers=headers)
        assert first.status_code == 201, first.text
        second = await client.post(url, json=answer(iid, "B"), headers=headers)
        assert 400 <= second.status_code < 500, second.text

    async with sessions() as db:
        resolutions = (await db.execute(select(InteractionResolution))).scalars().all()
        assert [r.resolution_message_id for r in resolutions] == [first.json()["id"]]
