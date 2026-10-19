# fix(peers): settle ask_peer questions by the peer's turn state instead of a fixed 30-minute deadline (#900)

- Commit: `f82fb5e` (f82fb5ee764811cc8effdb72771cb5f70c266942)
- Author: Changyong Um
- Date: 2026-10-19T15:51:51+09:00
- PR: #900

## Situation

An `ask_peer` question group (`PeerAskGroup`) settled every unfinished question as `timeout (deadline)` once 30 minutes (`DEFAULT_DEADLINE_SECONDS = 1800`) had passed since the first question. A peer that was still working was cut off, and its late answer was ignored. #892 had already changed turns to time out only when idle, so the group had no reason left to cut on total elapsed time. The turn system already guarantees that every turn that starts also ends, and every end passes through `mark_turn_terminal` → `on_turn_terminal`, which settles the question. The one case it does not cover is a turn that never starts, for example when the agent is running but not connected (it crashed and was not restarted, its machine is down, or spawn failed). Such a turn stays `pending` forever.

## Task

- Settle each question by the state of its peer's turn instead of by a fixed deadline.
- Wait while the peer is working, so long tasks are not cut off.
- Give up on a peer whose turn never starts after a configurable start timeout, and tell the caller why.
- Keep a last-resort cap on the whole group (24 hours), with no migration and the same column, index, and environment variable name.
- Never cancel the peer's turn.

## Action

- `packages/cluster/anygarden/orchestration/peer_fanin.py`:
  - `DEFAULT_DEADLINE_SECONDS` 1800 → 86400. Added `DEFAULT_START_TIMEOUT_SECONDS = 900` and `start_timeout_seconds()` (`ANYGARDEN_PEER_ASK_START_TIMEOUT_SEC`), which shares a `_env_seconds` helper with `deadline_seconds()`.
  - Rewrote `_advance_collecting`. A single query joins the non-terminal targets of `collecting` groups to their `AgentTurn` and any active (`leased`/`started`) `AgentTurnAttempt`. Each target is then handled as follows:
    - no turn → `failed (turn_missing)`
    - terminal turn → its real outcome via `_target_outcome` (repairs a missed hook)
    - past the group's `deadline_at` → `timeout (deadline)`
    - turn `pending`/`retrying` with no active attempt, waiting longer than the start timeout → `timeout (peer_unavailable)`
    - anything else (running) → left to the turn system
  - The wait for a `retrying` turn counts from its last attempt's lease or end time (`_last_attempt_times`), not from when the question was asked.
  - Every closed-caller group is still checked for readiness on each tick, as before.
  - `render_results` adds a one-line plain-language note under fan-in-assigned reasons (`peer_unavailable`, `deadline`, `turn_missing`), so the caller can tell the user why a peer has no answer.
- `packages/cluster/anygarden/db/models.py`: updated the `PeerAskGroup` docstring (no schema change).
- `packages/cluster/tests/test_mcp_ask_peer.py`:
  - Added tests for these cases:
    - a never-started peer times out as `peer_unavailable` and the caller is woken, while the peer's turn stays `pending`
    - a leased peer older than 2 hours stays pending
    - a `retrying` peer is timed from its last attempt
    - the group cap times out even a leased peer, with `timeout (deadline)` and the turn still `leased`
    - a terminal turn with a pending row is repaired
    - a missing turn fails with `turn_missing`
    - default and environment-variable values of both limits
  - Updated the old deadline test and the three-peer COL-06 test to use the never-started path.
  - Extended the `render_results` text pin.
- `docs/requirements.md`: COL-06 now states the new behavior: wait while the peer is working, time out after 15 minutes if its turn never starts, and stop at a 24-hour cap.

## Decisions

- **Turn state plus a start timeout plus a 24-hour cap (plan option D)**, over three rejected options:
  - Raising the deadline would still cut off long work and would hold callers on dead peers.
  - Dropping the deadline would never wake a caller whose peer never connects.
  - Extending the deadline on typing activity would repeat the turn system's job, and the in-memory typing state would be lost on restart.
- **The start timeout applies whether or not the agent is connected.** The decision uses DB state only, so it is simple. A peer queued behind its own earlier turn could be timed out, so the default is a generous 15 minutes and can be changed with an environment variable. The plan left this as an open question to revisit if false positives show up in live use.
- **A `retrying` turn is timed from its last attempt.** The plan only named `turn.created_at`. With that rule, a peer that had worked for 20 minutes and was waiting for re-delivery after its lease lapsed would have been marked unavailable at once.
- **The cap is checked before the start timeout**, so a group past its cap always reads `deadline`.
- **The group-cap test** leaves the peer's turn `leased`, which shows that settling a question never cancels the turn.

## Result

- A peer that is working is waited for, up to the 24-hour cap. A peer whose turn never starts is reported to the caller after 15 minutes as `timeout (peer_unavailable)`, with an explanation line.
- No migration, and the existing environment variable keeps working.
- `tests/test_mcp_ask_peer.py`, `test_turn_terminal_hook.py`, and `test_peer_ask.py` all pass (69 tests). The full cluster suite passes: 3971 passed, 7 skipped.
- Not done: `docs/features.md` (COL-06 and SC-COL-2) is a local file excluded through `.git/info/exclude` in the main checkout, not a tracked file, so this branch could not update it.
