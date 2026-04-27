"""Integration tests for the Codex app-server adapter."""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from doorae_agent.integrations.codex import (
    CodexAdapter,
    _make_lenient_parse_notification,
    integrate_with_codex,
)
import doorae_agent.integrations.codex as codex_mod


def _make_fake_codex_module():
    """Create a fake codex module for testing.

    Also builds a fake ``codex.options`` submodule with a stub
    ``ThreadStartOptions`` so Issue #134 bypass flags flow through
    the adapter and can be asserted from the test side.
    """
    mock_thread = MagicMock()
    mock_thread.run_text = MagicMock(return_value="Hello from codex")

    mock_codex = MagicMock()
    mock_codex.start_thread = MagicMock(return_value=mock_thread)
    mock_codex.close = MagicMock()

    module = MagicMock()
    module.Codex = MagicMock(return_value=mock_codex)

    class FakeThreadStartOptions:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    options_mod = MagicMock()
    options_mod.ThreadStartOptions = FakeThreadStartOptions
    return module, options_mod, mock_codex, mock_thread


def _patch_codex(fake_mod, options_mod):
    """Stub both ``codex`` and ``codex.options`` in sys.modules."""
    return patch.dict(sys.modules, {
        "codex": fake_mod,
        "codex.options": options_mod,
    })


class TestCodexAdapter:
    def test_default_sandbox_is_workspace_write(self) -> None:
        """Default sandbox must be workspace-write."""
        adapter = CodexAdapter()
        assert adapter._sandbox == "workspace-write"

    def test_default_model(self) -> None:
        adapter = CodexAdapter()
        assert adapter._model == "gpt-5.5"

    @pytest.mark.asyncio
    async def test_start_initializes_client(self) -> None:
        """start() creates Codex client."""
        fake_mod, options_mod, mock_codex, _ = _make_fake_codex_module()
        with _patch_codex(fake_mod, options_mod):
            adapter = CodexAdapter()
            await adapter.start()
            assert adapter._codex is mock_codex

    @pytest.mark.asyncio
    async def test_on_message_creates_thread_and_returns_response(self) -> None:
        """on_message creates a thread for the room and returns the response."""
        fake_mod, options_mod, mock_codex, mock_thread = _make_fake_codex_module()
        with _patch_codex(fake_mod, options_mod):
            adapter = CodexAdapter()
            await adapter.start()

            result = await adapter.on_message({
                "content": "Hello",
                "room_id": "room-1",
            })
            assert result == "Hello from codex"
            assert "room-1" in adapter._threads
            mock_codex.start_thread.assert_called_once()
            # Issue #190 — run_text is now always called with a
            # ``signal`` kwarg (threading.Event) so the timeout path
            # can abort stuck turns. Assert content + signal presence
            # without pinning the event instance.
            call = mock_thread.run_text.call_args
            assert call.args == ("Hello",)
            assert isinstance(call.kwargs.get("signal"), threading.Event)

    @pytest.mark.asyncio
    async def test_start_thread_passes_bypass_options(self) -> None:
        """Issue #134 — start_thread must receive approval_policy="never"
        and sandbox="workspace-write" so attached MCP servers can run
        tool calls without an interactive approval prompt that a
        headless agent can never answer.
        """
        fake_mod, options_mod, mock_codex, _ = _make_fake_codex_module()
        with _patch_codex(fake_mod, options_mod):
            adapter = CodexAdapter()
            await adapter.start()
            await adapter.on_message({
                "content": "Hello",
                "room_id": "room-1",
            })
            call = mock_codex.start_thread.call_args
            options_obj = None
            if call.args:
                options_obj = call.args[0]
            elif "options" in call.kwargs:
                options_obj = call.kwargs["options"]
            assert options_obj is not None, "start_thread was called without options"
            # ThreadStartOptions values are passed through pydantic in
            # production but our FakeThreadStartOptions stub copies
            # kwargs to attributes, so reading them back is trivial.
            assert getattr(options_obj, "approval_policy", None) == "never"
            assert getattr(options_obj, "sandbox", None) == "workspace-write"

    @pytest.mark.asyncio
    async def test_on_message_reuses_thread(self) -> None:
        """Subsequent messages to same room reuse the thread."""
        fake_mod, options_mod, mock_codex, mock_thread = _make_fake_codex_module()
        with _patch_codex(fake_mod, options_mod):
            adapter = CodexAdapter()
            await adapter.start()

            await adapter.on_message({"content": "msg1", "room_id": "room-1"})
            await adapter.on_message({"content": "msg2", "room_id": "room-1"})

            assert mock_codex.start_thread.call_count == 1
            assert mock_thread.run_text.call_count == 2

    @pytest.mark.asyncio
    async def test_on_message_returns_none_when_not_started(self) -> None:
        adapter = CodexAdapter()
        result = await adapter.on_message({"content": "Hello", "room_id": "r1"})
        assert result is None

    @pytest.mark.asyncio
    async def test_separate_threads_per_room(self) -> None:
        """Different rooms get different threads."""
        fake_mod, options_mod, mock_codex, _ = _make_fake_codex_module()
        mock_codex.start_thread = MagicMock(side_effect=lambda **kw: MagicMock(
            run_text=MagicMock(return_value="ok"),
        ))
        with _patch_codex(fake_mod, options_mod):
            adapter = CodexAdapter()
            await adapter.start()

            await adapter.on_message({"content": "a", "room_id": "room-1"})
            await adapter.on_message({"content": "b", "room_id": "room-2"})

            assert len(adapter._threads) == 2
            assert mock_codex.start_thread.call_count == 2

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self) -> None:
        """stop() clears threads and closes codex."""
        fake_mod, options_mod, mock_codex, _ = _make_fake_codex_module()
        with _patch_codex(fake_mod, options_mod):
            adapter = CodexAdapter()
            await adapter.start()
            adapter._threads["room-1"] = MagicMock()

            await adapter.stop()
            assert adapter._threads == {}
            assert adapter._codex is None
            mock_codex.close.assert_called_once()


class TestIntegrateWithCodex:
    @pytest.mark.asyncio
    async def test_integrate_registers_handler(self) -> None:
        """integrate_with_codex registers a message handler on the client."""
        from doorae_agent.client import ChatClient

        fake_mod, options_mod, _, _ = _make_fake_codex_module()
        with _patch_codex(fake_mod, options_mod):
            client = ChatClient("ws://localhost:8000", token="t", agent_name="Bot")
            assert len(client._message_handlers) == 0

            adapter = await integrate_with_codex(client)

            assert len(client._message_handlers) == 1
            assert isinstance(adapter, CodexAdapter)


class _FakeProtocolError(Exception):
    """Stand-in for ``codex.app_server.errors.AppServerProtocolError``.

    Lets the shim's error path be exercised without importing codex-python
    internals in the test environment."""


class _FakeGenericNotification:
    def __init__(self, method=None, params=None) -> None:
        self.method = method
        self.params = params


class TestInstallShim:
    """Issue #190 — the shim must patch every module that holds a
    reference to ``parse_notification``. The SDK's ``_session`` reads
    via ``from codex.app_server._protocol_helpers import
    parse_notification`` at import time, so patching only
    ``_protocol_helpers`` leaves the hot path unchanged and the bug
    stays live. Regression test pins this explicitly."""

    def test_patches_both_protocol_helpers_and_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Reset the idempotency flag so this test actually runs the
        # install logic even if a prior test booted an adapter.
        monkeypatch.setattr(codex_mod, "_PARSE_NOTIFICATION_PATCHED", False)

        original_parse = MagicMock(name="original_parse_notification")

        class _FakePh:
            parse_notification = original_parse
            GenericNotification = _FakeGenericNotification

        class _FakeSession:
            # Import-time local binding to the original, exactly like
            # the real SDK: ``from ... import parse_notification``.
            parse_notification = original_parse

        fake_app_server = MagicMock()
        fake_app_server._protocol_helpers = _FakePh
        fake_app_server._session = _FakeSession
        fake_app_server.errors = MagicMock()
        fake_app_server.errors.AppServerProtocolError = _FakeProtocolError

        with patch.dict(sys.modules, {
            "codex": MagicMock(),
            "codex.app_server": fake_app_server,
            "codex.app_server._protocol_helpers": _FakePh,
            "codex.app_server._session": _FakeSession,
            "codex.app_server.errors": fake_app_server.errors,
        }):
            codex_mod._install_parse_notification_shim()

        assert codex_mod._PARSE_NOTIFICATION_PATCHED is True
        # Original should be replaced in both modules.
        assert _FakePh.parse_notification is not original_parse
        assert _FakeSession.parse_notification is not original_parse
        # And both must point at the *same* wrapper so the hot-path in
        # _session and any diagnostic use via helpers stay in sync.
        assert _FakePh.parse_notification is _FakeSession.parse_notification

    def test_install_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(codex_mod, "_PARSE_NOTIFICATION_PATCHED", False)

        original = MagicMock(return_value="ok")

        class _FakePh:
            parse_notification = original
            GenericNotification = _FakeGenericNotification

        class _FakeSession:
            parse_notification = original

        fake_app_server = MagicMock()
        fake_app_server._protocol_helpers = _FakePh
        fake_app_server._session = _FakeSession
        fake_app_server.errors = MagicMock()
        fake_app_server.errors.AppServerProtocolError = _FakeProtocolError

        with patch.dict(sys.modules, {
            "codex": MagicMock(),
            "codex.app_server": fake_app_server,
            "codex.app_server._protocol_helpers": _FakePh,
            "codex.app_server._session": _FakeSession,
            "codex.app_server.errors": fake_app_server.errors,
        }):
            codex_mod._install_parse_notification_shim()
            first_wrapper = _FakePh.parse_notification
            codex_mod._install_parse_notification_shim()
            second_wrapper = _FakePh.parse_notification

        assert first_wrapper is second_wrapper, (
            "second install must not double-wrap — idempotent flag guards it"
        )


class TestLenientParseNotification:
    """Issue #190 — SDK raises ``AppServerProtocolError`` on known-method /
    unknown-payload combinations (e.g. ``item/completed`` with new
    ``ThreadItem`` variants from the bundled codex-cli binary). The shim
    downgrades those to a generic notification so the SDK's stream loop
    keeps flowing and ``task_complete`` still reaches the adapter."""

    def test_passthrough_when_original_succeeds(self) -> None:
        sentinel = object()
        original = MagicMock(return_value=sentinel)

        lenient = _make_lenient_parse_notification(
            original,
            _FakeGenericNotification,
            _FakeProtocolError,
        )

        msg = {"method": "item/completed", "params": {"item": {"type": "webSearch"}}}
        assert lenient(msg, strict=True) is sentinel
        # Shim downgrades to non-strict regardless of the caller's strict flag:
        # the SDK's strict mode is what surfaces the bug we're masking.
        original.assert_called_once_with(msg, strict=False)

    def test_falls_back_to_generic_on_protocol_error(self) -> None:
        original = MagicMock(side_effect=_FakeProtocolError("schema mismatch"))
        lenient = _make_lenient_parse_notification(
            original,
            _FakeGenericNotification,
            _FakeProtocolError,
        )

        msg = {
            "method": "item/completed",
            "params": {"item": {"type": "webSearchFuture"}, "threadId": "t"},
        }
        result = lenient(msg, strict=True)

        assert isinstance(result, _FakeGenericNotification)
        assert result.method == "item/completed"
        # params should be forwarded verbatim (copied, not aliased).
        assert result.params == msg["params"]
        assert result.params is not msg["params"]

    def test_reraises_when_method_not_string(self) -> None:
        """Completely malformed frames still raise — we only mask the
        known-method-unknown-payload case, not every validation failure."""
        err = _FakeProtocolError("bad frame")
        original = MagicMock(side_effect=err)
        lenient = _make_lenient_parse_notification(
            original,
            _FakeGenericNotification,
            _FakeProtocolError,
        )

        with pytest.raises(_FakeProtocolError):
            lenient({"method": 123, "params": {}}, strict=True)

    def test_reraises_when_params_not_mapping(self) -> None:
        err = _FakeProtocolError("bad frame")
        original = MagicMock(side_effect=err)
        lenient = _make_lenient_parse_notification(
            original,
            _FakeGenericNotification,
            _FakeProtocolError,
        )

        with pytest.raises(_FakeProtocolError):
            lenient({"method": "item/completed", "params": "not-a-dict"}, strict=True)


class TestCodexTurnTimeout:
    """Issue #190 — ``thread.run_text`` has no intrinsic timeout. A hung
    turn used to lock the room's WS receive loop indefinitely. The
    adapter now wraps the call in ``asyncio.wait_for`` + a
    ``threading.Event`` signal so the SDK can abort cleanly on timeout
    and the room stays responsive."""

    @pytest.mark.asyncio
    async def test_on_message_timeout_aborts_and_evicts_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_mod, options_mod, mock_codex, mock_thread = _make_fake_codex_module()

        captured: dict[str, object] = {}

        def slow_run_text(
            content: str,
            turn_options=None,
            *,
            signal: threading.Event | None = None,
        ):
            """Blocks until the signal fires or ~1s elapses.

            Mirrors how the codex SDK's signal watcher interrupts a stuck
            turn — polling the event keeps the worker thread from leaking
            after the test times out."""
            captured["content"] = content
            captured["signal"] = signal
            deadline = time.time() + 1.0
            while time.time() < deadline:
                if signal is not None and signal.is_set():
                    captured["aborted"] = True
                    return ""
                time.sleep(0.02)
            captured["aborted"] = False
            return "should-not-be-returned"

        mock_thread.run_text = slow_run_text

        # Drive the timeout below the worker's deadline so the wait_for
        # path fires deterministically without making the suite slow.
        monkeypatch.setattr(codex_mod, "_CODEX_TURN_TIMEOUT", 0.2)

        with _patch_codex(fake_mod, options_mod):
            adapter = CodexAdapter()
            await adapter.start()

            result = await adapter.on_message(
                {"content": "slow-question", "room_id": "room-1"}
            )

        assert result is None, "timeout path must not deliver a reply"
        assert "room-1" not in adapter._threads, (
            "broken thread must be evicted so the next turn starts fresh"
        )
        # The signal must have been created and set so the SDK can abort.
        signal = captured.get("signal")
        assert isinstance(signal, threading.Event)
        assert signal.is_set(), "timeout handler must flip the abort signal"

        # The worker thread runs in a ThreadPoolExecutor and polls the
        # signal every 20ms. Yield to the loop long enough for the
        # worker to observe the abort and exit cleanly. Without this
        # the test races the polling loop.
        deadline = time.time() + 1.0
        while time.time() < deadline and captured.get("aborted") is None:
            await asyncio.sleep(0.02)
        assert captured.get("aborted") is True, (
            "worker did not observe abort signal within 1s"
        )

    @pytest.mark.asyncio
    async def test_on_message_passes_signal_to_run_text(self) -> None:
        """The timeout relies on the SDK's ``signal`` parameter — if the
        kwarg is ever dropped we want the tests to notice before prod."""
        fake_mod, options_mod, _, mock_thread = _make_fake_codex_module()

        captured: dict[str, object] = {}

        def fast_run_text(
            content: str,
            turn_options=None,
            *,
            signal: threading.Event | None = None,
        ):
            captured["signal"] = signal
            captured["turn_options"] = turn_options
            return "ok"

        mock_thread.run_text = fast_run_text

        with _patch_codex(fake_mod, options_mod):
            adapter = CodexAdapter()
            await adapter.start()
            result = await adapter.on_message(
                {"content": "fast", "room_id": "room-1"}
            )

        assert result == "ok"
        assert isinstance(captured.get("signal"), threading.Event)
        assert not captured["signal"].is_set(), (
            "happy path must not fire the abort signal"
        )


class TestCodexSharedContextReinjection:
    """#255 — ``<shared-context>`` must be re-injected when its bytes
    change across turns (file upload / backfill arriving after the
    first turn), not cached once-and-forever like #237's memory
    policy block. The first-turn optimisation from #237 stays for
    unchanged blocks — we add a per-block-sha cache so identical
    content is still dropped.
    """

    @staticmethod
    def _patch_suffix(values):
        """Patch ``compose_memory_suffix`` to return ``values`` in order
        on successive calls. The adapter imports the helper from
        ``base`` inline, so patching at the source module covers both
        sites."""
        from itertools import cycle
        it = iter(values) if not isinstance(values, str) else cycle([values])

        def _fake(*_args, **_kwargs):
            try:
                return next(it)
            except StopIteration:
                return ""

        return patch(
            "doorae_agent.integrations.base.compose_memory_suffix",
            side_effect=_fake,
        )

    @pytest.mark.asyncio
    async def test_first_turn_injects_shared_block_as_prefix(self) -> None:
        fake_mod, options_mod, _codex, mock_thread = _make_fake_codex_module()
        suffix = "<shared-context>\n<file name=\"a.md\"/>\n</shared-context>\n"
        with _patch_codex(fake_mod, options_mod), self._patch_suffix(suffix):
            adapter = CodexAdapter()
            await adapter.start()
            await adapter.on_message({"content": "Q1", "room_id": "r1"})

        sent = mock_thread.run_text.call_args.args[0]
        assert suffix.rstrip() in sent
        assert sent.rstrip().endswith("Q1"), (
            "shared block must be a prefix, user content is the tail"
        )

    @pytest.mark.asyncio
    async def test_unchanged_block_skips_reinjection(self) -> None:
        """Identical suffix across turns → only the first turn carries
        it. #237's rationale (Codex threads persist history) holds."""
        fake_mod, options_mod, _codex, mock_thread = _make_fake_codex_module()
        suffix = "<shared-context>same</shared-context>\n"
        with _patch_codex(fake_mod, options_mod), self._patch_suffix(suffix):
            adapter = CodexAdapter()
            await adapter.start()
            await adapter.on_message({"content": "Q1", "room_id": "r1"})
            await adapter.on_message({"content": "Q2", "room_id": "r1"})

        second = mock_thread.run_text.call_args_list[1].args[0]
        assert "<shared-context>" not in second
        assert second == "Q2"

    @pytest.mark.asyncio
    async def test_changed_block_reinjects_with_update_marker(self) -> None:
        """#255 core — when the suffix changes (new file uploaded,
        backfill arrived), the next turn must carry the fresh block
        tagged as an update so the agent notices the diff."""
        fake_mod, options_mod, _codex, mock_thread = _make_fake_codex_module()
        v1 = "<shared-context>v1</shared-context>\n"
        v2 = "<shared-context>v2-with-new-file</shared-context>\n"
        with _patch_codex(fake_mod, options_mod), self._patch_suffix([v1, v2, v2]):
            adapter = CodexAdapter()
            await adapter.start()
            await adapter.on_message({"content": "Q1", "room_id": "r1"})
            await adapter.on_message({"content": "Q2", "room_id": "r1"})

        second = mock_thread.run_text.call_args_list[1].args[0]
        assert "v2-with-new-file" in second
        assert "v1" not in second, "stale version must not appear"
        # Must be user-visibly tagged as update so the model treats
        # it as a refresh rather than a duplicate paste.
        assert "업데이트" in second or "updated" in second.lower()
        assert second.rstrip().endswith("Q2")

    @pytest.mark.asyncio
    async def test_empty_suffix_never_prefixes(self) -> None:
        fake_mod, options_mod, _codex, mock_thread = _make_fake_codex_module()
        with _patch_codex(fake_mod, options_mod), self._patch_suffix(""):
            adapter = CodexAdapter()
            await adapter.start()
            await adapter.on_message({"content": "Q1", "room_id": "r1"})
            await adapter.on_message({"content": "Q2", "room_id": "r1"})

        assert mock_thread.run_text.call_args_list[0].args[0] == "Q1"
        assert mock_thread.run_text.call_args_list[1].args[0] == "Q2"

    @pytest.mark.asyncio
    async def test_per_room_cache_independent(self) -> None:
        """A change in room A must not force a re-inject in room B and
        vice-versa — the cache key is (room_id, sha)."""
        fake_mod, options_mod, mock_codex, _ = _make_fake_codex_module()
        # Per-room threads need distinct run_text instances so each
        # room's prompts are isolated in call_args_list.
        room_threads: dict[str, MagicMock] = {}

        def make_thread(**_kw):
            t = MagicMock()
            t.run_text = MagicMock(return_value="ok")
            return t

        mock_codex.start_thread = MagicMock(side_effect=make_thread)

        a_suffix = "<shared-context>A</shared-context>\n"
        b_suffix = "<shared-context>B</shared-context>\n"
        seq = [a_suffix, b_suffix, a_suffix, b_suffix]  # A1, B1, A2, B2 — unchanged each
        with _patch_codex(fake_mod, options_mod), self._patch_suffix(seq):
            adapter = CodexAdapter()
            await adapter.start()
            await adapter.on_message({"content": "A1", "room_id": "r-a"})
            await adapter.on_message({"content": "B1", "room_id": "r-b"})
            await adapter.on_message({"content": "A2", "room_id": "r-a"})
            await adapter.on_message({"content": "B2", "room_id": "r-b"})

        # Second turn per room: suffix unchanged → no prefix.
        a_thread = adapter._threads["r-a"]
        b_thread = adapter._threads["r-b"]
        assert "<shared-context>" not in a_thread.run_text.call_args_list[1].args[0]
        assert "<shared-context>" not in b_thread.run_text.call_args_list[1].args[0]


class TestCodexRoomConversationWrapper:
    """Issue #284 — drained pending context is wrapped in a
    ``<room_conversation>`` XML block before being prepended to the
    next turn's user content. For codex this matters extra because
    the thread accumulates history natively, so a leaky wrapper
    would pollute every subsequent turn."""

    @pytest.mark.asyncio
    async def test_drained_prefix_appears_inside_room_conversation_tags(
        self,
    ) -> None:
        fake_mod, options_mod, mock_codex, mock_thread = _make_fake_codex_module()
        with _patch_codex(fake_mod, options_mod):
            adapter = CodexAdapter()
            await adapter.start()

            # Stash one ambient message — server-side ``ingest_only``
            # broadcast lands here as the next turn's prefix.
            await adapter.ingest_context({
                "room_id": "room-1",
                "participant_id": "peer-agent",
                "content": "비행 8시 출발입니다",
                "metadata": {},
            })
            await adapter.on_message({"content": "다음 일정?", "room_id": "room-1"})

        turn_content = mock_thread.run_text.call_args.args[0]
        assert "<room_conversation>" in turn_content
        assert "</room_conversation>" in turn_content
        # Ambient line lands inside the wrapper.
        open_idx = turn_content.index("<room_conversation>")
        close_idx = turn_content.index("</room_conversation>")
        assert open_idx < turn_content.index("[참고]") < close_idx
        # Preamble's no-relay phrase must reach the prompt — the
        # whole point of #284.
        assert "전달하지 마세요" in turn_content
        # Actual user question stays outside the wrapper so codex
        # still sees it as the input to address.
        user_idx = turn_content.index("다음 일정?")
        assert user_idx > close_idx

    @pytest.mark.asyncio
    async def test_solo_turn_has_no_wrapper(self) -> None:
        """Empty pending-context buffer must not produce wrapper tags
        — pre-#284 byte-identical behaviour for the common case."""
        fake_mod, options_mod, mock_codex, mock_thread = _make_fake_codex_module()
        with _patch_codex(fake_mod, options_mod):
            adapter = CodexAdapter()
            await adapter.start()
            await adapter.on_message({"content": "안녕", "room_id": "room-1"})

        turn_content = mock_thread.run_text.call_args.args[0]
        assert "<room_conversation>" not in turn_content
        # No memory_md cached → memory suffix path is also empty,
        # so the bare content reaches the thread untouched.
        assert turn_content == "안녕"
