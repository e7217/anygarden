"""Tests for build_engine_secrets (#359).

The matrix this locks down is the engine guard — we have to make
absolutely sure that flipping ``ANYGARDEN_LLM_GATEWAY_ENABLED=true`` does
NOT inject ``OPENAI_BASE_URL`` / ``OPENAI_API_KEY`` into the spawn
frame for claude-code / codex / gemini-cli agents, because those
provider env names are SDK-wide standards and would silently
re-route those engines into a gateway that has no matching upstream
models registered yet.
"""

from __future__ import annotations

import pytest

from anygarden.scheduler.gateway_secrets import build_engine_secrets


class TestEngineGuard:
    """The engine name decides whether secrets are emitted at all."""

    def test_openhands_with_all_inputs_emits_secrets(self) -> None:
        result = build_engine_secrets(
            engine="openhands",
            gateway_enabled=True,
            cluster_external_url="http://localhost:8001",
            agent_token="agt_xyz",
        )
        assert result == {
            "OPENAI_BASE_URL": "http://localhost:8001/api/v1/llm/v1",
            "OPENAI_API_KEY": "agt_xyz",
        }

    @pytest.mark.parametrize(
        "engine", ["claude-code", "codex", "gemini-cli", "unknown-engine"]
    )
    def test_non_openhands_engines_get_empty(self, engine: str) -> None:
        """Other engines must NOT receive gateway env vars.

        Even with gateway fully enabled, claude-code / codex /
        gemini-cli agents should keep their existing OAuth-based
        flow untouched. This is the regression guard for the user-
        reported scenario where flipping gateway broke the three
        already-working CLI agents.
        """
        result = build_engine_secrets(
            engine=engine,
            gateway_enabled=True,
            cluster_external_url="http://localhost:8001",
            agent_token="agt_xyz",
        )
        assert result == {}


class TestActivationGuards:
    """Even openhands gets ``{}`` when the gateway prerequisites
    aren't met. Each guard is independent so a future regression
    that drops one of them shows up as exactly one failed test."""

    def test_gateway_disabled_returns_empty(self) -> None:
        result = build_engine_secrets(
            engine="openhands",
            gateway_enabled=False,
            cluster_external_url="http://localhost:8001",
            agent_token="agt_xyz",
        )
        assert result == {}

    def test_no_cluster_url_returns_empty(self) -> None:
        result = build_engine_secrets(
            engine="openhands",
            gateway_enabled=True,
            cluster_external_url=None,
            agent_token="agt_xyz",
        )
        assert result == {}

    def test_empty_cluster_url_returns_empty(self) -> None:
        # ``cluster_external_url_or_default`` returns ``""`` when
        # nothing is set; treat that the same as ``None`` so the
        # caller doesn't have to special-case both shapes.
        result = build_engine_secrets(
            engine="openhands",
            gateway_enabled=True,
            cluster_external_url="",
            agent_token="agt_xyz",
        )
        assert result == {}

    def test_no_agent_token_returns_empty(self) -> None:
        result = build_engine_secrets(
            engine="openhands",
            gateway_enabled=True,
            cluster_external_url="http://localhost:8001",
            agent_token=None,
        )
        assert result == {}

    def test_empty_agent_token_returns_empty(self) -> None:
        result = build_engine_secrets(
            engine="openhands",
            gateway_enabled=True,
            cluster_external_url="http://localhost:8001",
            agent_token="",
        )
        assert result == {}


class TestUrlNormalisation:
    def test_trailing_slash_is_stripped(self) -> None:
        # Operators sometimes set ANYGARDEN_CLUSTER_EXTERNAL_URL with a
        # trailing slash from copy-paste. The result has to stay
        # ``/api/v1/llm/v1`` (no double slash) so the agent SDK's URL
        # join doesn't produce a 404.
        result = build_engine_secrets(
            engine="openhands",
            gateway_enabled=True,
            cluster_external_url="http://localhost:8001/",
            agent_token="agt_xyz",
        )
        assert result["OPENAI_BASE_URL"] == "http://localhost:8001/api/v1/llm/v1"
