"""Tests for the OpenHands V1 SDK adapter (#355 Phase 0).

The real ``openhands-sdk`` package is heavy and pulls in litellm, so
tests use a fake module installed into ``sys.modules``. This mirrors
the ``test_claude_code`` / ``test_codex`` / ``test_gemini_cli`` pattern
so the suite stays fast and provider-agnostic.

Phase 0 contracts validated here:

- Lifecycle: ``start`` survives a missing SDK and degrades to no-op.
- Context plumbing: ``assemble_user_content`` (``<room_conversation>``
  wrap) AND ``compose_session_context_suffix`` (memory + roster) both
  fire — this is the #292 trap (silent degradation when plumbing is
  absent) we explicitly want to lock down.
- Per-room ``Conversation`` reuse for multi-turn state.
- ``MessageEvent`` capture → assistant text returned.
- ``ingest_context`` buffer → next active turn drain.
- Secrets bridged via ``secrets_in_env`` during the SDK call.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from doorae_agent import secrets as agent_secrets
from doorae_agent.integrations.openhands_engine import (
    OpenHandsAdapter,
    _OPENHANDS_SDK_ENV_KEYS,
    _load_mcp_manifest,
    _load_skills_summary,
    _parse_skill_frontmatter,
    integrate_with_openhands,
)


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install a fake ``openhands.sdk`` module with recording stubs.

    Returns a dict carrying:
      - ``conversations``: list of created Conversation instances so
        tests can inspect callbacks / send_message calls.
      - ``llm_kwargs``: list of LLM(...) kwargs.
      - ``agent_kwargs``: list of Agent(...) kwargs.
    """
    state: dict[str, Any] = {
        "conversations": [],
        "llm_kwargs": [],
        "agent_kwargs": [],
    }

    class FakeLLM:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            state["llm_kwargs"].append(kwargs)

    class FakeAgent:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            state["agent_kwargs"].append(kwargs)

    # NOTE: class name MUST be ``MessageEvent`` — the adapter's
    # capture closure dispatches on ``type(event).__name__`` (defensive
    # since the real SDK's event class lives in
    # ``openhands.sdk.event`` and we don't want a hard import just for
    # ``isinstance``). Renaming this class will make the capture skip
    # the event silently.
    class MessageEvent:
        """Mimics openhands.sdk.event.MessageEvent shape just enough."""

        def __init__(self, role: str, content: str) -> None:
            self.role = role
            self.content = content

    class FakeConversation:
        def __init__(
            self,
            agent: Any,
            workspace: Any,
            callbacks: list | None = None,
            **kwargs: Any,
        ) -> None:
            self.agent = agent
            self.workspace = workspace
            self.callbacks = list(callbacks or [])
            self.kwargs = kwargs
            self.sent_messages: list[str] = []
            self.run_count = 0
            self.closed = False
            state["conversations"].append(self)

        def send_message(self, content: str) -> None:
            self.sent_messages.append(content)

        def run(self) -> None:
            self.run_count += 1
            # Synthesize a single assistant MessageEvent so the
            # capture closure has something to emit. Tests can patch
            # this if they need other event sequences.
            event = MessageEvent(
                role="assistant",
                content=f"echo: {self.sent_messages[-1]}"
                if self.sent_messages
                else "echo: <empty>",
            )
            for cb in self.callbacks:
                cb(event)

        def close(self) -> None:
            self.closed = True

    fake_sdk_mod = types.ModuleType("openhands.sdk")
    fake_sdk_mod.LLM = FakeLLM  # type: ignore[attr-defined]
    fake_sdk_mod.Agent = FakeAgent  # type: ignore[attr-defined]
    fake_sdk_mod.Conversation = FakeConversation  # type: ignore[attr-defined]
    fake_sdk_mod.Tool = object  # type: ignore[attr-defined]

    fake_pkg = types.ModuleType("openhands")
    fake_pkg.sdk = fake_sdk_mod  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "openhands", fake_pkg)
    monkeypatch.setitem(sys.modules, "openhands.sdk", fake_sdk_mod)

    state["MessageEvent"] = MessageEvent
    state["Conversation"] = FakeConversation
    return state


# --------------------------------------------------------------------- start


class TestStart:
    @pytest.mark.asyncio
    async def test_start_without_sdk_installed_degrades(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing SDK → adapter logs and stays in no-op mode.

        This is the same defensive contract claude_code follows. A
        deployment without ``openhands-sdk`` installed must not crash
        on agent boot.
        """
        # Force ImportError by removing any cached stub.
        monkeypatch.setitem(sys.modules, "openhands.sdk", None)
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        assert adapter._sdk is None

    @pytest.mark.asyncio
    async def test_start_caches_sdk_handles(
        self, fake_sdk: dict[str, Any]
    ) -> None:
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        assert adapter._sdk is True
        assert adapter._llm_cls is not None
        assert adapter._agent_cls is not None
        assert adapter._conversation_cls is not None


# --------------------------------------------------------------- on_message


class TestOnMessage:
    @pytest.mark.asyncio
    async def test_returns_none_when_sdk_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setitem(sys.modules, "openhands.sdk", None)
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        result = await adapter.on_message(
            {"content": "hi", "room_id": "r1"}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_content(
        self, fake_sdk: dict[str, Any]
    ) -> None:
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        assert await adapter.on_message({"content": "", "room_id": "r1"}) is None

    @pytest.mark.asyncio
    async def test_send_message_and_run_invoked(
        self, fake_sdk: dict[str, Any]
    ) -> None:
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        reply = await adapter.on_message(
            {"content": "hello world", "room_id": "r1"}
        )
        assert fake_sdk["conversations"], "Conversation should have been created"
        conv = fake_sdk["conversations"][0]
        assert conv.sent_messages == ["hello world"]
        assert conv.run_count == 1
        assert reply == "echo: hello world"

    @pytest.mark.asyncio
    async def test_per_room_conversation_reuse(
        self, fake_sdk: dict[str, Any]
    ) -> None:
        """Second message in same room must reuse the Conversation.

        Multi-turn context lives inside the SDK's event-sourced state,
        so a per-turn fresh Conversation would silently lose history
        — exactly the failure mode #292 cited for the dead adapters.
        """
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        await adapter.on_message({"content": "first", "room_id": "r1"})
        await adapter.on_message({"content": "second", "room_id": "r1"})
        assert len(fake_sdk["conversations"]) == 1
        assert fake_sdk["conversations"][0].sent_messages == ["first", "second"]

    @pytest.mark.asyncio
    async def test_separate_rooms_get_separate_conversations(
        self, fake_sdk: dict[str, Any]
    ) -> None:
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        await adapter.on_message({"content": "a", "room_id": "r1"})
        await adapter.on_message({"content": "b", "room_id": "r2"})
        assert len(fake_sdk["conversations"]) == 2

    @pytest.mark.asyncio
    async def test_assemble_user_content_pipeline_runs(
        self, fake_sdk: dict[str, Any]
    ) -> None:
        """Pending context must wrap as ``<room_conversation>`` (#286).

        We seed the buffer manually, fire on_message, and assert the
        prompt that hits send_message has the wrap envelope. If this
        breaks, the new engine ate ambient room chatter that the
        three CLI adapters preserve — a #292-style silent regression.
        """
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        await adapter.ingest_context(
            {
                "content": "@alice helped earlier",
                "room_id": "r1",
                "participant_id": "p-bob",
                "metadata": {},
            }
        )
        await adapter.on_message({"content": "now answer this", "room_id": "r1"})
        sent = fake_sdk["conversations"][0].sent_messages[0]
        assert "<room_conversation>" in sent
        assert "now answer this" in sent

    @pytest.mark.asyncio
    async def test_session_context_suffix_prepended_when_present(
        self, fake_sdk: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """memory + roster suffix must be prepended (#293).

        We patch ``compose_session_context_suffix`` to return a
        marker; the adapter must hand the marker text to send_message.
        """
        marker = "##MEMORY-AND-ROSTER##"
        monkeypatch.setattr(
            "doorae_agent.integrations.openhands_engine.compose_session_context_suffix",
            lambda *a, **kw: marker,
        )
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        await adapter.on_message({"content": "ping", "room_id": "r1"})
        sent = fake_sdk["conversations"][0].sent_messages[0]
        assert sent.startswith(marker)
        assert "ping" in sent


# ---------------------------------------------------------- ingest_context


class TestIngestContext:
    @pytest.mark.asyncio
    async def test_appends_formatted_line(
        self, fake_sdk: dict[str, Any]
    ) -> None:
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        await adapter.ingest_context(
            {
                "content": "ambient note",
                "room_id": "r1",
                "participant_id": "p-bob",
                "metadata": {},
            }
        )
        assert "r1" in adapter._pending_context
        assert adapter._pending_context["r1"], "buffer should have one entry"

    @pytest.mark.asyncio
    async def test_drops_unrenderable_message(
        self, fake_sdk: dict[str, Any]
    ) -> None:
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        await adapter.ingest_context({"content": "", "room_id": "r1"})
        assert "r1" not in adapter._pending_context


# --------------------------------------------------------------- secrets


class TestSecretsBridging:
    @pytest.mark.asyncio
    async def test_secrets_present_in_env_during_run(
        self,
        fake_sdk: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """During send_message+run, ANTHROPIC_API_KEY (and friends)
        must be visible in os.environ; outside, they must not be.

        Belt-and-suspenders for #184: keys live in private
        ``agent_secrets`` storage so a tool call can't read them off
        ``/proc/self/environ`` between turns. The adapter must use
        the ``secrets_in_env`` context manager to bridge them only
        for the SDK call duration.
        """
        import os

        captured_env: dict[str, str | None] = {}

        # Replace Conversation.run so it inspects os.environ during call.
        original_conv = fake_sdk["Conversation"]

        class CapturingConversation(original_conv):  # type: ignore[misc, valid-type]
            def run(self) -> None:
                for key in _OPENHANDS_SDK_ENV_KEYS:
                    captured_env[key] = os.environ.get(key)
                super().run()

        sdk_mod = sys.modules["openhands.sdk"]
        monkeypatch.setattr(sdk_mod, "Conversation", CapturingConversation)

        agent_secrets.set_secrets({"ANTHROPIC_API_KEY": "secret-xyz"})
        try:
            adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
            await adapter.start()
            assert "ANTHROPIC_API_KEY" not in os.environ, (
                "secret must not leak into env outside the SDK call window"
            )
            await adapter.on_message({"content": "x", "room_id": "r1"})
            assert captured_env.get("ANTHROPIC_API_KEY") == "secret-xyz"
            assert "ANTHROPIC_API_KEY" not in os.environ, (
                "secret must be removed from env after the SDK call"
            )
        finally:
            agent_secrets.clear()


# ----------------------------------------------------------------- stop


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_closes_each_conversation(
        self, fake_sdk: dict[str, Any]
    ) -> None:
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        await adapter.on_message({"content": "x", "room_id": "r1"})
        await adapter.on_message({"content": "y", "room_id": "r2"})
        await adapter.stop()
        assert all(c.closed for c in fake_sdk["conversations"])
        assert adapter._conversations == {}
        assert adapter._sdk is None


# ----------------------------------------------------------- integration


class _FakeChatClient:
    """Minimal ChatClient stub for integrate_with_openhands tests."""

    def __init__(self) -> None:
        self._handler = None
        self._typing_calls: list[tuple[str, bool]] = []
        self.lifecycle_events: list[dict[str, Any]] = []

    def on_message(self, fn):  # noqa: ANN001 — match real decorator signature
        self._handler = fn
        return fn

    async def sendTyping(self, room_id: str, on: bool) -> None:
        self._typing_calls.append((room_id, on))

    async def sendLifecycle(self, room_id, request_id, *, event, outcome=None,
                            error=None, **kwargs) -> None:  # noqa: ANN001
        self.lifecycle_events.append(
            {
                "room_id": room_id,
                "request_id": request_id,
                "event": event,
                "outcome": outcome,
                "error": error,
                **kwargs,
            }
        )

    # decide_policy reads a few attributes; minimal stubs to match.
    @property
    def _my_participant_ids(self) -> set[str]:
        return set()

    @property
    def _agent_name(self) -> str:
        return "OpenHands"

    @property
    def _recent_msgs(self) -> dict[str, tuple]:
        return {}


class TestIntegrate:
    @pytest.mark.asyncio
    async def test_integrate_returns_started_adapter(
        self, fake_sdk: dict[str, Any]
    ) -> None:
        client = _FakeChatClient()
        adapter = await integrate_with_openhands(
            client,  # type: ignore[arg-type]
            agent_config={
                "name": "TestAgent",
                "model": "anthropic/claude-opus-4-7",
            },
        )
        assert isinstance(adapter, OpenHandsAdapter)
        assert adapter._sdk is True
        assert client._handler is not None, "on_message handler must be wired"


# ---------------------------------------------------- Phase 1: MCP loader


class TestLoadMcpManifest:
    """Cover the .mcp.json reader paranoia surface (#352 → #354 trap).

    A bad manifest must never crash the adapter — it has to degrade
    to "no MCP" so the agent still boots and the operator can see
    the failure in logs.
    """

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert _load_mcp_manifest(tmp_path / "absent.json") is None

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        path.write_text("", encoding="utf-8")
        assert _load_mcp_manifest(path) is None

    def test_invalid_json_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert _load_mcp_manifest(path) is None

    def test_non_object_root_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text('["unexpected"]', encoding="utf-8")
        assert _load_mcp_manifest(path) is None

    def test_empty_servers_returns_none(self, tmp_path: Path) -> None:
        path = tmp_path / "empty_servers.json"
        path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        assert _load_mcp_manifest(path) is None

    def test_valid_manifest_returns_dict(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        manifest = {
            "mcpServers": {
                "doorae": {
                    "type": "http",
                    "url": "http://localhost/mcp/rpc",
                    "headers": {"Authorization": "Bearer xyz"},
                }
            }
        }
        path.write_text(json.dumps(manifest), encoding="utf-8")
        loaded = _load_mcp_manifest(path)
        assert loaded == manifest


# ---------------------------------------------- Phase 1: Agent mcp_config


class TestMcpConfigForwarded:
    @pytest.mark.asyncio
    async def test_mcp_config_passed_to_agent_when_manifest_exists(
        self,
        fake_sdk: dict[str, Any],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If ``.mcp.json`` exists at cwd, the dict reaches Agent(...)."""
        manifest = {
            "mcpServers": {
                "doorae": {
                    "type": "http",
                    "url": "http://localhost:8000/mcp/rpc",
                    "headers": {"Authorization": "Bearer test"},
                }
            }
        }
        (tmp_path / ".mcp.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "r1"})

        assert fake_sdk["agent_kwargs"], "Agent should have been constructed"
        agent_kw = fake_sdk["agent_kwargs"][0]
        assert "mcp_config" in agent_kw, (
            "mcp_config kwarg must be forwarded when manifest exists"
        )
        assert agent_kw["mcp_config"] == manifest

    @pytest.mark.asyncio
    async def test_no_mcp_config_kwarg_when_manifest_missing(
        self,
        fake_sdk: dict[str, Any],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No manifest → Agent constructed without ``mcp_config`` kwarg.

        Passing ``mcp_config=None`` or ``{}`` could trip stricter SDK
        validation; absent kwarg is the cleanest "no MCP" signal.
        """
        monkeypatch.chdir(tmp_path)
        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        await adapter.on_message({"content": "hi", "room_id": "r1"})

        assert fake_sdk["agent_kwargs"]
        agent_kw = fake_sdk["agent_kwargs"][0]
        assert "mcp_config" not in agent_kw

    @pytest.mark.asyncio
    async def test_agent_falls_back_when_sdk_rejects_mcp_config(
        self,
        fake_sdk: dict[str, Any],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An older SDK without mcp_config kwarg → adapter still boots.

        The first construction raises TypeError; the adapter retries
        without mcp_config and the agent comes up with no MCP. The
        regression we're guarding: "new feature crashes adapter on
        legacy SDK, room goes dark" — the same flavour of silent
        breakage #292 cited.
        """
        manifest = {"mcpServers": {"doorae": {"url": "http://x/mcp"}}}
        (tmp_path / ".mcp.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

        sdk_mod = sys.modules["openhands.sdk"]
        original_agent = sdk_mod.Agent  # type: ignore[attr-defined]

        class StrictAgent(original_agent):  # type: ignore[misc, valid-type]
            def __init__(self, **kwargs: Any) -> None:
                if "mcp_config" in kwargs:
                    raise TypeError(
                        "Agent.__init__() got an unexpected keyword "
                        "argument 'mcp_config'"
                    )
                super().__init__(**kwargs)

        monkeypatch.setattr(sdk_mod, "Agent", StrictAgent)

        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        reply = await adapter.on_message({"content": "hi", "room_id": "r1"})
        # Reply still produced — Agent boots, Conversation runs.
        assert reply is not None
        # The successful kwargs (post-fallback) should NOT carry mcp_config.
        agent_kw = fake_sdk["agent_kwargs"][-1]
        assert "mcp_config" not in agent_kw


# --------------------------------------------- Phase 2: skills awareness


class TestParseSkillFrontmatter:
    def test_no_frontmatter_returns_empty(self) -> None:
        assert _parse_skill_frontmatter("# Just a heading") == {}

    def test_unterminated_frontmatter_returns_empty(self) -> None:
        # Opening --- but no closing fence — malformed.
        assert _parse_skill_frontmatter("---\nname: x\n") == {}

    def test_basic_pairs(self) -> None:
        raw = "---\nname: tdd\ndescription: Run tests first\n---\nbody"
        meta = _parse_skill_frontmatter(raw)
        assert meta == {"name": "tdd", "description": "Run tests first"}

    def test_quoted_value_unwrapped(self) -> None:
        # Description with embedded colon needs quote wrapping; the
        # parser must strip the wrapping quotes so the rendered block
        # doesn't show them.
        raw = '---\ndescription: "Use this: do that"\n---\nbody'
        assert _parse_skill_frontmatter(raw) == {
            "description": "Use this: do that"
        }

    def test_comment_lines_skipped(self) -> None:
        raw = "---\n# this is a comment\nname: x\n---\nbody"
        assert _parse_skill_frontmatter(raw) == {"name": "x"}


class TestLoadSkillsSummary:
    def _make_skill(
        self, root: Path, slug: str, name: str, description: str
    ) -> None:
        skill_dir = root / slug
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {description}\n---\n# Body",
            encoding="utf-8",
        )

    def test_missing_dir_returns_none(self, tmp_path: Path) -> None:
        assert _load_skills_summary(tmp_path / "absent") is None

    def test_empty_dir_returns_none(self, tmp_path: Path) -> None:
        (tmp_path / "skills").mkdir()
        assert _load_skills_summary(tmp_path / "skills") is None

    def test_skill_without_skill_md_skipped(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        (skills / "loose-dir").mkdir(parents=True)
        # Only the slug dir, no SKILL.md inside → should be skipped.
        assert _load_skills_summary(skills) is None

    def test_skill_without_description_skipped(self, tmp_path: Path) -> None:
        """Listing a name with nothing alongside it just wastes prompt
        tokens — the parser drops those entries silently."""
        skills = tmp_path / "skills"
        skills.mkdir()
        (skills / "anon").mkdir()
        (skills / "anon" / "SKILL.md").write_text(
            "---\nname: anon\n---\nbody", encoding="utf-8"
        )
        assert _load_skills_summary(skills) is None

    def test_renders_block_for_one_skill(self, tmp_path: Path) -> None:
        skills = tmp_path / "skills"
        self._make_skill(skills, "tdd", "tdd", "Run tests first")
        block = _load_skills_summary(skills)
        assert block is not None
        assert "## Available skills" in block
        assert "**tdd** — Run tests first" in block

    def test_multiple_skills_listed_alphabetically(
        self, tmp_path: Path
    ) -> None:
        skills = tmp_path / "skills"
        # Insertion order zigzags; iterdir returns dir-entry order so
        # we sort. Verify alphabetical sort by slug at the listing
        # level — keeps prompt content stable across machines.
        self._make_skill(skills, "zzz-late", "zzz-late", "last alphabetically")
        self._make_skill(skills, "aaa-early", "aaa-early", "first alphabetically")
        block = _load_skills_summary(skills)
        assert block is not None
        a_idx = block.index("aaa-early")
        z_idx = block.index("zzz-late")
        assert a_idx < z_idx, "skills should appear in alphabetical order"


class TestSkillsInjectedIntoSystemPrompt:
    @pytest.mark.asyncio
    async def test_skills_block_prepended_to_system_prompt(
        self,
        fake_sdk: dict[str, Any],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """skills block + caller system_prompt → both reach Agent.

        The block must come first so the LLM has the capability
        inventory before any task-specific narrowing takes effect.
        """
        skills = tmp_path / "skills" / "code-review"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text(
            "---\nname: code-review\ndescription: Review code carefully\n---\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        adapter = OpenHandsAdapter(
            model="anthropic/claude-opus-4-7",
            system_prompt="You are a helpful assistant.",
        )
        await adapter.start()
        await adapter.on_message({"content": "ping", "room_id": "r1"})

        agent_kw = fake_sdk["agent_kwargs"][0]
        sp = agent_kw.get("system_prompt") or agent_kw.get("system_message")
        assert sp is not None, "system prompt must reach Agent"
        # Skills first, then user prompt.
        assert sp.index("Available skills") < sp.index(
            "You are a helpful assistant."
        )
        assert "code-review" in sp

    @pytest.mark.asyncio
    async def test_no_skills_no_block(
        self,
        fake_sdk: dict[str, Any],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Empty skills dir → only the caller's system_prompt.

        The block costs prompt tokens; we must not emit a stub
        header when there's nothing to list.
        """
        monkeypatch.chdir(tmp_path)

        adapter = OpenHandsAdapter(
            model="anthropic/claude-opus-4-7",
            system_prompt="caller prompt",
        )
        await adapter.start()
        await adapter.on_message({"content": "ping", "room_id": "r1"})

        agent_kw = fake_sdk["agent_kwargs"][0]
        sp = agent_kw.get("system_prompt") or agent_kw.get("system_message")
        assert sp == "caller prompt"
        assert "Available skills" not in sp

    @pytest.mark.asyncio
    async def test_skills_only_no_caller_prompt(
        self,
        fake_sdk: dict[str, Any],
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """No caller system_prompt but skills present → block alone reaches Agent."""
        skills = tmp_path / "skills" / "tdd"
        skills.mkdir(parents=True)
        (skills / "SKILL.md").write_text(
            "---\nname: tdd\ndescription: Test-first\n---\n",
            encoding="utf-8",
        )
        monkeypatch.chdir(tmp_path)

        adapter = OpenHandsAdapter(model="anthropic/claude-opus-4-7")
        await adapter.start()
        await adapter.on_message({"content": "ping", "room_id": "r1"})

        agent_kw = fake_sdk["agent_kwargs"][0]
        sp = agent_kw.get("system_prompt") or agent_kw.get("system_message")
        assert sp is not None
        assert "Available skills" in sp
        assert "**tdd** — Test-first" in sp
