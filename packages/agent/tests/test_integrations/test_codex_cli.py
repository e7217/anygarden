"""Unit tests for the codex-cli (``codex exec``) adapter (#496)."""

from __future__ import annotations

import json

import pytest

from anygarden_agent.integrations.codex_cli import (
    CodexCliAdapter,
    _resolve_codex_cli_args,
)


class TestResolveCodexCliArgs:
    """Tier → ``codex exec`` flag mapping (shares codex's tier table)."""

    def test_restricted(self) -> None:
        assert _resolve_codex_cli_args("restricted") == [
            "-s",
            "read-only",
            "-c",
            "approval_policy=untrusted",
        ]

    def test_standard(self) -> None:
        assert _resolve_codex_cli_args("standard") == [
            "-s",
            "workspace-write",
            "-c",
            "approval_policy=never",
        ]

    def test_trusted(self) -> None:
        assert _resolve_codex_cli_args("trusted") == [
            "-s",
            "danger-full-access",
            "-c",
            "approval_policy=never",
        ]

    def test_none_defaults_to_standard(self) -> None:
        # ``None`` → standard tier (pre-#309 behaviour), same as codex SDK.
        assert _resolve_codex_cli_args(None) == [
            "-s",
            "workspace-write",
            "-c",
            "approval_policy=never",
        ]

    def test_unknown_tier_raises(self) -> None:
        with pytest.raises(ValueError):
            _resolve_codex_cli_args("trustred")  # typo fails loud


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

    async def test_first_turn_captures_thread_id(self, monkeypatch) -> None:
        adapter = CodexCliAdapter()
        adapter._codex_path = "/usr/bin/codex"

        async def fake_exec_once(prompt, thread_id):
            assert thread_id is None  # first turn has no session
            return "reply", "new-tid", {"input_tokens": 1, "output_tokens": 2}, False

        monkeypatch.setattr(adapter, "_exec_once", fake_exec_once)
        resp = await adapter._call_codex("hi", "room1")
        assert resp == "reply"
        assert adapter._room_thread_ids["room1"] == "new-tid"
        assert adapter._take_last_usage() == {
            "model": "gpt-5.5",
            "input_tokens": 1,
            "output_tokens": 2,
            "cost_usd": None,
        }

    async def test_second_turn_resumes_existing_session(self, monkeypatch) -> None:
        adapter = CodexCliAdapter()
        adapter._codex_path = "/usr/bin/codex"
        adapter._room_thread_ids["room1"] = "existing-tid"
        seen: dict[str, str | None] = {}

        async def fake_exec_once(prompt, thread_id):
            seen["tid"] = thread_id
            return "r", "existing-tid", None, False

        monkeypatch.setattr(adapter, "_exec_once", fake_exec_once)
        await adapter._call_codex("hi", "room1")
        assert seen["tid"] == "existing-tid"

    async def test_resume_failure_retries_fresh(self, monkeypatch) -> None:
        adapter = CodexCliAdapter()
        adapter._codex_path = "/usr/bin/codex"
        adapter._room_thread_ids["room1"] = "stale"
        calls: list[str | None] = []

        async def fake_exec_once(prompt, thread_id):
            calls.append(thread_id)
            if thread_id == "stale":
                return None, None, None, True  # resume_failed
            return "fresh-reply", "new-tid", None, False

        monkeypatch.setattr(adapter, "_exec_once", fake_exec_once)
        resp = await adapter._call_codex("hi", "room1")
        assert resp == "fresh-reply"
        assert calls == ["stale", None]  # tried resume, then fresh
        assert adapter._room_thread_ids["room1"] == "new-tid"


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
        from anygarden_agent.runtime.handler_wrapper import EngineError

        adapter = CodexCliAdapter()
        adapter._codex_path = "/usr/bin/codex"

        async def boom(prompt, room_id):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(adapter, "_call_codex", boom)
        with pytest.raises(EngineError):
            await adapter.on_message({"content": "hi", "room_id": "r1", "metadata": {}})
