"""Tests for the ``@DOORAE_AGENT_TOKEN`` sentinel substitution (#197).

The server emits this sentinel under ``engine_secrets`` because it
only has the argon2 hash of the agent's token after grant — the
plaintext is machine-side state. The machine spawner substitutes the
sentinel with the live agent token just before piping the JSON
payload to the agent's stdin.
"""

from __future__ import annotations

from doorae_machine.spawner import (
    AGENT_TOKEN_SENTINEL,
    _expand_agent_token_sentinel,
)


class TestExpandAgentTokenSentinel:
    def test_substitutes_sentinel_values_only(self) -> None:
        out = _expand_agent_token_sentinel(
            {
                "ANTHROPIC_BASE_URL": "https://server/api/v1/llm",
                "ANTHROPIC_AUTH_TOKEN": AGENT_TOKEN_SENTINEL,
                "OPENAI_API_KEY": AGENT_TOKEN_SENTINEL,
                "OTHER": "literal-value",
            },
            agent_token="sk-doorae-agent-xyz",
        )
        assert out == {
            "ANTHROPIC_BASE_URL": "https://server/api/v1/llm",
            "ANTHROPIC_AUTH_TOKEN": "sk-doorae-agent-xyz",
            "OPENAI_API_KEY": "sk-doorae-agent-xyz",
            "OTHER": "literal-value",
        }

    def test_empty_input_returns_empty(self) -> None:
        assert _expand_agent_token_sentinel({}, "ignored") == {}
        assert _expand_agent_token_sentinel({}, "") == {}

    def test_no_sentinel_passthrough_returns_copy(self) -> None:
        original = {"K": "V"}
        out = _expand_agent_token_sentinel(original, "tok")
        assert out == original
        # New dict — mutating the result must not leak into the input.
        out["K"] = "X"
        assert original["K"] == "V"

    def test_sentinel_value_must_match_exactly(self) -> None:
        # A value that merely CONTAINS the sentinel substring is not
        # a sentinel — only exact equality triggers substitution.
        # Otherwise a legitimate string that happens to embed the
        # magic text could be accidentally replaced with a token.
        out = _expand_agent_token_sentinel(
            {"TOKEN_LIKE": f"prefix-{AGENT_TOKEN_SENTINEL}-suffix"},
            agent_token="sk-xyz",
        )
        assert out == {"TOKEN_LIKE": f"prefix-{AGENT_TOKEN_SENTINEL}-suffix"}
