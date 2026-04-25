"""Integration tests for the Gemini CLI adapter."""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from doorae_agent.integrations.gemini_cli import (
    GeminiCliAdapter,
    integrate_with_gemini_cli,
)


class TestGeminiCliAdapter:
    @pytest.mark.asyncio
    async def test_start_finds_gemini_or_not(self) -> None:
        """Adapter detects gemini CLI availability on start."""
        adapter = GeminiCliAdapter()
        await adapter.start()
        if shutil.which("gemini"):
            assert adapter._gemini_path is not None
        else:
            assert adapter._gemini_path is None

    @pytest.mark.asyncio
    async def test_on_message_returns_none_when_not_available(self) -> None:
        adapter = GeminiCliAdapter()
        adapter._gemini_path = None
        result = await adapter.on_message(
            {"type": "message", "content": "Hi", "room_id": "r1", "seq": 1}
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_on_message_calls_gemini(self) -> None:
        adapter = GeminiCliAdapter()
        adapter._gemini_path = "/usr/bin/gemini"

        with patch.object(
            adapter,
            "_call_gemini",
            new_callable=AsyncMock,
            return_value="hello from gemini",
        ):
            result = await adapter.on_message(
                {"content": "Hi", "room_id": "r1"}
            )

        assert result == "hello from gemini"
        # Conversation state captured both turns.
        assert adapter._conversations["r1"][0]["role"] == "user"
        assert adapter._conversations["r1"][1]["role"] == "assistant"
        assert adapter._conversations["r1"][1]["content"] == "hello from gemini"

    @pytest.mark.asyncio
    async def test_on_message_rollback_on_empty_response(self) -> None:
        """If gemini returns nothing, the user turn should not linger
        in conversation history — otherwise the next call re-sends a
        message that the model never actually saw a response to.
        """
        adapter = GeminiCliAdapter()
        adapter._gemini_path = "/usr/bin/gemini"

        with patch.object(
            adapter, "_call_gemini", new_callable=AsyncMock, return_value=None
        ):
            result = await adapter.on_message(
                {"content": "Hi", "room_id": "r1"}
            )
        assert result is None
        assert adapter._conversations["r1"] == []

    @pytest.mark.asyncio
    async def test_on_message_rollback_on_exception(self) -> None:
        adapter = GeminiCliAdapter()
        adapter._gemini_path = "/usr/bin/gemini"

        async def boom(prompt: str) -> str | None:
            raise RuntimeError("oops")

        with patch.object(adapter, "_call_gemini", new=boom):
            result = await adapter.on_message(
                {"content": "Hi", "room_id": "r1"}
            )
        assert result is None
        assert adapter._conversations["r1"] == []

    @pytest.mark.asyncio
    async def test_per_room_isolation(self) -> None:
        """Two rooms must not share conversation history."""
        adapter = GeminiCliAdapter()
        adapter._gemini_path = "/usr/bin/gemini"

        with patch.object(
            adapter, "_call_gemini", new_callable=AsyncMock, return_value="ok"
        ):
            await adapter.on_message({"content": "room1 only", "room_id": "r1"})
            await adapter.on_message({"content": "room2 only", "room_id": "r2"})

        assert len(adapter._conversations["r1"]) == 2
        assert len(adapter._conversations["r2"]) == 2
        assert adapter._conversations["r1"][0]["content"] == "room1 only"
        assert adapter._conversations["r2"][0]["content"] == "room2 only"


class TestParseResponse:
    def test_extracts_response_field(self) -> None:
        raw = '{"response": "hello world"}'
        assert GeminiCliAdapter._parse_response(raw) == "hello world"

    def test_extracts_text_field(self) -> None:
        raw = '{"text": "from text field"}'
        assert GeminiCliAdapter._parse_response(raw) == "from text field"

    def test_extracts_content_field(self) -> None:
        raw = '{"content": "from content field"}'
        assert GeminiCliAdapter._parse_response(raw) == "from content field"

    def test_extracts_output_field(self) -> None:
        raw = '{"output": "from output field"}'
        assert GeminiCliAdapter._parse_response(raw) == "from output field"

    def test_falls_back_to_raw_on_bad_json(self) -> None:
        raw = "not json at all"
        assert GeminiCliAdapter._parse_response(raw) == "not json at all"

    def test_dumps_dict_when_no_known_field(self) -> None:
        raw = '{"unknown_field": "value"}'
        result = GeminiCliAdapter._parse_response(raw)
        assert result is not None
        assert "unknown_field" in result

    def test_empty_string_returns_none(self) -> None:
        assert GeminiCliAdapter._parse_response("") is None
        assert GeminiCliAdapter._parse_response("   ") is None


class TestCallGemini:
    """Regression tests for the subprocess invocation surface.

    Three things that the adapter MUST get right on every call:

    1. ``cwd=agent_root`` (one level above the agent's python cwd),
       because gemini's ``findProjectRoot`` walks upward from cwd
       looking for ``.git``. In the per-agent layout no ``.git``
       exists, so whichever directory the subprocess is launched
       from becomes the "project root" and thereby the sole place
       gemini looks for ``.gemini/settings.json`` +
       hierarchical-memory context files like ``AGENTS.md``. If the
       cwd stays at ``workspace/`` (the python cwd) gemini finds
       nothing and behaves like a stock session — no skills, no
       role, no rules.

    2. ``--approval-mode yolo`` so gemini does not block on
       human-in-the-loop tool approval. Non-interactive gemini
       defaults to "prompt for approval" the moment any tool call
       fires; with no human behind the subprocess the request hangs
       until the timeout. Matches the trust model of the codex and
       claude-code adapters which also run unattended.

    3. ``--skip-trust`` so gemini does not downgrade ``--approval-mode
       yolo`` to ``default`` because the cwd is not in
       ``~/.gemini/trustedFolders.json``. The agent_root is a fresh
       UUID directory on every spawn and cannot be pre-registered;
       without this flag gemini 0.39.x exits with code 55 and an
       empty stdout, which the adapter then surfaces as ``None`` —
       i.e. the user sees no response at all (#261).
    """

    @pytest.mark.asyncio
    async def test_cwd_is_agent_root_and_approval_mode_yolo(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Pretend the agent process was launched from
        # ``<tmp>/agent_root/workspace/`` — the cwd the machine
        # spawner pins agents to. The adapter derives agent_root
        # as ``Path.cwd().parent``.
        agent_root = tmp_path / "agent_root"
        workspace = agent_root / "workspace"
        workspace.mkdir(parents=True)
        monkeypatch.chdir(workspace)

        adapter = GeminiCliAdapter()
        adapter._gemini_path = "/usr/bin/gemini"

        captured: dict[str, object] = {}

        class FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return (b'{"response": "ok"}', b"")

        async def fake_exec(*args: object, **kwargs: object) -> FakeProc:
            captured["args"] = args
            captured["cwd"] = kwargs.get("cwd")
            return FakeProc()

        monkeypatch.setattr(
            "asyncio.create_subprocess_exec", fake_exec
        )

        result = await adapter._call_gemini("hello")
        assert result == "ok"

        # cwd pinned at agent_root, NOT workspace
        assert captured["cwd"] == str(agent_root)

        # --approval-mode yolo is present in the argv
        argv = list(captured["args"])  # type: ignore[arg-type]
        assert "--approval-mode" in argv
        assert argv[argv.index("--approval-mode") + 1] == "yolo"

        # sanity: json output format is still requested
        assert "--output-format" in argv
        assert argv[argv.index("--output-format") + 1] == "json"

    @pytest.mark.asyncio
    async def test_skip_trust_flag_is_passed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # See class docstring item 3. Without ``--skip-trust`` gemini
        # 0.39.x silently downgrades yolo to default and exits 55 in
        # non-interactive mode, leaving the user with no response.
        agent_root = tmp_path / "agent_root"
        workspace = agent_root / "workspace"
        workspace.mkdir(parents=True)
        monkeypatch.chdir(workspace)

        adapter = GeminiCliAdapter()
        adapter._gemini_path = "/usr/bin/gemini"

        captured: dict[str, object] = {}

        class FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return (b'{"response": "ok"}', b"")

        async def fake_exec(*args: object, **kwargs: object) -> FakeProc:
            captured["args"] = args
            return FakeProc()

        monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)

        await adapter._call_gemini("hello")

        argv = list(captured["args"])  # type: ignore[arg-type]
        assert "--skip-trust" in argv


class TestIntegrateWithGeminiCli:
    @pytest.mark.asyncio
    async def test_integrate_registers_handler(self) -> None:
        from doorae_agent.client import ChatClient

        client = ChatClient("ws://localhost:8000", token="t", agent_name="Bot")
        assert len(client._message_handlers) == 0

        adapter = await integrate_with_gemini_cli(client)

        assert len(client._message_handlers) == 1
        assert isinstance(adapter, GeminiCliAdapter)
