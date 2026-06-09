# feat(observability): Phase 1 — consume the instrumentation data we already have (#425)

- Commit: `ec983a3` (ec983a3 on branch feat/425-instrumentation-phase1)
- Author: Changyong Um
- Date: 2026-06-09
- PR: #425

## Situation

An instrumentation assessment (`docs/llm-instrumentation-state.html`) found that
anygarden's observability data is already rich — LifecycleFrames carry
`outcome`/`duration_ms`/`engine` to the cluster, and ActivityLog groups a turn by
`request_id` — but the *consumption* end was empty. ActivityPanel ignored the
authoritative `duration_ms`/`engine`/`error` and recomputed duration from row
timestamps; 7 of 12 Prometheus metrics were dead and none counted turns/latency;
`request_id` was only bound to logs inside the LifecycleFrame branch; and Langfuse
had no per-room grouping. Phase 1 is the gateway-free, low-risk "last mile".

## Task

- Surface the authoritative ActivityLog fields in the UI and fix the #422
  regression where a failed turn (whose error notice is itself a `response_sent`)
  was mislabelled 'responded'.
- Add turn-outcome and engine-latency Prometheus metrics from the existing
  LifecycleFrame data, independent of OTEL.
- Group a room's turns in Langfuse and mark `rejected` as an error.
- Bind `request_id`/`room_id` to logs across the whole turn.
- Constraints: gateway-free, no migration, bounded metric labels, no behaviour
  change to the request path.

## Action

- `observability/metrics.py`: import `Histogram`; add `agent_turns_total{outcome}`
  Counter and `engine_call_duration_ms{engine,outcome}` Histogram (buckets
  100–300000 ms). Labels deliberately exclude agent_id/room_id (cardinality).
- `ws/handler.py`: new `_apply_lifecycle_to_metrics(frame)` (best-effort) called
  next to `_apply_lifecycle_to_trace` in the LifecycleFrame dispatch —
  `engine_call_finished`→observe histogram, `handler_finished`→inc counter. Added
  `clear_contextvars()` + `bind_contextvars(room_id=…)` at the receive-loop top and
  `bind_contextvars(message_id=msg.id)` after message persist.
- `observability/tracing.py`: stamp `langfuse.session.id = room_id` in
  `start_request` (root) and `record_llm_call` (generation); add `rejected` to the
  error-outcome set in `_end`.
- `frontend/.../ActivityPanel.tsx`: extract `finalOutcome`/`durationMs`/`engine`/
  `roomId`/`error` in `splitLogs`; new exported `turnLabel`/`turnDotClass` prefer the
  authoritative `handler_finished.details.outcome`; header shows engine + authoritative
  duration; expanded rows show per-event `eventDetail` + room + error.
- Tests: `tests/test_observability_phase1.py` (7) and `ActivityPanel.test.ts` (+4).

## Decisions

- **Metric labels: `{outcome}` for the turn counter, not `{outcome,engine}` as the
  roadmap suggested.** The `engine` field rides only on `engine_call_*` frames, not on
  `handler_finished`; adding it to the turn counter would need per-request engine
  state. Rejected that statefulness — the per-engine latency breakdown already lives
  on the histogram (observed at `engine_call_finished`, where engine is present), so
  the split is: counter = outcome rates, histogram = engine latency.
- **Metrics in a dedicated `_apply_lifecycle_to_metrics`, not inside
  `_apply_lifecycle_to_trace`.** The trace hook early-returns when tracing is disabled;
  metrics must count regardless, so they get their own function and call site. Rejected
  a periodic ActivityLog aggregator (new polling loop, double-count risk) — counters
  belong at event time, which already exists.
- **Log correlation key on the SendFrame path: `room_id` (+`message_id`), not a single
  `request_id`.** A user send fans out to N per-agent request_ids; binding one would
  mislabel the other N-1 agents' logs. Per-agent `request_id` is already bound in the
  LifecycleFrame branch.
- **UI outcome from `handler_finished.details.outcome`, heuristic kept as fallback.**
  The legacy `outcome`/`deriveOutcome` stays (existing tests + pre-outcome rows); the
  authoritative field only overrides display.
- Assumption: `handler_finished` fires exactly once per turn (the #420/#422
  invariant). If broken, counts drift.

## Result

- cluster suite 1048 passed (7 new); ruff clean; frontend 8 tests pass; `tsc -b` clean.
- `/metrics` now exposes `anygarden_agent_turns_total` and
  `anygarden_engine_call_duration_ms`; Langfuse groups a room's turns by session;
  rejected turns read as errors; ActivityPanel shows engine/real-duration/error and
  labels failed turns correctly.
- No migration, no wire/protocol change, gateway-free.
- Pending: Phase 2 (orphan metric + dead-gauge revival, response_sent span event,
  DB-orphan↔span bridge, `/rooms/{id}/activity`, room_id first-class column) and
  Phase 3 (A→B causal links, room-flow view, optional per-engine LLM detail).
