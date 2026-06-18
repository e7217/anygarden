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

from anygarden_agent.integrations.claude_code import (
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
            # Issue #144 — ``name`` and ``input`` are what the
            # observability log reads; we keep the values distinct
            # from anything that would show up as a leaked reply.
            text = "TOOL USE: activate_skill"
            name = "mcp__github__get_me"
            input = {"reason": "debug"}

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

    # Issue #159 Phase C — the real SDK exposes ``tool`` /
    # ``create_sdk_mcp_server`` for in-process MCP servers. The
    # adapter builds a ``handoff_to`` tool on top of them, so the
    # fake SDK has to mimic the decorator + server-config shape.
    from types import SimpleNamespace

    def fake_tool(name: str, description: str, input_schema: Any,
                  annotations: Any = None):
        def decorator(handler):
            return SimpleNamespace(
                name=name,
                description=description,
                input_schema=input_schema,
                handler=handler,
                annotations=annotations,
            )
        return decorator

    def fake_create_sdk_mcp_server(
        name: str, version: str = "1.0.0", tools: list | None = None,
    ):
        # The real shape is an ``McpSdkServerConfig`` TypedDict; for
        # tests we just return a plain dict that preserves identity.
        return {
            "type": "sdk",
            "name": name,
            "version": version,
            "tools": tools or [],
        }

    fake_module = types.ModuleType("claude_agent_sdk")
    fake_module.ClaudeAgentOptions = FakeOptions  # type: ignore[attr-defined]
    fake_module.query = fake_query  # type: ignore[attr-defined]
    fake_module.tool = fake_tool  # type: ignore[attr-defined]
    fake_module.create_sdk_mcp_server = fake_create_sdk_mcp_server  # type: ignore[attr-defined]
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

    @pytest.mark.asyncio
    async def test_tool_use_emits_structlog_entry(
        self, fake_sdk: list[dict[str, Any]], capfd: pytest.CaptureFixture[str]
    ) -> None:
        """Issue #144 — every ToolUseBlock in the stream should fire a
        ``claude_code.tool_use`` log entry carrying the tool name and
        the *keys* of the input payload (values are omitted to avoid
        leaking tokens / PII that MCP tools often carry).

        structlog writes to stdout directly (not through the stdlib
        logging root), so ``capfd`` is the right capture fixture.
        """
        adapter = ClaudeCodeAdapter()
        await adapter.start()
        await adapter.on_message({"content": "Hi", "room_id": "r1"})

        out = capfd.readouterr().out
        tool_use_lines = [
            line for line in out.splitlines() if "claude_code.tool_use" in line
        ]
        assert tool_use_lines, (
            f"expected tool_use log entry in stdout, got:\n{out}"
        )
        line = tool_use_lines[0]
        assert "tool_name=mcp__github__get_me" in line
        # Input *keys* must be logged, *values* must not.
        assert "'reason'" in line  # the key name
        # The value ("debug") must not appear in the tool_use line.
        # (Note: the substring "debug" as a log level name can appear
        # in other lines, so we scope the check to this line only.)
        assert "debug" not in line


class TestIntegrateWithClaudeCode:
    @pytest.mark.asyncio
    async def test_integrate_registers_handler(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Bot")
        assert len(client._message_handlers) == 0

        adapter = await integrate_with_claude_code(client, {"name": "Bot"})

        assert len(client._message_handlers) == 1
        assert isinstance(adapter, ClaudeCodeAdapter)


class TestHandoffTool:
    """Issue #159 Phase C — ``handoff_to`` custom tool exposed by the
    Claude Code adapter when the agent is the orchestrator of the
    current room.

    The tool is *conditionally* wired: only orchestrator rooms get
    the MCP server registration. Worker agents (and rooms under
    ``mentioned_only`` / ``round_robin``) must not see it, otherwise
    the LLM is tempted to use it outside its designed scope.
    """

    @pytest.mark.asyncio
    async def test_handoff_tool_exposed_when_orchestrator(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Orchestrator → options carry an ``mcp_servers`` entry under
        the ``handoff`` key (#319 — name disambiguated from the cluster's
        HTTP anygarden entry the spawner writes into ``.mcp.json``).

        ``allowed_tools`` is intentionally *not* set so the cluster's
        anygarden HTTP MCP (``mark_task_status`` etc.) and any admin-attached
        third-party MCP server (e.g. GitHub) remain reachable from the
        orchestrator turn.
        """
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Orc")
        client._agent_id = "agent-alpha"
        client._my_participant_ids = {"orc-pid"}
        client._orchestrator_agent_id["room-a"] = "agent-alpha"

        adapter = ClaudeCodeAdapter(client=client)
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        opts = fake_sdk[-1]["options"].kwargs
        assert "mcp_servers" in opts
        assert "handoff" in opts["mcp_servers"]
        # #319 — the cluster's HTTP anygarden MCP must NOT be shadowed by
        # an in-process server reusing the same name.
        assert "anygarden" not in opts["mcp_servers"]
        # #319 — no narrow whitelist; cluster + admin MCP entries
        # autoload from ``.mcp.json`` and need to stay reachable.
        assert "allowed_tools" not in opts

    @pytest.mark.asyncio
    async def test_handoff_tool_hidden_when_not_orchestrator(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Worker agent in an orchestrator room → no ``mcp_servers``
        stamp. The tool is strictly orchestrator-scoped."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Worker")
        client._agent_id = "agent-beta"  # not the orchestrator
        client._orchestrator_agent_id["room-a"] = "agent-alpha"

        adapter = ClaudeCodeAdapter(client=client)
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        opts = fake_sdk[-1]["options"].kwargs
        assert "mcp_servers" not in opts
        assert "allowed_tools" not in opts

    @pytest.mark.asyncio
    async def test_handoff_tool_hidden_without_client(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Adapter constructed without a ChatClient reference (legacy
        call site) must still function — just without the tool. This
        preserves source-compat for any caller not yet updated."""
        adapter = ClaudeCodeAdapter()
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        opts = fake_sdk[-1]["options"].kwargs
        assert "mcp_servers" not in opts

    @pytest.mark.asyncio
    async def test_handoff_handler_sends_marker_to_client(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Invoking the tool handler directly calls ``client.send``
        with the ``[HANDOFF] <@user:pid> reason`` marker. The server
        picks this up and updates ``Room.next_speaker_participant_id``
        (tested separately in cluster suite)."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Orc")
        client._agent_id = "agent-alpha"
        client._orchestrator_agent_id["room-a"] = "agent-alpha"

        sent: list[dict[str, Any]] = []

        async def fake_send(room_id, content, metadata=None, **kwargs):
            sent.append({"room_id": room_id, "content": content, "metadata": metadata})

        client.send = fake_send  # type: ignore[method-assign]

        adapter = ClaudeCodeAdapter(client=client)
        await adapter.start()
        # Drive one on_message so the SDK options capture the
        # ``mcp_servers`` entry (and the MCP server config is built).
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        # Fish the handoff tool out of the fake SDK server config and
        # invoke its handler directly. In production the SDK calls
        # this callback *during* the ``query()`` iteration, while
        # ``_current_room_id`` is still set — we simulate that by
        # re-setting it before invoking the handler.
        opts = fake_sdk[-1]["options"].kwargs
        server_cfg = opts["mcp_servers"]["handoff"]
        handoff_tool = next(
            t for t in server_cfg["tools"] if t.name == "handoff_to"
        )
        adapter._current_room_id = "room-a"
        result = await handoff_tool.handler(
            {"participant_id": "worker-pid-123", "reason": "logs expert"}
        )

        assert len(sent) == 1
        assert sent[0]["room_id"] == "room-a"
        assert sent[0]["content"].startswith("[HANDOFF] <@user:worker-pid-123>")
        assert "logs expert" in sent[0]["content"]
        assert sent[0]["metadata"] == {
            "handoff": {
                "target_participant_id": "worker-pid-123",
                "reason": "logs expert",
            }
        }
        # Tool must return a non-error MCP response shape so the
        # LLM sees a confirmation rather than a retry prompt.
        assert result.get("is_error") is not True
        content = result.get("content") or []
        assert content and content[0].get("type") == "text"

    @pytest.mark.asyncio
    async def test_handoff_handler_reports_error_on_missing_args(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Tool called without a participant id → tool returns an
        ``is_error`` MCP response so the LLM can retry."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Orc")
        client._agent_id = "agent-alpha"
        client._orchestrator_agent_id["room-a"] = "agent-alpha"
        sent: list[Any] = []

        async def fake_send(*args, **kwargs):
            sent.append((args, kwargs))

        client.send = fake_send  # type: ignore[method-assign]

        adapter = ClaudeCodeAdapter(client=client)
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        opts = fake_sdk[-1]["options"].kwargs
        handoff_tool = next(
            t for t in opts["mcp_servers"]["handoff"]["tools"]
            if t.name == "handoff_to"
        )
        adapter._current_room_id = "room-a"
        result = await handoff_tool.handler({"reason": "no target"})

        assert result.get("is_error") is True
        assert len(sent) == 0


class TestOrchestratorRosterPrompt:
    """Issue #221 — the orchestrator adapter injects the room roster
    into ``system_prompt`` so the LLM can call ``handoff_to`` with a
    real participant UUID. Before this was wired, the LLM had no
    source of UUIDs and routinely emitted display names that the
    server rejected as unknown participants."""

    @pytest.mark.asyncio
    async def test_orchestrator_prompt_includes_room_roster(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Roster lines carry the participant_id as data (``id: uuid``)
        rather than as a live ``<@user:uuid>`` routing token (#288).
        The LLM still has the UUID for ``handoff_to`` calls but no
        copyable token to spray across prose."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Orc")
        client._agent_id = "agent-alpha"
        client._my_participant_ids = {"orc-pid"}
        client._orchestrator_agent_id["room-a"] = "agent-alpha"
        client._participants_by_room["room-a"] = {
            "orc-pid": {
                "id": "orc-pid",
                "display_name": "orc",
                "kind": "agent",
                "agent_id": "agent-alpha",
            },
            "worker-pid": {
                "id": "worker-pid",
                "display_name": "worker",
                "kind": "agent",
                "agent_id": "agent-beta",
            },
            "user-pid": {
                "id": "user-pid",
                "display_name": "alice",
                "kind": "user",
                "agent_id": None,
            },
        }

        adapter = ClaudeCodeAdapter(system_prompt="You are Orc.", client=client)
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        opts = fake_sdk[-1]["options"].kwargs
        prompt = opts.get("system_prompt", "")
        assert prompt.startswith("You are Orc.")
        # ID is exposed as data, not as a live routing token.
        assert "id: worker-pid" in prompt
        assert "id: user-pid" in prompt
        # The orchestrator must not see itself in the roster —
        # ``handoff_to me`` would be a no-op cycle.
        assert "id: orc-pid" not in prompt
        # The display_name still appears so the LLM can reason about
        # *who* to address, not just UUIDs.
        assert "worker" in prompt
        assert "alice" in prompt
        # #288 regression guard — no peer-specific routing token may
        # appear in the assembled prompt (the literal placeholder
        # ``<@user:PARTICIPANT_ID>`` from the usage hint is allowed
        # but real UUIDs in token form are not).
        assert "<@user:worker-pid>" not in prompt
        assert "<@user:user-pid>" not in prompt

    @pytest.mark.asyncio
    async def test_non_orchestrator_prompt_unchanged(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Worker agents don't get roster stamping — their prompt
        remains verbatim from construction. This keeps the roster
        strictly scoped to the agent that can actually use it."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Worker")
        client._agent_id = "agent-beta"
        client._orchestrator_agent_id["room-a"] = "agent-alpha"
        client._participants_by_room["room-a"] = {
            "other-pid": {
                "id": "other-pid",
                "display_name": "other",
                "kind": "agent",
                "agent_id": "agent-alpha",
            },
        }

        adapter = ClaudeCodeAdapter(
            system_prompt="You are Worker.", client=client
        )
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        opts = fake_sdk[-1]["options"].kwargs
        assert opts.get("system_prompt") == "You are Worker."

    @pytest.mark.asyncio
    async def test_orchestrator_without_base_prompt_gets_roster_only(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """An adapter created without a custom system_prompt should
        still receive the roster as a standalone prompt — we don't
        want the CLAUDE.md-driven default path to silently skip the
        roster."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Orc")
        client._agent_id = "agent-alpha"
        client._my_participant_ids = {"orc-pid"}
        client._orchestrator_agent_id["room-a"] = "agent-alpha"
        client._participants_by_room["room-a"] = {
            "worker-pid": {
                "id": "worker-pid",
                "display_name": "worker",
                "kind": "agent",
                "agent_id": "agent-beta",
            },
        }

        adapter = ClaudeCodeAdapter(client=client)
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        opts = fake_sdk[-1]["options"].kwargs
        prompt = opts.get("system_prompt")
        assert prompt is not None
        # #288 — peer is exposed as data, not as a live token.
        assert "id: worker-pid" in prompt
        assert "<@user:worker-pid>" not in prompt

    @pytest.mark.asyncio
    async def test_orchestrator_with_empty_roster_leaves_prompt_unchanged(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Pre-#221 servers don't stamp the roster — the adapter must
        still produce a workable prompt without it. Roster absence
        should not inject a stray "Room participants:" header."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Orc")
        client._agent_id = "agent-alpha"
        client._orchestrator_agent_id["room-a"] = "agent-alpha"
        # _participants_by_room not populated — pre-#221 server path.

        adapter = ClaudeCodeAdapter(system_prompt="You are Orc.", client=client)
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        opts = fake_sdk[-1]["options"].kwargs
        assert opts.get("system_prompt") == "You are Orc."

    @pytest.mark.asyncio
    async def test_orchestrator_roster_includes_peer_description(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Issue #271 — peers carrying a ``description`` get an em-dash
        suffix so the LLM can route on intent. Peers without one fall
        back to the legacy "name only" line for backwards compatibility."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Orc")
        client._agent_id = "agent-alpha"
        client._my_participant_ids = {"orc-pid"}
        client._orchestrator_agent_id["room-a"] = "agent-alpha"
        client._participants_by_room["room-a"] = {
            "worker-pid": {
                "id": "worker-pid",
                "display_name": "frontend-bot",
                "kind": "agent",
                "agent_id": "agent-beta",
                "description": "Reviews React components and accessibility",
            },
            "legacy-pid": {
                "id": "legacy-pid",
                "display_name": "legacy-bot",
                "kind": "agent",
                "agent_id": "agent-gamma",
                # No description — pre-#271 agent.
            },
        }

        adapter = ClaudeCodeAdapter(system_prompt="You are Orc.", client=client)
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        prompt = fake_sdk[-1]["options"].kwargs["system_prompt"]
        # #288 — id appears as data, not as a routing token. Description
        # still rides along on the same line for routing intent.
        assert (
            "- frontend-bot (id: worker-pid, kind: agent) — "
            "Reviews React components and accessibility"
        ) in prompt
        # Legacy peer (no description) — same id-as-data format.
        assert "- legacy-bot (id: legacy-pid, kind: agent)\n" in prompt + "\n"
        assert "legacy-bot (id: legacy-pid, kind: agent) —" not in prompt
        # No raw routing token for either peer.
        assert "<@user:worker-pid>" not in prompt
        assert "<@user:legacy-pid>" not in prompt

    @pytest.mark.asyncio
    async def test_orchestrator_roster_truncates_long_description(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """#271 — the REST layer caps incoming description at 200, but
        we double-cap on the runtime side too. Newlines fold to spaces
        so a single line stays a single line in the prompt."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Orc")
        client._agent_id = "agent-alpha"
        client._my_participant_ids = {"orc-pid"}
        client._orchestrator_agent_id["room-a"] = "agent-alpha"
        long_desc = "x" * 500 + "\nshould be folded"
        client._participants_by_room["room-a"] = {
            "p1": {
                "id": "p1",
                "display_name": "long",
                "kind": "agent",
                "agent_id": "agent-beta",
                "description": long_desc,
            },
        }

        adapter = ClaudeCodeAdapter(system_prompt="You are Orc.", client=client)
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        prompt = fake_sdk[-1]["options"].kwargs["system_prompt"]
        # #288 — locate the roster line for ``p1`` via ``id: p1``
        # (the post-#288 data form) and assert the trailing
        # description is exactly 200 chars (no newline leaked through).
        line = next(line for line in prompt.splitlines() if "id: p1" in line)
        assert " — " in line
        desc_in_line = line.split(" — ", 1)[1]
        assert len(desc_in_line) == 200
        assert "\n" not in desc_in_line

    @pytest.mark.asyncio
    async def test_collaborative_non_orchestrator_gets_roster_with_hint(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Issue #279 — a collaborative agent that is *not* the room's
        orchestrator still receives the roster, plus a peer-mention
        usage hint paragraph that solo agents never see."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Buddy")
        client._agent_id = "agent-buddy"
        client._my_participant_ids = {"buddy-pid"}
        # Note: orchestrator points at a *different* agent — Buddy is
        # collaborative but not the orchestrator. Pre-#279 Buddy
        # received nothing; #279 makes Buddy receive the roster + hint.
        client._orchestrator_agent_id["room-a"] = "agent-other"
        client._collaboration_mode_by_room["room-a"] = "collaborative"
        client._participants_by_room["room-a"] = {
            "buddy-pid": {
                "id": "buddy-pid",
                "display_name": "Buddy",
                "kind": "agent",
                "agent_id": "agent-buddy",
            },
            "peer-pid": {
                "id": "peer-pid",
                "display_name": "peer",
                "kind": "agent",
                "agent_id": "agent-peer",
            },
        }

        adapter = ClaudeCodeAdapter(
            system_prompt="You are Buddy.", client=client
        )
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        prompt = fake_sdk[-1]["options"].kwargs["system_prompt"]
        # #288 — peer id is data, not a routing token.
        assert "id: peer-pid" in prompt
        # Buddy must not see itself — peer-mention to self is a no-op.
        assert "id: buddy-pid" not in prompt
        # No raw token for the peer in the prompt.
        assert "<@user:peer-pid>" not in prompt
        # The collaborative hint paragraph must be present. Both
        # halves of the rewritten guidance are asserted so a future
        # copy edit that drops either half fails loudly.
        assert "build the routing token" in prompt
        assert "reaches the user directly" in prompt
        assert "only need to synthesize if the user explicitly asks" in prompt
        # The reference-vs-routing guidance must explicitly tell the
        # model to use display name only for non-call references.
        assert "use only the display name" in prompt
        assert (
            "Never put a routing token in prose that merely "
            "mentions or lists peers"
        ) in prompt
        # The placeholder pattern must be advertised so the model
        # builds a real token by substitution rather than copying.
        assert "<@user:PARTICIPANT_ID>" in prompt
        # Negative guard — the old "always synthesize" framing must
        # NOT come back. This is the entire point of the #283 follow-up.
        assert "synthesize a final answer" not in prompt

    @pytest.mark.asyncio
    async def test_solo_non_orchestrator_prompt_unchanged(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Issue #279 — solo agents that aren't the orchestrator must
        receive the prompt byte-for-byte identical to pre-#279, with
        no roster and no collaborative hint."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Solo")
        client._agent_id = "agent-solo"
        client._orchestrator_agent_id["room-a"] = "agent-other"
        # collaboration_mode default ("solo") — explicit for clarity.
        client._collaboration_mode_by_room["room-a"] = "solo"
        client._participants_by_room["room-a"] = {
            "peer-pid": {
                "id": "peer-pid",
                "display_name": "peer",
                "kind": "agent",
                "agent_id": "agent-peer",
            },
        }

        adapter = ClaudeCodeAdapter(
            system_prompt="You are Solo.", client=client
        )
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        opts = fake_sdk[-1]["options"].kwargs
        assert opts.get("system_prompt") == "You are Solo."

    @pytest.mark.asyncio
    async def test_orchestrator_collaborative_combination_attaches_hint(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Issue #279 — when an agent is both the orchestrator and
        collaborative, the roster + collaborative hint must coexist
        with the handoff_to MCP wiring (mcp_servers stays populated)."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Orc")
        client._agent_id = "agent-alpha"
        client._my_participant_ids = {"orc-pid"}
        client._orchestrator_agent_id["room-a"] = "agent-alpha"
        client._collaboration_mode_by_room["room-a"] = "collaborative"
        client._participants_by_room["room-a"] = {
            "peer-pid": {
                "id": "peer-pid",
                "display_name": "peer",
                "kind": "agent",
                "agent_id": "agent-beta",
            },
        }

        adapter = ClaudeCodeAdapter(system_prompt="You are Orc.", client=client)
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        opts = fake_sdk[-1]["options"].kwargs
        prompt = opts.get("system_prompt", "")
        # #288 — peer id appears as data, not a routing token.
        assert "id: peer-pid" in prompt
        assert "<@user:peer-pid>" not in prompt
        # Collaborative hint phrasing reflects #283 + #288.
        assert "build the routing token" in prompt
        # Orchestrator wiring must remain — collaborative is additive.
        assert "mcp_servers" in opts


class TestRosterRoutingVsReference:
    """Issue #288 — guard against accidental peer-invocation when
    the LLM is merely recommending or listing peers.

    The structural invariant we keep: in the assembled system prompt,
    the only ``<@user:...>`` substrings are the placeholder
    ``<@user:PARTICIPANT_ID>`` from the usage hint. No real
    participant_id ever appears inside a ``<@user:...>`` token,
    because the roster lines list ids as data (``id: <uuid>``)
    rather than as live tokens. This is what stops the
    parse_mentions pipeline on the cluster from spuriously waking
    peers when the agent's prose copies a roster line."""

    @pytest.mark.asyncio
    async def test_roster_assembly_emits_no_real_routing_tokens(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Orc")
        client._agent_id = "agent-alpha"
        client._my_participant_ids = {"orc-pid"}
        client._orchestrator_agent_id["room-a"] = "agent-alpha"
        client._collaboration_mode_by_room["room-a"] = "collaborative"
        client._participants_by_room["room-a"] = {
            "claude2-pid": {
                "id": "claude2-pid",
                "display_name": "claude2",
                "kind": "agent",
                "agent_id": "agent-claude2",
            },
            "codex-pid": {
                "id": "codex-pid",
                "display_name": "codex",
                "kind": "agent",
                "agent_id": "agent-codex",
            },
            "gemini-pid": {
                "id": "gemini-pid",
                "display_name": "gemini",
                "kind": "agent",
                "agent_id": "agent-gemini",
            },
        }

        adapter = ClaudeCodeAdapter(system_prompt="You are Orc.", client=client)
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "room-a"})

        prompt = fake_sdk[-1]["options"].kwargs["system_prompt"]

        # Names show up as prose-friendly references.
        assert "claude2" in prompt
        assert "codex" in prompt
        assert "gemini" in prompt
        # IDs show up as data.
        assert "id: claude2-pid" in prompt
        assert "id: codex-pid" in prompt
        assert "id: gemini-pid" in prompt
        # CRITICAL: every ``<@user:...>`` substring in the assembled
        # prompt must be the literal placeholder, never a real id.
        # If any real id ever lands inside a routing token here, an
        # agent that recommends peers will wake them.
        import re

        tokens = re.findall(r"<@user:[^>]+>", prompt)
        for token in tokens:
            assert token == "<@user:PARTICIPANT_ID>", (
                f"unexpected routing token in prompt: {token!r}; only "
                "the placeholder may appear in prompt-side text"
            )


class TestIngestContext:
    """Issue #74 — `ingest_context` absorbs ambient messages into a
    per-room buffer that the next active turn consumes as a prompt
    prefix, without triggering an LLM call itself."""

    @pytest.mark.asyncio
    async def test_ingest_then_on_message_prepends_prefix(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        adapter = ClaudeCodeAdapter()
        await adapter.start()

        await adapter.ingest_context({
            "room_id": "r1",
            "participant_id": "rep-agent",
            "content": "대상 방은 GraphQL 선호",
            "metadata": {"room_query_result": {"target_room_id": "r2"}},
        })
        # Buffer should hold one formatted line.
        assert len(adapter._pending_context["r1"]) == 1

        await adapter.on_message({"content": "어때요?", "room_id": "r1"})

        sent_prompt = fake_sdk[-1]["prompt"]
        assert "[참고] 룸 r2에서" in sent_prompt
        assert "어때요?" in sent_prompt

    @pytest.mark.asyncio
    async def test_prefix_consumed_once(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Drain clears the buffer — re-injecting the same line on
        turn 2 would duplicate it in both the prompt and the SDK
        session, wasting tokens and drifting into "the model thinks
        the same context repeats every turn" confusion."""
        adapter = ClaudeCodeAdapter()
        await adapter.start()

        await adapter.ingest_context({
            "room_id": "r1",
            "participant_id": "agent-a",
            "content": "참고 발언",
            "metadata": {},
        })
        await adapter.on_message({"content": "질문 1", "room_id": "r1"})
        await adapter.on_message({"content": "질문 2", "room_id": "r1"})

        assert "[참고]" in fake_sdk[0]["prompt"]
        assert "[참고]" not in fake_sdk[1]["prompt"]

    @pytest.mark.asyncio
    async def test_rooms_have_independent_buffers(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        adapter = ClaudeCodeAdapter()
        await adapter.start()

        await adapter.ingest_context({
            "room_id": "r1",
            "participant_id": "a",
            "content": "r1-only",
            "metadata": {},
        })

        await adapter.on_message({"content": "in r2", "room_id": "r2"})

        # r2's prompt must be clean — r1's buffer leaking across
        # rooms would mix unrelated conversations into each other.
        assert "r1-only" not in fake_sdk[-1]["prompt"]

    @pytest.mark.asyncio
    async def test_empty_content_not_stashed(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Typing indicators and membership events carry empty
        content on the message frame; they're not conversational and
        shouldn't take a slot in the limited context buffer."""
        adapter = ClaudeCodeAdapter()
        await adapter.start()

        await adapter.ingest_context({
            "room_id": "r1",
            "participant_id": "a",
            "content": "",
            "metadata": {"ingest_only": True},
        })
        await adapter.ingest_context({
            "room_id": "r1",
            "participant_id": "a",
            "content": "   ",
            "metadata": {"ingest_only": True},
        })

        assert adapter._pending_context.get("r1") is None

    @pytest.mark.asyncio
    async def test_size_cap_drops_oldest(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Eleventh entry evicts the first so a chatty room can't
        balloon prompt size unbounded. FIFO gives the freshest
        messages a consistent guarantee of making it into the
        prefix."""
        adapter = ClaudeCodeAdapter()
        await adapter.start()

        from anygarden_agent.integrations.claude_code import _PENDING_CONTEXT_MAX

        for i in range(_PENDING_CONTEXT_MAX + 2):
            await adapter.ingest_context({
                "room_id": "r1",
                "participant_id": "a",
                "content": f"mID{i:02d}",
                "metadata": {},
            })

        buf = adapter._pending_context["r1"]
        assert len(buf) == _PENDING_CONTEXT_MAX
        rendered = [line for _, line in buf]
        # First two entries evicted by FIFO. Remaining span is
        # mID02 .. mID11 inclusive.
        assert all("mID00" not in line for line in rendered)
        assert all("mID01" not in line for line in rendered)
        assert any("mID02" in line for line in rendered)
        assert any("mID11" in line for line in rendered)

    @pytest.mark.asyncio
    async def test_format_room_query_result_locator(self) -> None:
        """The target room id is the locator a reader needs to go
        look at the original thread — it must survive into the
        breadcrumb. Without it, 'the other room said X' is
        ungrounded."""
        adapter = ClaudeCodeAdapter()
        line = adapter._format_context_line({
            "room_id": "r1",
            "participant_id": "rep",
            "content": "백엔드 팀 결론 요약",
            "metadata": {"room_query_result": {"target_room_id": "backend-1"}},
        })
        assert line is not None
        assert "backend-1" in line
        assert "[참고]" in line

    @pytest.mark.asyncio
    async def test_handler_routes_ingest_only(
        self,
        fake_sdk: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end through the handler: an ``ingest_only`` message
        must land in the buffer AND must not trigger a reply on its
        own. This is the property the #74 fix is ultimately shipping
        — one `[취합 결과]` broadcast feeds all listeners' context
        without N duplicate responses."""
        from anygarden_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Bot")
        # Prime the client with a participant id so decide_policy's
        # self-message rule doesn't accidentally match. The rep
        # that broadcasts `[취합 결과]` is a different agent.
        client._my_participant_ids = {"me-pid"}

        # Stub the network surface so the handler can reach the
        # ``send`` / ``sendTyping`` calls without a live WS. The
        # handler doesn't care about payload delivery here — we're
        # asserting against the SDK call log.
        sent: list[tuple[str, str]] = []

        async def _fake_send(room_id: str, content: str, metadata=None):
            sent.append((room_id, content))

        async def _fake_typing(room_id: str, is_typing: bool):
            return None

        monkeypatch.setattr(client, "send", _fake_send)
        monkeypatch.setattr(client, "sendTyping", _fake_typing)

        adapter = await integrate_with_claude_code(client, {"name": "Bot"})
        handler = client._message_handlers[0]

        ingest_msg = {
            "room_id": "r1",
            "participant_id": "rep-pid",
            "content": "[취합 결과] (3/3명 응답)\n...",
            "metadata": {
                "_nonce": "x",
                "ingest_only": True,
                "room_query_result": {"target_room_id": "r2"},
            },
        }
        await handler(ingest_msg)

        # No LLM call yet — ingestion is non-responsive.
        assert fake_sdk == []
        # The line is buffered for the next active turn.
        assert len(adapter._pending_context["r1"]) == 1
        # And nothing was broadcast — single broadcast, no fan-out.
        assert sent == []

        # Next turn from a human carries the prefix into the SDK.
        await handler({
            "room_id": "r1",
            "participant_id": "human",
            "content": "앞 내용 참고해서 답변해줘",
            "metadata": {},
        })

        assert len(fake_sdk) == 1
        assert "[참고]" in fake_sdk[0]["prompt"]


class TestRoomConversationWrapper:
    """Issue #284 — drained pending context is wrapped in a
    ``<room_conversation>`` XML block so the LLM treats it as
    awareness rather than relay-target input. The wrapper must be
    a no-op for solo turns (empty buffer) so unrelated prompts stay
    byte-identical.
    """

    @pytest.mark.asyncio
    async def test_drained_prefix_appears_inside_room_conversation_tags(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        adapter = ClaudeCodeAdapter()
        await adapter.start()

        await adapter.ingest_context({
            "room_id": "r1",
            "participant_id": "peer-agent",
            "content": "비행 8시 출발입니다",
            "metadata": {},
        })
        await adapter.on_message({"content": "다음 일정?", "room_id": "r1"})

        prompt = fake_sdk[-1]["prompt"]
        # Wrapper present and well-formed.
        assert "<room_conversation>" in prompt
        assert "</room_conversation>" in prompt
        # Drained line lives *inside* the wrapper.
        open_idx = prompt.index("<room_conversation>")
        close_idx = prompt.index("</room_conversation>")
        assert open_idx < prompt.index("[참고]") < close_idx
        # Preamble's relay-prohibition phrase must reach the prompt
        # — that's the whole point of #284.
        assert "전달하지 마세요" in prompt
        # User question stays *outside* the wrapper so the LLM still
        # sees it as the actual input to address.
        user_idx = prompt.index("다음 일정?")
        assert user_idx > close_idx

    @pytest.mark.asyncio
    async def test_solo_turn_prompt_has_no_wrapper(
        self, fake_sdk: list[dict[str, Any]]
    ) -> None:
        """Empty pending-context buffer → no wrap → the prompt is the
        bare user content, byte-identical to pre-#284. Without this
        guard the wrapper could leak into every turn and inflate
        token cost."""
        adapter = ClaudeCodeAdapter()
        await adapter.start()
        await adapter.on_message({"content": "안녕하세요", "room_id": "r1"})

        prompt = fake_sdk[-1]["prompt"]
        assert "<room_conversation>" not in prompt
        assert prompt == "안녕하세요"


class TestResultUsageExtraction:
    """#461 (Wave 2d) — claude-code is the confirmed gateway-free LLM
    telemetry source: its ``ResultMessage`` reports token usage AND a
    self-reported cost."""

    def test_extract_full_usage(self) -> None:
        from types import SimpleNamespace

        from anygarden_agent.integrations.claude_code import (
            _extract_result_usage,
        )

        msg = SimpleNamespace(
            usage={"input_tokens": 1200, "output_tokens": 350},
            total_cost_usd=0.0123,
            model_usage={"claude-sonnet-4-5": {"input_tokens": 1200}},
        )
        rec = _extract_result_usage(msg)
        assert rec == {
            "model": "claude-sonnet-4-5",
            "input_tokens": 1200,
            "output_tokens": 350,
            "cost_usd": 0.0123,
        }

    def test_extract_openai_shape_tokens(self) -> None:
        from types import SimpleNamespace

        from anygarden_agent.integrations.claude_code import (
            _extract_result_usage,
        )

        # OpenAI-style key fallback so a future SDK shape change degrades
        # gracefully rather than dropping the row.
        msg = SimpleNamespace(
            usage={"prompt_tokens": 9, "completion_tokens": 4},
            total_cost_usd=None,
            model_usage=None,
        )
        rec = _extract_result_usage(msg)
        assert rec["input_tokens"] == 9
        assert rec["output_tokens"] == 4
        assert rec["cost_usd"] is None
        assert rec["model"] is None

    def test_extract_returns_none_when_no_signal(self) -> None:
        from types import SimpleNamespace

        from anygarden_agent.integrations.claude_code import (
            _extract_result_usage,
        )

        # A ResultMessage with no usable usage signal → None → no row.
        msg = SimpleNamespace(usage=None, total_cost_usd=None, model_usage=None)
        assert _extract_result_usage(msg) is None

    @pytest.mark.asyncio
    async def test_collect_reply_stashes_usage_for_run_engine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A mocked ResultMessage with usage + total_cost_usd makes the
        adapter surface those values (read back via ``_take_last_usage``)
        so the run_engine closure can build an EngineTurn carrying them."""
        from types import SimpleNamespace

        class TextBlock:
            def __init__(self, text: str) -> None:
                self.text = text

        class AssistantMessage:
            content = [TextBlock("hi")]
            session_id = "sess-1"

        # The adapter dispatches on ``type(message).__name__ ==
        # "ResultMessage"``, so the fake must be an instance of a class
        # literally named ResultMessage (not a SimpleNamespace).
        ResultMessage = type("ResultMessage", (), {})
        rm = ResultMessage()
        rm.result = "hi"
        rm.session_id = "sess-1"
        rm.usage = {"input_tokens": 42, "output_tokens": 7}
        rm.total_cost_usd = 0.005
        rm.model_usage = {"claude-opus-4-1": {}}

        async def fake_query(*, prompt, options, transport=None):
            yield AssistantMessage()
            yield rm

        fake_module = types.ModuleType("claude_agent_sdk")
        fake_module.ClaudeAgentOptions = lambda **kw: SimpleNamespace(kwargs=kw)
        fake_module.query = fake_query
        fake_module.__version__ = "fake"
        monkeypatch.setitem(sys.modules, "claude_agent_sdk", fake_module)

        adapter = ClaudeCodeAdapter()
        await adapter.start()
        reply = await adapter.on_message({"content": "hello", "room_id": "r1"})
        assert reply == "hi"

        usage = adapter._take_last_usage()
        assert usage == {
            "model": "claude-opus-4-1",
            "input_tokens": 42,
            "output_tokens": 7,
            "cost_usd": 0.005,
        }
        # Drained — a second take is None (no leak into the next turn).
        assert adapter._take_last_usage() is None
