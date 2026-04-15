"""Tests for room_query module — representative agent opinion collection."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from doorae_agent.integrations.room_query import (
    RoomQuery,
    _strip_room_mention,
    execute_room_query,
    parse_room_query,
)


def _make_client(my_pids: set | None = None):
    client = MagicMock()
    client._my_participant_ids = my_pids or {"my-pid"}
    client._tasks = {"room-a": MagicMock(), "room-b": MagicMock()}
    client._message_handlers = []
    client.send = AsyncMock()
    client.join_room = AsyncMock()
    client.get_room_participants = AsyncMock(return_value=[
        {"id": "my-pid", "kind": "agent"},
        {"id": "agent-d-pid", "kind": "agent"},
        {"id": "agent-e-pid", "kind": "agent"},
    ])
    return client


class TestParseRoomQuery:
    def test_parse_with_room_query(self):
        msg = {
            "content": "<#room:xyz> 의견?",
            "metadata": {
                "room_query": {
                    "target_room_id": "xyz",
                    "source_room_id": "abc",
                    "query_id": "q-1",
                    "source_participant_id": "user-pid",
                },
            },
        }
        rq = parse_room_query(msg)
        assert rq is not None
        assert rq.target_room_id == "xyz"
        assert rq.source_room_id == "abc"
        assert rq.query_id == "q-1"
        assert rq.source_participant_id == "user-pid"

    def test_parse_legacy_metadata_without_query_id(self):
        """Legacy queue items (pre-#55 broadcast still in flight)
        must not crash. ``query_id`` falls back to empty string and
        ``source_participant_id`` to ``None`` so downstream code can
        still send the forward; the UI just won't have the matching
        token to dismiss the chip — acceptable since the affected
        window is the single deploy in which the upgrade lands."""
        msg = {
            "content": "<#room:xyz> 의견?",
            "metadata": {
                "room_query": {"target_room_id": "xyz", "source_room_id": "abc"},
            },
        }
        rq = parse_room_query(msg)
        assert rq is not None
        assert rq.query_id == ""
        assert rq.source_participant_id is None

    def test_parse_without_room_query(self):
        msg = {"content": "hello", "metadata": {}}
        assert parse_room_query(msg) is None

    def test_parse_no_metadata(self):
        msg = {"content": "hello"}
        assert parse_room_query(msg) is None


class TestStripRoomMention:
    """The forwarded ``[ROOM_QUERY]`` content must not carry the
    ``<#room:...>`` token that triggered the original routing —
    leaving it in causes the server's ``parse_mentions`` to
    re-attach ``room_query`` metadata on the forward, which is the
    infinite loop bug this whole module was patched for."""

    def test_strips_simple_token(self) -> None:
        assert (
            _strip_room_mention("<#room:abc-123> 내일 동탄 날씨는?")
            == "내일 동탄 날씨는?"
        )

    def test_strips_multiple_tokens(self) -> None:
        assert (
            _strip_room_mention("<#room:a> hi <#room:b> there")
            == "hi there"
        )

    def test_leaves_unrelated_text_intact(self) -> None:
        assert _strip_room_mention("그냥 평범한 질문") == "그냥 평범한 질문"

    def test_does_not_eat_user_mention(self) -> None:
        assert (
            _strip_room_mention("<@user:pid> 봐줘 <#room:r1>")
            == "<@user:pid> 봐줘"
        )

    def test_idempotent(self) -> None:
        once = _strip_room_mention("<#room:r1> hi")
        twice = _strip_room_mention(once)
        assert once == twice == "hi"

    def test_handles_token_only_content(self) -> None:
        # Edge case: content was ONLY the routing token. After the
        # strip we have an empty string. ``execute_room_query`` falls
        # back to the original content in that case (so the question
        # isn't lost), but the strip helper itself returns ``""``.
        assert _strip_room_mention("<#room:r1>") == ""


def _make_query(**overrides) -> RoomQuery:
    """Build a ``RoomQuery`` with the new (post-#55) fields populated.
    Tests can pass overrides for the bits they actually care about."""
    defaults = dict(
        target_room_id="room-b",
        source_room_id="room-a",
        content="API 설계 의견?",
        query_id="q-test",
        source_participant_id="user-source-pid",
    )
    defaults.update(overrides)
    return RoomQuery(**defaults)


class TestExecuteRoomQuery:
    @pytest.mark.asyncio
    async def test_sends_room_query_to_target(self):
        """Representative sends [ROOM_QUERY] to target room."""
        client = _make_client()
        query = _make_query()

        with patch("doorae_agent.integrations.room_query.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value.create_task = MagicMock()
            mock_asyncio.sleep = AsyncMock()
            await execute_room_query(client, {}, query)

        # Should send [ROOM_QUERY] to target room
        assert client.send.call_count == 1
        args, kwargs = client.send.call_args
        assert args == ("room-b", "[ROOM_QUERY] API 설계 의견?")
        # Issue #55: forward carries metadata so the target-room
        # bubble can render the ``↪ #source · @author`` badge.
        forward_meta = kwargs["metadata"]["room_query_forward"]
        assert forward_meta == {
            "source_room_id": "room-a",
            "source_participant_id": "user-source-pid",
            "query_id": "q-test",
        }
        # Should register a message handler
        assert len(client._message_handlers) == 1

    @pytest.mark.asyncio
    async def test_forward_strips_room_mention_token(self):
        """Regression guard for the infinite forwarding loop. The
        forwarded ``[ROOM_QUERY] ...`` payload must NOT include the
        ``<#room:...>`` token from the original — otherwise the
        server re-attaches ``room_query`` metadata and the target
        room's representative kicks off another hop."""
        client = _make_client()
        query = _make_query(content="<#room:room-b> 내일 동탄 날씨는?")

        with patch("doorae_agent.integrations.room_query.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value.create_task = MagicMock()
            mock_asyncio.sleep = AsyncMock()
            await execute_room_query(client, {}, query)

        args, _ = client.send.call_args
        assert args == ("room-b", "[ROOM_QUERY] 내일 동탄 날씨는?")

    @pytest.mark.asyncio
    async def test_forward_falls_back_when_strip_empties_content(self):
        """If the original content was *only* the routing token,
        stripping leaves an empty string. The fallback restores the
        original so we never send ``"[ROOM_QUERY] "`` (which is
        useless to the target room agents)."""
        client = _make_client()
        query = _make_query(content="<#room:room-b>")

        with patch("doorae_agent.integrations.room_query.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value.create_task = MagicMock()
            mock_asyncio.sleep = AsyncMock()
            await execute_room_query(client, {}, query)

        args, _ = client.send.call_args
        assert args == ("room-b", "[ROOM_QUERY] <#room:room-b>")

    @pytest.mark.asyncio
    async def test_solo_representative_delivers_solo_result(self):
        """Issue #55: if no other agents are in the target room the
        representative MUST still deliver a result message back to
        the source room — otherwise the user's banner chip would
        sit pending forever. The ``status="solo"`` tag tells the UI
        to render a distinct ``대상 방에 응답할 에이전트가 없음``
        header instead of the timeout one."""
        client = _make_client()
        # Only self in the room
        client.get_room_participants = AsyncMock(return_value=[
            {"id": "my-pid", "kind": "agent"},
        ])
        query = _make_query(content="질문")

        await execute_room_query(client, {}, query)

        # Should send the solo result message to the source room
        assert client.send.call_count == 1
        args, kwargs = client.send.call_args
        assert args[0] == "room-a"
        # Body still uses the existing ``[취합 결과]`` prefix so
        # ``should_respond``'s startswith path is unaffected.
        assert args[1].startswith("[취합 결과]")
        result_meta = kwargs["metadata"]["room_query_result"]
        assert result_meta["query_id"] == "q-test"
        assert result_meta["target_room_id"] == "room-b"
        assert result_meta["status"] == "solo"
        assert result_meta["responded"] == 0
        assert result_meta["expected"] == 0
        assert result_meta["responses"] == []
        # No ``[ROOM_QUERY]`` fanout, no callback registration —
        # the solo path short-circuits before either.
        assert len(client._message_handlers) == 0

    @pytest.mark.asyncio
    async def test_expected_count_excludes_offline(self):
        """#54 — offline agents MUST NOT count toward ``expected_count``.

        A dead process otherwise forces every cross-room query to
        wait the full timeout and then report "(1/2) — 1명 미응답",
        which is misleading: the agent wasn't slow, it was gone.
        """
        client = _make_client()
        # One online, one offline target agent besides self.
        client.get_room_participants = AsyncMock(return_value=[
            {"id": "my-pid", "kind": "agent"},
            {
                "id": "agent-online",
                "kind": "agent",
                "online": True,
                "display_name": "alive",
            },
            {
                "id": "agent-offline",
                "kind": "agent",
                "online": False,
                "display_name": "dead",
                "last_seen_at": None,
            },
        ])
        query = RoomQuery(
            target_room_id="room-b",
            source_room_id="room-a",
            content="의견?",
        )

        with patch("doorae_agent.integrations.room_query.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value.create_task = MagicMock()
            mock_asyncio.sleep = AsyncMock()
            await execute_room_query(client, {}, query)

        # Only the online agent should be awaited → one response
        # completes the collection.
        handler = client._message_handlers[0]
        client.send.reset_mock()
        await handler({
            "room_id": "room-b",
            "participant_id": "agent-online",
            "content": "좋습니다",
        })
        assert client.send.call_count == 1
        body = client.send.call_args[0][1]
        # Header should read "1/1", not "1/2".
        assert "1/1" in body
        assert "2명 미응답" not in body

    @pytest.mark.asyncio
    async def test_missing_responder_label_offline(self):
        """#54 — the offline candidate is listed in the summary with a
        ``(offline, ...)`` tag so users can see *why* they missed."""
        client = _make_client()
        client.get_room_participants = AsyncMock(return_value=[
            {"id": "my-pid", "kind": "agent"},
            {
                "id": "agent-online",
                "kind": "agent",
                "online": True,
                "display_name": "alive",
            },
            {
                "id": "agent-offline",
                "kind": "agent",
                "online": False,
                "display_name": "dead-codex",
                "last_seen_at": None,
            },
        ])
        query = RoomQuery(
            target_room_id="room-b",
            source_room_id="room-a",
            content="의견?",
        )

        with patch("doorae_agent.integrations.room_query.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value.create_task = MagicMock()
            mock_asyncio.sleep = AsyncMock()
            await execute_room_query(client, {}, query)

        handler = client._message_handlers[0]
        client.send.reset_mock()
        await handler({
            "room_id": "room-b",
            "participant_id": "agent-online",
            "content": "좋습니다",
        })

        body = client.send.call_args[0][1]
        assert "dead-codex" in body
        assert "offline" in body

    @pytest.mark.asyncio
    async def test_callback_collects_and_synthesizes(self):
        """Callback collects all responses then delivers summary
        with ``status="completed"`` metadata."""
        client = _make_client()
        query = _make_query(content="API 의견?")

        with patch("doorae_agent.integrations.room_query.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value.create_task = MagicMock()
            mock_asyncio.sleep = AsyncMock()
            await execute_room_query(client, {}, query)

        handler = client._message_handlers[0]
        client.send.reset_mock()

        # Simulate agent D response
        await handler({"room_id": "room-b", "participant_id": "agent-d-pid", "content": "GraphQL이 좋겠습니다"})
        # Not yet complete (1/2)
        assert client.send.call_count == 0

        # Simulate agent E response
        await handler({"room_id": "room-b", "participant_id": "agent-e-pid", "content": "REST가 더 적합합니다"})
        # Now complete (2/2) — should synthesize and deliver
        assert client.send.call_count == 1
        args, kwargs = client.send.call_args
        assert args[0] == "room-a"  # delivered to source room
        # Body format unchanged (regression guard for ``should_
        # respond`` startswith path).
        assert "[취합 결과]" in args[1]
        assert "2/2" in args[1]
        # Issue #55: structured result metadata.
        result_meta = kwargs["metadata"]["room_query_result"]
        assert result_meta["query_id"] == "q-test"
        assert result_meta["target_room_id"] == "room-b"
        assert result_meta["status"] == "completed"
        assert result_meta["responded"] == 2
        assert result_meta["expected"] == 2
        assert result_meta["responses"] == [
            {"participant_id": "agent-d-pid", "content": "GraphQL이 좋겠습니다"},
            {"participant_id": "agent-e-pid", "content": "REST가 더 적합합니다"},
        ]

    @pytest.mark.asyncio
    async def test_timeout_path_marks_status_timeout(self):
        """If the safety timeout fires before all responses arrive
        the result still ships with ``status="timeout"`` so the
        banner chip flips from pending → ⚠ and the result card can
        show ``N/M 응답 · K명 미응답``."""
        client = _make_client()
        query = _make_query(content="API 의견?")

        # Patch only the create_task / sleep calls so we can drive
        # the timeout path manually instead of waiting 5 minutes.
        with patch("doorae_agent.integrations.room_query.asyncio") as mock_asyncio:
            captured: dict[str, Any] = {}

            def _capture_task(coro):
                captured["coro"] = coro
                return MagicMock()

            mock_asyncio.get_event_loop.return_value.create_task = (
                _capture_task
            )
            mock_asyncio.sleep = AsyncMock()
            await execute_room_query(client, {}, query)

            handler = client._message_handlers[0]
            client.send.reset_mock()

            # Only one of two agents responds.
            await handler({
                "room_id": "room-b",
                "participant_id": "agent-d-pid",
                "content": "GraphQL이 좋겠습니다",
            })
            assert client.send.call_count == 0

            # Now run the captured cleanup coroutine — sleep is
            # already a no-op AsyncMock so it fires immediately.
            await captured["coro"]

        assert client.send.call_count == 1
        args, kwargs = client.send.call_args
        assert args[0] == "room-a"
        assert "[취합 결과]" in args[1]
        result_meta = kwargs["metadata"]["room_query_result"]
        assert result_meta["status"] == "timeout"
        assert result_meta["responded"] == 1
        assert result_meta["expected"] == 2
        assert len(result_meta["responses"]) == 1


class TestForwardBodyRegression:
    """Plan §6.1: ``[ROOM_QUERY]`` / ``[취합 결과]`` body prefixes
    are load-bearing — ``should_respond`` keys off ``startswith`` to
    decide whether to ignore a forwarded message. If we accidentally
    rename them while structuring the metadata, agents in the target
    room would start replying to the forward as if it were a
    regular question, kicking off the loop that issue #42 closed."""

    @pytest.mark.asyncio
    async def test_forward_body_unchanged(self):
        client = _make_client()
        query = _make_query(content="quick check")
        with patch("doorae_agent.integrations.room_query.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value.create_task = MagicMock()
            mock_asyncio.sleep = AsyncMock()
            await execute_room_query(client, {}, query)
        args, _ = client.send.call_args
        assert args[1].startswith("[ROOM_QUERY] ")

    @pytest.mark.asyncio
    async def test_result_body_unchanged(self):
        client = _make_client()
        # solo path is the simplest way to drive a result body.
        client.get_room_participants = AsyncMock(return_value=[
            {"id": "my-pid", "kind": "agent"},
        ])
        query = _make_query(content="quick check")
        await execute_room_query(client, {}, query)
        args, _ = client.send.call_args
        assert args[1].startswith("[취합 결과]")
