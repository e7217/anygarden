"""Unit tests for ``RoomHandlerSupervisor``.

The supervisor is the single point where every integration
(codex / claude_code / gemini) funnels handler invocations. It owns:

- per-room serialization (second concurrent dispatch is rejected,
  not queued, so the 5-seconds-33-pings concurrency bug observed
  in production is structurally impossible),
- the four-event lifecycle emission contract that
  docs/plans/2026-04-20-agent-observability-design.md §4 defines,
- the engine-call timeout that turns silent hangs into explicit
  ``engine_call_finished(outcome=timeout)`` events.

Tests here use a fake client that records every lifecycle / send /
typing call so the ordering can be asserted without a WebSocket.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from anygarden_agent.runtime.handler_wrapper import (
    EngineError,
    EngineTimeoutError,
    EngineTurn,
    RoomHandlerSupervisor,
    is_transient_error,
)


def test_engine_error_transient_flag_defaults_false():
    # #457 — existing call sites (EngineError("msg")) keep transient=False;
    # the flag is keyword-only so positional args still mean the message.
    assert EngineError("boom").transient is False
    assert EngineError("503 down", transient=True).transient is True


@pytest.mark.parametrize(
    "detail, expected",
    [
        ("HTTP 429 Too Many Requests", True),
        ("upstream returned 503", True),
        ("502 Bad Gateway", True),
        ("Connection reset by peer", True),
        ("httpx.ConnectError: connect error", True),
        ("Read timeout while waiting", True),
        ("model is overloaded, try again", True),
        ("400 unsupported model gpt-5.5", False),
        ("authentication failed: invalid api key", False),
        ("", False),
        # a port-like 5000 token must NOT be read as a 500 status code
        ("listening on port 5000", False),
    ],
)
def test_is_transient_error_classification(detail, expected):
    assert is_transient_error(detail) is expected


@dataclass
class _FakeClient:
    lifecycle_events: list[dict] = field(default_factory=list)
    sends: list[tuple[str, str, dict | None]] = field(default_factory=list)

    async def sendLifecycle(self, room_id, request_id, event, **details):
        self.lifecycle_events.append({
            "room_id": room_id,
            "request_id": request_id,
            "event": event,
            **details,
        })

    async def send(self, room_id, content, metadata=None):
        self.sends.append((room_id, content, metadata))


@pytest.mark.asyncio
async def test_ok_path_emits_four_lifecycle_events():
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def run_engine():
        await asyncio.sleep(0.01)
        return "hello"

    await sup.dispatch(room_id="r1", request_id="req-1", run_engine=run_engine)

    events = [e["event"] for e in client.lifecycle_events]
    assert events == [
        "handler_started",
        "engine_call_started",
        "engine_call_finished",
        "handler_finished",
    ]
    engine_fin = client.lifecycle_events[2]
    assert engine_fin["engine"] == "codex"
    assert engine_fin["outcome"] == "ok"
    assert engine_fin["duration_ms"] >= 0
    handler_fin = client.lifecycle_events[3]
    assert handler_fin["outcome"] == "ok"
    assert client.sends == [("r1", "hello", {"request_id": "req-1"})]


@pytest.mark.asyncio
async def test_engine_turn_carries_prompt_and_completion():
    # #433 — when run_engine returns an EngineTurn, the supervisor stamps
    # the augmented turn input (prompt) and engine reply (completion) onto
    # engine_call_finished so the cluster can put them on the span. The
    # reply is still delivered to the room exactly as before.
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def run_engine():
        return EngineTurn(response="the reply", prompt="augmented input")

    await sup.dispatch(room_id="r1", request_id="req-io", run_engine=run_engine)

    engine_fin = next(
        e for e in client.lifecycle_events if e["event"] == "engine_call_finished"
    )
    assert engine_fin["prompt"] == "augmented input"
    assert engine_fin["completion"] == "the reply"
    assert engine_fin["outcome"] == "ok"
    assert client.sends == [("r1", "the reply", {"request_id": "req-io"})]


@pytest.mark.asyncio
async def test_engine_turn_empty_response_keeps_prompt_drops_completion():
    # #433 × #422 — an EngineTurn with an empty response on a *tracked*
    # turn reclassifies to failed; the captured prompt is still surfaced
    # but completion is None (no output), and the user gets the notice.
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def run_engine():
        return EngineTurn(response="", prompt="augmented input")

    await sup.dispatch(room_id="r1", request_id="req-empty-io", run_engine=run_engine)

    engine_fin = next(
        e for e in client.lifecycle_events if e["event"] == "engine_call_finished"
    )
    assert engine_fin["outcome"] == "failed"
    assert engine_fin["prompt"] == "augmented input"
    assert engine_fin.get("completion") is None
    assert len(client.sends) == 1
    assert "생성하지 못했습니다" in client.sends[0][1]


@pytest.mark.asyncio
async def test_bare_str_return_stays_backward_compatible():
    # A legacy run_engine returning a plain str still works; no turn-I/O
    # fields are emitted (prompt/completion stay None → wire-excluded).
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def run_engine():
        return "plain reply"

    await sup.dispatch(room_id="r1", request_id="req-s", run_engine=run_engine)

    engine_fin = next(
        e for e in client.lifecycle_events if e["event"] == "engine_call_finished"
    )
    assert engine_fin.get("prompt") is None
    assert engine_fin.get("completion") is None
    assert client.sends == [("r1", "plain reply", {"request_id": "req-s"})]


@pytest.mark.asyncio
async def test_timeout_path_marks_both_events_and_notifies_user():
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=0.05)

    async def slow_engine():
        await asyncio.sleep(1.0)
        return "never reached"

    await sup.dispatch(room_id="r1", request_id="req-t", run_engine=slow_engine)

    events = [(e["event"], e.get("outcome")) for e in client.lifecycle_events]
    assert events == [
        ("handler_started", None),
        ("engine_call_started", None),
        ("engine_call_finished", "timeout"),
        ("handler_finished", "timeout"),
    ]
    assert client.sends and "타임아웃" in client.sends[0][1]
    assert client.sends[0][2] == {"request_id": "req-t"}


@pytest.mark.asyncio
async def test_failed_path_marks_failed_and_notifies_user():
    # #422 — a crashing turn must surface as ``failed`` AND notify the
    # user, instead of being swallowed into a silent ``ok``.
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def crashing_engine():
        raise RuntimeError("boom")

    await sup.dispatch(room_id="r1", request_id="req-f", run_engine=crashing_engine)

    engine_fin = next(
        e for e in client.lifecycle_events if e["event"] == "engine_call_finished"
    )
    assert engine_fin["outcome"] == "failed"
    assert engine_fin["error"] == "boom"
    assert len(client.sends) == 1
    assert "생성하지 못했습니다" in client.sends[0][1]
    assert client.sends[0][2] == {"request_id": "req-f"}


@pytest.mark.asyncio
async def test_engine_error_marks_failed_and_notifies_user():
    # #422 — adapters raise EngineError instead of returning None; the
    # supervisor maps it to failed + notice (same as a bare exception).
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def failing_engine():
        raise EngineError("model 400: gpt-5.5 unsupported")

    await sup.dispatch(room_id="r1", request_id="req-e", run_engine=failing_engine)

    handler_fin = next(
        e for e in client.lifecycle_events if e["event"] == "handler_finished"
    )
    assert handler_fin["outcome"] == "failed"
    assert "gpt-5.5" in handler_fin["error"]
    assert len(client.sends) == 1
    assert "생성하지 못했습니다" in client.sends[0][1]


@pytest.mark.asyncio
async def test_engine_timeout_error_marks_timeout():
    # #422 — an adapter-level turn timeout surfaces as ``timeout`` (not
    # ``failed``) and notifies the user.
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def timing_out_engine():
        raise EngineTimeoutError("codex turn exceeded 600s")

    await sup.dispatch(room_id="r1", request_id="req-to", run_engine=timing_out_engine)

    outcomes = {
        e["event"]: e.get("outcome")
        for e in client.lifecycle_events
        if e["event"] in ("engine_call_finished", "handler_finished")
    }
    assert outcomes["engine_call_finished"] == "timeout"
    assert outcomes["handler_finished"] == "timeout"
    assert client.sends and "타임아웃" in client.sends[0][1]


@pytest.mark.asyncio
async def test_over_cap_dispatch_is_rejected():
    # #457 — a follow-up that arrives while a turn is in flight is now
    # QUEUED (not rejected) up to the cap. The legacy rejected path only
    # triggers once the queue is full: with the default cap of 3, the
    # first three follow-ups queue and the fourth is rejected.
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    gate = asyncio.Event()

    async def gated_engine():
        await gate.wait()
        return "first done"

    first = asyncio.create_task(sup.dispatch("r1", "req-1", gated_engine))
    await asyncio.sleep(0.01)  # let `first` acquire the lock

    # Fill the queue to cap (3) then overflow with a 4th.
    for n in range(2, 5):  # req-2, req-3, req-4 → queued
        await sup.dispatch("r1", f"req-{n}", lambda: asyncio.sleep(0, result="x"))
    await sup.dispatch("r1", "req-5", lambda: asyncio.sleep(0, result="over"))

    # req-2..4 queued, req-5 rejected.
    for n in range(2, 5):
        ev = [e for e in client.lifecycle_events if e["request_id"] == f"req-{n}"]
        assert len(ev) == 1
        assert ev[0]["event"] == "handler_finished"
        assert ev[0]["outcome"] == "queued"

    rejected = [e for e in client.lifecycle_events if e["request_id"] == "req-5"]
    assert len(rejected) == 1
    assert rejected[0]["event"] == "handler_finished"
    assert rejected[0]["outcome"] == "rejected"
    assert "req-1" in rejected[0]["error"]

    gate.set()
    await first


@pytest.mark.asyncio
async def test_over_cap_dispatch_notifies_user():
    # The over-cap rejected dispatch must stay symmetric with the
    # timeout/failed paths: emit handler_finished(outcome=rejected) AND
    # notify the user so the dropped message isn't silent. Queued
    # follow-ups, by contrast, get NO notice (they will be answered).
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    gate = asyncio.Event()

    async def gated_engine():
        await gate.wait()
        return "first done"

    first = asyncio.create_task(sup.dispatch("r1", "req-1", gated_engine))
    await asyncio.sleep(0.01)  # let `first` acquire the lock

    # Three queued follow-ups (no notice), then one over-cap rejection.
    for n in range(2, 5):
        await sup.dispatch("r1", f"req-{n}", lambda: asyncio.sleep(0, result="x"))
    await sup.dispatch("r1", "req-over", lambda: asyncio.sleep(0, result="over"))

    rejected_events = [
        e
        for e in client.lifecycle_events
        if e["request_id"] == "req-over" and e["event"] == "handler_finished"
    ]
    assert len(rejected_events) == 1
    assert rejected_events[0]["outcome"] == "rejected"

    # The rejected request_id stamps its metadata on the notice send.
    rejected_sends = [s for s in client.sends if s[2] == {"request_id": "req-over"}]
    assert len(rejected_sends) == 1
    assert "받지 못했습니다" in rejected_sends[0][1]
    # Queued follow-ups did NOT produce a notice while the lock was held.
    queued_sends = [
        s for s in client.sends if s[2] in ({"request_id": f"req-{n}"} for n in range(2, 5))
    ]
    assert queued_sends == []

    gate.set()
    await first


@pytest.mark.asyncio
async def test_long_error_is_truncated_for_log_safety():
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def crashing_engine():
        raise RuntimeError("x" * 5000)

    await sup.dispatch(room_id="r1", request_id="req-long", run_engine=crashing_engine)

    engine_fin = next(
        e for e in client.lifecycle_events if e["event"] == "engine_call_finished"
    )
    assert len(engine_fin["error"]) <= 500


@pytest.mark.asyncio
async def test_no_request_id_skips_user_metadata():
    """Proactive sends (no triggering user message) pass through
    without a request_id; the reply must not stamp an empty one."""
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def run_engine():
        return "proactive"

    await sup.dispatch(room_id="r1", request_id=None, run_engine=run_engine)

    assert client.sends == [("r1", "proactive", None)]


@pytest.mark.asyncio
async def test_tracked_empty_response_is_failed_and_notifies():
    """#422 — a *tracked* turn (request_id present = user-triggered) that
    returns '' is a silent failure, not a no-reply. It surfaces as
    ``failed`` and notifies the user. (This is the gpt-5.5 symptom: the
    engine produced nothing and the user saw only silence.)"""
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def run_engine():
        return ""

    await sup.dispatch(room_id="r1", request_id="req-empty", run_engine=run_engine)

    outcomes = [
        e["outcome"] for e in client.lifecycle_events if e["event"] == "handler_finished"
    ]
    assert outcomes == ["failed"]
    assert len(client.sends) == 1
    assert "생성하지 못했습니다" in client.sends[0][1]


@pytest.mark.asyncio
async def test_proactive_empty_response_stays_silent_ok():
    """A proactive/untracked turn (request_id is None) that returns ''
    keeps the legitimate no-reply semantics: no send, outcome ok. Only
    these reach the supervisor empty in practice — ambient ingestion
    flows through ingest_context, never here."""
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def run_engine():
        return ""

    await sup.dispatch(room_id="r1", request_id=None, run_engine=run_engine)

    assert client.sends == []
    outcomes = [
        e["outcome"] for e in client.lifecycle_events if e["event"] == "handler_finished"
    ]
    assert outcomes == ["ok"]


@pytest.mark.asyncio
async def test_untracked_empty_marks_engine_frame_and_counts():
    """#482 — the untracked empty turn stays silent-ok (no send, no
    failed outcome — preserving the no-reply contract), but it is no
    longer *invisible*: the ``engine_call_finished`` frame carries a
    ``no_response(untracked)`` error sentinel for ActivityLog queries and
    the in-process counter increments so the no-response rate is
    measurable. The room behaviour is unchanged from the test above."""
    from anygarden_agent.observability import metrics

    metrics.agent_empty_untracked_total.reset()

    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    async def run_engine():
        return ""

    await sup.dispatch(room_id="r1", request_id=None, run_engine=run_engine)

    # Contract preserved: no room send, terminal outcome ok.
    assert client.sends == []
    handler_fin = next(
        e for e in client.lifecycle_events if e["event"] == "handler_finished"
    )
    assert handler_fin["outcome"] == "ok"

    # New: the engine frame is marked and the counter saw it.
    engine_fin = next(
        e for e in client.lifecycle_events if e["event"] == "engine_call_finished"
    )
    assert engine_fin["outcome"] == "ok"
    assert engine_fin["error"] == "no_response(untracked)"
    assert metrics.agent_empty_untracked_total.value() == 1


@pytest.mark.asyncio
async def test_proactive_nonempty_does_not_mark_or_count():
    """Guard: a *non-empty* untracked turn is an ordinary proactive
    reply — it must not carry the sentinel nor bump the counter."""
    from anygarden_agent.observability import metrics

    metrics.agent_empty_untracked_total.reset()

    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    await sup.dispatch(
        room_id="r1", request_id=None, run_engine=lambda: asyncio.sleep(0, result="hi")
    )

    engine_fin = next(
        e for e in client.lifecycle_events if e["event"] == "engine_call_finished"
    )
    assert engine_fin.get("error") is None
    assert metrics.agent_empty_untracked_total.value() == 0


# ── #457 Wave 2b — bounded per-room queue ────────────────────────────


@pytest.mark.asyncio
async def test_queued_followups_run_in_order_after_first_completes():
    # While a turn runs, follow-ups are deferred (outcome=queued, no
    # notice) and then drained FIFO by the lock holder after the first
    # turn finishes — each producing its own real reply, in order.
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    gate = asyncio.Event()

    async def first_engine():
        await gate.wait()
        return "first"

    first = asyncio.create_task(sup.dispatch("r1", "req-1", first_engine))
    await asyncio.sleep(0.01)  # let `first` acquire the lock

    # Two follow-ups arrive while the lock is held → queued, no notice yet.
    await sup.dispatch(
        "r1", "req-2", lambda: asyncio.sleep(0, result="second")
    )
    await sup.dispatch(
        "r1", "req-3", lambda: asyncio.sleep(0, result="third")
    )

    queued = [
        e
        for e in client.lifecycle_events
        if e["event"] == "handler_finished" and e.get("outcome") == "queued"
    ]
    assert [e["request_id"] for e in queued] == ["req-2", "req-3"]
    # No user-facing send happened yet (gate still closed).
    assert client.sends == []

    gate.set()
    await first

    # The first reply, then the two queued follow-ups, in arrival order.
    assert client.sends == [
        ("r1", "first", {"request_id": "req-1"}),
        ("r1", "second", {"request_id": "req-2"}),
        ("r1", "third", {"request_id": "req-3"}),
    ]
    # Each follow-up produced a full ok lifecycle on drain.
    ok_finished = [
        e["request_id"]
        for e in client.lifecycle_events
        if e["event"] == "handler_finished" and e.get("outcome") == "ok"
    ]
    assert ok_finished == ["req-1", "req-2", "req-3"]


@pytest.mark.asyncio
async def test_serialization_invariant_no_two_runs_concurrent():
    # The hard invariant: exactly one turn executes per room at a time,
    # even with queued follow-ups draining. A counter tracks concurrent
    # _run bodies; it must never exceed 1.
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    concurrent = 0
    max_concurrent = 0
    gate = asyncio.Event()

    async def make_engine(first: bool):
        nonlocal concurrent, max_concurrent

        async def engine():
            nonlocal concurrent, max_concurrent
            concurrent += 1
            max_concurrent = max(max_concurrent, concurrent)
            try:
                if first:
                    await gate.wait()
                else:
                    await asyncio.sleep(0)
                return "ok"
            finally:
                concurrent -= 1

        return engine

    first = asyncio.create_task(
        sup.dispatch("r1", "req-1", await make_engine(first=True))
    )
    await asyncio.sleep(0.01)
    # Queue two follow-ups while the first is gated.
    await sup.dispatch("r1", "req-2", await make_engine(first=False))
    await sup.dispatch("r1", "req-3", await make_engine(first=False))

    gate.set()
    await first

    assert max_concurrent == 1


@pytest.mark.asyncio
async def test_stale_queued_item_skipped_on_drain(monkeypatch):
    # A queued follow-up older than the TTL is skipped on drain (a late
    # reply is worse than none): handler_finished(outcome=rejected) +
    # a stale notice, and its run_engine never runs.
    import anygarden_agent.runtime.handler_wrapper as hw

    monkeypatch.setattr(hw, "_QUEUE_ITEM_TTL_SEC", 0.0)

    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    gate = asyncio.Event()
    ran_stale = False

    async def first_engine():
        await gate.wait()
        return "first"

    async def stale_engine():
        nonlocal ran_stale
        ran_stale = True
        return "should-not-run"

    first = asyncio.create_task(sup.dispatch("r1", "req-1", first_engine))
    await asyncio.sleep(0.01)
    await sup.dispatch("r1", "req-stale", stale_engine)

    # Let the enqueued_at timestamp age past the (0s) TTL.
    await asyncio.sleep(0.001)
    gate.set()
    await first

    assert ran_stale is False
    stale_finished = [
        e
        for e in client.lifecycle_events
        if e["request_id"] == "req-stale" and e["event"] == "handler_finished"
    ]
    # queued (on enqueue) + rejected (skipped on drain)
    assert [e["outcome"] for e in stale_finished] == ["queued", "rejected"]
    stale_sends = [s for s in client.sends if s[2] == {"request_id": "req-stale"}]
    assert len(stale_sends) == 1
    assert "너무 오래되어" in stale_sends[0][1]


# ── #457 Wave 2b — transient retry (default OFF) ─────────────────────


@pytest.mark.asyncio
async def test_default_no_retry_for_transient_failure():
    # Default _MAX_RETRY_ATTEMPTS == 0 → a transient EngineError is NOT
    # retried; behaviour is identical to a plain failed turn. This proves
    # the no-behaviour-change default.
    import anygarden_agent.runtime.handler_wrapper as hw

    assert hw._MAX_RETRY_ATTEMPTS == 0  # the shipped default is OFF

    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    calls = 0

    async def transient_engine():
        nonlocal calls
        calls += 1
        raise EngineError("503 service unavailable", transient=True)

    await sup.dispatch("r1", "req-1", transient_engine)

    assert calls == 1  # no retry
    outcomes = [
        e["outcome"] for e in client.lifecycle_events if e["event"] == "handler_finished"
    ]
    assert outcomes == ["failed"]
    assert "retrying" not in [
        e.get("outcome") for e in client.lifecycle_events
    ]


@pytest.mark.asyncio
async def test_transient_empty_failure_retries_then_succeeds(monkeypatch):
    # With max=1: a transient EngineError with empty output retries once
    # then succeeds. The retry emits outcome=retrying, then the success
    # reply is delivered.
    import anygarden_agent.runtime.handler_wrapper as hw

    monkeypatch.setattr(hw, "_MAX_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(hw, "_RETRY_BACKOFF_BASE_SEC", 0.0)

    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    calls = 0

    async def flaky_engine():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise EngineError("429 too many requests", transient=True)
        return "recovered"

    await sup.dispatch("r1", "req-1", flaky_engine)

    assert calls == 2  # one retry
    handler_outcomes = [
        e["outcome"] for e in client.lifecycle_events if e["event"] == "handler_finished"
    ]
    # retrying (intermediate signal) then the terminal ok.
    assert handler_outcomes == ["retrying", "ok"]
    assert client.sends == [("r1", "recovered", {"request_id": "req-1"})]


@pytest.mark.asyncio
async def test_transient_retry_exhausted_emits_outcome_and_notice(monkeypatch):
    # With max=1: a transient EngineError that keeps failing empty
    # exhausts the single retry → terminal outcome=retry_exhausted plus
    # the standard failed notice.
    import anygarden_agent.runtime.handler_wrapper as hw

    monkeypatch.setattr(hw, "_MAX_RETRY_ATTEMPTS", 1)
    monkeypatch.setattr(hw, "_RETRY_BACKOFF_BASE_SEC", 0.0)

    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    calls = 0

    async def always_transient():
        nonlocal calls
        calls += 1
        raise EngineError("502 bad gateway", transient=True)

    await sup.dispatch("r1", "req-1", always_transient)

    assert calls == 2  # initial + 1 retry
    handler_outcomes = [
        e["outcome"] for e in client.lifecycle_events if e["event"] == "handler_finished"
    ]
    assert handler_outcomes == ["retrying", "retry_exhausted"]
    assert len(client.sends) == 1
    assert "생성하지 못했습니다" in client.sends[0][1]


@pytest.mark.asyncio
async def test_output_producing_transient_failure_does_not_retry(monkeypatch):
    # Even with retries enabled, a transient EngineError is NOT retried
    # when output was already produced (guard against double-posting a
    # partial reply). Here the adapter returns a non-empty EngineTurn
    # response, so there is nothing empty to retry.
    import anygarden_agent.runtime.handler_wrapper as hw

    monkeypatch.setattr(hw, "_MAX_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(hw, "_RETRY_BACKOFF_BASE_SEC", 0.0)

    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    calls = 0

    async def partial_then_ok():
        nonlocal calls
        calls += 1
        # Produces output → no retry path regardless of transient flag.
        return EngineTurn(response="partial output", prompt="in")

    await sup.dispatch("r1", "req-1", partial_then_ok)

    assert calls == 1  # ran exactly once, no retry
    handler_outcomes = [
        e["outcome"] for e in client.lifecycle_events if e["event"] == "handler_finished"
    ]
    assert handler_outcomes == ["ok"]


@pytest.mark.asyncio
async def test_non_transient_failure_not_retried(monkeypatch):
    # A non-transient EngineError (e.g. a 400 model error) is NOT retried
    # even with retries enabled — only transient causes qualify.
    import anygarden_agent.runtime.handler_wrapper as hw

    monkeypatch.setattr(hw, "_MAX_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(hw, "_RETRY_BACKOFF_BASE_SEC", 0.0)

    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    calls = 0

    async def hard_failure():
        nonlocal calls
        calls += 1
        raise EngineError("400 unsupported model", transient=False)

    await sup.dispatch("r1", "req-1", hard_failure)

    assert calls == 1  # no retry
    handler_outcomes = [
        e["outcome"] for e in client.lifecycle_events if e["event"] == "handler_finished"
    ]
    assert handler_outcomes == ["failed"]
