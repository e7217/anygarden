"""Tests that the claude_code adapter surfaces gateway env vars
into ``os.environ`` only for the duration of its SDK call (#197 Phase 5).

The contract: when ``engine_secrets`` (piped via stdin at startup,
stored in :mod:`anygarden_agent.secrets`) carries ``ANTHROPIC_BASE_URL`` /
``ANTHROPIC_AUTH_TOKEN``, the adapter must temporarily place those into
``os.environ`` so the in-process SDK's credential discovery can read
them — and clean them back out after the call returns so a later tool
invocation can't leak them via ``/proc/self/environ``.
"""

from __future__ import annotations

import os

import pytest

from anygarden_agent import secrets as agent_secrets
from anygarden_agent.integrations.claude_code import ClaudeCodeAdapter


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
            "ANTHROPIC_BASE_URL": "https://anygarden-server/api/v1/llm",
            "ANTHROPIC_AUTH_TOKEN": "sk-anygarden-agent-token-xyz",
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
        assert observed["base_url"] == "https://anygarden-server/api/v1/llm"
        assert observed["auth_token"] == "sk-anygarden-agent-token-xyz"

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
