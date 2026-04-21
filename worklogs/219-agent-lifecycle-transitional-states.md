# feat(agent,machine,frontend): surface starting/stopping transitional states (#219)

- Commit: `4e49eda` (4e49eda786bbb76e43fdf9dfdb3f1f2f5d826262)
- Author: Changyong Um
- Date: 2026-04-21T17:30:41+09:00
- PR: #219

## Situation

Admins clicked Start/Stop on the Machine page and got no feedback: the
button didn't change, the badge kept saying `running`, and they had to
hard-refresh to find out whether anything happened. A quick fix instinct
was "make it WebSocket based", but the real bottleneck wasn't the
transport — it was the data. The machine daemon only ever emitted
`actual_state="running"` on a 30-second periodic cadence, the protocol
didn't even accept a `stopping` value, and the cluster stop endpoint
only updated `desired_state` so the next GET returned the same
pre-click state. Between the 500 ms client-side `setTimeout` and the
30 s daemon cadence, the transition window was a black hole.

## Task

- Make the three layers involved (machine daemon → cluster → frontend)
  each carry the transitional state explicitly instead of silently
  skipping over it.
- Keep the change bounded: no WebSocket broadcast, no optimistic
  updates, no churn to the generation/lock machinery that keeps spawn
  reconciliation race-free (#183).
- Preserve the declarative reconcile model — `desired_state` stays the
  admin intent, `actual_state` stays machine-reported truth. The only
  new shape is a short-lived "in-flight" annotation that both sides
  can agree on.

## Action

- Machine daemon: added `stopping` to the `AgentActual` Literal and a
  `_transitional_states: dict[str, str]` map on `MachineDaemon`
  (`packages/machine/doorae_machine/daemon.py`). `_reconcile_agent`
  sets `starting` right before dispatching the spawn task and
  `stopping` right before `spawner.kill`, in both cases calling
  `_report_actual_state()` immediately so the server sees the
  transition within sub-second. `_report_actual_state` now merges the
  spawner's running list with the transitional map (stopping wins
  over the still-alive process; starting stands alone when no process
  exists yet) and prunes stale entries after
  `TRANSITIONAL_LEAK_GRACE = 60 s` as a safety net. Cleanup in
  `_on_agent_stopped` / `_on_agent_crashed` / end of
  `_request_token_and_spawn`.
- Cluster: `AgentLifecycle.request_stop`
  (`packages/cluster/doorae/scheduler/lifecycle.py`) now flips
  `actual_state` to `stopping` atomically with `desired_state=stopped`
  for placed agents, and short-circuits orphans (no
  `placed_on_machine_id`) to `stopped` so the absent-from-report
  convergence loop isn't waiting for a daemon that will never report.
  The existing absent-from-report branch in
  `handle_report_actual_state` already converges `stopping` to
  `stopped` when the machine drops the agent from its next frame.
- Frontend: `useAgents`
  (`packages/cluster/frontend/src/hooks/useAgents.ts`) exposes
  `pendingIds` for in-flight mutations and auto-polls `/api/v1/agents`
  every 1.5 s whenever any agent is in a transitional state
  (`pending`/`starting`/`stopping`). Stuck-pending rows with
  `last_crash_reason` are filtered so the poll doesn't run forever on
  sticky failures like "no suitable machine". `AdminMachines.tsx`
  swaps the Play/Square button for a disabled `Loader2` spinner while
  the POST is in flight, drops the 500 ms `setTimeout(fetchDetail)`
  hack, and mirrors the poll cadence into the per-machine detail
  fetch so the machine-scoped badge list keeps pace with the global
  list.
- Tests: `test_protocol_frames::TestAgentActual::test_stopping_state`
  covers the new Literal.
  `test_daemon::TestTransitionalStatesReport` and
  `::TestTransitionalStatesLifecycle` cover report merging
  (running/starting/stopping cases), reconcile-level dispatch
  ordering, and callback cleanup — 6 new tests.
  `test_lifecycle` gains 4 cases: running→stopping, starting→stopping,
  orphan→stopped, and stopping→stopped via absent report.
  `test_agents_api::TestAgentStopEndpoint` covers the stop endpoint
  surfacing `stopping` in both the response and a subsequent DB read.

## Result

- 283 machine tests pass (6 new), 680 cluster tests pass (5 new),
  frontend `npm run build` clean (tsc + vite 8.83 s). One pre-existing
  `test_openai::test_integrate_registers_handler` failure reproduced
  on `main` — OPENAI_API_KEY env dependency, unrelated.
- Expected user-visible effect: clicking Start → button spinner
  immediate, `starting` badge within sub-second (machine daemon
  fast-path report), `running` badge when the process comes up.
  Clicking Stop → button spinner immediate, `stopping` badge within
  one 1.5 s poll tick (cluster endpoint writes it atomically),
  `stopped` on the machine's next report (absent-from-report
  convergence).
- Deliberately out of scope (deferred as follow-ups):
  `D` — WebSocket broadcast of agent lifecycle events for multi-admin
  live sync; `optimistic update` — rolled out only after A/B/C
  validated the transitional path in production. The plan doc at
  `.tmp/plan-219-agent-lifecycle-transitional-states.md` records the
  decision rationale.
