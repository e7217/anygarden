"""Issue #733 — the roster suffix tells the model who *it* is.

Messages reach the model with their raw ``<@user:{pid}>`` tokens intact,
but the roster used to drop the agent's own line (to keep it out of the
handoff candidates) and the identity header carries only the name. A
mention of the agent's own participant id therefore looked like an
unknown participant: the model excluded itself from "list the people in
this room" and answered that the id was not in the roster.

The fix states the agent's own name and id on a separate ``You:`` line,
kept outside the peer list so routing still only picks peers.
"""

from __future__ import annotations

import pytest

from anygarden_agent.client import ChatClient


def _welcome(pid: str, room_id: str, participants: list[dict]) -> dict:
    return {
        "type": "welcome",
        "room_id": room_id,
        "participant_id": pid,
        "participants": participants,
    }


SELF = {"id": "p-self", "display_name": "local-agent", "kind": "agent"}
PEER = {"id": "p-peer", "display_name": "agent01", "kind": "agent"}
ADMIN = {"id": "p-admin", "display_name": "admin", "kind": "user"}


async def _client_in(room_id: str, pid: str, participants: list[dict]) -> ChatClient:
    client = ChatClient("ws://x", token="t")
    await client._process_frame(room_id, _welcome(pid, room_id, participants))
    return client


class TestSelfLine:
    @pytest.mark.asyncio
    async def test_self_named_with_id_outside_peer_list(self) -> None:
        client = await _client_in("room-a", "p-self", [ADMIN, SELF, PEER])
        suffix = client.compose_roster_suffix("room-a")

        you_line = next(
            line for line in suffix.splitlines() if line.startswith("You:")
        )
        assert "local-agent" in you_line
        assert "p-self" in you_line
        assert "<@user:p-self>" in you_line  # the token that addresses it

        # Peer list (routing candidates) still excludes self.
        peer_lines = [line for line in suffix.splitlines() if line.startswith("- ")]
        assert any("p-peer" in line for line in peer_lines)
        assert any("p-admin" in line for line in peer_lines)
        assert not any("p-self" in line for line in peer_lines)

    @pytest.mark.asyncio
    async def test_self_line_forbids_own_token_in_reply(self) -> None:
        client = await _client_in("room-a", "p-self", [SELF, PEER])
        suffix = client.compose_roster_suffix("room-a")
        assert "never put that token in your own reply" in suffix.lower()

    @pytest.mark.asyncio
    async def test_alone_in_room_keeps_self_line_without_routing_guidance(
        self,
    ) -> None:
        client = await _client_in("room-a", "p-self", [SELF])
        suffix = client.compose_roster_suffix("room-a")
        assert "Current room ID: room-a" in suffix
        assert "You: local-agent (id: p-self)" in suffix
        # No peers → nothing to route to.
        assert "Room participants" not in suffix
        assert "Construct a routing token" not in suffix
        assert "routing token in the final reply" not in suffix

    @pytest.mark.asyncio
    async def test_empty_roster_yields_no_suffix(self) -> None:
        client = ChatClient("ws://x", token="t")
        assert client.compose_roster_suffix("room-a") == ""

    @pytest.mark.asyncio
    async def test_self_id_is_per_room(self) -> None:
        """``_my_participant_ids`` is one set across rooms; each room's
        suffix must name the pid that belongs to *that* room."""
        client = await _client_in("room-a", "p-self-a", [
            {**SELF, "id": "p-self-a"}, PEER,
        ])
        await client._process_frame("room-b", _welcome("p-self-b", "room-b", [
            {**SELF, "id": "p-self-b"}, PEER,
        ]))
        a = client.compose_roster_suffix("room-a")
        b = client.compose_roster_suffix("room-b")
        assert "You: local-agent (id: p-self-a)" in a and "p-self-b" not in a
        assert "You: local-agent (id: p-self-b)" in b and "p-self-a" not in b


class TestSelfMentionDoesNotLoop:
    """Knowing its own id must not let the agent wake itself: its own
    message — even one carrying its own mention token — is dropped by
    the client's hard sender filter before any handler runs."""

    @pytest.mark.asyncio
    async def test_own_message_with_self_mention_triggers_no_turn(self) -> None:
        client = await _client_in("room-a", "p-self", [SELF, PEER])
        calls: list[dict] = []

        @client.on_message
        async def _handler(msg: dict) -> None:
            calls.append(msg)

        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "room_id": "room-a",
                "participant_id": "p-self",
                "content": "<@user:p-self> 제가 정리하겠습니다",
                "mentions": [{"type": "user", "id": "p-self"}],
            },
        )
        assert calls == []


class TestPeerCallGuidance:
    """#737 — peers are called through the ``ask_peer`` tool; a routing
    token is only the fallback when the tool is unavailable."""

    @pytest.mark.asyncio
    async def test_roster_points_to_ask_peer_with_token_fallback(self) -> None:
        client = await _client_in("room-a", "p-self", [ADMIN, SELF, PEER])
        suffix = client.compose_roster_suffix("room-a")

        assert "call the ask_peer tool" in suffix
        assert "do not also write the requests in your reply" in suffix
        assert "rejected" in suffix
        # #762 — the asking turn ends with a draft and is woken with the
        # answers; the caller writes one final answer. The #283 "only
        # synthesize if asked" rule is gone.
        assert "kept as your draft" in suffix
        assert "write the final answer once" in suffix
        assert "failed or timed out" in suffix
        assert "synthesize if" not in suffix
        assert "정리해줘" not in suffix
        assert "ask_peer only calls agents" in suffix
        assert "Only if the ask_peer tool is unavailable" in suffix
        assert "routing token in the final reply" in suffix
        # Rules kept from #283/#288.
        assert "Refer to peers by display name in prose" in suffix
        assert "Don't peer-ask for trivial greetings" in suffix

    @pytest.mark.asyncio
    async def test_alone_in_room_has_no_ask_peer_guidance(self) -> None:
        client = await _client_in("room-a", "p-self", [SELF])
        assert "ask_peer" not in client.compose_roster_suffix("room-a")
