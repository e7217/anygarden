"""Tests that :meth:`AgentLifecycle._build_gateway_engine_secrets`
populates ``engine_secrets`` correctly.

Gateway routing is an **explicit opt-in** via a virtual engine
(``codex-extra``) — not an automatic side-effect of
``DOORAE_LLM_GATEWAY_ENABLED``. Plain ``codex`` / ``claude-code``
agents use host-level credentials even when the gateway flag is on,
so admins can pick the mode per-agent in the Add Agent dialog.

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
    def test_flag_off_returns_empty_for_any_engine(self) -> None:
        lc = _make_lifecycle(enabled=False)
        assert lc._build_gateway_engine_secrets("claude-code") == {}
        assert lc._build_gateway_engine_secrets("codex") == {}
        assert lc._build_gateway_engine_secrets("codex-extra") == {}

    def test_base_codex_does_not_auto_enroll(self) -> None:
        """Plain ``codex`` is host-auth even when gateway is enabled.

        The old contract auto-injected gateway env for every codex /
        claude-code agent when the flag was on. That was reverted in
        favour of the explicit ``-extra`` virtual engine so admins
        don't get surprising routing behaviour from a server flag.
        """
        lc = _make_lifecycle(enabled=True)
        assert lc._build_gateway_engine_secrets("codex") == {}

    def test_base_claude_code_does_not_auto_enroll(self) -> None:
        lc = _make_lifecycle(enabled=True)
        assert lc._build_gateway_engine_secrets("claude-code") == {}

    def test_codex_extra_populates_openai_vars(self) -> None:
        lc = _make_lifecycle(enabled=True)
        out = lc._build_gateway_engine_secrets("codex-extra")
        assert out == {
            "OPENAI_BASE_URL": "http://doorae.example:8000/api/v1/llm/v1",
            "OPENAI_API_KEY": AgentLifecycle.AGENT_TOKEN_SENTINEL,
        }

    def test_codex_extra_wss_server_url_becomes_https(self) -> None:
        lc = _make_lifecycle(
            enabled=True, server_url="wss://doorae.example:443",
        )
        out = lc._build_gateway_engine_secrets("codex-extra")
        assert out["OPENAI_BASE_URL"] == (
            "https://doorae.example:443/api/v1/llm/v1"
        )

    def test_unknown_engine_returns_empty(self) -> None:
        """Engines outside the opt-in list stay on host credentials."""
        lc = _make_lifecycle(enabled=True)
        assert lc._build_gateway_engine_secrets("openhands") == {}
        assert lc._build_gateway_engine_secrets("deepagents") == {}
        assert lc._build_gateway_engine_secrets("gemini-cli") == {}
        assert lc._build_gateway_engine_secrets("openai") == {}
        assert lc._build_gateway_engine_secrets("anthropic") == {}

    def test_codex_extra_requires_reachable_server_url(self) -> None:
        lc = _make_lifecycle(enabled=True, server_url="")
        # Without a reachable base, injecting a broken URL would be
        # worse than leaving the agent on the host-level fallback.
        assert lc._build_gateway_engine_secrets("codex-extra") == {}
