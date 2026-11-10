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
        {"agent_id": "a-peer", "display_name": "개발자", "when_to_ask": "구현이 필요할 때"},
        {"agent_id": "a-sec", "display_name": "보안 담당", "when_to_ask": "인증이 걸리면"},
    ],
    "a-peer": [
        {"agent_id": "a-self", "display_name": "리뷰어", "when_to_ask": "검토"},
    ],
}


@pytest.mark.asyncio
@pytest.mark.req("COL-15")
async def test_links_are_listed_with_their_condition() -> None:
    client = await _client([SELF, PEER, LINKED_GUEST], LINKS)
    suffix = client.compose_roster_suffix("room-a")

    assert "Agents you are linked to" in suffix
    assert "- 개발자 (in this room) — ask when: 구현이 필요할 때" in suffix
    assert "- 보안 담당 (agent id: a-sec) — ask when: 인증이 걸리면" in suffix
    # Another agent's links are not ours.
    assert "ask when: 검토" not in suffix


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
