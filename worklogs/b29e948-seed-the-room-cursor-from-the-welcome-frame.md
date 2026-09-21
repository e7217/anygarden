# fix(agent): seed the room cursor from the welcome frame

- Commit: `b29e948`
- Author: Changyong Um
- Date: 2026-09-21
- PR: —

## Situation

Durable room cursors (#640) persist `room_id -> seq` so a respawned agent reconnects with `since_seq` and the server replays what it missed. But the cursor is only written when a frame arrives, so it does not exist until the agent has actually received a message in that room. The live re-test on 2026-09-21 hit exactly that hole: the node had just restarted, `테스트에이전트01` had received nothing yet, so stopping it, sending a mention, and starting it again still delivered nothing — no cursor file existed, the agent connected at `since_seq=0`, and the server replays nothing at 0.

## Task

- Give every room a replay baseline as soon as the agent connects, not only after its first received message.
- Never let that baseline clobber a real cursor — the gap between a stored cursor and the room head is precisely what must be replayed.
- Do not turn a first-ever join into a replay of the room's whole history.

## Action

- `packages/cluster/anygarden/ws/protocol.py`: `WelcomeOut.last_seq` (default 0), documented as the room's head seq at welcome time.
- `packages/cluster/anygarden/ws/handler.py`: computes `max(Message.seq)` for the room inside the existing welcome-time session and stamps it on the frame; adds the `func` and `Message` imports.
- `packages/agent/anygarden_agent/client.py`: `_seed_cursor()` adopts `last_seq` only when the room has no cursor (`> 0` guard on both sides), persisting through the same `state_dir`; the welcome branch calls it.
- Tests: `packages/cluster/tests/test_ws_handler.py::TestWelcomeRoomSeq` (welcome reports 0 on an empty room, 1 after a message) and `packages/agent/tests/test_room_cursor.py::TestWelcomeSeedsTheCursor` (seeds a missing cursor, never overwrites a persisted one).

## Decisions

- The baseline is taken at connect time rather than 0, so a first-ever join still does not replay history. The trade-off is that messages sent before an agent's very first connection to a room are not delivered — that window is unavoidable without a server-side pending-wake table, which was already rejected for #640.
- Seeding happens on welcome rather than on `join_room`, because the welcome frame is where the server already reports per-room state and the client already caches it; no extra REST round trip.
- `_seed_cursor` refuses to overwrite any cursor `> 0`. Without that guard, a reconnect would jump the cursor to the head and silently swallow the very gap the feature exists to replay.
- `last_seq` defaults to 0, so an older agent ignores the field and an older server simply never seeds — both degrade to the pre-#640 behavior.

## Result

- `cd packages/agent && uv run pytest`: 585 passed; `cd packages/cluster && uv run pytest`: 1773 passed, 1 deselected. All four new tests failed before the change.
- Lint on the changed lines is clean (the files carry pre-existing findings elsewhere).
- Live verification of the stop → mention → start cycle is still pending; it needs this on the node.
