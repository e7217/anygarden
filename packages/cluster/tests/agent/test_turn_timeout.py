"""Tests for the shared turn-timeout resolution helper.

Issue #492 — symmetrize per-engine turn timeouts. #892 — the turn timeout is
an idle limit enforced by the engine runtime; the WS ``ping_timeout`` is a
fixed keepalive and the supervisor has no cap, so neither derives from it.
"""

from __future__ import annotations

import pytest

from anygarden.agent.integrations import _turn_timeout as tt

_TIMEOUT_ENV_KEYS = (
    "ANYGARDEN_AGENT_TURN_TIMEOUT_SEC",
    "ANYGARDEN_AGENT_CODEX_TURN_TIMEOUT_SEC",
    "ANYGARDEN_AGENT_PI_TURN_TIMEOUT_SEC",
    "ANYGARDEN_AGENT_PI_TURN_TIMEOUT_SEC",
    "ANYGARDEN_AGENT_CODEX_TURN_TIMEOUT_SEC",
    "ANYGARDEN_AGENT_ENGINE_TIMEOUT_SEC",
    "ANYGARDEN_AGENT_WS_PING_TIMEOUT_SEC",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts from a known env: no timeout overrides set."""
    for key in _TIMEOUT_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


# --- resolve_turn_timeout: per-engine global env > hardcoded default --------


@pytest.mark.req("ENG-04")
def test_hardcoded_defaults_per_engine():
    assert tt.resolve_turn_timeout("codex") == 600.0
    assert tt.resolve_turn_timeout("pi") == 600.0


@pytest.mark.req("ENG-04")
def test_per_engine_env_overrides_default(monkeypatch):
    monkeypatch.setenv("ANYGARDEN_AGENT_PI_TURN_TIMEOUT_SEC", "300")
    assert tt.resolve_turn_timeout("pi") == 300.0
    # other engines unaffected
    assert tt.resolve_turn_timeout("codex") == 600.0


def test_pi_timeout_key(monkeypatch):
    """The helper must read the same env keys Codex already use."""
    monkeypatch.setenv("ANYGARDEN_AGENT_PI_TURN_TIMEOUT_SEC", "720")
    monkeypatch.setenv("ANYGARDEN_AGENT_CODEX_TURN_TIMEOUT_SEC", "480")
    assert tt.resolve_turn_timeout("pi") == 720.0
    assert tt.resolve_turn_timeout("codex") == 480.0


def test_new_codex_key(monkeypatch):
    monkeypatch.setenv("ANYGARDEN_AGENT_CODEX_TURN_TIMEOUT_SEC", "900")
    assert tt.resolve_turn_timeout("codex") == 900.0


@pytest.mark.req("ENG-04")
def test_per_agent_overrides_everything(monkeypatch):
    # #493 — the engine-agnostic per-agent env wins over the per-engine env.
    monkeypatch.setenv("ANYGARDEN_AGENT_TURN_TIMEOUT_SEC", "450")
    monkeypatch.setenv("ANYGARDEN_AGENT_CODEX_TURN_TIMEOUT_SEC", "900")
    assert tt.resolve_turn_timeout("codex") == 450.0
    # and applies regardless of engine, including Pi
    assert tt.resolve_turn_timeout("pi") == 450.0


# --- resolve_ping_timeout: fixed keepalive, env override (#892) -----------


@pytest.mark.req("ENG-04")
def test_ping_timeout_is_fixed_regardless_of_turn_timeout(monkeypatch):
    assert tt.resolve_ping_timeout() == 660.0
    # A long idle limit no longer stretches the keepalive.
    monkeypatch.setenv("ANYGARDEN_AGENT_TURN_TIMEOUT_SEC", "7200")
    assert tt.resolve_ping_timeout() == 660.0


def test_ping_timeout_env_override(monkeypatch):
    monkeypatch.setenv("ANYGARDEN_AGENT_WS_PING_TIMEOUT_SEC", "900")
    assert tt.resolve_ping_timeout() == 900.0


def test_supervisor_timeout_helper_is_gone():
    # #892 — the supervisor no longer caps a turn.
    assert not hasattr(tt, "resolve_supervisor_timeout")


# --- ChatClient ping_timeout wiring ----------------------------------------


def test_chat_client_ping_timeout_default():
    from anygarden.agent.client import ChatClient

    client = ChatClient("ws://example", token="t")
    assert client._ping_timeout == 660.0


def test_chat_client_ping_timeout_env(monkeypatch):
    from anygarden.agent.client import ChatClient

    monkeypatch.setenv("ANYGARDEN_AGENT_WS_PING_TIMEOUT_SEC", "120")
    assert ChatClient("ws://example", token="t")._ping_timeout == 120.0


def test_chat_client_ping_timeout_override():
    from anygarden.agent.client import ChatClient

    client = ChatClient("ws://example", token="t", ping_timeout=760.0)
    assert client._ping_timeout == 760.0
