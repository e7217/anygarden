# feat(topology): highlight rooms with active typing (#84)

- Commit: `b11b52d` (b11b52dab350fc1d903b27c6b2f84c0faef024b4)
- Author: Changyong Um
- Date: 2026-04-17T02:59:50+09:00
- PR: #84

## Situation

`/topology` Room nodes were static — there was no way to glance at the
graph and tell which conversations were active right now. Real-time
typing state already existed server-side in
`packages/cluster/doorae/orchestration/rules.py::TypingTracker`
(in-memory singleton, 5 s TTL, populated by the WS handler at
`packages/cluster/doorae/ws/handler.py:78,221,430`), but the
information stopped at the chat surface and never reached the
topology view.

## Task

- Surface "is anyone typing in this room?" on the topology graph
  without redesigning the WS layer.
- Reach within ~5 s of a typing event with no extra infrastructure.
- Stay inside the existing `GET /api/v1/graph` ETag/`max-age=5` cache
  contract — polling must be cheap (304-friendly) yet still flip when
  state actually changes.
- Match the visual language already established by #82 (hover dim) and
  #83 (AgentNode running pulse) and respect
  `prefers-reduced-motion: reduce`.
- No new endpoints, no new dependencies, no permission-model changes.

## Action

Backend (`packages/cluster/doorae/api/v1/graph.py`):
- Added `_is_typing_for(typing_tracker, room_id)` that defends against
  a missing `app.state.typing_tracker` (test apps that bypass the
  lifespan) and degrades to `False`. `TypingTracker.get_typing`
  already drops stale TTL entries, so no extra filtering is needed.
- Threaded the tracker into `_build_global_graph` and
  `_build_personal_graph` and emit `is_typing: bool` on every Room
  node's `data`. `_etag_for` (lines 577-609) hashes the full node
  `data` dict, so the new field participates in cache invalidation
  with no extra wiring.
- Endpoint reads the tracker via
  `getattr(request.app.state, "typing_tracker", None)`.

Backend tests (`packages/cluster/tests/test_graph_api.py`):
- Wired a `TypingTracker(ttl_seconds=5.0)` into the `graph_env`
  fixture (the lifespan is skipped under `ASGITransport` in this
  fixture; the production app initializes the same singleton at
  `packages/cluster/doorae/app.py:281-282`).
- Added `TestRoomTypingFlag` with three cases:
  `test_is_typing_false_when_no_one_typing`,
  `test_is_typing_true_when_tracker_has_active_entry` (asserts only
  the targeted room flips), and
  `test_typing_change_invalidates_etag` (pins the cache contract so a
  future refactor of `_etag_for` can't silently break the polling
  loop).

Frontend types (`packages/cluster/frontend/src/components/topology/types.ts`):
- Added `is_typing?: boolean` to `RoomNodeData` (optional for
  forward/backward compatibility with cached payloads).

Frontend hook (`packages/cluster/frontend/src/components/topology/useGraphData.ts`):
- New optional `pollInterval?: number` parameter. When set, a
  `useEffect` registers a `setInterval(refresh, pollInterval)` that
  bumps the same `trigger` counter the manual refresh path uses, so
  fetch logic stays single-sourced.
- `visibilitychange` listener pauses polling while
  `document.visibilityState === 'hidden'` and resumes on the next
  visibility event. A defensive in-tick check skips the call even if
  the listener somehow misses an event.

Frontend page (`packages/cluster/frontend/src/pages/TopologyPage.tsx`):
- Switched `useGraphData('auto')` to `useGraphData('auto', 5000)` so
  the typing pulse stays inside the server's `Cache-Control: max-age=5`
  envelope.

Frontend node (`packages/cluster/frontend/src/components/topology/nodes/RoomNode.tsx`
and new `RoomNode.css`):
- New CSS file owns the active-pulse layer:
  `@keyframes topology-room-typing` (1.8 s, box-shadow ring 0 → 4 px,
  alpha 0.35 → 0) toggled via `.room-node--active`. The
  `prefers-reduced-motion: reduce` branch swaps to a static 2 px ring
  so the signal stays legible without animation.
- `RoomNode` reads `data.is_typing` and toggles the className between
  `'room-node'` and `'room-node room-node--active'` while keeping the
  existing inline pill style (border, padding, radius) intact.

Frontend tests (new `packages/cluster/frontend/src/components/topology/nodes/RoomNode.test.tsx`):
- 4 vitest cases cover `is_typing=true`, `is_typing=false`, missing
  flag (forward-compat), and `selected + is_typing` co-existence.

## Decisions

Source: `.tmp/plan-84-room-active-typing-highlight.md` §3.2 weighed four
options (B1 = graph-API field + 5 s polling, B2 = same with 2 s
polling, C1 = dedicated `/topology/activity` endpoint, H1 = user-scoped
WS topic).

What tipped the scale toward B1 (chosen):
- `TypingTracker` is already an `app.state` singleton with O(1)
  `get_typing(room_id)` — surfacing it through the existing graph API
  is a one-line server-side change.
- `useGraphData` had no polling at all (mount + manual refresh only),
  so adding a 5 s heartbeat didn't perturb other consumers.
- The endpoint's existing ETag + `max-age=5` already absorbs the load:
  unchanged ticks 304, only state flips ship a body. The `max-age=5`
  cache directive and the typing TTL of 5 s also produce a predictable
  staleness ceiling (0–5 s lag, matching the UX target).

Rejected and why:
- B2 (2 s polling): UX goal is "this room is alive right now" — 5 s
  resolution is sufficient, and bumping interval is a one-line change
  if needed (YAGNI).
- C1 (separate `/topology/activity` endpoint): adds endpoint
  surface, tests, and auth wiring for a marginal latency win the
  ETag path already covers cheaply.
- H1 (user-scoped WS push): the right long-term answer but blocked on
  designing a user-scoped channel (broadcast scope, permission model)
  — split into follow-up issue #87 so this PR stays scoped.

Assumptions to revisit if violated:
- ETag hash includes full node `data` (verified — `_etag_for` at
  `graph.py:577` hashes the entire dict and a regression test now
  pins this).
- `TypingTracker.get_typing` filters stale entries (verified — see
  `rules.py:155-169`, expired entries are pruned and excluded from
  the returned list).
- Personal scope already restricts visibility, so exposing `is_typing`
  doesn't create a permission leak (no new query plane — only adds a
  bool to rooms the caller can already see).
- Polling load: tab × 5 s × ETag-304 is well within budget. The
  visibility guard prevents background-tab amplification; revisit if
  topology becomes a high-traffic surface.

## Result

- Backend: `cd packages/cluster && uv run pytest -x -v
  tests/test_graph_api.py` — 16 passed (3 new). Full per-package
  regression: `packages/cluster` 392 pass, `packages/machine` 213
  pass, `packages/agent` 131 pass + 1 pre-existing failure unrelated
  to this change (`test_openai.py` requires `OPENAI_API_KEY` env
  var, present on `origin/main` before the patch).
- Frontend: `cd packages/cluster/frontend && npm test` — 92 passed
  (4 new RoomNode cases). `npm run build` (`tsc -b && vite build`)
  succeeds with no type errors.
- `is_typing` now reaches the topology view within 0–5 s of a
  participant starting/stopping typing. Pulse uses the same
  `box-shadow` pattern as #83's AgentNode running pulse so the two
  signals visually rhyme but read as distinct states (agent running
  vs. room actively conversing). Reduced-motion users get a static
  ring with the same color signal.
- Pending (out of scope): real-time (<1 s) reflection — gated on the
  user-scoped WS channel work tracked separately in #87. Once #87
  ships, `is_typing` can be promoted from poll → WS delta with a
  thin replacement PR.
