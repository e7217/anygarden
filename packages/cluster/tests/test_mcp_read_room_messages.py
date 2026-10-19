"""The ``read_room_messages`` MCP tool (#866).

An agent woken by a mention only gets what was pushed to it; this tool lets
it read the room (or a thread) itself when it lacks the conversation.
"""

from __future__ import annotations

import pytest
from anygarden.db.models import Agent, Message, Participant, Project, Room, User
from anygarden.mcp.tools import read_room_messages

from tests.test_ws_handler import ws_env as ws_env


async def _seed(db, *, main: int = 3) -> dict[str, str]:
    user = User(email="h@test.com", password_hash="x", display_name="human")
    project = Project(name="p")
    db.add_all([user, project])
    await db.flush()
    room = Room(project_id=project.id, name="r")
    other_room = Room(project_id=project.id, name="other")
    me = Agent(name="pm", engine="codex")
    peer = Agent(name="dev", engine="codex")
    db.add_all([room, other_room, me, peer])
    await db.flush()
    human_p = Participant(room_id=room.id, user_id=user.id, role="member")
    me_p = Participant(room_id=room.id, agent_id=me.id, role="member")
    peer_p = Participant(room_id=room.id, agent_id=peer.id, role="member")
    db.add_all([human_p, me_p, peer_p])
    await db.flush()
    ids: dict[str, str] = {
        "room": room.id, "other_room": other_room.id, "me": me.id,
        "me_p": me_p.id, "peer_p": peer_p.id, "human_p": human_p.id,
    }
    seq = 0
    for i in range(main):
        seq += 1
        m = Message(room_id=room.id, participant_id=human_p.id,
                    content=f"main {i + 1}", seq=seq)
        db.add(m)
        await db.flush()
        ids[f"main{i + 1}"] = m.id
    seq += 1
    root = Message(room_id=room.id, participant_id=peer_p.id, content="thread root", seq=seq)
    db.add(root)
    await db.flush()
    ids["root"] = root.id
    for i in range(2):
        seq += 1
        db.add(Message(room_id=room.id, participant_id=human_p.id,
                       content=f"reply {i + 1}", seq=seq,
                       parent_message_id=root.id, root_message_id=root.id))
    await db.flush()
    ids["last_seq"] = str(seq)
    return ids


def _contents(result: dict) -> list[str]:
    return [m["content"] for m in result["structuredContent"]["messages"]]


@pytest.mark.asyncio
async def test_main_room_lists_latest_roots_with_speakers(db) -> None:
    ids = await _seed(db)
    result = await read_room_messages(db, agent_id=ids["me"], arguments={"room_id": ids["room"]})

    assert result["isError"] is False
    assert _contents(result) == ["main 1", "main 2", "main 3", "thread root"]
    msgs = result["structuredContent"]["messages"]
    assert msgs[0]["speaker"] == "human" and msgs[0]["kind"] == "user"
    assert msgs[-1]["speaker"] == "dev" and msgs[-1]["kind"] == "agent"
    # Threads are folded in the main view; the reply count says one is there.
    assert msgs[-1]["thread_replies"] == 2
    assert result["structuredContent"]["has_more_before"] is False


@pytest.mark.asyncio
async def test_thread_scope_includes_root_and_replies(db) -> None:
    ids = await _seed(db)
    result = await read_room_messages(
        db, agent_id=ids["me"],
        arguments={"room_id": ids["room"], "thread_root_id": ids["root"]},
    )
    assert _contents(result) == ["thread root", "reply 1", "reply 2"]


@pytest.mark.asyncio
async def test_before_seq_pages_backwards(db) -> None:
    ids = await _seed(db, main=5)
    first = await read_room_messages(
        db, agent_id=ids["me"], arguments={"room_id": ids["room"], "limit": 2},
    )
    assert _contents(first) == ["main 5", "thread root"]
    page = first["structuredContent"]
    assert page["has_more_before"] is True
    assert "before_seq" in first["content"][0]["text"]

    second = await read_room_messages(
        db, agent_id=ids["me"],
        arguments={"room_id": ids["room"], "limit": 2, "before_seq": page["next_before_seq"]},
    )
    assert _contents(second) == ["main 3", "main 4"]


@pytest.mark.asyncio
async def test_around_message_reads_its_conversation(db) -> None:
    ids = await _seed(db, main=5)
    result = await read_room_messages(
        db, agent_id=ids["me"],
        arguments={"room_id": ids["room"], "around_message_id": ids["main3"]},
    )
    # Main-room neighbours only: the thread's replies stay folded.
    assert _contents(result) == ["main 1", "main 2", "main 3", "main 4", "main 5", "thread root"]


@pytest.mark.asyncio
async def test_non_participant_is_rejected(db) -> None:
    ids = await _seed(db)
    result = await read_room_messages(
        db, agent_id=ids["me"], arguments={"room_id": ids["other_room"]},
    )
    assert result["isError"] is True


@pytest.mark.asyncio
async def test_thread_root_must_be_a_root_in_this_room(db) -> None:
    ids = await _seed(db)
    result = await read_room_messages(
        db, agent_id=ids["me"],
        arguments={"room_id": ids["room"], "thread_root_id": "nope"},
    )
    assert result["isError"] is True


@pytest.mark.asyncio
async def test_long_deleted_and_hidden_messages(db) -> None:
    ids = await _seed(db, main=0)
    db.add_all([
        Message(room_id=ids["room"], participant_id=ids["human_p"], content="x" * 1500, seq=100),
        Message(room_id=ids["room"], participant_id=ids["human_p"], content="",
                extra_metadata={"deleted_at": "2026-10-16T00:00:00Z"}, seq=101),
        Message(room_id=ids["room"], participant_id=ids["me_p"], content="internal",
                extra_metadata={"system_origin": "peer_ask_results"}, seq=102),
    ])
    await db.flush()
    result = await read_room_messages(db, agent_id=ids["me"], arguments={"room_id": ids["room"]})
    msgs = result["structuredContent"]["messages"]
    long_msg = next(m for m in msgs if m["seq"] == 100)
    assert long_msg["truncated"] is True and len(long_msg["content"]) <= 1001
    assert next(m for m in msgs if m["seq"] == 101)["content"] == "(deleted)"
    assert all(m["seq"] != 102 for m in msgs)


async def test_tool_is_reachable_over_mcp_rpc(ws_env) -> None:
    """The tool is wired into ``/mcp/rpc``, not only callable in-process."""
    from starlette.testclient import TestClient

    from tests.test_peer_mention_safety_net import _seed_sender_and_peer

    app, sf, room = ws_env["app"], ws_env["session_factory"], ws_env["room"]
    token, _sender_pid, _peer_pid = await _seed_sender_and_peer(sf, room)
    async with sf() as db:
        db.add(Message(room_id=room.id, participant_id=_peer_pid, content="hello room", seq=1))
        await db.commit()

    with TestClient(app) as client:
        resp = client.post(
            "/mcp/rpc",
            headers={"Authorization": f"Bearer {token}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "read_room_messages", "arguments": {"room_id": room.id},
            }},
        )
    result = resp.json()["result"]
    assert result["isError"] is False
    assert [m["content"] for m in result["structuredContent"]["messages"]] == ["hello room"]


@pytest.mark.req("ENG-12")
@pytest.mark.asyncio
async def test_an_agent_reads_replies_without_their_work_trace(db) -> None:
    # #880 — the trace is for people; peers read only what was said.
    ids = await _seed(db, main=0)
    db.add(Message(room_id=ids["room"], participant_id=ids["peer_p"], content="done",
                   seq=int(ids["last_seq"]) + 1,
                   extra_metadata={"execution_trace": {"v": 1, "steps": [
                       {"narration": "Deploying now.", "activities": {"commands": 2}}],
                       "totals": {"commands": 2}, "truncated": 0}}))
    await db.flush()
    result = await read_room_messages(db, agent_id=ids["me"], arguments={"room_id": ids["room"]})
    assert "Deploying now." not in str(result)
    assert "execution_trace" not in str(result)
