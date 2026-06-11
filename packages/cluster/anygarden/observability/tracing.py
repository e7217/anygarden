"""OpenTelemetry tracing for the Anygarden cluster (#420).

Anygarden agents run their LLM engines as external CLI subprocesses
(codex / claude-code / gemini), so a W3C trace context cannot be
propagated from the cluster, through the agent, into the actual LLM
HTTP call. We therefore *reconstruct* the request trace entirely on
the cluster side (design "approach (i)"):

    chat.request            (user message_received → response_sent)   [root]
      └─ agent.handler      (handler_started → handler_finished)
           └─ agent.engine_call  (engine_call_started → _finished)
                └─ llm.generation ×N   (reverse-proxy, GenAI semconv)

The four agent-side spans are rebuilt from the ``LifecycleFrame``
events the agent already emits (the agent stays unchanged). The LLM
generation spans are captured at the reverse proxy — the single point
where prompt, completion, model, tokens and latency are all visible —
and stitched onto the correct request via an in-flight map keyed by
``agent_id`` (the only identifier the proxy can read from the caller's
token). Because ``RoomHandlerSupervisor`` serializes handlers per
room, a single active engine call per agent is the common case and the
correlation is reliable; concurrent multi-room work is flagged
``ambiguous`` rather than mis-attributed.

Everything here is best-effort: when tracing is disabled (no OTLP
endpoint) the service is a no-op, and any exporter/span error is
swallowed so the request path is never broken.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog
from opentelemetry import trace as ot_trace
from opentelemetry.trace import Span, Status, StatusCode

logger = structlog.get_logger(__name__)

_TRACER_NAME = "anygarden.observability"

# Span names — kept stable so dashboards / tests can key off them.
SPAN_REQUEST = "chat.request"
SPAN_HANDLER = "agent.handler"
SPAN_ENGINE = "agent.engine_call"
SPAN_LLM = "llm.generation"

# OpenTracing's FOLLOWS_FROM reference type (#431). OTEL has no built-in
# FOLLOWS_FROM link kind — a bare ``Link`` is untyped and a consumer
# cannot tell it from a child-of reference — so the *causal* (vs
# parent-child) relationship is carried on this link attribute, the
# standard OpenTracing→OTEL bridge key. Hard-coded rather than imported
# from ``opentelemetry.semconv._incubating`` (an unstable module) but
# kept equal to its ``OPENTRACING_REF_TYPE`` / ``FOLLOWS_FROM`` values.
_REF_TYPE_KEY = "opentracing.ref_type"
_REF_TYPE_FOLLOWS_FROM = "follows_from"


def parse_otlp_headers(raw: str) -> dict[str, str]:
    """Parse ``"k1=v1,k2=v2"`` into a headers dict.

    Forgiving by design: blank segments and segments without ``=`` are
    skipped rather than raising, because a malformed header string must
    not stop the server from booting (it only degrades export auth).
    A value may itself contain ``=`` (e.g. base64 padding) — only the
    first ``=`` splits.
    """
    headers: dict[str, str] = {}
    for segment in raw.split(","):
        segment = segment.strip()
        if not segment or "=" not in segment:
            continue
        key, _, value = segment.partition("=")
        key = key.strip()
        value = value.strip()
        if key:
            headers[key] = value
    return headers


_TRACES_PATH = "/v1/traces"


def normalize_otlp_endpoint(endpoint: str) -> str:
    """Append ``/v1/traces`` unless the URL already ends with it.

    The OTLP/HTTP exporter uses an explicit ``endpoint=`` argument
    verbatim (the ``/v1/traces`` auto-append only applies to the
    ``OTEL_EXPORTER_OTLP_ENDPOINT`` *base* env var, which we don't use).
    So a base URL like ``https://host/api/public/otel`` would POST to
    the wrong path and silently 404. Normalizing here lets operators
    pass either the base URL or the full traces path.
    """
    e = endpoint.rstrip("/")
    if e.endswith(_TRACES_PATH):
        return e
    return e + _TRACES_PATH


def resolve_otlp_headers(config: Any) -> dict[str, str]:
    """OTLP export headers from explicit override or Langfuse keys.

    ``otel_otlp_headers`` (raw ``k=v,k=v``) wins when set. Otherwise, if
    both Langfuse keys are present, build HTTP Basic auth from
    ``base64("<public>:<secret>")`` so operators don't hand-encode it.
    """
    raw = getattr(config, "otel_otlp_headers", "") or ""
    if raw.strip():
        return parse_otlp_headers(raw)
    public = (getattr(config, "otel_langfuse_public_key", "") or "").strip()
    secret = (getattr(config, "otel_langfuse_secret_key", "") or "").strip()
    if public and secret:
        import base64

        token = base64.b64encode(f"{public}:{secret}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    return {}


def setup_tracing(config: Any) -> Optional[Any]:
    """Build a :class:`TracerProvider`, or ``None`` when tracing is off.

    Returns ``None`` (tracing disabled) when ``otel_enabled`` is false
    or no OTLP endpoint is set — the caller treats ``None`` as "run the
    server exactly as before". Any import/config failure is logged and
    downgraded to ``None`` so a bad OTEL config can never block boot.
    """
    if not getattr(config, "otel_enabled", False):
        return None
    endpoint = getattr(config, "otel_otlp_endpoint", "") or ""
    if not endpoint:
        logger.warning("otel.disabled", reason="otel_enabled but no otlp_endpoint")
        return None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        resource = Resource.create(
            {"service.name": getattr(config, "otel_service_name", "anygarden-cluster")}
        )
        ratio = float(getattr(config, "otel_sampling_ratio", 1.0))
        provider = TracerProvider(
            resource=resource, sampler=ParentBased(TraceIdRatioBased(ratio))
        )
        traces_endpoint = normalize_otlp_endpoint(endpoint)
        exporter = OTLPSpanExporter(
            endpoint=traces_endpoint,
            headers=resolve_otlp_headers(config),
        )
        provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("otel.enabled", endpoint=traces_endpoint, sampling_ratio=ratio)
        return provider
    except Exception as exc:  # noqa: BLE001 — never let OTEL setup block boot
        logger.warning("otel.setup_failed", error=str(exc))
        return None


@dataclass
class _RequestTrace:
    """Live spans for one in-flight request, keyed by ``request_id``."""

    root: Span
    created_monotonic: float
    handler: Optional[Span] = None
    engine_call: Optional[Span] = None


@dataclass
class _Inflight:
    """An agent's currently-open engine call (for proxy correlation)."""

    room_id: Optional[str]
    request_id: str
    engine_call_span: Span


@dataclass
class LLMCorrelation:
    """Outcome of correlating a proxied LLM call to a request."""

    room_id: Optional[str] = None
    request_id: Optional[str] = None
    mode: str = "none"  # "linked" | "ambiguous" | "none"
    parent: Optional[Span] = field(default=None, repr=False)


class TracingService:
    """Facade over the tracer + span registry + in-flight map.

    Stored on ``app.state.tracing``. When ``provider`` is ``None`` every
    method is a cheap no-op, so call sites stay branch-free.
    """

    def __init__(self, provider: Optional[Any], *, capture_content: bool = True,
                 capture_max_chars: int = 8000) -> None:
        self._enabled = provider is not None
        self._tracer = provider.get_tracer(_TRACER_NAME) if provider else None
        self._capture_content = capture_content
        self._capture_max_chars = capture_max_chars
        self._registry: dict[str, _RequestTrace] = {}
        self._inflight: dict[str, list[_Inflight]] = {}

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ── request lifecycle → spans ────────────────────────────────────

    def start_request(
        self,
        request_id: str,
        *,
        room_id: Optional[str],
        agent_id: Optional[str],
        parent_request_id: Optional[str] = None,
    ) -> Optional[str]:
        """Open the root ``chat.request`` span. Returns its trace_id hex
        (for log correlation) or ``None`` when disabled / duplicate.

        ``parent_request_id`` (#431) ties an agent→agent turn back to the
        turn that triggered it. When the parent's root span is still in
        the registry — it is, because B is minted on A's ``response_sent``,
        which fires before A's ``handler_finished`` closes A — we attach a
        FOLLOWS_FROM :class:`Link` to it. B stays its OWN trace (not a
        child: A and B are independent lifecycles and A's root closes
        first), and the link records the causal edge for the backend.
        The ``anygarden.parent_request_id`` attribute is stamped
        regardless of whether the parent span could be resolved, since the
        DB-side parent link is independent of tracing.
        """
        if not self._enabled or not request_id:
            return None
        if request_id in self._registry:  # idempotent: first start wins
            return _trace_id_hex(self._registry[request_id].root)
        links = self._parent_links(parent_request_id)
        span = self._tracer.start_span(SPAN_REQUEST, links=links)
        _set(span, "anygarden.request_id", request_id)
        _set(span, "anygarden.room_id", room_id)
        _set(span, "anygarden.agent_id", agent_id)
        # #431 — informational; the FOLLOWS_FROM Link above is what the
        # trace backend reads, this is for our own /activity queries.
        _set(span, "anygarden.parent_request_id", parent_request_id)
        # #425 — Langfuse groups traces sharing this magic attribute into
        # one Session, so a room's turns read as one conversation.
        _set(span, "langfuse.session.id", room_id)
        self._registry[request_id] = _RequestTrace(
            root=span, created_monotonic=time.monotonic()
        )
        return _trace_id_hex(span)

    def _parent_links(self, parent_request_id: Optional[str]) -> Optional[list]:
        """Build a one-element FOLLOWS_FROM link list, or ``None`` (#431).

        Returns ``None`` (not ``[]``) so the common no-parent path passes
        ``links=None`` to ``start_span`` exactly as before. A parent that
        isn't in the registry (never started / already reaped) degrades to
        no link — the turn is still tracked, just without the causal edge.
        """
        if not parent_request_id:
            return None
        parent = self._registry.get(parent_request_id)
        if parent is None:
            return None
        try:
            return [
                ot_trace.Link(
                    parent.root.get_span_context(),
                    # Typed FOLLOWS_FROM — a bare Link conveys no relation.
                    attributes={_REF_TYPE_KEY: _REF_TYPE_FOLLOWS_FROM},
                )
            ]
        except Exception as exc:  # noqa: BLE001 — never raise from instrumentation
            logger.warning("otel.parent_link_failed", error=str(exc))
            return None

    def start_handler(self, request_id: str, *, room_id: Optional[str] = None) -> None:
        rt = self._registry.get(request_id) if self._enabled else None
        if rt is None or rt.handler is not None:
            return
        rt.handler = self._child(rt.root, SPAN_HANDLER, {"anygarden.room_id": room_id})

    def start_engine_call(
        self,
        request_id: str,
        *,
        engine: Optional[str],
        room_id: Optional[str],
        agent_id: Optional[str],
    ) -> None:
        rt = self._registry.get(request_id) if self._enabled else None
        if rt is None or rt.engine_call is not None:
            return
        parent = rt.handler or rt.root
        span = self._child(
            parent, SPAN_ENGINE, {"anygarden.engine": engine, "anygarden.room_id": room_id}
        )
        rt.engine_call = span
        if agent_id and span is not None:
            self._inflight.setdefault(agent_id, []).append(
                _Inflight(room_id=room_id, request_id=request_id, engine_call_span=span)
            )

    def finish_engine_call(
        self,
        request_id: str,
        *,
        outcome: Optional[str],
        duration_ms: Optional[int],
        error: Optional[str] = None,
        agent_id: Optional[str] = None,
        prompt: Optional[str] = None,
        completion: Optional[str] = None,
    ) -> None:
        rt = self._registry.get(request_id) if self._enabled else None
        self._drop_inflight(request_id, agent_id)
        if rt is None or rt.engine_call is None:
            return
        # #433 — gateway-free turn I/O: the agent captured the prompt it
        # handed the engine and the engine's reply at the run_engine
        # boundary and shipped them on the engine_call_finished frame.
        # Stamp them onto the engine span (same ``gen_ai.*`` keys as the
        # proxy path) before it closes, gated by the content toggle.
        if self._capture_content:
            if prompt:
                _set(rt.engine_call, "gen_ai.prompt", self._clip_text(prompt))
            if completion:
                _set(rt.engine_call, "gen_ai.completion", self._clip_text(completion))
        self._end(rt.engine_call, outcome=outcome, duration_ms=duration_ms, error=error)
        rt.engine_call = None

    def finish_handler(
        self,
        request_id: str,
        *,
        outcome: Optional[str],
        duration_ms: Optional[int],
        error: Optional[str] = None,
    ) -> None:
        rt = self._registry.get(request_id) if self._enabled else None
        if rt is None or rt.handler is None:
            return
        self._end(rt.handler, outcome=outcome, duration_ms=duration_ms, error=error)
        rt.handler = None

    def finish_request(
        self, request_id: str, *, outcome: Optional[str] = None
    ) -> None:
        """Close the root span and drop the request from the registry.

        Also ends any still-open child spans defensively (e.g. a
        ``response_sent`` that races ahead of ``handler_finished``).
        """
        rt = self._registry.pop(request_id, None) if self._enabled else None
        if rt is None:
            return
        if rt.engine_call is not None:
            self._end(rt.engine_call, outcome=outcome, duration_ms=None)
        if rt.handler is not None:
            self._end(rt.handler, outcome=outcome, duration_ms=None)
        self._end(rt.root, outcome=outcome, duration_ms=None)
        self._drop_inflight(request_id, None)

    def reap_request(self, request_id: str) -> None:
        """Close a request's spans as ``orphaned`` right now (#427).

        Called by the cluster orphan sweeper so the DB ``handler_orphaned``
        decision and the in-memory span reaper agree immediately, instead
        of waiting out the separate reaper TTL. No-op if the request
        already finished (not in the registry).
        """
        self.finish_request(request_id, outcome="orphaned")

    def note_response_sent(
        self, request_id: str, message_id: Optional[str] = None
    ) -> None:
        """Record the agent's delivered reply as a root-span event (#427).

        The root span closes on ``handler_finished``; ``response_sent``
        fires just before it, so the root is still open here. Lets a
        trace show whether/when the reply actually reached the room and
        which message carried it. No-op when disabled / already closed.
        """
        if not self._enabled or not request_id:
            return
        rt = self._registry.get(request_id)
        if rt is None:
            return
        try:
            attrs = {"message_id": message_id} if message_id else None
            rt.root.add_event("response_sent", attributes=attrs)
        except Exception as exc:  # noqa: BLE001 — best-effort
            logger.warning("otel.note_response_sent_failed", error=str(exc))

    # ── reverse-proxy LLM call → span + correlation ──────────────────

    def record_llm_call(
        self,
        *,
        agent_id: Optional[str],
        model_name: str,
        prompt_tokens: Optional[int],
        completion_tokens: Optional[int],
        duration_ms: int,
        status_code: int,
        request_body: Optional[bytes] = None,
        response_body: Optional[bytes] = None,
        error: Optional[str] = None,
    ) -> LLMCorrelation:
        """Emit an ``llm.generation`` span and report the correlation.

        The returned :class:`LLMCorrelation` carries ``room_id`` so the
        caller can stamp it onto the persisted usage row.
        """
        if not self._enabled:
            return LLMCorrelation()
        corr = self._correlate(agent_id)
        try:
            end_ns = time.time_ns()
            start_ns = end_ns - max(0, duration_ms) * 1_000_000
            ctx = ot_trace.set_span_in_context(corr.parent) if corr.parent else None
            span = self._tracer.start_span(SPAN_LLM, context=ctx, start_time=start_ns)
            _set(span, "gen_ai.operation.name", "chat")
            _set(span, "gen_ai.request.model", model_name or None)
            _set(span, "gen_ai.usage.input_tokens", prompt_tokens)
            _set(span, "gen_ai.usage.output_tokens", completion_tokens)
            _set(span, "anygarden.agent_id", agent_id)
            _set(span, "anygarden.correlation", corr.mode)
            _set(span, "anygarden.room_id", corr.room_id)
            _set(span, "anygarden.request_id", corr.request_id)
            # #425 — same Langfuse session as the room's lifecycle trace.
            _set(span, "langfuse.session.id", corr.room_id)
            _set(span, "http.response.status_code", status_code)
            if self._capture_content:
                if request_body:
                    _set(span, "gen_ai.prompt", self._clip(request_body))
                if response_body:
                    _set(span, "gen_ai.completion", self._clip(response_body))
            if status_code >= 400 or error:
                span.set_status(Status(StatusCode.ERROR, (error or f"http {status_code}")[:200]))
            span.end(end_time=end_ns)
        except Exception as exc:  # noqa: BLE001 — span emission is best-effort
            logger.warning("otel.llm_span_failed", error=str(exc))
        return corr

    # ── reaper ───────────────────────────────────────────────────────

    def reap(self, ttl_seconds: float) -> int:
        """End spans for requests with no terminal event past ``ttl``.

        Guards against unbounded registry growth when a lifecycle frame
        is lost. Returns the number of requests reaped.
        """
        if not self._enabled:
            return 0
        now = time.monotonic()
        stale = [
            rid
            for rid, rt in self._registry.items()
            if now - rt.created_monotonic > ttl_seconds
        ]
        for rid in stale:
            rt = self._registry.pop(rid, None)
            if rt is None:
                continue
            for span in (rt.engine_call, rt.handler, rt.root):
                if span is not None:
                    self._end(span, outcome="orphaned", duration_ms=None)
            self._drop_inflight(rid, None)
        return len(stale)

    def shutdown(self) -> None:
        """Close any spans still open at process shutdown."""
        if not self._enabled:
            return
        for rid in list(self._registry):
            self.finish_request(rid, outcome="orphaned")

    # ── internals ────────────────────────────────────────────────────

    def _correlate(self, agent_id: Optional[str]) -> LLMCorrelation:
        entries = self._inflight.get(agent_id or "", [])
        if len(entries) == 1:
            e = entries[0]
            return LLMCorrelation(
                room_id=e.room_id,
                request_id=e.request_id,
                mode="linked",
                parent=e.engine_call_span,
            )
        if len(entries) > 1:
            return LLMCorrelation(mode="ambiguous")
        return LLMCorrelation(mode="none")

    def _child(
        self, parent: Span, name: str, attrs: dict[str, Any]
    ) -> Optional[Span]:
        try:
            ctx = ot_trace.set_span_in_context(parent)
            span = self._tracer.start_span(name, context=ctx)
            for k, v in attrs.items():
                _set(span, k, v)
            return span
        except Exception as exc:  # noqa: BLE001
            logger.warning("otel.child_span_failed", name=name, error=str(exc))
            return None

    def _end(
        self,
        span: Span,
        *,
        outcome: Optional[str],
        duration_ms: Optional[int],
        error: Optional[str] = None,
    ) -> None:
        try:
            _set(span, "anygarden.outcome", outcome)
            _set(span, "anygarden.duration_ms", duration_ms)
            if error:
                _set(span, "anygarden.error", error[:500])
            # #425 — ``rejected`` (room busy, second concurrent dispatch)
            # is a real failure for the user and must count as ERROR;
            # ``cancelled`` stays non-error (deliberate interruption).
            if outcome in ("failed", "timeout", "orphaned", "rejected"):
                span.set_status(Status(StatusCode.ERROR, str(outcome)))
            span.end()
        except Exception as exc:  # noqa: BLE001
            logger.warning("otel.end_span_failed", error=str(exc))

    def _drop_inflight(self, request_id: str, agent_id: Optional[str]) -> None:
        keys = [agent_id] if agent_id else list(self._inflight)
        for key in keys:
            entries = self._inflight.get(key)
            if not entries:
                continue
            remaining = [e for e in entries if e.request_id != request_id]
            if remaining:
                self._inflight[key] = remaining
            else:
                self._inflight.pop(key, None)

    def _clip(self, body: bytes) -> str:
        return self._clip_text(body.decode("utf-8", errors="replace"))

    def _clip_text(self, text: str) -> str:
        """Bound a captured string to ``capture_max_chars`` (#433).

        The str sibling of :meth:`_clip` — turn I/O arrives as text from
        the agent frame rather than raw proxy bytes.
        """
        if len(text) <= self._capture_max_chars:
            return text
        return text[: self._capture_max_chars - 1] + "…"


def _set(span: Optional[Span], key: str, value: Any) -> None:
    """Set a span attribute, skipping ``None`` (OTEL rejects null values)."""
    if span is None or value is None:
        return
    try:
        span.set_attribute(key, value)
    except Exception:  # noqa: BLE001 — never raise from instrumentation
        pass


def _trace_id_hex(span: Optional[Span]) -> Optional[str]:
    try:
        if span is None:
            return None
        ctx = span.get_span_context()
        return format(ctx.trace_id, "032x")
    except Exception:  # noqa: BLE001
        return None
