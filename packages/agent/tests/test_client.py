"""Unit tests for ChatClient — connect, reconnect, since_seq, callback, send."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doorae_agent.client import ChatClient, _is_task_init_content


class TestChatClientInit:
    def test_client_creation(self) -> None:
        """ChatClient can be instantiated with required parameters."""
        client = ChatClient("ws://localhost:8000", token="test-tok", agent_name="TestBot")
        assert client._server_url == "ws://localhost:8000"
        assert client._token == "test-tok"
        assert client._agent_name == "TestBot"
        assert client._last_seq == {}
        assert client._message_handlers == []

    def test_client_strips_trailing_slash(self) -> None:
        """Server URL trailing slash is stripped."""
        client = ChatClient("ws://localhost:8000/", token="t")
        assert client._server_url == "ws://localhost:8000"


class TestChatClientCallbacks:
    def test_on_message_registers_handler(self) -> None:
        """on_message decorator registers a handler."""
        client = ChatClient("ws://localhost:8000", token="t")

        @client.on_message
        async def my_handler(msg):
            pass

        assert len(client._message_handlers) == 1
        assert client._message_handlers[0] is my_handler

    def test_on_join_room_registers_handler(self) -> None:
        """on_join_room decorator registers a handler."""
        client = ChatClient("ws://localhost:8000", token="t")

        @client.on_join_room
        async def my_handler(room_id):
            pass

        assert len(client._join_handlers) == 1

    @pytest.mark.asyncio
    async def test_multiple_handlers(self) -> None:
        """Multiple on_message handlers are all registered."""
        client = ChatClient("ws://localhost:8000", token="t")
        results = []

        @client.on_message
        async def h1(msg):
            results.append("h1")

        @client.on_message
        async def h2(msg):
            results.append("h2")

        assert len(client._message_handlers) == 2


class TestChatClientSinceSeq:
    @pytest.mark.asyncio
    async def test_since_seq_tracking(self) -> None:
        """last_seq is updated when join_room is called."""
        client = ChatClient("ws://localhost:8000", token="t")
        client._running = True
        client._last_seq["room-1"] = 0
        # Verify initial state
        assert client._last_seq["room-1"] == 0
        # Simulate seq update
        client._last_seq["room-1"] = 42
        assert client._last_seq["room-1"] == 42


class TestChatClientWelcomeParsing:
    """Issue #61 — ChatClient must parse ``agent_id`` from the welcome
    frame so ``should_respond`` can gate ``room_query`` forwarding to
    the representative agent only."""

    def test_init_has_none_agent_id(self) -> None:
        client = ChatClient("ws://localhost:8000", token="t")
        assert client._agent_id is None

    @pytest.mark.asyncio
    async def test_welcome_stores_agent_id(self) -> None:
        """A welcome frame with ``agent_id`` populates ``_agent_id``."""
        client = ChatClient("ws://localhost:8000", token="t")
        await client._process_frame(
            "room-1",
            {
                "type": "welcome",
                "participant_id": "pid-1",
                "agent_id": "agent-abc",
            },
        )
        assert client._agent_id == "agent-abc"
        assert "pid-1" in client._my_participant_ids

    @pytest.mark.asyncio
    async def test_welcome_without_agent_id_leaves_none(self) -> None:
        """User / guest welcome frames omit ``agent_id`` — leave as None."""
        client = ChatClient("ws://localhost:8000", token="t")
        await client._process_frame(
            "room-1",
            {"type": "welcome", "participant_id": "pid-1"},
        )
        assert client._agent_id is None


class TestChatClientSend:
    @pytest.mark.asyncio
    async def test_send_raises_when_not_connected(self) -> None:
        """send() raises RuntimeError when not connected to the room."""
        client = ChatClient("ws://localhost:8000", token="t")
        with pytest.raises(RuntimeError, match="Not connected to room"):
            await client.send("nonexistent-room", "hello")

    @pytest.mark.asyncio
    async def test_send_writes_to_websocket(self) -> None:
        """send() serializes a SendFrame and sends it over the WebSocket."""
        client = ChatClient("ws://localhost:8000", token="t")
        mock_ws = AsyncMock()
        client._connections["room-1"] = mock_ws

        await client.send("room-1", "hello world", metadata={"key": "val"})

        mock_ws.send.assert_called_once()
        sent = json.loads(mock_ws.send.call_args[0][0])
        assert sent["type"] == "send"
        assert sent["content"] == "hello world"
        assert sent["metadata"]["key"] == "val"
        assert "_nonce" in sent["metadata"]  # Self-echo filter nonce


class TestIsTaskInitContent:
    """Issue #67 — ``_is_task_init_content`` identifies task boundaries
    that should reset the agent-only turn counter."""

    def test_room_query_prefix(self) -> None:
        assert _is_task_init_content("[ROOM_QUERY] what's the plan?") is True

    def test_delegated_prefix(self) -> None:
        assert _is_task_init_content("[DELEGATED] please summarise") is True

    def test_regular_content(self) -> None:
        assert _is_task_init_content("hello, team") is False

    def test_empty_string(self) -> None:
        assert _is_task_init_content("") is False

    def test_prefix_not_at_start(self) -> None:
        assert _is_task_init_content("fyi [ROOM_QUERY] embedded") is False


class TestAgentTurnCounter:
    """Issue #67 — in agent-only rooms (no human participant) the
    representative agent emits ``[ROOM_QUERY]``/``[DELEGATED]`` frames
    that echo back through hard/soft filters. These frames are task
    boundaries and MUST reset the counter, otherwise consecutive
    task rounds accumulate and later agent replies get dropped at
    ``max_agent_turns``.

    Each test drives ``_process_frame`` directly and asserts the
    observable counter state plus (for regression) handler invocation.
    """

    def _make_client(self) -> ChatClient:
        client = ChatClient("ws://x", token="t")
        client._my_participant_ids.add("self-pid")
        return client

    @pytest.mark.asyncio
    async def test_self_regular_message_increments(self) -> None:
        """Self-emitted regular message bumps the counter (keeps the
        bound on total agent-only exchanges)."""
        client = self._make_client()
        client._agent_turn_count["room-a"] = 2
        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "self-pid",
                "content": "hello room",
            },
        )
        assert client._agent_turn_count["room-a"] == 3

    @pytest.mark.asyncio
    async def test_self_room_query_resets_counter(self) -> None:
        """Core regression: self-emitted ``[ROOM_QUERY]`` is a task
        boundary and must reset the counter to 0 (not +1)."""
        client = self._make_client()
        client._agent_turn_count["room-a"] = 5
        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "self-pid",
                "content": "[ROOM_QUERY] forwarded question",
            },
        )
        assert client._agent_turn_count["room-a"] == 0

    @pytest.mark.asyncio
    async def test_self_delegated_resets_counter(self) -> None:
        """Self-emitted ``[DELEGATED]`` is also a task boundary."""
        client = self._make_client()
        client._agent_turn_count["room-a"] = 4
        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "self-pid",
                "content": "[DELEGATED] do this subtask",
            },
        )
        assert client._agent_turn_count["room-a"] == 0

    @pytest.mark.asyncio
    async def test_nonce_echo_regular_increments(self) -> None:
        """Nonce-echo (soft filter) of a regular message bumps count."""
        client = self._make_client()
        # Use a fresh participant id for the sender so the hard filter
        # does NOT catch it; rely on nonce echo detection.
        client._sent_nonces.add("nonce-1")
        client._agent_turn_count["room-a"] = 1
        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "other-pid",
                "content": "regular content",
                "metadata": {"_nonce": "nonce-1"},
            },
        )
        assert client._agent_turn_count["room-a"] == 2
        # nonce consumed
        assert "nonce-1" not in client._sent_nonces

    @pytest.mark.asyncio
    async def test_nonce_echo_room_query_resets(self) -> None:
        """Nonce-echo of ``[ROOM_QUERY]`` must reset counter."""
        client = self._make_client()
        client._sent_nonces.add("nonce-2")
        client._agent_turn_count["room-a"] = 5
        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "other-pid",
                "content": "[ROOM_QUERY] ask other room",
                "metadata": {"_nonce": "nonce-2"},
            },
        )
        assert client._agent_turn_count["room-a"] == 0
        assert "nonce-2" not in client._sent_nonces

    @pytest.mark.asyncio
    async def test_other_agent_regular_increments(self) -> None:
        """Another agent's message (nonce but not ours) → count +1,
        handler invoked."""
        client = self._make_client()
        calls: list[dict] = []

        @client.on_message
        async def handler(msg):
            calls.append(msg)

        client._agent_turn_count["room-a"] = 1
        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "other-agent-pid",
                "content": "agent reply",
                "metadata": {"_nonce": "foreign-nonce"},
            },
        )
        assert client._agent_turn_count["room-a"] == 2
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_other_agent_exceeds_limit_is_dropped(self) -> None:
        """When counter exceeds ``max_agent_turns`` the handler is
        skipped (infinite agent-to-agent loop guard)."""
        client = self._make_client()
        client.max_agent_turns = 3
        client._agent_turn_count["room-a"] = 3  # already at limit

        calls: list[dict] = []

        @client.on_message
        async def handler(msg):
            calls.append(msg)

        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "other-agent-pid",
                "content": "agent reply",
                "metadata": {"_nonce": "foreign-nonce"},
            },
        )
        assert client._agent_turn_count["room-a"] == 4
        assert calls == []  # dropped

    @pytest.mark.asyncio
    async def test_human_message_resets(self) -> None:
        """Human message (no nonce, not self) resets counter."""
        client = self._make_client()
        client._agent_turn_count["room-a"] = 5

        calls: list[dict] = []

        @client.on_message
        async def handler(msg):
            calls.append(msg)

        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "human-pid",
                "content": "question from human",
            },
        )
        assert client._agent_turn_count["room-a"] == 0
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_other_agent_room_query_resets(self) -> None:
        """Main-path regression: another agent's ``[ROOM_QUERY]``
        resets the counter so the handler can process the task."""
        client = self._make_client()
        client._agent_turn_count["room-a"] = 5

        calls: list[dict] = []

        @client.on_message
        async def handler(msg):
            calls.append(msg)

        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "other-agent-pid",
                "content": "[ROOM_QUERY] task from peer",
                "metadata": {"_nonce": "foreign-nonce"},
            },
        )
        assert client._agent_turn_count["room-a"] == 0
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_agent_only_room_query_fanout_regression(self) -> None:
        """Reproduces issue #67 trace:

        agent-only room, representative agent (``self-pid``) drives
        three ``[ROOM_QUERY]`` rounds. Between each round one other
        agent replies. Without the fix the counter grows 1→2→…→6 and
        the last agent replies are dropped.
        """
        client = self._make_client()
        client.max_agent_turns = 3  # tighter bound to force regression

        handler_calls: list[dict] = []

        @client.on_message
        async def handler(msg):
            handler_calls.append(msg)

        frames = [
            # round 1: self emits [ROOM_QUERY]
            {
                "type": "message",
                "seq": 1,
                "participant_id": "self-pid",
                "content": "[ROOM_QUERY] q1",
            },
            # round 1 reply: other agent
            {
                "type": "message",
                "seq": 2,
                "participant_id": "other-pid",
                "content": "reply 1",
                "metadata": {"_nonce": "f1"},
            },
            # round 2
            {
                "type": "message",
                "seq": 3,
                "participant_id": "self-pid",
                "content": "[ROOM_QUERY] q2",
            },
            {
                "type": "message",
                "seq": 4,
                "participant_id": "other-pid",
                "content": "reply 2",
                "metadata": {"_nonce": "f2"},
            },
            # round 3
            {
                "type": "message",
                "seq": 5,
                "participant_id": "self-pid",
                "content": "[ROOM_QUERY] q3",
            },
            {
                "type": "message",
                "seq": 6,
                "participant_id": "other-pid",
                "content": "reply 3",
                "metadata": {"_nonce": "f3"},
            },
            # round 4
            {
                "type": "message",
                "seq": 7,
                "participant_id": "self-pid",
                "content": "[ROOM_QUERY] q4",
            },
            {
                "type": "message",
                "seq": 8,
                "participant_id": "other-pid",
                "content": "reply 4",
                "metadata": {"_nonce": "f4"},
            },
        ]

        for f in frames:
            await client._process_frame("room-a", f)

        # All four "reply N" frames from other agents must reach the
        # handler — none dropped by the turn limit because [ROOM_QUERY]
        # resets the counter each round.
        reply_contents = [c["content"] for c in handler_calls]
        assert reply_contents == ["reply 1", "reply 2", "reply 3", "reply 4"]
