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
