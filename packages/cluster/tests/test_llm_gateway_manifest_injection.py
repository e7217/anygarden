"""Tests that :meth:`AgentLifecycle._build_gateway_engine_secrets`
populates ``engine_secrets`` correctly when the gateway flag is on
(#197 Phase 5).

The manifest builder uses a ``@DOORAE_AGENT_TOKEN`` sentinel for the
auth-token value because the server only holds the argon2 hash of
the agent's token after grant — the machine daemon substitutes the
sentinel with the plaintext token before piping to the agent.
"""

from __future__ import annotations

from doorae.scheduler.lifecycle import AgentLifecycle
from doorae.scheduler.machine_bus import MachineBus


def _make_lifecycle(
    *,
    enabled: bool,
    server_url: str = "ws://doorae.example:8000",
) -> AgentLifecycle:
    # The tests only exercise the sync _build_gateway_engine_secrets
    # helper, which doesn't touch DB / machine bus — pass placeholders.
    return AgentLifecycle(
        db_factory=lambda: None,  # type: ignore[arg-type]
        machine_bus=MachineBus(),
        server_url=server_url,
        llm_gateway_enabled=enabled,
    )


class TestGatewayEngineSecrets:
    def test_flag_off_returns_empty(self) -> None:
        lc = _make_lifecycle(enabled=False)
        assert lc._build_gateway_engine_secrets("claude-code") == {}
        assert lc._build_gateway_engine_secrets("codex") == {}

    def test_claude_code_populates_anthropic_vars(self) -> None:
        lc = _make_lifecycle(enabled=True)
        out = lc._build_gateway_engine_secrets("claude-code")
        assert out == {
            "ANTHROPIC_BASE_URL": "http://doorae.example:8000/api/v1/llm",
            "ANTHROPIC_AUTH_TOKEN": AgentLifecycle.AGENT_TOKEN_SENTINEL,
        }

    def test_codex_populates_openai_vars(self) -> None:
        lc = _make_lifecycle(enabled=True)
        out = lc._build_gateway_engine_secrets("codex")
        assert out == {
            "OPENAI_BASE_URL": "http://doorae.example:8000/api/v1/llm/v1",
            "OPENAI_API_KEY": AgentLifecycle.AGENT_TOKEN_SENTINEL,
        }

    def test_wss_server_url_becomes_https(self) -> None:
        lc = _make_lifecycle(
            enabled=True, server_url="wss://doorae.example:443",
        )
        out = lc._build_gateway_engine_secrets("claude-code")
        assert out["ANTHROPIC_BASE_URL"] == (
            "https://doorae.example:443/api/v1/llm"
        )

    def test_unknown_engine_returns_empty(self) -> None:
        lc = _make_lifecycle(enabled=True)
        # Engines without a known env-var contract fall back to their
        # existing host-level credentials until follow-up wiring lands.
        assert lc._build_gateway_engine_secrets("openhands") == {}
        assert lc._build_gateway_engine_secrets("deepagents") == {}
        assert lc._build_gateway_engine_secrets("gemini-cli") == {}

    def test_empty_server_url_returns_empty_even_when_enabled(self) -> None:
        lc = _make_lifecycle(enabled=True, server_url="")
        # Without a reachable base, injecting a broken URL would be
        # worse than leaving the agent on the host-level fallback.
        assert lc._build_gateway_engine_secrets("claude-code") == {}
