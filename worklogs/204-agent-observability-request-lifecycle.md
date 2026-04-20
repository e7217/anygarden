# feat(agent,cluster): explicit request lifecycle + orphan sweeper for observability (#204)

- Branch: `feat/204-agent-observability`
- Commits: `78757ab`..`364342e` (10 commits)
- Author: Changyong Um
- Date: 2026-04-20
- Issue: #204

## Situation

Users reported agent responses being intermittent: sometimes fast, sometimes slow, sometimes completely missing. Live instrumentation against `agent01-codex` across five days of traffic surfaced three problems that the existing activity log couldn't isolate.

- **Log noise swamping real signal**. `processing_started` was written to `ActivityLog` on every typing ping (every 2 s for as long as the handler ran). 5 days = 859 `processing_started` rows vs. 43 `response_sent` rows. One burst on 2026-04-19 23:54 emitted **195 typing pings across 388 s with zero `response_sent`** — a hang that was invisible because the event stream contained nothing but identical duplicates.
- **Handler leak**. Another burst on 2026-04-20 11:39 emitted **33 typing pings within 5 seconds**. `asyncio.sleep(2)` makes that physically impossible for a single loop, so the same room had 6–7 concurrent handlers running. Every incoming message was spawning a fresh handler + fresh typing loop without checking whether the prior one had finished.
- **Transport alive ≠ responsive**. `PresenceService` (`packages/cluster/doorae/presence/service.py:93-165`) inferred online-ness from WS keepalive alone. An agent hung inside the codex subprocess still looked "online" to the UI because its WS was still ping-ponging; only the work had stopped.

The combined result: when a user asked why an agent hadn't replied, nothing in the observability layer could distinguish "engine still running", "response produced but dropped on the wire", "handler crashed silently", or "another message stole the slot".

## Task

Replace the implicit, typing-driven state model with an explicit, request-scoped lifecycle. Constraints:

- Every successful turn must resolve as a **single `request_id`-linked chain**: `message_received` → `handler_started` → `engine_call_started` → `engine_call_finished` → `handler_finished` → `response_sent`.
- Three engines (codex, claude-code, gemini) must all emit the chain — the supervisor logic cannot be duplicated across integrations.
- Hang must stop being a silent terminal state: the handler needs a timeout that converts into an explicit `engine_call_finished(outcome=timeout)` event.
- Handler leak must be structurally impossible, not just diagnosed. A second message to the same room while the prior handler holds the lock must be rejected synchronously.
- Cluster needs a backstop for cases the agent can't self-report (crash, reconnect, WS drop): a periodic sweeper that promotes dangling `handler_started` rows to `handler_orphaned`.
- No schema break for legacy rows (including the to-be-removed `processing_started` bursts): they must stay queryable as `request_id=NULL`.
- Agent/cluster deployment order must be independent — shipping one before the other cannot crash the session.

## Action

Delivered in 10 commits on `feat/204-agent-observability`. The phased order mirrors the design's rollout plan (`docs/plans/2026-04-20-agent-observability-design.md` §6): storage first, protocol next, then cluster, then agent, then sweeper, so each step is independently deployable and revertible.

**1. DB schema (`78757ab`)**

- Added `activity_logs.request_id VARCHAR(36) NULL` + `ix_activity_logs_request` index. Alembic revision `026`. Legacy rows keep `request_id=NULL`; the standalone index makes "all events for request X" O(log n).
- Updated `tests/test_migrations.py`'s head-revision assertions from `"025"` to `"026"`.

**2. Protocol (`21ebbc9`, `9d793b8`)**

- New `LifecycleFrame` with `type="lifecycle"`, `request_id`, `room_id`, `event ∈ {handler_started, handler_finished, engine_call_started, engine_call_finished}`, plus optional `outcome ∈ {ok, failed, timeout, cancelled, rejected}`, `duration_ms`, `engine`, `error`.
- Mirrored verbatim on both SDK (`packages/agent/doorae_agent/protocol/frames.py`) and cluster (`packages/cluster/doorae/ws/protocol.py`). `parse_incoming` dispatches `"lifecycle"` on both sides. Four regression tests on the SDK pin the dump/parse contract.

**3. Cluster fan-out + lifecycle receive + noise removal (`8d5cbf4`)**

- New `ConnectionManager.broadcast_tailored(room_id, make_frame)` — per-recipient frame factory. Used to hand each agent its own `metadata.request_id` without the ID leaking onto non-agent subscribers or the stored message row.
- `ws/handler.py`: on a user `SendFrame`, mint `request_id = uuid4()` per target agent (keyed by `Participant.id` so the tailored broadcast can look it up), stamp it on `message_received`, and inject it into each agent's outgoing `MessageOut.metadata`. On an agent `SendFrame`, echo the agent's `metadata.request_id` onto the `response_sent` row.
- New module-level helpers `_lifecycle_details(frame)` and `_persist_lifecycle_event(db, agent_id, frame)`; agent-kind identities writing a `LifecycleFrame` land as `ActivityLog` rows under the propagated `request_id`. Non-agent senders of lifecycle frames are dropped with a warning (not crashed).
- Removed `processing_started` typing-driven writes (`handler.py:781-789` on main). Three unit tests in `test_ws_handler_lifecycle.py` pin the `_persist_lifecycle_event` contract — `handler_started`, `engine_call_finished(timeout)`, `handler_finished(rejected)`.

**4. Agent-side supervisor (`f1774f9`)**

- New `packages/agent/doorae_agent/runtime/handler_wrapper.py:RoomHandlerSupervisor`. Owns three invariants:
  - **Serialization**: `asyncio.Lock` per room. A second `dispatch(...)` while the lock is held emits a single `handler_finished(outcome=rejected, error="room busy with request_id=...")` frame and returns synchronously. No queue.
  - **Lifecycle**: four events per ok turn, three on rejection (only `handler_finished`), four on timeout (`engine_call_finished` carries `outcome=timeout`, `handler_finished` does too).
  - **Timeout**: `asyncio.wait_for(run_engine(), timeout=engine_timeout)`. Default 900 s (15 min); override via `DOORAE_AGENT_ENGINE_TIMEOUT_SEC`. On timeout the user gets `"⚠️ 응답이 타임아웃으로 중단되었습니다."` tagged with the request_id.
- Error messages truncated to 500 chars so a multi-MB stacktrace can't bloat `ActivityLog.details`. `asyncio.CancelledError` is passed through after emitting `outcome=cancelled` events so shutdown semantics stay correct. `request_id=None` (proactive sends) short-circuits the lifecycle emits and bypasses `metadata.request_id` stamping on the reply.
- Six (later seven) unit tests: ok path / timeout path / failed path / rejected path / long-error truncation / no-request-id proactive send / empty-response skip-send.

**5. Client helper (`b0a61c3`)**

- `AgentClient.sendLifecycle(room_id, request_id, event, **details)` — best-effort emit. No-op on `request_id=None` or no active room subscription. Transport errors are swallowed so a broken WS can't cascade into the handler (the original failure mode we're trying to observe).
- Message dispatch already forwards `data["metadata"]` verbatim to handlers, so integrations read `data["metadata"]["request_id"]` directly; no plumbing change needed.

**6. Integration switch (`4a3e1a6`, `166e8d9`, `364342e`)**

- All three integrations (`codex.py`, `claude_code.py`, `gemini_cli.py`) now funnel their post-gating path through `supervisor.dispatch(..., run_engine=...)`. The inline `typing_task + adapter.on_message` block becomes a `run_engine` closure that the supervisor wraps in `wait_for`. `engine_name` is set per integration (`"codex"`, `"claude-code"`, `"gemini"`) so downstream activity queries can filter by engine.
- The `"…typing"` UX pulse survives inside each `run_engine` — it just no longer drives the DB log. Claude Code's `_last_session_id` → `_sessions[room_id]` promotion is preserved inside the closure so turn resumption keeps working.
- One late style fix (`364342e`) dropped a redundant local `import asyncio as _asyncio` the first pass had added to Claude Code's `run_engine`.

**7. Cluster orphan sweeper (`268ea02`)**

- `scheduler/lifecycle.py:sweep_orphaned_requests(session_factory, threshold_sec=1200)`. Single `GROUP BY request_id, agent_id` query with `HAVING sum(handler_started) > 0 AND sum(handler_finished|handler_orphaned) = 0` selects stalled groups; a second per-group lookup fetches `handler_started.details.room_id` for the orphan row. Idempotent by construction — already-orphaned groups are excluded by the `HAVING`.
- `DOORAE_ORPHAN_SWEEPER_INTERVAL_SEC` (default 60) controls cadence; `0` disables (tests). `_run_orphan_sweeper` is wired into the FastAPI lifespan on the same pattern `skill_stale_cron` already used — 15 s warm-up delay, per-iteration exception containment, clean cancel on shutdown.
- Six unit tests: orphan promotion / finished request left alone / idempotent second sweep / young request left alone / NULL-request_id rows ignored / default threshold pin.

## Decisions

Five non-obvious calls, all made during brainstorming (`docs/plans/2026-04-20-agent-observability-design.md`) and confirmed by live-data evidence:

- **Hybrid event emission (option C), not pure agent-emitted or pure cluster-inferred.** Cluster still owns `message_received` and `response_sent` because they mark boundaries the agent never sees authoritatively; agent owns the four handler/engine events because they describe its internal state. One new frame type (`LifecycleFrame`) is enough protocol surface to cover both. Pure cluster-inferred (option B) was rejected because "is the agent hung?" cannot be answered from the cluster's side without the agent telling it; pure agent-emitted (option A) was rejected because duplicating cluster-side message boundaries on the wire serves no one.
- **Cluster-issued `request_id`, per-agent, stamped into `MessageOut.metadata` on a tailored broadcast.** Rejected agent-side `request_id` generation because correlating cluster's `message_received` with the agent's `handler_started` would then require a fuzzy `(room_id, agent_id, timestamp)` join — fragile under the very concurrent-handler bug we're trying to observe. Rejected reusing `MessageOut.seq` as the key because it isn't per-agent; a room with two agents would have both processing the same `seq`. The `broadcast_tailored` helper is new surface but it's 15 lines and keeps non-agent subscribers free of the request_id leak on the on-wire frame.
- **`request_id` as a dedicated column, rest in `details` JSON.** Rejected "put everything in `details`" because the dominant read — "all events for request X" — becomes a JSON-path probe that can't use an index in either SQLite or Postgres. Rejected a separate `request_lifecycle_events` table because the rest of the ecosystem (existing `state_changed`, `start_requested`, `stop_requested`) already lives in `activity_logs` and splitting makes the timeline query two-table. The one indexed column + JSON bag hits the right balance for current query patterns; legacy rows (`request_id=NULL`) coexist naturally.
- **Reject on room-lock contention, not queue.** Observed live handler leak already shows 6–7 concurrent handlers for one room; queuing would turn that into unbounded memory pressure and make `handler_finished` emission ordering ambiguous. Rejection also gives the user a visible signal ("your second message didn't go through; retry") and gives the activity log an explicit `outcome=rejected` — the concurrency bug from the original diagnosis is now first-class observable instead of requiring statistical interpretation of typing-ping timing.
- **Engine timeout 900 s default; sweeper threshold 1200 s.** Codex tool-heavy turns legitimately run 6–8 minutes (see #190); 900 s leaves ~50% slack. The sweeper threshold is 15 + 5 min so the agent's own timeout almost always fires first — the sweeper is a backstop, not the primary mechanism. Tests override `threshold_sec` to exercise the sweeper on seconds-old data.

Assumption that should trigger a revisit: if a future non-codex engine surfaces turns that legitimately exceed 900 s, the `DOORAE_AGENT_ENGINE_TIMEOUT_SEC` override is the escape hatch — but the sweeper threshold (`ORPHAN_THRESHOLD_SEC_DEFAULT`) would need to move in lockstep or the sweeper will race the engine timeout on normal completions.

## Result

End-to-end verification on the local dev cluster:

- **Full regression**: `cluster 625 + agent 249 + machine 258 = 1132 tests` pass. Ruff clean on all files the change touched (residual warnings in `app.py`, `client.py`, `gemini_cli.py` pre-date this branch — confirmed by running ruff on main for the same paths).
- **Frontend build**: `cd packages/cluster/frontend && npm run build` type-checks and builds. This branch ships no frontend changes; the request-grouped Activity view is a deliberate follow-up PR.
- **Protocol compatibility**: `test_protocol_compat.py` retains all six pre-existing assertions plus four new `LifecycleFrame` cases, so the SDK still matches the cluster's wire format on all frame types.
- **Concurrency bug closed structurally**: `test_second_concurrent_dispatch_is_rejected` pins `RoomHandlerSupervisor`'s lock behaviour. No amount of fast retries from the same room can reproduce the "33 pings in 5 s" pattern anymore — a second dispatch emits exactly one `handler_finished(outcome=rejected)` event and returns.
- **Hang becomes a visible event**: timeout path exercised in `test_timeout_path_marks_both_events_and_notifies_user` with a synthetic 0.05 s engine_timeout. The user-facing notice (`"⚠️ 응답이 타임아웃으로 중단되었습니다."`) carries the same `request_id` so the activity trail terminates cleanly.
- **Orphan sweeper**: `sweep_orphaned_requests` returns 1 on a stale `handler_started` row older than threshold, 0 on finished requests, 0 on rows already marked `handler_orphaned`, 0 on below-threshold rows, and 0 on legacy NULL-request_id rows. Default threshold pinned at 1200 s to catch drift from the design.

Out of scope for this branch, intentionally:

- **Activity UI request grouping**. The backend now emits every signal needed to render the 6-event chain in the frontend Activity dialog (`packages/cluster/frontend/src/components/AgentSettingsDialog.*`), but the collapsible per-request view itself is a separate PR — the grouping can land without any further backend churn.
- **Retention/purge for legacy `processing_started` rows**. They stay in `activity_logs` with `request_id=NULL`. A retention job can be added later if the log size becomes a concern; none of the new queries touch them.
- **Postgres compatibility for `room_id` extraction in the sweeper**. Current query uses a second per-group lookup to dodge dialect-specific JSON path syntax. If doorae ever moves off SQLite the two-query sweep becomes a single query with `jsonb_extract_path_text` — trivially revisitable.

Design (`docs/plans/2026-04-20-agent-observability-design.md`) and plan (`.tmp/plan-agent-observability.md`) are intentionally local-only per the user's "plan은 올리지 않지" direction. The issue body and this worklog carry the durable record.
