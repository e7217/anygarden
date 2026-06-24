"""Shared turn-timeout resolution and auto-derivation.

Issue #492 — every engine adapter (codex, claude-code, gemini, openhands) caps a
single turn with ``asyncio.wait_for``. Historically codex/gemini hardcoded the
cap while claude/openhands read a dedicated env (#483), leaving the override
surface asymmetric. This module is the single source of truth for:

1. the per-engine turn timeout, resolved as
   ``ANYGARDEN_AGENT_<ENGINE>_TURN_TIMEOUT_SEC`` env  >>  hardcoded default;
2. the derived WS ``ping_timeout`` and supervisor ``engine_timeout``, computed
   from the turn timeout so the invariant ``turn < ping <= supervisor`` holds by
   construction (raising the turn cap can no longer silently drop a response on
   the ping deadline or get pre-empted by the supervisor).

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
    "codex": 600.0,
    "claude": 600.0,
    "openhands": 600.0,
    "gemini": 120.0,
}

# Slack added on top of the turn timeout when deriving the outer layers.
PING_SLACK = 60.0
SUP_SLACK = 300.0

# Floors preserve today's behaviour for the common (small-turn) case:
# ping_timeout never drops below 600s, supervisor never below 900s.
_PING_FLOOR = 600.0
_SUP_FLOOR = 900.0


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


def resolve_supervisor_timeout(turn_timeout: float) -> float:
    """Supervisor ``engine_timeout`` ≥ turn + SUP_SLACK, env floor, and 900s.

    The supervisor is the last-resort coroutine cancel; it must stay strictly
    above the adapter's turn timeout so the engine-side cancellation + room
    notice runs first.
    """
    env_floor = float(os.environ.get("ANYGARDEN_AGENT_ENGINE_TIMEOUT_SEC", "900"))
    return max(turn_timeout + SUP_SLACK, env_floor, _SUP_FLOOR)


def resolve_ping_timeout(turn_timeout: float) -> float:
    """WS ``ping_timeout`` ≥ turn + PING_SLACK and 600s.

    If the ping timeout is below the turn timeout the socket closes mid-turn and
    the response is silently dropped (#190); deriving it from the turn timeout
    keeps it safely above.
    """
    return max(turn_timeout + PING_SLACK, _PING_FLOOR)
