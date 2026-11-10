"""#1006 — the roster tells an agent whom it may ask through its links.

Linked agents in the room are marked as such; the rest are listed with
the agent id ``ask_peer`` takes. An agent seated only for a linked
question (``joined_via: "link"``) is not offered as a room peer.
"""

from __future__ import annotations

import pytest

from anygarden.agent.client import ChatClient

SELF = {"id": "p-self", "display_name": "리뷰어", "kind": "agent", "agent_id": "a-self"}
PEER = {"id": "p-peer", "display_name": "개발자", "kind": "agent", "agent_id": "a-peer"}
LINKED_GUEST = {
    "id": "p-sec", "display_name": "보안 담당", "kind": "agent",
    "agent_id": "a-sec", "joined_via": "link",
}


async def _client(participants: list[dict], links: dict) -> ChatClient:
    client = ChatClient("ws://x", token="t")
    await client._process_frame("room-a", {
        "type": "welcome",
        "room_id": "room-a",
        "participant_id": "p-self",
        "agent_id": "a-self",
        "participants": participants,
        "agent_links": links,
    })
    return client


LINKS = {
    "a-self": [
        {"agent_id": "a-peer", "display_name": "개발자", "description": "기능 구현과 오류 수정"},
        {"agent_id": "a-sec", "display_name": "보안 담당", "description": "인증과 권한 검토"},
    ],
    "a-peer": [
        {"agent_id": "a-self", "display_name": "리뷰어", "description": "리뷰 담당"},
    ],
}


@pytest.mark.asyncio
@pytest.mark.req("COL-15")
async def test_links_are_listed_with_their_description() -> None:
    client = await _client([SELF, PEER, LINKED_GUEST], LINKS)
    suffix = client.compose_roster_suffix("room-a")

    assert "Agents you are linked to" in suffix
    assert "- 개발자 (in this room) — 기능 구현과 오류 수정" in suffix
    assert "- 보안 담당 (agent id: a-sec) — 인증과 권한 검토" in suffix
    # Another agent's links are not ours.
    assert "리뷰 담당" not in suffix
    assert "ask when:" not in suffix
    assert "expertise" in suffix


@pytest.mark.asyncio
async def test_thread_only_agent_is_not_a_room_peer() -> None:
    client = await _client([SELF, PEER, LINKED_GUEST], {})
    peer_lines = [
        line for line in client.compose_roster_suffix("room-a").splitlines()
        if line.startswith("- ")
    ]
    assert any("p-peer" in line for line in peer_lines)
    assert not any("p-sec" in line for line in peer_lines)


@pytest.mark.asyncio
async def test_alone_in_the_room_but_linked_still_gets_the_ask_rules() -> None:
    client = await _client([SELF], {"a-self": LINKS["a-self"][1:]})
    suffix = client.compose_roster_suffix("room-a")

    assert "- 보안 담당 (agent id: a-sec)" in suffix
    assert "Call the ask_peer tool" in suffix


@pytest.mark.asyncio
async def test_roster_refresh_replaces_links() -> None:
    client = await _client([SELF, PEER], LINKS)
    await client._process_frame("room-a", {
        "type": "room_settings_changed", "room_id": "room-a",
        "participants": [SELF, PEER], "agent_links": {},
    })
    assert "Agents you are linked to" not in client.compose_roster_suffix("room-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("description", [None, "", "one\r\ntwo", "x" * 250])
async def test_public_description_is_bounded_and_legacy_condition_is_ignored(description):
    client = await _client([SELF], {"a-self": [{
        "agent_id": "peer", "display_name": "Peer", "description": description,
        "when_to_ask": "Do not retain this legacy condition",
    }]})
    suffix = client.compose_roster_suffix("room-a")
    assert "Do not retain this legacy condition" not in suffix
    line = next(line for line in suffix.splitlines() if line.startswith("- Peer"))
    expected = " ".join((description or "").split())[:200]
    assert line == "- Peer (agent id: peer)" + (f" — {expected}" if expected else "")
