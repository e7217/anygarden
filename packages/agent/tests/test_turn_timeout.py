"""Tests for the shared turn-timeout resolution + auto-derivation helper.

Issue #492 — symmetrize per-engine turn timeouts and derive the WS
``ping_timeout`` / supervisor ``engine_timeout`` from the turn timeout so the
invariant ``turn < ping <= supervisor`` always holds.
"""

from __future__ import annotations

import pytest

from anygarden_agent.integrations import _turn_timeout as tt

_TIMEOUT_ENV_KEYS = (
    "ANYGARDEN_AGENT_TURN_TIMEOUT_SEC",
    "ANYGARDEN_AGENT_CODEX_TURN_TIMEOUT_SEC",
    "ANYGARDEN_AGENT_GEMINI_TURN_TIMEOUT_SEC",
    "ANYGARDEN_AGENT_CLAUDE_TURN_TIMEOUT_SEC",
    "ANYGARDEN_AGENT_OPENHANDS_TURN_TIMEOUT_SEC",
    "ANYGARDEN_AGENT_ENGINE_TIMEOUT_SEC",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts from a known env: no timeout overrides set."""
    for key in _TIMEOUT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


# --- resolve_turn_timeout: per-engine global env > hardcoded default --------


def test_hardcoded_defaults_per_engine():
    assert tt.resolve_turn_timeout("codex") == 600.0
    assert tt.resolve_turn_timeout("claude") == 600.0
    assert tt.resolve_turn_timeout("openhands") == 600.0
    # gemini intentionally keeps its faster 120s profile when unset.
    assert tt.resolve_turn_timeout("gemini") == 120.0


def test_per_engine_env_overrides_default(monkeypatch):
    monkeypatch.setenv("ANYGARDEN_AGENT_GEMINI_TURN_TIMEOUT_SEC", "300")
    assert tt.resolve_turn_timeout("gemini") == 300.0
    # other engines unaffected
    assert tt.resolve_turn_timeout("codex") == 600.0


def test_existing_claude_openhands_keys_preserved(monkeypatch):
    """The helper must read the same env keys claude/openhands already use."""
    monkeypatch.setenv("ANYGARDEN_AGENT_CLAUDE_TURN_TIMEOUT_SEC", "720")
    monkeypatch.setenv("ANYGARDEN_AGENT_OPENHANDS_TURN_TIMEOUT_SEC", "480")
    assert tt.resolve_turn_timeout("claude") == 720.0
    assert tt.resolve_turn_timeout("openhands") == 480.0


def test_new_codex_key(monkeypatch):
    monkeypatch.setenv("ANYGARDEN_AGENT_CODEX_TURN_TIMEOUT_SEC", "900")
    assert tt.resolve_turn_timeout("codex") == 900.0


def test_per_agent_overrides_everything(monkeypatch):
    # #493 — the engine-agnostic per-agent env wins over the per-engine env.
    monkeypatch.setenv("ANYGARDEN_AGENT_TURN_TIMEOUT_SEC", "450")
    monkeypatch.setenv("ANYGARDEN_AGENT_CODEX_TURN_TIMEOUT_SEC", "900")
    assert tt.resolve_turn_timeout("codex") == 450.0
    # and applies regardless of engine, including gemini's 120s default
    assert tt.resolve_turn_timeout("gemini") == 450.0


# --- resolve_supervisor_timeout: floor 900 + SUP_SLACK 300 ------------------


def test_supervisor_floor_for_small_turn():
    # turn + 300 <= 900 -> pinned to the 900 floor
    assert tt.resolve_supervisor_timeout(120) == 900.0
    assert tt.resolve_supervisor_timeout(600) == 900.0


def test_supervisor_slack_for_large_turn():
    assert tt.resolve_supervisor_timeout(700) == 1000.0
    assert tt.resolve_supervisor_timeout(870) == 1170.0


def test_supervisor_respects_env_floor(monkeypatch):
    monkeypatch.setenv("ANYGARDEN_AGENT_ENGINE_TIMEOUT_SEC", "1200")
    # env floor wins when higher than turn + slack
    assert tt.resolve_supervisor_timeout(600) == 1200.0
    # turn + slack wins when higher than env floor
    assert tt.resolve_supervisor_timeout(1000) == 1300.0


# --- resolve_ping_timeout: floor 600 + PING_SLACK 60 -----------------------


def test_ping_floor_for_small_turn():
    assert tt.resolve_ping_timeout(120) == 600.0


def test_ping_slack_for_large_turn():
    assert tt.resolve_ping_timeout(600) == 660.0
    assert tt.resolve_ping_timeout(700) == 760.0


# --- invariant: turn < ping <= supervisor ----------------------------------


@pytest.mark.parametrize("turn", [60, 120, 300, 600, 700, 870])
def test_invariant_turn_lt_ping_le_supervisor(turn):
    ping = tt.resolve_ping_timeout(turn)
    sup = tt.resolve_supervisor_timeout(turn)
    assert turn < ping <= sup


# --- ChatClient ping_timeout wiring (#492) ---------------------------------


def test_chat_client_ping_timeout_default():
    from anygarden_agent.client import ChatClient

    client = ChatClient("ws://example", token="t")
    # plain text client (no engine) keeps the 600s floor
    assert client._ping_timeout == 600.0


def test_chat_client_ping_timeout_override():
    from anygarden_agent.client import ChatClient

    client = ChatClient("ws://example", token="t", ping_timeout=760.0)
    assert client._ping_timeout == 760.0
