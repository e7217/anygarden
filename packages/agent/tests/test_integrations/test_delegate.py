"""Tests for the /delegate command parsing."""

import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anygarden_agent.integrations.delegate import (
    _register_reply_callback,
    parse_delegate,
)


class TestParseDelegate:
    def test_basic(self):
        r = parse_delegate("/delegate 디자인검토 API 스키마 리뷰해줘")
        assert r is not None
        assert r.sub_room_name == "디자인검토"
        assert r.task == "API 스키마 리뷰해줘"

    def test_with_mention_prefix(self):
        r = parse_delegate("@테스트에이전트 /delegate 서브룸 작업내용 여기")
        assert r is not None
        assert r.sub_room_name == "서브룸"
        assert r.task == "작업내용 여기"

    def test_multiline_task(self):
        r = parse_delegate("/delegate 코드리뷰 이 코드를 봐줘\n```\nprint('hello')\n```")
        assert r is not None
        assert r.sub_room_name == "코드리뷰"
        assert "print('hello')" in r.task

    def test_no_match_regular_message(self):
        assert parse_delegate("안녕하세요") is None

    def test_no_match_missing_room_name(self):
        assert parse_delegate("/delegate") is None

    def test_no_match_missing_task(self):
        assert parse_delegate("/delegate 서브룸") is None

    def test_no_match_similar_but_wrong(self):
        assert parse_delegate("/delegating 서브룸 작업") is None

    def test_whitespace_handling(self):
        r = parse_delegate("  @agent  /delegate  myroom  do something  ")
        assert r is not None
        assert r.sub_room_name == "myroom"
        assert r.task == "do something"


def _make_client():
    client = MagicMock()
    client._my_participant_ids = {"my-pid"}
    client._message_handlers = []
    client.send = AsyncMock()
    return client


class TestRegisterReplyCallback:
    """#445 (10b) — the safety-timeout task is scheduled with
    ``asyncio.get_running_loop().create_task`` (not the deprecated
    ``get_event_loop``). ``_register_reply_callback`` runs inside the
    adapter's already-running loop, so ``get_running_loop`` is the
    correct, warning-free call."""

    def test_uses_get_running_loop_not_get_event_loop(self):
        client = _make_client()

        with patch(
            "anygarden_agent.integrations.delegate.asyncio"
        ) as mock_asyncio:
            create_task = MagicMock()
            mock_asyncio.get_running_loop.return_value.create_task = create_task
            mock_asyncio.sleep = AsyncMock()

            _register_reply_callback(
                client,
                parent_room_id="room-a",
                sub_room_id="room-b",
                sub_room_name="서브룸",
            )

        # Scheduling went through get_running_loop, not get_event_loop.
        mock_asyncio.get_running_loop.assert_called_once_with()
        mock_asyncio.get_event_loop.assert_not_called()
        create_task.assert_called_once()
        # And the one-shot reply handler is registered.
        assert len(client._message_handlers) == 1

    @pytest.mark.asyncio
    async def test_no_deprecation_warning_inside_running_loop(self):
        """Behavioral guard: invoked inside a real running loop the
        registration emits no DeprecationWarning (which the deprecated
        ``get_event_loop()`` would raise when no current loop is set)."""
        client = _make_client()

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            _register_reply_callback(
                client,
                parent_room_id="room-a",
                sub_room_id="room-b",
                sub_room_name="서브룸",
            )

        assert len(client._message_handlers) == 1
