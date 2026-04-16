# fix(ws): enforce single connection per participant (#79)

- Commit: `ed6c7bc` (ed6c7bc8544dc9d66401ca7418787647c013ec1b)
- Author: Changyong Um
- Date: 2026-04-17T00:43:45+09:00
- PR: #79

## Situation

`doorae-machine` reconciles `desired_state=running` agents by spawning their CLI processes; users sometimes also launch the same `doorae-agent` from a shell with the same `DOORAE_TOKEN`. Both clients then opened WS connections that resolved to the same `participant_id`, but `ConnectionManager.subscribe()` simply appended each `_Subscription` to `self._rooms[room_id]` without dropping the prior one. Every broadcast fanned out to both sockets, both clients passed `should_respond`, and both called the LLM — producing two identical replies for DMs/mentions and two `[ROOM_QUERY]` forwards (which then collected double responses and emitted two `[취합 결과]` cards). The `representative_agent_id` guard added in #61 only stopped *different* agents from racing each other, not multi-instance of the same agent.

## Task

- Make `ConnectionManager` enforce one live subscription per `participant_id` so the freshest connection wins.
- Close the displaced socket cleanly so its client knows the session was preempted (code `4040` "superseded").
- Don't let a flaky old socket's `close()` failure abort the new subscribe — the new client must come live regardless.
- Don't change the broadcast/`unsubscribe` contracts; downstream `PresenceService` and the WS handler should keep working unchanged.

## Action

- `packages/cluster/doorae/ws/manager.py:67` — `subscribe()` now reads any existing `_by_participant[participant_id]`, evicts it from `self._rooms[old.room_id]` (pruning empty rooms), then installs the new sub. The displaced socket is closed *outside* the lock with `code=4040 reason="superseded"`; a `try/except Exception` swallows close failures so a half-dead old socket can't block the new client.
- `packages/cluster/tests/test_ws_manager_single_session.py` (new, +151) — four `_RecordingWS` doubles cover the contract:
  - `test_second_subscribe_supersedes_first`: old socket closed `(4040, "superseded")` and a follow-up `broadcast` only reaches the new socket.
  - `test_supersede_across_different_rooms`: reconnect from a different room evicts and prunes the old room's entry; broadcasting to the old room is a no-op.
  - `test_supersede_swallows_close_error`: a `_RaisingCloseWS` whose `close` raises still leaves the manager in a consistent state with the new socket live.
  - `test_distinct_participants_coexist`: distinct `participant_id`s never trigger eviction (sanity).

## Decisions

- **Server-side fix vs SDK fix.** Considered: (a) make the agent SDK refuse a second `subscribe` if one is already live, (b) make the cluster do single-session enforcement. Picked (b) because the bug surfaces *because* something outside the SDK (machine reconcile + manual launch) opened a second process — fixing it in the SDK assumes both copies use the same SDK build, while the cluster guard works for *any* client (including the new TS runtime from #73).
- **Evict + close vs reject the new connection.** Rejecting the new connection would mean a restarted process can't reclaim its session, which is the common case (old process crashed, new process tries to take over). "Newest wins" matches typical heartbeat-style semantics and keeps `doorae-machine` reconcile self-healing.
- **Close code 4040.** WebSocket close codes 4000-4999 are application-defined. 4040 was chosen as a memorable, unused-in-this-codebase value; reason string `"superseded"` is the SDK-readable signal. Not standardised in WebSocket spec — fine since both client and server are ours.
- **Best-effort close (`try/except Exception`).** A flaky/half-closed old socket throwing on `close()` must not fail the new client's subscribe. We only need the old socket to *stop receiving frames*, which the eviction from `_rooms` already guarantees; the `close()` is a courtesy signal. Trade-off: the old client may see a broken pipe instead of a clean 4040 frame, which is acceptable.
- **Lock discipline preserved.** Eviction stays inside `self._lock` (mutating `_rooms`/`_by_participant`); the `await ws.close(...)` runs *after* releasing the lock, mirroring the existing `presence.publish` pattern in this file. Avoids holding the lock across an `await` that talks to ASGI send.
- **Out of scope (rejected for this PR):** SDK behavior on receiving close 4040. Currently the SDK reconnects which, if the old process is still alive, can ping-pong (each new connect supersedes the previous). The PR description on #79 calls this out as a follow-up — in practice the racing scenario is two-sided machine reconcile, which converges quickly. If observed in the wild, the SDK should treat `4040` as terminal-no-retry.

## Result

- New behavior: when the same `participant_id` connects twice, the older WS is force-closed and only the new one receives broadcasts. Single-instance scenarios are unaffected.
- Test suite: cluster `uv run pytest` passes 389 (1 deselected) including the four new cases; `ruff check doorae/ws/manager.py tests/test_ws_manager_single_session.py` is clean.
- Operationally verified during the session that triggered this fix: with double `doorae-agent` processes a DM to `@agent01-claude` produced two identical replies; after the patch (and culling the duplicate process so no fresh re-spawn occurred mid-test), a single `PONG-SINGLE` reply confirms the duplicate fan-out is gone.
- Pending: SDK-side handling of close code 4040 to avoid potential ping-pong if both processes stay alive long enough to keep reconnecting; tracked separately in the #79 description.
