"""Unit tests for the codex-cli (``codex exec``) adapter (#496)."""

from __future__ import annotations

from anygarden.agent.memory.scope import room_session_key

import json
from pathlib import Path

import pytest
from anygarden.agent.integrations.codex_cli import (
    CodexCliAdapter,
    _resolve_codex_cli_args,
)


@pytest.fixture(autouse=True)
def _isolate_agent_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # #526 — the adapter now persists per-room session handles to a file
    # under ``Path.cwd()``. Isolate each test's cwd so those writes can't
    # leak into a shared dir and pollute sibling tests.
    monkeypatch.chdir(tmp_path)


class TestResolveCodexCliArgs:
    """Tier → ``codex exec`` flag mapping (shares codex's tier table)."""

    def test_restricted(self) -> None:
        assert _resolve_codex_cli_args("restricted") == [
            "-c",
            "sandbox_mode=read-only",
            "-c",
            "approval_policy=untrusted",
        ]

    def test_standard(self) -> None:
        assert _resolve_codex_cli_args("standard") == [
            "-c",
            "sandbox_mode=workspace-write",
            "-c",
            "approval_policy=never",
        ]

    def test_trusted(self) -> None:
        assert _resolve_codex_cli_args("trusted") == [
            "-c",
            "sandbox_mode=danger-full-access",
            "-c",
            "approval_policy=never",
        ]

    def test_none_defaults_to_standard(self) -> None:
        # ``None`` → standard tier (pre-#309 behaviour), same as codex SDK.
        assert _resolve_codex_cli_args(None) == [
            "-c",
            "sandbox_mode=workspace-write",
            "-c",
            "approval_policy=never",
        ]

    def test_unknown_tier_raises(self) -> None:
        with pytest.raises(ValueError):
            _resolve_codex_cli_args("trustred")  # typo fails loud

    # #858 — the outbox lives outside the codex workspace (reached via a
    # symlink), so workspace-write must list it as an extra writable root.
    def test_standard_adds_outbox_writable_root(self, tmp_path) -> None:
        outbox = tmp_path / "memory" / "outbox"
        args = _resolve_codex_cli_args("standard", outbox_dir=outbox)
        assert args[-2:] == [
            "-c",
            f'sandbox_workspace_write.writable_roots=["{outbox}"]',
        ]

    @pytest.mark.parametrize("tier", ["restricted", "trusted"])
    def test_other_tiers_ignore_outbox(self, tier, tmp_path) -> None:
        args = _resolve_codex_cli_args(tier, outbox_dir=tmp_path / "outbox")
        assert not any("writable_roots" in arg for arg in args)


class TestParseCodexJsonl:
    """JSONL event parsing — the decoupling payoff (no SDK shim needed)."""

    def test_full_stream(self) -> None:
        raw = "\n".join(
            [
                json.dumps({"type": "thread.started", "thread_id": "abc-123"}),
                json.dumps({"type": "turn.started"}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "Hello"},
                    }
                ),
                json.dumps(
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 10, "output_tokens": 3},
                    }
                ),
            ]
        )
        tid, text, usage = CodexCliAdapter._parse_codex_jsonl(raw)
        assert tid == "abc-123"
        assert text == "Hello"
        assert usage == {"input_tokens": 10, "output_tokens": 3}

    def test_unknown_event_types_skipped(self) -> None:
        # An unrecognised ``type`` must not break parsing — this is why
        # codex-cli needs no parse_notification shim (#190).
        raw = "\n".join(
            [
                json.dumps({"type": "some.future.event", "payload": {"x": 1}}),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "reasoning", "text": "thinking"},
                    }
                ),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "hi"},
                    }
                ),
            ]
        )
        tid, text, usage = CodexCliAdapter._parse_codex_jsonl(raw)
        assert tid is None
        assert text == "hi"  # only agent_message contributes
        assert usage is None

    def test_invalid_lines_skipped(self) -> None:
        raw = (
            "not json\n"
            "{bad json}\n"
            + json.dumps({"type": "thread.started", "thread_id": "t1"})
        )
        tid, _text, _usage = CodexCliAdapter._parse_codex_jsonl(raw)
        assert tid == "t1"

    def test_multiple_agent_messages_joined(self) -> None:
        raw = "\n".join(
            [
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "a"},
                    }
                ),
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": "b"},
                    }
                ),
            ]
        )
        _tid, text, _usage = CodexCliAdapter._parse_codex_jsonl(raw)
        assert text == "a\nb"

    def test_empty(self) -> None:
        assert CodexCliAdapter._parse_codex_jsonl("") == (None, None, None)


class TestExtractUsage:
    def test_maps_input_output_tokens(self) -> None:
        usage = CodexCliAdapter._extract_usage(
            {
                "input_tokens": 16671,
                "cached_input_tokens": 2432,
                "output_tokens": 24,
                "reasoning_output_tokens": 17,
            },
            "gpt-5.5",
        )
        assert usage == {
            "model": "gpt-5.5",
            "input_tokens": 16671,
            "output_tokens": 24,
            "cost_usd": None,
        }

    def test_missing_usage_is_model_only(self) -> None:
        assert CodexCliAdapter._extract_usage(None, "gpt-5.5") == {
            "model": "gpt-5.5",
            "input_tokens": None,
            "output_tokens": None,
            "cost_usd": None,
        }

    def test_bool_rejected_as_token_count(self) -> None:
        usage = CodexCliAdapter._extract_usage(
            {"input_tokens": True, "output_tokens": 5}, "m"
        )
        assert usage["input_tokens"] is None
        assert usage["output_tokens"] == 5


class TestCallCodexSession:
    """Session continuity: first turn captures id, later turns resume."""

    @pytest.mark.req("ENG-06")
    async def test_first_turn_captures_thread_id(self, monkeypatch) -> None:
        adapter = CodexCliAdapter()
        adapter._codex_path = "/usr/bin/codex"

        async def fake_exec_once(prompt, thread_id):
            assert thread_id is None  # first turn has no session
            return "reply", "new-tid", {"input_tokens": 1, "output_tokens": 2}, False

        monkeypatch.setattr(adapter, "_exec_once", fake_exec_once)
        resp = await adapter._call_codex("hi", "room1")
        assert resp == "reply"
        assert adapter._room_thread_ids[room_session_key(adapter._client, "room1")] == "new-tid"
        assert adapter._take_last_usage() == {
            "model": "gpt-6.1-sol",
            "input_tokens": 1,
            "output_tokens": 2,
            "cost_usd": None,
        }

    async def test_second_turn_resumes_existing_session(self, monkeypatch) -> None:
        adapter = CodexCliAdapter()
        adapter._codex_path = "/usr/bin/codex"
        adapter._room_thread_ids[room_session_key(adapter._client, "room1")] = "existing-tid"
        seen: dict[str, str | None] = {}

        async def fake_exec_once(prompt, thread_id):
            seen["tid"] = thread_id
            return "r", "existing-tid", None, False

        monkeypatch.setattr(adapter, "_exec_once", fake_exec_once)
        await adapter._call_codex("hi", "room1")
        assert seen["tid"] == "existing-tid"

    async def test_resume_failure_never_retries_fresh(self, monkeypatch) -> None:
        adapter = CodexCliAdapter()
        adapter._codex_path = "/usr/bin/codex"
        adapter._room_thread_ids[room_session_key(adapter._client, "room1")] = "stale"
        calls: list[str | None] = []

        async def fake_exec_once(prompt, thread_id):
            calls.append(thread_id)
            if thread_id == "stale":
                return None, None, None, True  # resume_failed
            return "fresh-reply", "new-tid", None, False

        monkeypatch.setattr(adapter, "_exec_once", fake_exec_once)
        from anygarden.agent.runtime.handler_wrapper import EngineError
        with pytest.raises(EngineError) as error:
            await adapter._call_codex("hi", "room1")
        assert error.value.transient is False
        assert calls == ["stale"]
        assert adapter._room_thread_ids[room_session_key(adapter._client, "room1")] == "stale"


class TestOnMessage:
    async def test_returns_response_and_records_turn_input(self, monkeypatch) -> None:
        adapter = CodexCliAdapter()
        adapter._codex_path = "/usr/bin/codex"

        async def fake_call(prompt, room_id):
            return "the reply"

        monkeypatch.setattr(adapter, "_call_codex", fake_call)
        resp = await adapter.on_message(
            {"content": "hello", "room_id": "r1", "metadata": {}}
        )
        assert resp == "the reply"

    async def test_empty_content_returns_none(self) -> None:
        adapter = CodexCliAdapter()
        adapter._codex_path = "/usr/bin/codex"
        assert await adapter.on_message({"content": "", "room_id": "r1"}) is None

    async def test_no_binary_returns_none(self) -> None:
        adapter = CodexCliAdapter()
        adapter._codex_path = None  # binary not found
        assert await adapter.on_message({"content": "hi", "room_id": "r1"}) is None

    async def test_exception_wrapped_as_engine_error(self, monkeypatch) -> None:
        from anygarden.agent.runtime.handler_wrapper import EngineError

        adapter = CodexCliAdapter()
        adapter._codex_path = "/usr/bin/codex"

        async def boom(prompt, room_id):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(adapter, "_call_codex", boom)
        with pytest.raises(EngineError):
            await adapter.on_message({"content": "hi", "room_id": "r1", "metadata": {}})


# -- non-zero exit diagnosis + transient classification (2026-10-12) -------


def _api_error(status: int, etype: str, message: str) -> str:
    return json.dumps(
        {"type": "error", "status": status, "error": {"type": etype, "message": message}}
    )


def _jsonl(*events: dict) -> bytes:
    return ("\n".join(json.dumps(e) for e in events) + "\n").encode()


def _failure_stream(message: str, *, side_effect: bool = False) -> bytes:
    events: list[dict] = [
        {"type": "thread.started", "thread_id": "tid"},
        {"type": "turn.started"},
    ]
    if side_effect:
        events += [
            {"type": "item.started", "item": {"id": "i0", "type": "command_execution", "command": "rm x"}},
            {"type": "item.completed", "item": {"id": "i0", "type": "command_execution", "exit_code": 0}},
        ]
    events += [
        {"type": "error", "message": message},
        {"type": "turn.failed", "error": {"message": message}},
    ]
    return _jsonl(*events)


class _FakeProc:
    pid = 4242

    def __init__(self, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self._out = (stdout, stderr)
        self.returncode = returncode

    async def communicate(self, input: bytes | None = None):
        return self._out

    async def wait(self) -> int:
        return self.returncode


def _fake_subprocess(monkeypatch, stdout: bytes, stderr: bytes = b"", returncode: int = 1):
    async def fake_exec(*args, **kwargs):
        return _FakeProc(stdout, stderr, returncode)

    monkeypatch.setattr(
        "anygarden.agent.integrations.codex_cli.asyncio.create_subprocess_exec", fake_exec
    )


class TestNonzeroExitClassification:
    """codex ``exec --json`` failures: reason surfaced, transient only when safe."""

    @staticmethod
    def _adapter() -> CodexCliAdapter:
        adapter = CodexCliAdapter()
        adapter._codex_path = "/usr/bin/codex"
        return adapter

    @pytest.mark.req("EXE-10")
    async def test_429_before_any_item_is_transient(self, monkeypatch) -> None:
        from anygarden.agent.runtime.handler_wrapper import EngineError

        _fake_subprocess(
            monkeypatch,
            _failure_stream(_api_error(429, "rate_limit_exceeded", "Rate limit reached for gpt")),
        )
        with pytest.raises(EngineError) as error:
            await self._adapter()._exec_once("hi", None)
        assert error.value.transient is True
        assert "429" in str(error.value) and "Rate limit reached" in str(error.value)

    @pytest.mark.req("EXE-10")
    async def test_500_after_command_execution_is_not_transient(self, monkeypatch) -> None:
        from anygarden.agent.runtime.handler_wrapper import EngineError

        _fake_subprocess(
            monkeypatch,
            _failure_stream(_api_error(500, "server_error", "upstream exploded"), side_effect=True),
        )
        with pytest.raises(EngineError) as error:
            await self._adapter()._exec_once("hi", None)
        assert error.value.transient is False
        assert "reconciliation required" in str(error.value)
        assert "500" in str(error.value)

    @pytest.mark.req("EXE-10")
    async def test_400_invalid_request_not_transient_with_reason(self, monkeypatch) -> None:
        from anygarden.agent.runtime.handler_wrapper import EngineError

        _fake_subprocess(
            monkeypatch,
            _failure_stream(
                _api_error(400, "invalid_request_error", "The 'gpt-x' model is not supported")
            ),
        )
        with pytest.raises(EngineError) as error:
            await self._adapter()._exec_once("hi", None)
        assert error.value.transient is False
        text = str(error.value)
        assert "status 400" in text
        assert "invalid_request_error" in text
        assert "is not supported" in text

    @pytest.mark.req("EXE-10")
    async def test_reason_and_stderr_tail_logged_and_redacted(self, monkeypatch) -> None:
        from structlog.testing import capture_logs

        from anygarden.agent.runtime.handler_wrapper import EngineError

        monkeypatch.setenv("ANYGARDEN_AGENT_TOKEN", "agent-secret-token-9876")
        stderr = ("noise " * 200 + "ERROR codex_core::client: stream error agent-secret-token-9876").encode()
        _fake_subprocess(
            monkeypatch,
            _failure_stream("stream disconnected before completion: agent-secret-token-9876"),
            stderr=stderr,
        )
        with capture_logs() as logs, pytest.raises(EngineError) as error:
            await self._adapter()._exec_once("hi", None)
        assert error.value.transient is True
        assert "stream disconnected" in str(error.value)
        assert "agent-secret-token-9876" not in str(error.value)
        entry = next(e for e in logs if e["event"] == "codex_cli.nonzero_exit")
        assert entry["exit_code"] == 1
        assert "stream disconnected" in entry["reason"]
        assert "codex_core::client" in entry["stderr_tail"]
        assert len(entry["stderr_tail"]) <= 500
        assert "agent-secret-token-9876" not in str(entry)

    async def test_unparseable_output_is_not_transient(self, monkeypatch) -> None:
        from anygarden.agent.runtime.handler_wrapper import EngineError

        _fake_subprocess(monkeypatch, b"garbage\n", stderr=b"429 Too Many Requests")
        with pytest.raises(EngineError) as error:
            await self._adapter()._exec_once("hi", None)
        assert error.value.transient is False
        assert "reconciliation required" in str(error.value)
