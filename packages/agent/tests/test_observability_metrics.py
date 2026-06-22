"""Unit tests for the agent-side silent-path metrics shim (#482).

The agent process has no Prometheus registry / HTTP exposition endpoint
(unlike the cluster, which ships ``prometheus-client``). #482 therefore
surfaces the agent's silent-drop paths through a tiny in-memory counter
shim that doubles as a structured-log signal: each ``inc()`` bumps a
module-level integer (unit-testable) *and* emits a structlog event so an
operator tailing the agent log can see *when* a silent path fired.
"""

from __future__ import annotations

from anygarden_agent.observability import metrics


def _reset() -> None:
    for counter in metrics.ALL_SILENT_PATH_COUNTERS:
        counter.reset()


def test_counters_start_at_zero_after_reset() -> None:
    _reset()
    assert metrics.agent_empty_untracked_total.value() == 0
    assert metrics.decide_policy_cycle_skip_total.value() == 0
    assert metrics.agent_turn_limit_skip_total.value() == 0
    assert metrics.client_handler_error_total.value() == 0


def test_inc_increments_value() -> None:
    _reset()
    metrics.agent_empty_untracked_total.inc()
    metrics.agent_empty_untracked_total.inc()
    assert metrics.agent_empty_untracked_total.value() == 2


def test_inc_emits_structlog_event(monkeypatch) -> None:
    """Each increment leaves a structured-log breadcrumb so the silent
    path is visible in the agent log even without a metrics endpoint."""
    _reset()
    events: list[tuple[str, dict]] = []

    def _capture(event, **kw):
        events.append((event, kw))

    monkeypatch.setattr(metrics.logger, "info", _capture)
    metrics.decide_policy_cycle_skip_total.inc()

    assert len(events) == 1
    event_name, kw = events[0]
    assert event_name == "metrics.counter_inc"
    assert kw["counter"] == "decide_policy_cycle_skip_total"
    assert kw["value"] == 1


def test_counters_are_independent() -> None:
    _reset()
    metrics.agent_turn_limit_skip_total.inc()
    assert metrics.agent_turn_limit_skip_total.value() == 1
    assert metrics.client_handler_error_total.value() == 0


def test_all_counters_registered() -> None:
    # The reset helper relies on the full registry; guard against a new
    # counter being added without being wired into it.
    names = {c.name for c in metrics.ALL_SILENT_PATH_COUNTERS}
    assert names == {
        "agent_empty_untracked_total",
        "decide_policy_cycle_skip_total",
        "agent_turn_limit_skip_total",
        "client_handler_error_total",
    }
