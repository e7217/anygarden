"""Unit tests for the OTEL tracing service (#420).

Drives the reconstruction logic with an in-memory span exporter and
asserts the span tree, the gateway-correlation outcomes, and the
reaper — no real OTLP backend involved.
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
    SPAN_LLM,
    SPAN_REQUEST,
    TracingService,
    normalize_otlp_endpoint,
    parse_otlp_headers,
    resolve_otlp_headers,
    setup_tracing,
)


class _Cfg:
    """Minimal config stand-in for setup_tracing / resolve_otlp_headers."""

    def __init__(self, **kw):
        self.otel_enabled = kw.get("otel_enabled", True)
        self.otel_otlp_endpoint = kw.get("otel_otlp_endpoint", "")
        self.otel_otlp_headers = kw.get("otel_otlp_headers", "")
        self.otel_langfuse_public_key = kw.get("otel_langfuse_public_key", "")
        self.otel_langfuse_secret_key = kw.get("otel_langfuse_secret_key", "")
        self.otel_service_name = kw.get("otel_service_name", "test-svc")
        self.otel_sampling_ratio = kw.get("otel_sampling_ratio", 1.0)


def _provider_with_exporter() -> tuple[TracerProvider, InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def _by_name(exporter: InMemorySpanExporter) -> dict:
    return {s.name: s for s in exporter.get_finished_spans()}


# ── parse_otlp_headers ───────────────────────────────────────────────


def test_parse_headers_handles_blanks_and_embedded_equals():
    out = parse_otlp_headers("Authorization=Basic ab==, X-Foo = bar ,,bad,= ")
    assert out == {"Authorization": "Basic ab==", "X-Foo": "bar"}


def test_parse_headers_empty():
    assert parse_otlp_headers("") == {}


# ── normalize_otlp_endpoint ──────────────────────────────────────────


def test_normalize_endpoint_appends_traces_path_to_base_url():
    assert (
        normalize_otlp_endpoint("https://host/api/public/otel")
        == "https://host/api/public/otel/v1/traces"
    )


def test_normalize_endpoint_leaves_full_path_untouched():
    assert (
        normalize_otlp_endpoint("https://host/api/public/otel/v1/traces")
        == "https://host/api/public/otel/v1/traces"
    )


def test_normalize_endpoint_strips_trailing_slash():
    assert (
        normalize_otlp_endpoint("http://localhost:4318/")
        == "http://localhost:4318/v1/traces"
    )


# ── resolve_otlp_headers ─────────────────────────────────────────────


def test_resolve_headers_explicit_override_wins():
    cfg = _Cfg(
        otel_otlp_headers="Authorization=Basic explicit",
        otel_langfuse_public_key="pk-lf-x",
        otel_langfuse_secret_key="sk-lf-y",
    )
    assert resolve_otlp_headers(cfg) == {"Authorization": "Basic explicit"}


def test_resolve_headers_builds_basic_from_langfuse_keys():
    import base64

    cfg = _Cfg(
        otel_langfuse_public_key="pk-lf-pub",
        otel_langfuse_secret_key="sk-lf-sec",
    )
    expected = base64.b64encode(b"pk-lf-pub:sk-lf-sec").decode()
    assert resolve_otlp_headers(cfg) == {"Authorization": f"Basic {expected}"}


def test_resolve_headers_empty_when_nothing_configured():
    assert resolve_otlp_headers(_Cfg()) == {}


# ── setup_tracing ────────────────────────────────────────────────────


def test_setup_tracing_disabled_returns_none():
    class Off:
        otel_enabled = False

    class Endpointless:
        otel_enabled = True
        otel_otlp_endpoint = ""

    assert setup_tracing(Off()) is None
    assert setup_tracing(Endpointless()) is None


def test_setup_tracing_enabled_builds_provider():
    class Cfg:
        otel_enabled = True
        otel_otlp_endpoint = "http://localhost:4318/v1/traces"
        otel_otlp_headers = "Authorization=Basic x"
        otel_service_name = "test-svc"
        otel_sampling_ratio = 1.0

    provider = setup_tracing(Cfg())
    assert provider is not None
    provider.shutdown()


def test_setup_tracing_with_base_url_and_langfuse_keys():
    # Operator supplies a base URL + pk/sk (no manual /v1/traces, no
    # hand-encoded header). setup_tracing normalizes + builds auth.
    cfg = _Cfg(
        otel_otlp_endpoint="https://langfuse.example/api/public/otel",
        otel_langfuse_public_key="pk-lf-pub",
        otel_langfuse_secret_key="sk-lf-sec",
    )
    provider = setup_tracing(cfg)
    assert provider is not None
    provider.shutdown()


# ── full trace tree ──────────────────────────────────────────────────


def test_full_request_trace_tree():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider)
    rid = "req-1"

    ts.start_request(rid, room_id="room-1", agent_id="agent-1")
    ts.start_handler(rid, room_id="room-1")
    ts.start_engine_call(rid, engine="codex", room_id="room-1", agent_id="agent-1")
    corr = ts.record_llm_call(
        agent_id="agent-1",
        model_name="gpt-4",
        prompt_tokens=10,
        completion_tokens=5,
        duration_ms=1200,
        status_code=200,
        request_body=b'{"messages":[{"role":"user","content":"hi"}]}',
        response_body=b'{"choices":[{"message":{"content":"yo"}}]}',
    )
    assert corr.mode == "linked"
    assert corr.room_id == "room-1"
    assert corr.request_id == "req-1"

    ts.finish_engine_call(rid, outcome="ok", duration_ms=1200, agent_id="agent-1")
    ts.finish_handler(rid, outcome="ok", duration_ms=1300)
    ts.finish_request(rid, outcome="ok")

    spans = _by_name(exporter)
    assert {SPAN_REQUEST, SPAN_HANDLER, SPAN_ENGINE, SPAN_LLM} <= set(spans)
    root, handler = spans[SPAN_REQUEST], spans[SPAN_HANDLER]
    engine, llm = spans[SPAN_ENGINE], spans[SPAN_LLM]

    # parent chain: root → handler → engine → llm
    assert root.parent is None
    assert handler.parent.span_id == root.context.span_id
    assert engine.parent.span_id == handler.context.span_id
    assert llm.parent.span_id == engine.context.span_id
    # one trace
    trace_ids = {s.context.trace_id for s in (root, handler, engine, llm)}
    assert len(trace_ids) == 1

    # GenAI + correlation attributes
    assert llm.attributes["gen_ai.request.model"] == "gpt-4"
    assert llm.attributes["gen_ai.usage.input_tokens"] == 10
    assert llm.attributes["gen_ai.usage.output_tokens"] == 5
    assert llm.attributes["anygarden.correlation"] == "linked"
    assert llm.attributes["anygarden.room_id"] == "room-1"
    assert "gen_ai.prompt" in llm.attributes
    assert "gen_ai.completion" in llm.attributes
    assert engine.attributes["anygarden.outcome"] == "ok"
    assert root.attributes["anygarden.request_id"] == "req-1"


def test_start_request_returns_trace_id_hex_and_is_idempotent():
    provider, _ = _provider_with_exporter()
    ts = TracingService(provider)
    first = ts.start_request("r", room_id=None, agent_id=None)
    assert first is not None and len(first) == 32
    # second start for the same id is ignored but returns the same trace id
    assert ts.start_request("r", room_id=None, agent_id=None) == first


# ── A→B causal link (#431) ───────────────────────────────────────────


def _requests_by_rid(exporter: InMemorySpanExporter) -> dict:
    return {
        s.attributes.get("anygarden.request_id"): s
        for s in exporter.get_finished_spans()
        if s.name == SPAN_REQUEST
    }


def test_start_request_with_parent_links_to_parent_trace():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider)
    # A's turn (parent) is still open when B starts — B is minted on A's
    # response_sent, which fires before A's handler_finished closes it.
    ts.start_request("rid-A", room_id="room-1", agent_id="agent-A")
    parent_ctx = ts._registry["rid-A"].root.get_span_context()
    ts.start_request(
        "rid-B", room_id="room-1", agent_id="agent-B", parent_request_id="rid-A"
    )
    ts.finish_request("rid-B", outcome="ok")
    ts.finish_request("rid-A", outcome="ok")

    b = _requests_by_rid(exporter)["rid-B"]
    # FOLLOWS_FROM: a Link to A's root, NOT a parent-child edge (A and B
    # are independent request lifecycles, so B is its own trace).
    assert b.parent is None
    assert len(b.links) == 1
    assert b.links[0].context.span_id == parent_ctx.span_id
    assert b.links[0].context.trace_id == parent_ctx.trace_id
    assert b.context.trace_id != parent_ctx.trace_id
    assert b.attributes["anygarden.parent_request_id"] == "rid-A"
    # The link must be a *typed* FOLLOWS_FROM (a bare Link is untyped and
    # indistinguishable from child-of). #431.
    assert b.links[0].attributes["opentracing.ref_type"] == "follows_from"


def test_start_request_link_survives_parent_close():
    # The link only needs the parent's span_context, which stays valid
    # after the parent span ends — so even if A finishes first the edge
    # is intact.
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider)
    ts.start_request("rid-A", room_id="r", agent_id="a")
    parent_ctx = ts._registry["rid-A"].root.get_span_context()
    ts.start_request("rid-B", room_id="r", agent_id="b", parent_request_id="rid-A")
    ts.finish_request("rid-A", outcome="ok")  # close parent first
    ts.finish_request("rid-B", outcome="ok")
    b = _requests_by_rid(exporter)["rid-B"]
    assert len(b.links) == 1
    assert b.links[0].context.span_id == parent_ctx.span_id


def test_start_request_unknown_parent_skips_link_keeps_attribute():
    # parent never started (or already reaped) → no link, but the
    # informational attribute is still stamped (the DB parent_request_id
    # is independent of whether tracing could resolve the parent span).
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider)
    ts.start_request("rid-B", room_id="r", agent_id="b", parent_request_id="ghost")
    ts.finish_request("rid-B", outcome="ok")
    b = _requests_by_rid(exporter)["rid-B"]
    assert len(b.links) == 0
    assert b.attributes["anygarden.parent_request_id"] == "ghost"


def test_start_request_without_parent_has_no_link_or_attribute():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider)
    ts.start_request("rid", room_id="r", agent_id="a")
    ts.finish_request("rid", outcome="ok")
    b = _requests_by_rid(exporter)["rid"]
    assert len(b.links) == 0
    assert "anygarden.parent_request_id" not in b.attributes


# ── correlation modes ────────────────────────────────────────────────


def test_ambiguous_when_agent_has_two_active_engine_calls():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider)
    ts.start_request("r1", room_id="roomA", agent_id="agent-1")
    ts.start_engine_call("r1", engine="codex", room_id="roomA", agent_id="agent-1")
    ts.start_request("r2", room_id="roomB", agent_id="agent-1")
    ts.start_engine_call("r2", engine="codex", room_id="roomB", agent_id="agent-1")

    corr = ts.record_llm_call(
        agent_id="agent-1",
        model_name="m",
        prompt_tokens=None,
        completion_tokens=None,
        duration_ms=5,
        status_code=200,
    )
    assert corr.mode == "ambiguous"
    assert corr.room_id is None
    llm = [s for s in exporter.get_finished_spans() if s.name == SPAN_LLM][0]
    assert llm.parent is None  # not attributed to either request
    assert llm.attributes["anygarden.correlation"] == "ambiguous"


def test_standalone_when_no_inflight():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider)
    corr = ts.record_llm_call(
        agent_id="ghost",
        model_name="m",
        prompt_tokens=None,
        completion_tokens=None,
        duration_ms=5,
        status_code=200,
    )
    assert corr.mode == "none"
    assert corr.room_id is None
    llm = [s for s in exporter.get_finished_spans() if s.name == SPAN_LLM][0]
    assert llm.parent is None


# ── engine_call turn I/O capture (#433) ──────────────────────────────


def test_finish_engine_call_stamps_prompt_and_completion():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider, capture_content=True)
    ts.start_request("r", room_id="rm", agent_id="a")
    ts.start_engine_call("r", engine="codex", room_id="rm", agent_id="a")
    ts.finish_engine_call(
        "r", outcome="ok", duration_ms=10, agent_id="a",
        prompt="the augmented input", completion="the reply",
    )
    ts.finish_request("r", outcome="ok")
    engine = [s for s in exporter.get_finished_spans() if s.name == SPAN_ENGINE][0]
    assert engine.attributes["gen_ai.prompt"] == "the augmented input"
    assert engine.attributes["gen_ai.completion"] == "the reply"


def test_finish_engine_call_omits_content_when_capture_off():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider, capture_content=False)
    ts.start_request("r", room_id="rm", agent_id="a")
    ts.start_engine_call("r", engine="codex", room_id="rm", agent_id="a")
    ts.finish_engine_call(
        "r", outcome="ok", duration_ms=10, agent_id="a",
        prompt="secret input", completion="secret reply",
    )
    ts.finish_request("r", outcome="ok")
    engine = [s for s in exporter.get_finished_spans() if s.name == SPAN_ENGINE][0]
    assert "gen_ai.prompt" not in engine.attributes
    assert "gen_ai.completion" not in engine.attributes


def test_finish_engine_call_truncates_content():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider, capture_content=True, capture_max_chars=10)
    ts.start_request("r", room_id="rm", agent_id="a")
    ts.start_engine_call("r", engine="codex", room_id="rm", agent_id="a")
    ts.finish_engine_call(
        "r", outcome="ok", duration_ms=10, agent_id="a",
        prompt="x" * 100, completion="y" * 100,
    )
    ts.finish_request("r", outcome="ok")
    engine = [s for s in exporter.get_finished_spans() if s.name == SPAN_ENGINE][0]
    assert len(engine.attributes["gen_ai.prompt"]) == 10
    assert len(engine.attributes["gen_ai.completion"]) == 10


def test_finish_engine_call_without_io_leaves_no_content():
    # Legacy call (no prompt/completion) stamps nothing — backward
    # compatible with pre-#433 frames.
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider, capture_content=True)
    ts.start_request("r", room_id="rm", agent_id="a")
    ts.start_engine_call("r", engine="codex", room_id="rm", agent_id="a")
    ts.finish_engine_call("r", outcome="ok", duration_ms=10, agent_id="a")
    ts.finish_request("r", outcome="ok")
    engine = [s for s in exporter.get_finished_spans() if s.name == SPAN_ENGINE][0]
    assert "gen_ai.prompt" not in engine.attributes
    assert "gen_ai.completion" not in engine.attributes


def test_finish_engine_call_clears_inflight():
    provider, _ = _provider_with_exporter()
    ts = TracingService(provider)
    ts.start_request("r1", room_id="roomA", agent_id="agent-1")
    ts.start_engine_call("r1", engine="codex", room_id="roomA", agent_id="agent-1")
    ts.finish_engine_call("r1", outcome="ok", duration_ms=1, agent_id="agent-1")
    # after the engine call ends, a later LLM call no longer correlates
    corr = ts.record_llm_call(
        agent_id="agent-1",
        model_name="m",
        prompt_tokens=None,
        completion_tokens=None,
        duration_ms=1,
        status_code=200,
    )
    assert corr.mode == "none"


# ── content capture toggle ───────────────────────────────────────────


def test_capture_content_off_omits_bodies():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider, capture_content=False)
    ts.start_request("r", room_id=None, agent_id="a")
    ts.start_engine_call("r", engine="e", room_id=None, agent_id="a")
    ts.record_llm_call(
        agent_id="a",
        model_name="m",
        prompt_tokens=1,
        completion_tokens=1,
        duration_ms=1,
        status_code=200,
        request_body=b"secret-prompt",
        response_body=b"secret-completion",
    )
    llm = [s for s in exporter.get_finished_spans() if s.name == SPAN_LLM][0]
    assert "gen_ai.prompt" not in llm.attributes
    assert "gen_ai.completion" not in llm.attributes


def test_capture_content_truncates():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider, capture_content=True, capture_max_chars=10)
    ts.start_request("r", room_id=None, agent_id="a")
    ts.start_engine_call("r", engine="e", room_id=None, agent_id="a")
    ts.record_llm_call(
        agent_id="a",
        model_name="m",
        prompt_tokens=1,
        completion_tokens=1,
        duration_ms=1,
        status_code=200,
        request_body=b"x" * 100,
    )
    llm = [s for s in exporter.get_finished_spans() if s.name == SPAN_LLM][0]
    assert len(llm.attributes["gen_ai.prompt"]) == 10


# ── reaper ───────────────────────────────────────────────────────────


def test_reaper_orphans_stale_requests():
    provider, exporter = _provider_with_exporter()
    ts = TracingService(provider)
    ts.start_request("r1", room_id="x", agent_id="a")
    ts.start_handler("r1")
    ts.start_engine_call("r1", engine="e", room_id="x", agent_id="a")

    # ttl < 0 makes every entry stale immediately
    assert ts.reap(ttl_seconds=-1) == 1
    spans = _by_name(exporter)
    assert spans[SPAN_REQUEST].attributes["anygarden.outcome"] == "orphaned"
    assert spans[SPAN_HANDLER].attributes["anygarden.outcome"] == "orphaned"
    assert spans[SPAN_ENGINE].attributes["anygarden.outcome"] == "orphaned"
    # registry now empty
    assert ts.reap(ttl_seconds=-1) == 0


# ── disabled no-op ───────────────────────────────────────────────────


def test_disabled_service_is_noop():
    ts = TracingService(None)
    assert ts.enabled is False
    assert ts.start_request("r", room_id=None, agent_id=None) is None
    ts.start_handler("r")
    ts.start_engine_call("r", engine=None, room_id=None, agent_id=None)
    ts.finish_engine_call("r", outcome="ok", duration_ms=1)
    ts.finish_handler("r", outcome="ok", duration_ms=1)
    ts.finish_request("r")
    assert ts.record_llm_call(
        agent_id="a",
        model_name="m",
        prompt_tokens=None,
        completion_tokens=None,
        duration_ms=1,
        status_code=200,
    ).mode == "none"
    assert ts.reap(0) == 0
    ts.shutdown()
