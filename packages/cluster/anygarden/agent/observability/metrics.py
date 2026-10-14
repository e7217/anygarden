"""Silent-path counters for the Anygarden agent runtime (#482).

The cluster exposes Prometheus metrics via ``prometheus-client`` and a
``/metrics`` ASGI mount (see ``packages/cluster/anygarden/observability/
metrics.py``). The agent process has **neither** — it is a long-running
SDK client with no HTTP server and no Prometheus dependency. So rather
than pull in a registry the agent can't even scrape, #482 surfaces its
silent-drop paths through a tiny in-memory counter that also emits a
structlog event on every increment.

That gives two complementary signals without new dependencies:

* the ``value()`` is unit-testable (assert a path actually fired), and
* the ``metrics.counter_inc`` structlog event is the operational
  breadcrumb — an operator tailing the agent log sees *when* a silent
  path tripped, which is the whole point of the issue (the paths used to
  vanish with only an ``info``/``warning`` that read like normal flow).

These counters are deliberately label-free (cardinality is a non-issue
in-process) and process-local; they are reset only in tests. They are
NOT a substitute for the cluster's Prometheus series — the cluster
already owns the room-visible turn outcomes. These count the
*agent-internal* drop points that never reach a LifecycleFrame outcome
the cluster would notice as anything but ``ok``.
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


class _Counter:
    """A minimal monotonic counter with a structured-log side effect.

    ``inc`` bumps the in-process total and logs ``metrics.counter_inc``
    so the silent path is observable in the agent log even though the
    agent exposes no metrics endpoint. ``reset`` exists for tests only.
    """

    __slots__ = ("name", "description", "_value")

    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description
        self._value = 0

    def inc(self, amount: int = 1) -> None:
        self._value += amount
        logger.info(
            "metrics.counter_inc",
            counter=self.name,
            value=self._value,
        )

    def value(self) -> int:
        return self._value

    def reset(self) -> None:
        """Test-only: zero the counter so cases don't leak into each other."""
        self._value = 0


# #482 — agent→agent non-nominated peer mention that returns an empty
# response with ``request_id is None``. It cannot be reclassified to
# ``failed`` (that would spam the room with false failures for a
# legitimate no-reply), so it stays ``outcome=ok`` and was previously
# invisible. This counts the untracked empties so the no-response rate
# is measurable; the ``engine_call_finished`` frame also carries a
# ``no_response(untracked)`` error sentinel for ActivityLog queries.
agent_empty_untracked_total = _Counter(
    "agent_empty_untracked_total",
    "Untracked (request_id=None) agent turns that produced no response",
)

# #482 — ``decide_policy`` semantic-cycle SKIP. The agent declines to
# speak because the same (sender, content) pair is repeating. Previously
# only a ``decide_policy.cycle_detected`` warning marked it; the counter
# makes the cycle-suppression rate alertable.
decide_policy_cycle_skip_total = _Counter(
    "decide_policy_cycle_skip_total",
    "decide_policy SKIP decisions caused by semantic cycle detection",
)

# #482 — client-level ``max_agent_turns`` drop. An agent-only message
# arrives after the consecutive-turn bound is hit and is silently
# returned (no handler dispatch). Previously only a ``ws.agent_turn_limit``
# info logged it.
agent_turn_limit_skip_total = _Counter(
    "agent_turn_limit_skip_total",
    "Inbound messages dropped because max_agent_turns was exceeded",
)

# #482 — client-level handler exception swallow. A registered message
# handler raised and the error was logged-and-continued so one bad
# handler can't kill the dispatch loop. The counter makes that swallow
# rate visible (previously only a ``handler.message_error`` error log).
client_handler_error_total = _Counter(
    "client_handler_error_total",
    "Message-handler invocations that raised and were swallowed",
)

# Full registry — the single source of truth for the test reset helper
# and any future aggregate exposition. Keep new silent-path counters here.
ALL_SILENT_PATH_COUNTERS: tuple[_Counter, ...] = (
    agent_empty_untracked_total,
    decide_policy_cycle_skip_total,
    agent_turn_limit_skip_total,
    client_handler_error_total,
)
