"""LifecycleFrame → OTEL span mapping in the WS handler (#420).

Exercises ``_apply_lifecycle_to_trace`` (the pure mapping the handler
calls per inbound lifecycle frame) against a real ``TracingService``
backed by an in-memory exporter, so the span tree is asserted without
standing up a WebSocket.
"""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from anygarden.observability.tracing import (
    SPAN_ENGINE,
    SPAN_HANDLER,
    SPAN_REQUEST,
    TracingService,
)
from anygarden.ws.handler import _apply_lifecycle_to_trace
from anygarden.ws.protocol import LifecycleFrame


def _service() -> tuple[TracingService, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return TracingService(provider), exporter


def _frame(event: str, *, rid: str = "req-1", room: str = "room-1", **kw) -> LifecycleFrame:
    return LifecycleFrame(request_id=rid, room_id=room, event=event, **kw)


def test_full_lifecycle_builds_and_closes_span_tree():
    ts, exporter = _service()
    ts.start_request("req-1", room_id="room-1", agent_id="agent-1")

    _apply_lifecycle_to_trace(ts, agent_id="agent-1", frame=_frame("handler_started"))
    _apply_lifecycle_to_trace(
        ts, agent_id="agent-1", frame=_frame("engine_call_started", engine="codex")
    )
    # an in-flight engine call exists → an LLM call would correlate
    assert ts.record_llm_call(
        agent_id="agent-1",
        model_name="m",
        prompt_tokens=None,
        completion_tokens=None,
        duration_ms=1,
        status_code=200,
    ).mode == "linked"

    _apply_lifecycle_to_trace(
        ts,
        agent_id="agent-1",
        frame=_frame("engine_call_finished", engine="codex", outcome="ok", duration_ms=900),
    )
    _apply_lifecycle_to_trace(
        ts,
        agent_id="agent-1",
        frame=_frame("handler_finished", outcome="ok", duration_ms=950),
    )

    names = {s.name for s in exporter.get_finished_spans()}
    assert {SPAN_REQUEST, SPAN_HANDLER, SPAN_ENGINE} <= names
    # handler_finished closed the root → a later LLM call no longer links
    assert ts.record_llm_call(
        agent_id="agent-1",
        model_name="m",
        prompt_tokens=None,
        completion_tokens=None,
        duration_ms=1,
        status_code=200,
    ).mode == "none"


def test_rejected_handler_finished_without_handler_start_closes_root():
    # The "room busy" path emits handler_finished(outcome=rejected) with
    # no handler_started / engine_call_started in between.
    ts, exporter = _service()
    ts.start_request("req-9", room_id="room-1", agent_id="agent-1")
    _apply_lifecycle_to_trace(
        ts,
        agent_id="agent-1",
        frame=_frame("handler_finished", rid="req-9", outcome="rejected"),
    )
    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert SPAN_REQUEST in spans
    assert spans[SPAN_REQUEST].attributes["anygarden.outcome"] == "rejected"


def test_frame_without_request_id_is_ignored():
    # request_id is non-optional on the wire model, but guard anyway:
    # an empty string must be a no-op rather than registering a span.
    ts, exporter = _service()
    _apply_lifecycle_to_trace(
        ts, agent_id="a", frame=_frame("handler_started", rid="")
    )
    assert exporter.get_finished_spans() == ()


def test_disabled_service_mapping_is_noop():
    ts = TracingService(None)
    # Should not raise even though no request was ever started.
    _apply_lifecycle_to_trace(ts, agent_id="a", frame=_frame("handler_started"))
    _apply_lifecycle_to_trace(
        ts, agent_id="a", frame=_frame("handler_finished", outcome="ok")
    )
