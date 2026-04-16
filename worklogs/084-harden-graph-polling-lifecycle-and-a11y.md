# fix(topology): harden graph polling lifecycle and a11y (#84)

- Commit: `6ab3b8f` (6ab3b8fbf3d11c65f5b77ebf1628f4fc2457323a)
- Author: Changyong Um
- Date: 2026-04-17T03:23:26+09:00
- PR: #88

## Situation

PR #88 introduced 5-second polling on the topology graph to drive the
active-typing pulse on `RoomNode`. Codex review flagged two lifecycle
defects and two recommendations against the polling hook
(`useGraphData`) and the `RoomNode` component: the interval callback
could abort its own in-flight fetch on every tick (livelock on slow
networks), the tab-return handler waited up to `pollInterval` before
refreshing, the typing state was only announced visually, and the
polling hook had no unit coverage.

## Task

- Remove the polling livelock without changing the public shape of
  `useGraphData` (topology page wiring unchanged).
- Make hidden→visible transitions refresh immediately before rearming
  the interval.
- Surface `is_typing` through the `RoomNode` accessible name and hover
  tooltip so assistive tech users get the signal.
- Codify the polling contract with a vitest smoke suite so regressions
  on the six targeted behaviors fail loudly.
- Keep the Agent pulse CSS tokenization out of this fixup (nit for a
  follow-up issue).

## Action

- `useGraphData.ts`: added `loadingRef` mirror of `loading` state,
  set/cleared at the fetch start and finally blocks; interval tick
  returns early when `loadingRef.current` is true so slow fetches
  finish instead of being aborted. In the `visibilitychange` handler
  the visible branch now calls `refresh()` before `start()` so the
  tab-return refresh is immediate.
- `RoomNode.tsx`: computed `ariaLabel` and `titleText` off `isTyping`;
  active rooms read `Room #foo, N participants, typing active` with
  `title` `#foo · N · typing`. Inactive rooms keep the original copy.
- `RoomNode.test.tsx`: added a fifth case asserting `aria-label`
  contains `typing active` and `title` contains `typing` when
  `is_typing=true`.
- `useGraphData.test.ts` (new): seven tests using `vi.useFakeTimers()`
  + stubbed `fetch`. Covers single fetch on mount + second fetch at
  `pollInterval`, no polling when `pollInterval` is `undefined` or
  `<= 0`, hidden-tab suspension, immediate refresh on hidden→visible
  transition, unmount teardown of `clearInterval` and
  `visibilitychange` listener, and the livelock guard — a
  prototype-level `AbortController.prototype.abort` wrapper tracks
  that zero aborts occur while four 100ms ticks fire during an
  in-flight 500ms fetch.

## Decisions

- **Option A (loadingRef guard) vs Option B (separate non-aborting
  timer path)**: the feedback document suggested A as simpler and
  that held up — B would have fanned the fetch logic across two
  effects (initial/manual vs polling) and duplicated the header,
  ETag, and error-handling code. A keeps `refresh()` as the single
  source of truth and adds two lines of mirror state plus one early
  return in the interval callback. The mirror `ref` pattern avoids
  re-subscribing timers when `loading` flips.
- **Rejected**: spying on every spawned `AbortController` instance
  (via `vi.stubGlobal('AbortController', Subclass)` with per-instance
  `vi.spyOn(this, 'abort')`). Vitest 2.x strict typings reject
  `spyOn(this, 'abort')` on a subclass instance. Patching
  `AbortController.prototype.abort` with a counter is equivalent in
  assertion power and type-clean.
- **A11y copy**: English `typing active` to stay consistent with the
  rest of the topology labels which are all English (`Room #foo, N
  participants`, agent state labels). The feedback allowed either
  phrasing; consistency wins.
- **Scope**: Agent pulse CSS tokenization (shared keyframes between
  `AgentNode.css` and `RoomNode.css`) was explicitly deferred to a
  follow-up issue per the feedback. Not touched here.
- **Assumption**: the backend responds in <`pollInterval` under
  normal conditions, so the livelock guard is defensive rather than
  hot-path. If the guard ever starts dropping every other tick in
  production, the right response is lengthening `pollInterval` on
  the caller side rather than reinstating abort-on-tick.

## Result

- Polling no longer cascades into aborts when responses are slow:
  tick-while-loading now no-ops.
- Tab return refreshes immediately, observable via the new
  `refreshes immediately on hidden→visible transition` test.
- `RoomNode` with `is_typing=true` announces the state to assistive
  tech and hover tooltips.
- Frontend test suite grew from 88 → 100 tests across 10 → 11 files
  (7 new `useGraphData` cases, 1 new `RoomNode` case); all passing.
  `npm run build` (tsc + vite) green.
- Pending: Agent pulse CSS consolidation — to be filed as a
  standalone issue and noted on PR #88.
