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
    RoomHandlerSupervisor,
)


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
async def test_second_concurrent_dispatch_is_rejected():
    client = _FakeClient()
    sup = RoomHandlerSupervisor(client=client, engine_name="codex", engine_timeout=5.0)

    gate = asyncio.Event()

    async def gated_engine():
        await gate.wait()
        return "first done"

    first = asyncio.create_task(sup.dispatch("r1", "req-1", gated_engine))
    await asyncio.sleep(0.01)  # let `first` acquire the lock

    await sup.dispatch("r1", "req-2", lambda: asyncio.sleep(0, result="second"))

    second_events = [
        e for e in client.lifecycle_events if e["request_id"] == "req-2"
    ]
    assert len(second_events) == 1
    assert second_events[0]["event"] == "handler_finished"
    assert second_events[0]["outcome"] == "rejected"
    assert "req-1" in second_events[0]["error"]

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
