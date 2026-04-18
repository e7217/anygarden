"""Tests for the Claude Agent SDK adapter.

The adapter is covered via a fake ``claude_agent_sdk`` module
installed into ``sys.modules`` so tests stay fast and deterministic
regardless of whether the real package is installed in the venv.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from doorae_agent.integrations.claude_code import (
    ClaudeCodeAdapter,
    integrate_with_claude_code,
)


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stub the claude_agent_sdk module with a recording query()."""
    calls: list[dict[str, Any]] = []

    class FakeOptions:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs

        def __repr__(self) -> str:  # pragma: no cover - debugging
            return f"FakeOptions({self.kwargs!r})"

    async def fake_query(
        *, prompt: str, options: FakeOptions, transport: Any = None
    ) -> AsyncIterator[Any]:
        calls.append({"prompt": prompt, "options": options})

        class TextBlock:
            def __init__(self, text: str) -> None:
                self.text = text

        class ToolUseBlock:
            # Real SDK puts the skill name and tool args here. We
            # give it a ``text`` attribute too so the adapter has
            # a chance to leak it — good regression target.
            text = "TOOL USE: activate_skill"

        class ToolResultBlock:
            # Same deal — skill activation returns the SKILL.md
            # body here, which must not show up in the reply.
            text = "SKILL BODY LEAKED INTO TOOL RESULT"

        class AssistantMessage:
            content = [
                TextBlock("hello from fake claude"),
                ToolUseBlock(),
                ToolResultBlock(),
            ]
            session_id = "sess-abc"

        class ResultMessage:
            result = "hello from fake claude"
            session_id = "sess-abc"

        yield AssistantMessage()
        yield ResultMessage()

    fake_module = types.ModuleType("claude_agent_sdk")
    fake_module.ClaudeAgentOptions = FakeOptions  # type: ignore[attr-defined]
    fake_module.query = fake_query  # type: ignore[attr-defined]
    fake_module.__version__ = "0.1.58-fake"

    monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)
    return calls


class TestStart:
    @pytest.mark.asyncio
    async def test_start_without_sdk_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Adapter starts gracefully when claude-agent-sdk is missing."""
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
        adapter = ClaudeCodeAdapter(agent_name="TestBot")
        await adapter.start()
        assert adapter._sdk is None

    @pytest.mark.asyncio
    async def test_on_message_returns_none_without_sdk(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", None)
        adapter = ClaudeCodeAdapter()
        await adapter.start()

        result = await adapter.on_message(
            {"content": "Hi", "room_id": "r1"}
        )
        assert result is None


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_passes_cwd_and_setting_sources(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        adapter = ClaudeCodeAdapter()
        await adapter.start()
        result = await adapter.on_message(
            {"content": "Hi", "room_id": "r1"}
        )
        # The reply must be the TextBlock content, NOT the tool-use
        # or tool-result blocks that also carry a ``text`` attr.
        # This was the real-world bug: the adapter was harvesting
        # every block with a ``text`` attribute, so a skill
        # activation (ToolResultBlock) echoed the SKILL.md body
        # directly into the room.
        assert result == "hello from fake claude"
        assert "SKILL BODY LEAKED" not in (result or "")
        assert "TOOL USE" not in (result or "")
        opts = fake_sdk[0]["options"].kwargs

        # cwd must be Path.cwd() so the SDK resolves per-agent
        # state from the materialized directory.
        assert opts["cwd"] == str(Path.cwd())

        # setting_sources=["project"] is the non-negotiable flag
        # that makes CLAUDE.md / project skills actually load.
        # A silent regression here would drop all per-agent
        # configuration — pin the exact value.
        assert opts["setting_sources"] == ["project"]

        # Issue #134 — permission_mode must be bypassPermissions so
        # MCP tool calls aren't gated by an interactive approval
        # prompt that will never be answered in a headless agent.
        # Without this, any attached MCP (GitHub, Linear, etc.) is
        # effectively unusable: Claude reports "no permission" and
        # never actually invokes the tool.
        assert opts["permission_mode"] == "bypassPermissions"

    @pytest.mark.asyncio
    async def test_system_prompt_only_passed_when_set(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        adapter = ClaudeCodeAdapter()  # system_prompt defaults to None
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "r1"})
        assert "system_prompt" not in fake_sdk[-1]["options"].kwargs

        adapter = ClaudeCodeAdapter(system_prompt="you are a bot")
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "r1"})
        assert (
            fake_sdk[-1]["options"].kwargs.get("system_prompt")
            == "you are a bot"
        )

    @pytest.mark.asyncio
    async def test_model_passed_when_set(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        adapter = ClaudeCodeAdapter(model="claude-sonnet-4-6")
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "r1"})
        assert (
            fake_sdk[-1]["options"].kwargs.get("model") == "claude-sonnet-4-6"
        )

    @pytest.mark.asyncio
    async def test_session_resumes_per_room(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Turn 1 has no ``resume``; turn 2 resumes the session id
        captured from turn 1's messages.
        """
        adapter = ClaudeCodeAdapter()
        await adapter.start()

        await adapter.on_message({"content": "turn 1", "room_id": "r1"})
        assert "resume" not in fake_sdk[0]["options"].kwargs
        # ``_last_session_id`` gets captured during the query loop;
        # integrate_with_claude_code normally promotes it into the
        # room map. Mimic that promotion here.
        adapter._sessions["r1"] = adapter._last_session_id  # type: ignore[attr-defined]

        await adapter.on_message({"content": "turn 2", "room_id": "r1"})
        assert fake_sdk[1]["options"].kwargs.get("resume") == "sess-abc"

    @pytest.mark.asyncio
    async def test_rooms_are_isolated(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        adapter = ClaudeCodeAdapter()
        await adapter.start()

        await adapter.on_message({"content": "room 1", "room_id": "r1"})
        adapter._sessions["r1"] = adapter._last_session_id  # type: ignore[attr-defined]
        # Room 2's first turn must NOT inherit room 1's session.
        await adapter.on_message({"content": "room 2", "room_id": "r2"})

        assert fake_sdk[1]["options"].kwargs.get("resume") is None


class TestIntegrateWithClaudeCode:
    @pytest.mark.asyncio
    async def test_integrate_registers_handler(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        from doorae_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Bot")
        assert len(client._message_handlers) == 0

        adapter = await integrate_with_claude_code(client, {"name": "Bot"})

        assert len(client._message_handlers) == 1
        assert isinstance(adapter, ClaudeCodeAdapter)
