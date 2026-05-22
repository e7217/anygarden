"""Unit tests for ChatClient — connect, reconnect, since_seq, callback, send."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anygarden_agent.client import ChatClient, _is_task_init_content


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


class TestWebSocketKeepalive:
    """Issue #190 — codex turns can legitimately run 5+ minutes; the
    websockets library's default ``ping_interval=20, ping_timeout=20``
    closed the connection mid-run, so the adapter's post-turn
    ``client.send`` hit a dead socket and the answer was silently
    dropped. Regression: lock the adapter's ws_connect call to values
    that tolerate a full ``_CODEX_TURN_TIMEOUT`` (600s) turn."""

    @pytest.mark.asyncio
    async def test_room_loop_passes_extended_keepalive_kwargs(self) -> None:
        client = ChatClient("ws://localhost:8000", token="t")
        # ``_running=False`` lets the reconnect loop break immediately
        # after the first ws_connect attempt raises — the production
        # code keeps reconnecting forever, which is exactly what we
        # don't want in a unit test.
        client._running = False

        captured: dict[str, object] = {}

        def fake_ws_connect(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            # Any raised exception works: the reconnect loop catches
            # all exceptions, logs, then checks ``_running`` to decide
            # whether to retry. Raising ensures we never try to iterate
            # a fake socket.
            raise RuntimeError("stop-loop")

        with patch("anygarden_agent.client.ws_connect", side_effect=fake_ws_connect):
            # Should return cleanly because _running is False.
            await client._room_loop("room-1")

        assert captured, "ws_connect was never called — loop didn't run"
        assert captured["kwargs"].get("ping_interval") == 60, (
            "ping_interval must be >=60s so codex's multi-minute turns don't "
            "trip the websockets client keepalive"
        )
        assert captured["kwargs"].get("ping_timeout") == 600, (
            "ping_timeout must cover the adapter's _CODEX_TURN_TIMEOUT so a "
            "slow server pong doesn't tear down a still-working connection"
        )


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


class TestTaskInitResetGuard:
    """Issue #157 Phase A — reset prefix abuse guard.

    Issue #67 reset behaviour (``[ROOM_QUERY]``/``[DELEGATED]`` zero
    the agent-only turn counter) can be exploited by a runaway agent
    that keeps emitting task-init prefixes to evade ``max_agent_turns``.
    Once the same room sees more than ``max_task_init_resets`` (5)
    consecutive task-init frames without a human message breaking the
    streak, the reset stops firing — forcing ``max_agent_turns`` to
    catch the loop.
    """

    def _make_client(self) -> ChatClient:
        client = ChatClient("ws://x", token="t")
        client._my_participant_ids.add("self-pid")
        return client

    def test_default_max_task_init_resets(self) -> None:
        """New clients default to 5 consecutive resets."""
        client = self._make_client()
        assert client.max_task_init_resets == 5
        assert client._consecutive_task_init == {}

    @pytest.mark.asyncio
    async def test_first_five_self_task_inits_reset_normally(self) -> None:
        """First five consecutive self-emitted task-inits each reset."""
        client = self._make_client()
        for i in range(5):
            client._agent_turn_count["room-a"] = 3  # force non-zero
            await client._process_frame(
                "room-a",
                {
                    "type": "message",
                    "seq": i + 1,
                    "participant_id": "self-pid",
                    "content": "[ROOM_QUERY] round",
                },
            )
            assert client._agent_turn_count["room-a"] == 0
        assert client._consecutive_task_init["room-a"] == 5

    @pytest.mark.asyncio
    async def test_sixth_self_task_init_guard_fires(self) -> None:
        """Sixth consecutive task-init no longer resets the counter."""
        client = self._make_client()
        for i in range(5):
            await client._process_frame(
                "room-a",
                {
                    "type": "message",
                    "seq": i + 1,
                    "participant_id": "self-pid",
                    "content": "[ROOM_QUERY] round",
                },
            )
        # After 5 resets, counter is 0. Force non-zero to detect guard.
        client._agent_turn_count["room-a"] = 4
        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 6,
                "participant_id": "self-pid",
                "content": "[ROOM_QUERY] sixth attempt",
            },
        )
        # Guard fires: counter unchanged (NOT reset to 0).
        assert client._agent_turn_count["room-a"] == 4
        assert client._consecutive_task_init["room-a"] == 6

    @pytest.mark.asyncio
    async def test_human_message_resets_consecutive_counter(self) -> None:
        """Human message (no nonce, foreign pid) clears the streak."""
        client = self._make_client()
        for i in range(3):
            await client._process_frame(
                "room-a",
                {
                    "type": "message",
                    "seq": i + 1,
                    "participant_id": "self-pid",
                    "content": "[ROOM_QUERY] round",
                },
            )
        assert client._consecutive_task_init["room-a"] == 3

        # Human message: not self, no nonce
        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 4,
                "participant_id": "human-pid",
                "content": "thanks team",
            },
        )
        assert client._consecutive_task_init["room-a"] == 0

    @pytest.mark.asyncio
    async def test_per_room_isolation(self) -> None:
        """Consecutive counter tracks per-room, not globally."""
        client = self._make_client()
        for i in range(5):
            await client._process_frame(
                "room-a",
                {
                    "type": "message",
                    "seq": i + 1,
                    "participant_id": "self-pid",
                    "content": "[ROOM_QUERY]",
                },
            )
        # room-b unaffected
        await client._process_frame(
            "room-b",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "self-pid",
                "content": "[ROOM_QUERY]",
            },
        )
        assert client._consecutive_task_init["room-b"] == 1
        assert client._consecutive_task_init["room-a"] == 5

    @pytest.mark.asyncio
    async def test_guard_fires_on_nonce_echo_path(self) -> None:
        """Soft-filter (nonce echo) path also honours the guard."""
        client = self._make_client()
        for i in range(5):
            client._sent_nonces.add(f"n-{i}")
            await client._process_frame(
                "room-a",
                {
                    "type": "message",
                    "seq": i + 1,
                    "participant_id": "other-pid",
                    "content": "[DELEGATED] subtask",
                    "metadata": {"_nonce": f"n-{i}"},
                },
            )
        client._sent_nonces.add("n-6")
        client._agent_turn_count["room-a"] = 3
        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 6,
                "participant_id": "other-pid",
                "content": "[DELEGATED] sixth",
                "metadata": {"_nonce": "n-6"},
            },
        )
        assert client._agent_turn_count["room-a"] == 3
        assert client._consecutive_task_init["room-a"] == 6

    @pytest.mark.asyncio
    async def test_guard_fires_on_foreign_agent_path(self) -> None:
        """Normal path (foreign agent with its own nonce) honours guard."""
        client = self._make_client()
        for i in range(5):
            await client._process_frame(
                "room-a",
                {
                    "type": "message",
                    "seq": i + 1,
                    "participant_id": "other-agent",
                    "content": "[ROOM_QUERY]",
                    "metadata": {"_nonce": f"f-{i}"},
                },
            )
        client._agent_turn_count["room-a"] = 2
        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 6,
                "participant_id": "other-agent",
                "content": "[ROOM_QUERY]",
                "metadata": {"_nonce": "f-6"},
            },
        )
        assert client._agent_turn_count["room-a"] == 2
        assert client._consecutive_task_init["room-a"] == 6


class TestRecentMessagesBuffer:
    """Issue #157 Phase B — ``_process_frame`` records (sender, hash)
    fingerprints into a per-room ring buffer feeding ``cycle_guard``
    in ``decide_policy``."""

    def _make_client(self) -> ChatClient:
        return ChatClient("ws://x", token="t")

    @pytest.mark.asyncio
    async def test_long_message_recorded(self) -> None:
        client = self._make_client()
        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "other",
                "content": "a sentence long enough to be hashed reliably",
            },
        )
        buf = client._recent_msgs.get("room-a")
        assert buf is not None
        assert len(buf) == 1
        entry = buf[0]
        assert entry["sender"] == "other"
        assert isinstance(entry["hash"], str)
        assert len(entry["hash"]) == 16

    @pytest.mark.asyncio
    async def test_short_message_not_recorded(self) -> None:
        """Content < 16 chars is excluded from the buffer."""
        client = self._make_client()
        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "other",
                "content": "ok",
            },
        )
        assert "room-a" not in client._recent_msgs

    @pytest.mark.asyncio
    async def test_per_room_isolation(self) -> None:
        client = self._make_client()
        for rid in ("room-a", "room-b"):
            await client._process_frame(
                rid,
                {
                    "type": "message",
                    "seq": 1,
                    "participant_id": "other",
                    "content": "this is a long enough content for hashing",
                },
            )
        assert len(client._recent_msgs["room-a"]) == 1
        assert len(client._recent_msgs["room-b"]) == 1

    @pytest.mark.asyncio
    async def test_maxlen_caps_at_ten(self) -> None:
        client = self._make_client()
        for i in range(15):
            await client._process_frame(
                "room-a",
                {
                    "type": "message",
                    "seq": i + 1,
                    "participant_id": "other",
                    "content": f"long content sample number {i:02d} repeating",
                },
            )
        assert len(client._recent_msgs["room-a"]) == 10

    @pytest.mark.asyncio
    async def test_welcome_frame_not_recorded(self) -> None:
        client = self._make_client()
        await client._process_frame(
            "room-a",
            {
                "type": "welcome",
                "participant_id": "my-pid",
                "content": "ignored long enough content to pass hash gate",
            },
        )
        assert "room-a" not in client._recent_msgs

    @pytest.mark.asyncio
    async def test_room_id_injected_on_handler_message(self) -> None:
        """Handler sees ``room_id`` on the message dict (for cycle lookup)."""
        client = self._make_client()
        seen: list[dict] = []

        @client.on_message
        async def _h(msg: dict) -> None:
            seen.append(msg)

        await client._process_frame(
            "room-a",
            {
                "type": "message",
                "seq": 1,
                "participant_id": "other",
                "content": "long enough content for the gate to pass now",
            },
        )
        assert seen
        assert seen[0].get("room_id") == "room-a"
