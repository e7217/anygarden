"""Phase 2 tracing additions: reap_request (DB-orphan ↔ span bridge)
and note_response_sent (delivered reply as a root-span event) (#427)."""

from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import StatusCode

from anygarden.observability.tracing import SPAN_REQUEST, TracingService


def _service():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return TracingService(provider), exporter


def test_reap_request_closes_spans_as_orphaned():
    ts, exporter = _service()
    ts.start_request("r1", room_id="room-1", agent_id="a")
    ts.start_handler("r1")
    ts.reap_request("r1")
    root = next(s for s in exporter.get_finished_spans() if s.name == SPAN_REQUEST)
    assert root.attributes["anygarden.outcome"] == "orphaned"
    assert root.status.status_code == StatusCode.ERROR
    # idempotent: already gone from the registry
    ts.reap_request("r1")


def test_reap_request_unknown_id_is_noop():
    ts, exporter = _service()
    ts.reap_request("nope")  # must not raise
    assert exporter.get_finished_spans() == ()


def test_note_response_sent_adds_root_event():
    ts, exporter = _service()
    ts.start_request("r1", room_id="room-1", agent_id="a")
    ts.note_response_sent("r1", "msg-123")
    ts.finish_request("r1", outcome="ok")
    root = next(s for s in exporter.get_finished_spans() if s.name == SPAN_REQUEST)
    names = [e.name for e in root.events]
    assert "response_sent" in names
    evt = next(e for e in root.events if e.name == "response_sent")
    assert evt.attributes["message_id"] == "msg-123"


def test_note_response_sent_after_close_is_noop():
    ts, exporter = _service()
    ts.start_request("r1", room_id="room-1", agent_id="a")
    ts.finish_request("r1", outcome="ok")
    # request already closed/popped → no event, no crash
    ts.note_response_sent("r1", "late")
    root = next(s for s in exporter.get_finished_spans() if s.name == SPAN_REQUEST)
    assert "response_sent" not in [e.name for e in root.events]


def test_disabled_service_phase2_methods_are_noop():
    ts = TracingService(None)
    ts.reap_request("r1")
    ts.note_response_sent("r1", "m")  # no exceptions
