# fix(rooms): unify participant membership + JoinRoomOut broadcast (#50) (#52)

- Commit: `a10904e` (a10904e2f75f37cf24f56e87c620595165e9408f)
- Author: Changyong Um
- Date: 2026-04-15T21:59:38+09:00
- PR: #52

## Situation

Three code paths inserted `Participant` rows independently: REST
`add_participant` in the rooms router, sub-room creation in the rooms
service, and the `#room` auto-join inside `ws/handler.py`. The three paths
had drifted — only some of them pushed a `JoinRoomOut` WS frame after the
insert. In particular, the handler's auto-join created the DB membership
for the representative agent but never broadcast a subscribe-trigger
frame. The agent was therefore a member on paper but never actually
subscribed over WebSocket, so the subsequent `[ROOM_QUERY]` broadcast
silently dropped and forwarding never happened. This was the immediate
bug behind issue #50.

## Task

- Collapse the three Participant-insert paths onto a single pair of
  helpers so every path has the same DB + WS behavior.
- Establish the invariant that a new `Participant` row always coincides
  with a subscribe-trigger `JoinRoomOut` frame on the joining agent's
  other sessions.
- Keep the existing idempotency semantics — joining a room twice must
  not create duplicate rows or duplicate frames.
- Improve reach of the sub-room notification for agents that were not
  parent-subscribers.

## Action

- New module `packages/cluster/doorae/rooms/membership.py` (+135 lines)
  exposes the unified pair of helpers, including `ensure_agent_in_room`
  which is idempotent at the DB layer and always pushes a `JoinRoomOut`
  through the agent's other WS sessions.
- `packages/cluster/doorae/rooms/router.py` (-99/+… refactor) routes
  REST `add_participant` through the shared helper instead of talking
  to the session directly.
- `packages/cluster/doorae/rooms/service.py` sub-room creation path
  switches from a parent-room broadcast to a targeted `send_to` via the
  same helper, so agents who weren't parent-subscribers still learn
  about the new membership.
- `packages/cluster/doorae/ws/handler.py` `#room` auto-join path now
  calls `ensure_agent_in_room`, which supplies the previously-missing
  `JoinRoomOut` frame.

Tests
- `packages/cluster/tests/test_membership.py` (new, 218 lines) exercises
  the helpers: idempotent insert, frame fanout to other sessions, and
  the no-op case when the row already exists.
- `packages/cluster/tests/test_ws_handler.py` (new, 104 lines) locks in
  the handler-path regression: `#room` auto-join now produces both the
  DB row and the `JoinRoomOut` frame, restoring the subscribe trigger
  that `[ROOM_QUERY]` forwarding depends on.

## Result

The three Participant-insert paths now share one code path, and the
"new Participant row ↔ JoinRoomOut frame" invariant is enforced in one
place instead of being re-implemented (and occasionally missed) at each
call site. The immediate `[ROOM_QUERY]` forwarding regression from
issue #50 is fixed: the representative agent reliably subscribes to the
source room after auto-join, so downstream broadcasts reach it.
Sub-room notifications now target the affected agents directly rather
than relying on parent-room subscription. No behavior change for
already-member idempotent inserts.
