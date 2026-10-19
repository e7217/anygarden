"""Shared turn-timeout resolution.

Issue #492 made one module the source of truth for the per-engine turn
timeout, resolved as ``ANYGARDEN_AGENT_<ENGINE>_TURN_TIMEOUT_SEC`` env >>
hardcoded default.

#892 — the value is an **idle** limit: the engine runtime ends a turn that
shows no activity (no output, no progress, no open tool) for this long. A turn
that keeps working is never cut by total length, so nothing outside the
engine runtime derives a cap from it any more: the supervisor ``wait_for`` is
gone and the WS ``ping_timeout`` is a fixed keepalive (``resolve_ping_timeout``).

A per-agent leg (``ANYGARDEN_AGENT_TURN_TIMEOUT_SEC``, engine-agnostic) takes
precedence over the per-engine env (#493). The machine spawner injects it into
the agent process env at spawn from the agent's DB column; it is absent for
agents with no per-agent override.

Env is read at call time. Adapters call ``resolve_turn_timeout`` at import to
fix their module-level constant — safe because each agent runs in its own
process whose env is fixed at spawn.
"""

from __future__ import annotations

import os

# Hardcoded per-engine defaults (seconds). gemini keeps a faster turn profile;
# the others mirror codex's original 600s cap (#190). The "codex" key is
# retained for codex-cli (#506 removed the SDK codex engine; codex-cli maps to
# this key via cli._ENGINE_TIMEOUT_KEY).
_ENGINE_DEFAULTS: dict[str, float] = {
    "pi": 600.0,
    "codex": 600.0,
}

# #892 — fixed WS keepalive timeout, the value the default (600s) turn
# already derived. Overridable for unusual networks.
_PING_TIMEOUT = 660.0
_PING_ENV = "ANYGARDEN_AGENT_WS_PING_TIMEOUT_SEC"


def resolve_turn_timeout(engine: str) -> float:
    """Resolve the turn timeout (seconds) for ``engine``.

    Precedence: per-agent override > per-engine global env > hardcoded default.
    The per-agent value (``ANYGARDEN_AGENT_TURN_TIMEOUT_SEC``, engine-agnostic)
    is injected by the machine spawner at spawn time from the agent's DB
    column (#493).
    """
    # #493 — per-agent override injected into the process env at spawn. Engine-
    # agnostic, so it wins over the per-engine global env for this one agent.
    per_agent = os.environ.get("ANYGARDEN_AGENT_TURN_TIMEOUT_SEC")
    if per_agent:
        return float(per_agent)
    per_engine = os.environ.get(f"ANYGARDEN_AGENT_{engine.upper()}_TURN_TIMEOUT_SEC")
    if per_engine:
        return float(per_engine)
    try:
        return _ENGINE_DEFAULTS[engine]
    except KeyError:  # pragma: no cover - defensive; unknown engine
        raise ValueError(f"unknown engine for turn timeout: {engine!r}") from None


def resolve_ping_timeout() -> float:
    """WS ``ping_timeout`` for the agent's room sockets.

    It used to be derived from the turn timeout (#190/#492) because the old
    SDK codex engine blocked the event loop for the whole turn, so pongs went
    unanswered and the socket dropped mid-turn. #506 removed that engine; the
    runtime now runs the CLI as an async subprocess and answers pings during
    a turn, so the keepalive no longer depends on how long a turn runs.
    """
    value = os.environ.get(_PING_ENV)
    if value:
        return float(value)
    return _PING_TIMEOUT
