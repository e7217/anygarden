# fix(agent): give fresh-session turns the recent conversation (#984)

- Commit: `910b995` (910b9954052e60c0ab73c969cd0673d0b4a9b7c0)
- Author: Changyong Um
- Date: 2026-10-30T16:21:46+09:00
- Issue: #984

## Situation

An operating room's lead agent starts every unbound turn in a fresh engine session (via `room_execution._project_session_scope`), and so does a retry (attempt_number ≥ 2). The server's conversation context logic introduced in #866 assumed the agent's previous completed turn was already loaded in the session and therefore treated that turn as a baseline — meaning everything the agent said after that point was excluded from the context window. As a result, the lead got nothing and forgot its own most-recent replies.

## Task

- Make `build_conversation_context` aware of whether the current delivery runs in a fresh engine session.
- When it does, skip the baseline derived from the previous completed turn and instead attach the recent tail of the same conversation, including the agent's own replies.
- Cap the fresh-session tail at 20 messages (1,000 chars each) — tighter than the 30-message default for resumed sessions.
- Leave resumed-session turns and execution turns unchanged.
- Add `COL-12` to the requirements table.

## Action

- `docs/requirements.md`: added `COL-12` requirement row — fresh-session turns receive up to 20 recent messages (own replies included); resumed sessions get only what they missed; execution turns get nothing.
- `packages/cluster/anygarden/turns/conversation_context.py`:
  - Added `FRESH_SESSION_CONTEXT_MESSAGES = 20` constant (`line 31`).
  - Extended `build_conversation_context` signature with `fresh_session: bool = False` keyword argument (`line 48`).
  - When `fresh_session=True`, baseline is forced to `None` so the query window is unbounded (recent tail, not just missed messages) (`line 65`).
  - Applied `FRESH_SESSION_CONTEXT_MESSAGES` limit in place of `MAX_CONTEXT_MESSAGES` for fresh-session queries (`lines 101-103`).
- `packages/cluster/anygarden/turns/service.py`:
  - Pre-computed `operating_lead` flag so it can be reused for the `fresh_session` argument (`line 764`).
  - Passed `fresh_session=operating_lead or attempt.attempt_number >= 2` to `build_conversation_context` (`lines 775-780`); added cross-reference comment pointing to `room_execution._project_session_scope`.
- `packages/cluster/anygarden/agent/integrations/room_execution.py`: updated comment on the operating-lead branch to note the symmetry with the server's `fresh_session` flag and direct readers to change them together.
- `packages/cluster/tests/test_conversation_context.py`: four new `@pytest.mark.req("COL-12")` tests covering (a) recent tail despite a completed turn, (b) cap and `omitted` count, (c) thread scoping, (d) execution turn exclusion.
- `packages/cluster/tests/test_durable_turns.py`: two new integration tests — `test_operating_lead_delivery_carries_its_own_previous_reply` (parametrized: subroom vs. non-subroom) and `test_retry_delivery_carries_the_recent_conversation`.

## Decisions

- **`fresh_session` flag on the builder** rather than a second entry-point: the existing `build_conversation_context` handles all the scoping (execution guard, room guard, thread filter, `omitted` count) and duplicating it would diverge. A single boolean parameter is the minimal diff.
- **Cap at 20 not 30**: fresh-session context is paid on every turn of the lead (not a one-off catch-up), so a tighter limit avoids inflating every delivery. Measured with o200k_base: worst case (20 × 1,000 chars) is about 11.7K tokens, 20 × 200 chars about 2.5K, 10 short messages about 0.9K. If leads routinely need more, `FRESH_SESSION_CONTEXT_MESSAGES` is a single constant to raise.
- **`attempt_number >= 2` as the retry signal**: `deliver_pending_outbox` already has the attempt object at that point; using its `attempt_number` avoids adding a new DB field. The alternative (checking a flag on the turn) was rejected because retries are managed by the delivery layer, not persisted on the turn model.
- **Own replies included** (baseline forced to `None`): for a fresh session the agent's earlier replies are not in the engine context, so they are genuinely new information. Excluding them (as the previous baseline logic did) left the agent amnesiac.
- Assumption: if the engine's session-creation behavior changes (e.g. operating leads start reusing sessions), `room_execution._project_session_scope` and `service.py`'s `fresh_session` call site must be updated together — the cross-reference comment in `room_execution.py` flags this.

## Result

- Operating room leads and retried turns now receive the recent conversation tail (up to 20 messages) regardless of whether a previous turn was completed, restoring memory across unbound turns.
- Resumed sessions are unaffected; execution turns still receive nothing.
- Seven test cases added under `COL-12` (four unit, three delivery); the full cluster suite passes (4628 passed, 7 skipped); the requirement is now tracked in `docs/requirements.md`.
- Not yet verified on a live node with a real engine (the room from the issue is not on this host).
