"""Phase 1 instrumentation: Langfuse session grouping, rejected→ERROR,
and turn metrics fed from LifecycleFrames (#425)."""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode
from prometheus_client import REGISTRY

from anygarden.observability.tracing import SPAN_REQUEST, TracingService
from anygarden.ws.handler import _apply_lifecycle_to_metrics
from anygarden.ws.protocol import LifecycleFrame


def _provider_with_exporter():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _frame(event, *, rid="r1", room="room-1", **kw):
    return LifecycleFrame(request_id=rid, room_id=room, event=event, **kw)


# ── Langfuse session.id = room_id ────────────────────────────────────


def test_root_span_carries_langfuse_session_id():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider)
    ts.start_request("r1", room_id="room-42", agent_id="a")
    ts.finish_request("r1", outcome="ok")
    root = next(s for s in exporter.get_finished_spans() if s.name == SPAN_REQUEST)
    assert root.attributes["langfuse.session.id"] == "room-42"


# ── rejected → ERROR status ──────────────────────────────────────────


def test_rejected_outcome_sets_error_status():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider)
    ts.start_request("r1", room_id="room-1", agent_id="a")
    # The "room busy" path emits handler_finished(outcome=rejected).
    ts.finish_request("r1", outcome="rejected")
    root = next(s for s in exporter.get_finished_spans() if s.name == SPAN_REQUEST)
    assert root.status.status_code == StatusCode.ERROR
    assert root.attributes["anygarden.outcome"] == "rejected"


def test_cancelled_outcome_stays_non_error():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider)
    ts.start_request("r1", room_id="room-1", agent_id="a")
    ts.finish_request("r1", outcome="cancelled")
    root = next(s for s in exporter.get_finished_spans() if s.name == SPAN_REQUEST)
    assert root.status.status_code != StatusCode.ERROR


# ── turn metrics from LifecycleFrames ────────────────────────────────


def _counter(outcome):
    return REGISTRY.get_sample_value(
        "anygarden_agent_turns_total", {"outcome": outcome}
    ) or 0.0


def _hist_count(engine, outcome):
    return REGISTRY.get_sample_value(
        "anygarden_engine_call_duration_ms_count",
        {"engine": engine, "outcome": outcome},
    ) or 0.0


def test_handler_finished_increments_turn_counter():
    before = _counter("failed")
    _apply_lifecycle_to_metrics(_frame("handler_finished", outcome="failed"))
    assert _counter("failed") == before + 1


def test_engine_call_finished_observes_histogram():
    before = _hist_count("codex", "ok")
    _apply_lifecycle_to_metrics(
        _frame("engine_call_finished", engine="codex", outcome="ok", duration_ms=1234)
    )
    assert _hist_count("codex", "ok") == before + 1


def test_non_terminal_frames_do_not_touch_metrics():
    before = _counter("ok")
    _apply_lifecycle_to_metrics(_frame("handler_started"))
    _apply_lifecycle_to_metrics(_frame("engine_call_started", engine="codex"))
    assert _counter("ok") == before


def test_missing_engine_or_outcome_falls_back_to_unknown():
    # engine_call_finished without engine → labelled "unknown", no crash.
    before = _hist_count("unknown", "ok")
    _apply_lifecycle_to_metrics(
        _frame("engine_call_finished", outcome="ok", duration_ms=10)
    )
    assert _hist_count("unknown", "ok") == before + 1
