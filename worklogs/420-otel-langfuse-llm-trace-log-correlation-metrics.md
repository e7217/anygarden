# feat(observability): OTEL × Langfuse LLM trace, log correlation, /metrics (#420)

- Commit: `3df51df` (3df51dfc90e0596936d31de667ca38edcb4c09f3)
- Author: Changyong Um
- Date: 2026-06-05T00:58:45+09:00
- PR: #420

## Situation

Anygarden already had a `request_id` lifecycle backbone (handler/engine
lifecycle frames persisted to `ActivityLog`, orphan sweeper, #204) and a
LiteLLM reverse-proxy that logged per-call token usage, but no
distributed tracing and no way to see prompt/completion content,
per-call latency, or which room/turn an LLM call belonged to. structlog
was configured but rendered console output (not JSON) with no
trace/request correlation, and the Prometheus metrics defined in
`observability/metrics.py` were never mounted on an endpoint, so they
were defined-but-unscrapeable. The goal was to add LLM trace + OTEL
export to Langfuse, log correlation, and metrics exposure.

## Task

- Reconstruct each user→agent turn as a single OTEL trace and export it
  over OTLP to Langfuse (vendor-neutral; LangSmith becomes a config
  swap).
- Capture LLM calls (prompt/completion/model/tokens/latency) at the
  reverse proxy and correlate them to the originating request/room.
- Render JSON logs in production with `request_id`/`trace_id`.
- Expose `/metrics`.
- Constraints: agent package must stay unchanged (external CLI engines
  can't propagate trace context); feature off by default; instrumentation
  best-effort (never break the request path); no DB schema change.

## Action

- New `packages/cluster/anygarden/observability/tracing.py`: `setup_tracing`
  (OTLP/HTTP exporter + BatchSpanProcessor, returns `None` when disabled),
  `TracingService` facade holding an in-process span registry
  (`request_id → root/handler/engine_call` spans) and an in-flight map
  (`agent_id → open engine calls`), plus `record_llm_call` (emits
  `llm.generation` GenAI-semconv spans), `reap`, and `parse_otlp_headers`.
- `config.py`: added `otel_enabled` (default `False`), `otel_otlp_endpoint`,
  `otel_otlp_headers`, `otel_service_name`, `otel_sampling_ratio`,
  `otel_llm_capture_content` (default `True`), `otel_llm_capture_max_chars`.
- `app.py`: init tracing in lifespan after `configure_logging`; start a
  `_run_span_reaper` background task (mirrors `_run_orphan_sweeper`, TTL
  1200s); flush/close the provider on shutdown; mount `/metrics` via
  `prometheus_client.make_asgi_app()`.
- `ws/handler.py`: `_apply_lifecycle_to_trace` maps lifecycle frames to
  span transitions (root closes on `handler_finished`); `start_request`
  called per agent at `message_received`; `request_id` bound to
  contextvars around lifecycle handling.
- `llm_gateway/reverse_proxy.py`: `_correlate_llm` helper calls
  `record_llm_call` on all three response paths (error / SSE / non-SSE)
  and stamps the resolved `room_id` onto the (previously unused)
  `LLMGatewayUsage.room_id` column.
- `observability/logging.py`: JSON renderer in prod / console in dev,
  plus a best-effort OTEL-active-span processor.
- `pyproject.toml`: `opentelemetry-sdk` + `opentelemetry-exporter-otlp-proto-http`
  added to `server` and `dev` extras (already in the lock transitively, so
  no lock churn).
- Tests: `test_observability_tracing.py`, `test_ws_handler_tracing.py`,
  `test_logging_correlation.py`, `test_llm_gateway_tracing.py`.

## Decisions

- **Cluster-reconstruction (approach "i") over distributed propagation
  (ii).** Options weighed: (i) cluster rebuilds spans from the lifecycle
  frames the agent already emits; (ii) agent runs its own OTEL SDK and
  propagates trace context. (ii) was rejected because its only real
  upside — fine-grained native spans — is nullified by the external CLI
  engines (codex/claude-code/gemini) being opaque subprocesses: you'd see
  no more than the same four lifecycle events, while paying for OTEL
  deploy + key distribution + N export points across the fleet. The user
  explicitly excluded (ii) entirely (including the openhands carve-out).
- **In-process span registry over deterministic `trace_id = f(request_id)`.**
  Because everything emits from one process (cluster), emitter decoupling
  — the only motivation for deterministic id derivation — isn't needed, so
  a plain registry with standard SDK parent contexts is simpler.
- **In-flight map for gateway↔request correlation.** The proxy only knows
  `agent_id` (from the caller token), never room/request; propagating a
  header through the external CLI is impossible. `RoomHandlerSupervisor`
  serializes handlers per room, so a single active engine call per agent
  is the common case and correlation is reliable; >1 active is flagged
  `ambiguous` (room_id left null) rather than mis-attributed.
- **Prompt capture on by default, Langfuse deploy out of scope** (user
  directives). Mitigated by `otel_enabled` defaulting off and the
  `otel_llm_capture_content` toggle + truncation.
- **Assumptions to revisit if violated**: agents mostly process one room
  at a time (else more `ambiguous` calls); the SSE path keeps buffering
  the full body (a future chunk-by-chunk relay would break completion
  capture); Langfuse's exact input/output attribute keys (GenAI semconv
  vs OpenInference) should be verified on first real ingestion.

## Result

- 1034 cluster tests pass (23 new); ruff clean; `/metrics` returns 200 with
  the counters; production startup logs now emit JSON. Default boot is
  unchanged (tracing is a no-op until `ANYGARDEN_OTEL_ENABLED` + an OTLP
  endpoint are set).
- No schema change; `LLMGatewayUsage.room_id` now populated when a call
  correlates to a single in-flight request.
- Agent/machine packages untouched. (A pre-existing cross-suite test
  isolation issue makes `agent`+`machine` fail when run in one pytest
  session; each passes alone — unrelated to this change.)
- Pending/out of scope: Langfuse instance deployment, optional
  `LLMGatewayUsage.request_id` column, `/metrics` auth.
