"""Integration tests for the Codex app-server adapter."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from doorae_agent.integrations.codex import CodexAdapter, integrate_with_codex


def _make_fake_codex_module():
    """Create a fake codex module for testing."""
    mock_thread = MagicMock()
    mock_thread.run_text = MagicMock(return_value="Hello from codex")

    mock_codex = MagicMock()
    mock_codex.start_thread = MagicMock(return_value=mock_thread)
    mock_codex.close = MagicMock()

    module = MagicMock()
    module.Codex = MagicMock(return_value=mock_codex)
    return module, mock_codex, mock_thread


class TestCodexAdapter:
    def test_default_sandbox_is_workspace_write(self) -> None:
        """Default sandbox must be workspace-write."""
        adapter = CodexAdapter()
        assert adapter._sandbox == "workspace-write"

    def test_default_model(self) -> None:
        adapter = CodexAdapter()
        assert adapter._model == "gpt-5.4"

    @pytest.mark.asyncio
    async def test_start_initializes_client(self) -> None:
        """start() creates Codex client."""
        fake_mod, mock_codex, _ = _make_fake_codex_module()
        with patch.dict(sys.modules, {"codex": fake_mod}):
            adapter = CodexAdapter()
            await adapter.start()
            assert adapter._codex is mock_codex

    @pytest.mark.asyncio
    async def test_on_message_creates_thread_and_returns_response(self) -> None:
        """on_message creates a thread for the room and returns the response."""
        fake_mod, mock_codex, mock_thread = _make_fake_codex_module()
        with patch.dict(sys.modules, {"codex": fake_mod}):
            adapter = CodexAdapter()
            await adapter.start()

            result = await adapter.on_message({
                "content": "Hello",
                "room_id": "room-1",
            })
            assert result == "Hello from codex"
            assert "room-1" in adapter._threads
            mock_codex.start_thread.assert_called_once()
            mock_thread.run_text.assert_called_once_with("Hello")

    @pytest.mark.asyncio
    async def test_on_message_reuses_thread(self) -> None:
        """Subsequent messages to same room reuse the thread."""
        fake_mod, mock_codex, mock_thread = _make_fake_codex_module()
        with patch.dict(sys.modules, {"codex": fake_mod}):
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
        fake_mod, mock_codex, _ = _make_fake_codex_module()
        mock_codex.start_thread = MagicMock(side_effect=lambda **kw: MagicMock(
            run_text=MagicMock(return_value="ok"),
        ))
        with patch.dict(sys.modules, {"codex": fake_mod}):
            adapter = CodexAdapter()
            await adapter.start()

            await adapter.on_message({"content": "a", "room_id": "room-1"})
            await adapter.on_message({"content": "b", "room_id": "room-2"})

            assert len(adapter._threads) == 2
            assert mock_codex.start_thread.call_count == 2

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self) -> None:
        """stop() clears threads and closes codex."""
        fake_mod, mock_codex, _ = _make_fake_codex_module()
        with patch.dict(sys.modules, {"codex": fake_mod}):
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

        fake_mod, _, _ = _make_fake_codex_module()
        with patch.dict(sys.modules, {"codex": fake_mod}):
            client = ChatClient("ws://localhost:8000", token="t", agent_name="Bot")
            assert len(client._message_handlers) == 0

            adapter = await integrate_with_codex(client)

            assert len(client._message_handlers) == 1
            assert isinstance(adapter, CodexAdapter)
