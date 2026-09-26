"""Tests for the /delegate command parsing."""

import asyncio
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from anygarden_agent.integrations.delegate import (
    _register_reply_callback,
    DelegateRequest,
    execute_delegate,
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
    client._delegate_result_handlers = []
    client._delegation_cleanups = set()
    client._turn_context = {}
    client._agent_id = "own-agent"
    client._tasks = {"room-b": object()}
    client.send = AsyncMock()
    client.sendLifecycle = AsyncMock()
    client.find_sub_room = AsyncMock(return_value="room-b")
    client.get_room_participants = AsyncMock(return_value=[{"id": "worker", "kind": "agent", "agent_id": "other-agent", "role": "member"}])
    client.wait_for_room = AsyncMock()
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
                delegation_id="d1", target_participant_ids={"worker"},
            )

        # Scheduling went through get_running_loop, not get_event_loop.
        mock_asyncio.get_running_loop.assert_called_once_with()
        mock_asyncio.get_event_loop.assert_not_called()
        create_task.assert_called_once()
        create_task.call_args.args[0].close()
        # And the one-shot reply handler is registered.
        assert len(client._delegate_result_handlers) == 1

    @pytest.mark.asyncio
    async def test_no_deprecation_warning_inside_running_loop(self):
        """Behavioral guard: invoked inside a real running loop the
        registration emits no DeprecationWarning (which the deprecated
        ``get_event_loop()`` would raise when no current loop is set)."""
        client = _make_client()

        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            cancel = _register_reply_callback(
                client,
                parent_room_id="room-a",
                sub_room_id="room-b",
                sub_room_name="서브룸",
                delegation_id="d1", target_participant_ids={"worker"},
            )

        assert len(client._delegate_result_handlers) == 1
        cancel()


def reply(delegation_id="d1", sender="worker", outcome="ok", content="done"):
    return {"room_id": "room-b", "participant_id": sender, "content": content,
            "metadata": {"delegation_id": delegation_id, "delegation_outcome": outcome}}


@pytest.mark.asyncio
async def test_concurrent_delegations_ignore_unrelated_messages_and_keep_parent_threads():
    client = _make_client()
    for identity in ("d1", "d2"):
        _register_reply_callback(
            client, "parent-" + identity, "room-b", "worker room",
            delegation_id=identity, target_participant_ids={"worker"},
            parent_request_id="request-" + identity, thread_root_id="thread-" + identity,
        )
    handlers = list(client._delegate_result_handlers)
    for message in [reply(sender="human"), reply("other"), reply(outcome="progress"),
                    {"room_id": "room-b", "participant_id": "worker", "content": "unrelated"}]:
        for handler in handlers:
            await handler(message)
    client.send.assert_not_awaited()
    for identity in ("d2", "d1"):
        for handler in handlers:
            await handler(reply(identity))
    assert [call.args[0] for call in client.send.await_args_list] == ["parent-d2", "parent-d1"]
    for call, identity in zip(client.send.await_args_list, ("d2", "d1")):
        assert call.kwargs["thread_root_id"] == "thread-" + identity
        assert call.kwargs["metadata"]["request_id"] == "request-" + identity
    assert client._delegate_result_handlers == []
    await handlers[0](reply())
    assert client.send.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "timeout", "cancelled", "rejected", "retry_exhausted"])
async def test_failure_is_reported_as_failure(outcome):
    client = _make_client()
    _register_reply_callback(client, "parent", "room-b", "worker room",
                             delegation_id="d1", target_participant_ids={"worker"})
    await client._delegate_result_handlers[0](reply(outcome=outcome))
    assert client.send.await_args.kwargs["metadata"]["delegation_outcome"] == outcome
    assert " 결과:" not in client.send.await_args.args[1]
    assert client._delegate_result_handlers == []


@pytest.mark.asyncio
async def test_timeout_removes_callback_before_reporting_and_ignores_late_reply():
    client = _make_client()
    _register_reply_callback(client, "parent", "room-b", "worker room", timeout=0.01,
                             delegation_id="d1", target_participant_ids={"worker"})
    handler = client._delegate_result_handlers[0]
    await asyncio.sleep(0.03)
    assert not client._delegate_result_handlers
    assert client.send.await_args.kwargs["metadata"]["delegation_outcome"] == "timeout"
    await handler(reply())
    assert client.send.await_count == 1


@pytest.mark.asyncio
async def test_fast_reply_during_forward_is_not_lost():
    client = _make_client()

    async def send(room_id, content, **kwargs):
        if room_id == "room-b":
            assert "request_id" not in kwargs["metadata"]
            for handler in list(client._delegate_result_handlers):
                await handler(reply(kwargs["metadata"]["delegation_id"]))

    client.send.side_effect = send
    await execute_delegate(client, {"room_id": "parent", "root_message_id": "thread",
                                   "metadata": {"request_id": "parent-request"}},
                           DelegateRequest("worker room", "task"))
    results = [call for call in client.send.await_args_list if call.kwargs.get("metadata", {}).get("delegation_outcome")]
    assert len(results) == 1
    assert results[0].kwargs["metadata"]["request_id"] == "parent-request"
    assert results[0].kwargs["thread_root_id"] == "thread"
    assert not client._delegate_result_handlers


@pytest.mark.asyncio
async def test_forward_failure_cleans_pending_callback_and_reports_failure():
    client = _make_client()

    async def send(room_id, content, **kwargs):
        if room_id == "room-b":
            raise RuntimeError("disconnected")

    client.send.side_effect = send
    await execute_delegate(client, {"room_id": "parent"}, DelegateRequest("worker room", "task"))
    assert not client._delegate_result_handlers
    assert client.send.await_args.kwargs["metadata"]["delegation_outcome"] == "failed"
    assert "전달했습니다" not in client.send.await_args.args[1]


@pytest.mark.asyncio
async def test_own_agent_in_another_room_is_not_a_delegation_target():
    client = _make_client()
    client.get_room_participants.return_value = [{"id": "own-other-pid", "kind": "agent", "agent_id": "own-agent"}]
    await execute_delegate(client, {"room_id": "parent"}, DelegateRequest("worker room", "task"))
    assert not client._delegate_result_handlers
    assert client.send.await_count == 1
    assert client.send.await_args.kwargs["metadata"]["delegation_outcome"] == "failed"


@pytest.mark.asyncio
async def test_nested_delegate_returns_the_upstream_correlation():
    client = _make_client()

    async def send(room_id, content, **kwargs):
        if room_id == "room-b":
            child_id = kwargs["metadata"]["delegation_id"]
            assert child_id != "outer-delegation"
            for handler in list(client._delegate_result_handlers):
                await handler(reply(child_id))

    client.send.side_effect = send
    await execute_delegate(client, {"room_id": "parent", "metadata": {
        "request_id": "middle-request", "delegation_id": "outer-delegation",
    }}, DelegateRequest("worker room", "task"))
    result = [call for call in client.send.await_args_list if call.kwargs.get("metadata", {}).get("delegation_outcome")][0]
    assert result.kwargs["metadata"]["delegation_id"] == "outer-delegation"


@pytest.mark.asyncio
async def test_old_parent_attempt_cannot_complete_a_new_attempt():
    client = _make_client()
    old = {"turn_attempt": 1, "turn_generation": 1, "turn_lease": "old"}
    client._turn_context["parent-request"] = {**old, "turn_attempt": 2, "turn_lease": "new"}
    _register_reply_callback(client, "parent", "room-b", "worker room",
                             delegation_id="d1", target_participant_ids={"worker"},
                             parent_request_id="parent-request", parent_metadata=old)
    await client._delegate_result_handlers[0](reply())
    client.send.assert_not_awaited()
    client.sendLifecycle.assert_not_awaited()
    assert not client._delegate_result_handlers
    assert client._turn_context["parent-request"]["turn_lease"] == "new"


@pytest.mark.asyncio
async def test_result_is_retained_and_retried_after_parent_connection_failure():
    client = _make_client()
    client.send.side_effect = [RuntimeError("disconnected"), None]
    _register_reply_callback(client, "parent", "room-b", "worker room", timeout=1,
                             delegation_id="d1", target_participant_ids={"worker"})
    await client._delegate_result_handlers[0](reply(content="completed work"))
    assert len(client._delegate_result_handlers) == 1
    await asyncio.sleep(0.02)
    assert client.send.await_count == 2
    assert client.send.await_args.kwargs["metadata"]["delegation_outcome"] == "ok"
    assert "completed work" in client.send.await_args.args[1]
    assert not client._delegate_result_handlers


@pytest.mark.asyncio
async def test_client_routes_control_results_before_engine_and_loop_guard():
    from anygarden_agent.client import ChatClient

    client = ChatClient("ws://test", token="test")
    client.send = AsyncMock()
    client.sendLifecycle = AsyncMock()
    primary = AsyncMock(side_effect=AssertionError("results must not wake an engine"))
    client.on_message(primary)
    client._agent_turn_count["room-b"] = client.max_agent_turns + 1
    _register_reply_callback(client, "parent", "room-b", "worker room",
                             delegation_id="d1", target_participant_ids={"worker"})
    frame = {**reply(), "type": "message", "seq": 1}
    frame["metadata"]["_nonce"] = "peer"
    await client._process_frame("room-b", frame)
    await client._process_frame("room-b", frame)
    assert client.send.await_count == 1
    assert client.send.await_args.kwargs["metadata"]["delegation_outcome"] == "ok"
    primary.assert_not_awaited()
    await client.close()


@pytest.mark.asyncio
async def test_client_close_cancels_pending_delegation_watchers():
    from anygarden_agent.client import ChatClient

    client = ChatClient("ws://test", token="test")
    _register_reply_callback(client, "parent", "room-b", "worker room",
                             delegation_id="d1", target_participant_ids={"worker"})
    await client.close()
    assert not client._delegate_result_handlers
    assert not client._delegation_cleanups
