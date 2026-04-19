# fix(cluster): silence welcome-race disconnect traceback in ws_room (#176)

- Commit: `c765c6a`
- Author: Changyong Um
- Date: 2026-04-19
- PR: #176

## Situation

`packages/cluster/doorae/ws/handler.py:264` wrote the welcome
frame (`websocket.send_text(welcome.model_dump_json())`) outside
the handler's main try/except. The main receive loop's
`except WebSocketDisconnect` (line 663) covered everything after
subscribe, but the welcome send was unguarded. In dev this reliably
produced noisy tracebacks every time Vite HMR reloaded a frontend
file or React StrictMode double-mounted an effect: the client-side
WS was already closed in the microseconds between accept and the
first server write, uvicorn saw `ClientDisconnected`, starlette
translated it to `WebSocketDisconnect(code=1006)`, and the handler
propagated the full stack.

No functional impact — the client was gone, there was nothing to
deliver — but the traceback buried real errors in the dev log.

## Task

- Catch the disconnect that races the welcome send without
  swallowing unrelated exceptions.
- Avoid any cleanup there — the failure point is before
  `manager.subscribe` runs and before the guest gauge is
  incremented, so early return is safe.
- Keep the fix narrow: the welcome send specifically, not the rest
  of the handler.

## Action

`packages/cluster/doorae/ws/handler.py:264`: wrap the single
welcome `send_text` in `try/except WebSocketDisconnect`. On the
race, log `ws.disconnected_before_welcome` at info level with
`room_id` and `participant_id`, then `return` directly from the
handler. Added an inline block comment explaining why the handler's
existing main-loop try/except doesn't cover this path and why
nothing needs cleaning up at this point.

No other changes: the `WelcomeOut` model, the subscribe flow, the
main receive loop, the `finally` cleanup stay identical.

## Decisions

Considered alternatives:

- **Move the `try:` block that guards the main loop up to before
  the welcome send.** Rejected — that `try:` is entered only after
  `is_guest_session` / `guest_gauge_incremented` have been set and
  `manager.subscribe` has returned. Pushing it earlier would force
  the `finally` (which calls `manager.unsubscribe` and decrements
  the guest gauge) to run even when no subscribe ever happened.
  The guest-gauge flag already defends against that, but
  `unsubscribe` would still be called with a participant that never
  subscribed; the manager is probably idempotent, but the narrow
  early-return is the minimum change that doesn't rely on that
  assumption.
- **Catch `ClientDisconnected` directly** instead of
  `WebSocketDisconnect`. Rejected — starlette already translates
  `ClientDisconnected` into `WebSocketDisconnect(1006)` before the
  handler sees it (see starlette/websockets.py:89). Catching the
  starlette-level exception is the correct layer.
- **Catch a broader `Exception`.** Rejected — would mask real
  transport errors or schema bugs. Only the disconnect pattern is
  expected; anything else should surface.

Assumption: every Vite HMR / StrictMode race produces
`WebSocketDisconnect` (not a lower-level `OSError`, not an
`asyncio.CancelledError`). If a different platform/loader variant
surfaces a different exception type, the fix will need to widen —
but the reference traceback for this report ends in
`WebSocketDisconnect` cleanly.

## Result

- `cd packages/cluster && uv run pytest -q` — 600 passed
  (existing WS handler tests still green; no new tests added
  because the race is timing-specific and hard to reproduce
  deterministically in unit tests).
- Next dev server start should show a single `ws.connected` /
  `ws.disconnected` log pair per HMR reconnect, no more full
  traceback between them.
- Scope intentionally narrow. If later profiling shows other
  similar races (subscribe disconnects, replay-loop disconnects),
  they can be addressed in follow-ups — this change doesn't
  preempt that.
