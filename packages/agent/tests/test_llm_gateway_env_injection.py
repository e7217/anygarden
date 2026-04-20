"""Tests that claude_code and codex adapters surface gateway env vars
into ``os.environ`` only for the duration of their SDK call (#197 Phase 5).

The contract: when ``engine_secrets`` (piped via stdin at startup,
stored in :mod:`doorae_agent.secrets`) carries ``ANTHROPIC_BASE_URL`` /
``OPENAI_API_KEY`` etc., the adapter must temporarily place those into
``os.environ`` so the in-process SDK's credential discovery can read
them — and clean them back out after the call returns so a later tool
invocation can't leak them via ``/proc/self/environ``.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from doorae_agent import secrets as agent_secrets
from doorae_agent.integrations.claude_code import ClaudeCodeAdapter
from doorae_agent.integrations.codex import CodexAdapter


@pytest.fixture(autouse=True)
def reset_secrets():
    """Each test gets a clean secrets slate."""
    agent_secrets.clear()
    yield
    agent_secrets.clear()


# ── claude-code adapter ────────────────────────────────────────────────


class TestClaudeCodeEnvInjection:
    async def test_anthropic_env_surfaced_during_query(self, monkeypatch):
        """During the SDK's ``query()`` iteration, ANTHROPIC_* vars
        from ``engine_secrets`` must be readable from ``os.environ`` —
        that's how claude-agent-sdk discovers the gateway URL.
        After the iteration ends, they must be gone again.
        """
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        agent_secrets.set_secrets({
            "ANTHROPIC_BASE_URL": "https://doorae-server/api/v1/llm",
            "ANTHROPIC_AUTH_TOKEN": "sk-doorae-agent-token-xyz",
        })

        observed: dict[str, str | None] = {}

        async def fake_query(*, prompt, options):  # noqa: ARG001
            # The SDK would build its HTTP client here by reading env.
            observed["base_url"] = os.environ.get("ANTHROPIC_BASE_URL")
            observed["auth_token"] = os.environ.get("ANTHROPIC_AUTH_TOKEN")
            if False:
                yield None  # pragma: no cover — satisfy async-gen shape

        adapter = ClaudeCodeAdapter()
        adapter._query_fn = fake_query  # skip real SDK import
        adapter._sdk = object()  # sentinel so ``on_message`` path enters

        reply = await adapter._collect_reply("hi", options=None)
        assert reply is None  # no messages → no reply

        # The SDK call saw the injected values.
        assert observed["base_url"] == "https://doorae-server/api/v1/llm"
        assert observed["auth_token"] == "sk-doorae-agent-token-xyz"

        # After the call returns the environment is restored — no
        # leak into the long-running agent process.
        assert "ANTHROPIC_BASE_URL" not in os.environ
        assert "ANTHROPIC_AUTH_TOKEN" not in os.environ

    async def test_preserves_prior_env_value(self, monkeypatch):
        """A pre-existing env var (e.g. operator's host-level
        ANTHROPIC_API_KEY) must be restored after the SDK call,
        not scrubbed by the context manager exit."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "host-level-key")

        agent_secrets.set_secrets({"ANTHROPIC_API_KEY": "gateway-key"})

        seen_during: list[str | None] = []

        async def fake_query(*, prompt, options):  # noqa: ARG001
            seen_during.append(os.environ.get("ANTHROPIC_API_KEY"))
            if False:
                yield None  # pragma: no cover

        adapter = ClaudeCodeAdapter()
        adapter._query_fn = fake_query
        adapter._sdk = object()

        await adapter._collect_reply("hi", options=None)

        # During the call the SDK saw the gateway value.
        assert seen_during == ["gateway-key"]
        # After the call the host-level value is back in place.
        assert os.environ["ANTHROPIC_API_KEY"] == "host-level-key"


# ── codex adapter ──────────────────────────────────────────────────────


class TestCodexEnvInjection:
    async def test_openai_env_surfaced_during_client_construction(
        self, monkeypatch
    ):
        """``Codex()`` reads ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``
        at construction and also spawns an app-server subprocess that
        inherits env. Both must see the gateway values for that one
        call; after it returns the env is clean again.
        """
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        agent_secrets.set_secrets({
            "OPENAI_BASE_URL": "https://doorae-server/api/v1/llm/v1",
            "OPENAI_API_KEY": "sk-doorae-agent-token-abc",
        })

        observed: dict[str, str | None] = {}

        class _FakeCodex:
            def __init__(self) -> None:
                observed["base_url"] = os.environ.get("OPENAI_BASE_URL")
                observed["api_key"] = os.environ.get("OPENAI_API_KEY")

            def close(self) -> None:  # pragma: no cover — shutdown path
                pass

        class _FakeThreadOptions:
            def __init__(self, **kwargs: Any) -> None:  # noqa: ARG002
                pass

        class _FakeCodexMod:
            Codex = _FakeCodex

        class _FakeOptionsMod:
            ThreadStartOptions = _FakeThreadOptions

        import sys
        monkeypatch.setitem(sys.modules, "codex", _FakeCodexMod)
        monkeypatch.setitem(sys.modules, "codex.options", _FakeOptionsMod)
        # ``_install_parse_notification_shim`` probes additional codex
        # submodules; silence its import chain by installing stubs.
        def _stub_parse_notification(*args: Any, **kwargs: Any) -> None:  # noqa: ARG001
            return None

        class _StubProto:
            parse_notification = staticmethod(_stub_parse_notification)
            GenericNotification = type("G", (), {})
        class _StubSession:
            parse_notification = None
        class _StubErrors:
            class AppServerProtocolError(Exception):
                pass
        monkeypatch.setitem(
            sys.modules, "codex.app_server._protocol_helpers", _StubProto
        )
        monkeypatch.setitem(
            sys.modules, "codex.app_server._session", _StubSession
        )
        monkeypatch.setitem(
            sys.modules, "codex.app_server.errors", _StubErrors
        )

        adapter = CodexAdapter()
        await adapter.start()

        assert observed["base_url"] == "https://doorae-server/api/v1/llm/v1"
        assert observed["api_key"] == "sk-doorae-agent-token-abc"

        # After start() returns the env is clean.
        assert "OPENAI_BASE_URL" not in os.environ
        assert "OPENAI_API_KEY" not in os.environ
