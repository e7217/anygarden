# feat(workspace): remove an agent from a room and report a hired agent's rooms (#982)

- Commit: `bf8c30f` (bf8c30f4dc092ba9bdc632c90cb76096e01c426c)
- Author: Changyong Um
- Date: 2026-10-30T15:12:51+09:00
- PR: #982

## Situation

The workspace ask-action system had `add_room_member` but no counterpart for removal: the only way to remove an agent from a room was through the web UI. Additionally, when an agent was hired (`hire_agent`), the result contained only the new agent's profile fields, so the woken agent had to guess which rooms it had been placed in. Both gaps forced callers to supplement MCP tool results with out-of-band knowledge.

## Task

- Add a new `remove_room_member` ask-action that requires approval, mirrors the web UI's `remove_participant` semantics (open tasks released, representative handed on), and enforces the same `MEMBER_MANAGE` permission path.
- Refuse obviously invalid removal proposals (self-removal, DM room, non-member, hidden room) before posting a card.
- Enrich `hire_agent`'s result with the actual rooms the new agent joined (excluding auto-created DMs).
- Add a test for `add_room_member` (which had no coverage) and extend the lifecycle fixture with `on_room_added`.
- Document requirements WSP-11 and WSP-12.

## Action

- `packages/cluster/anygarden/workspace/policy.py`: Added `RemoveRoomMemberParams(room_id, agent_id)` and registered `remove_room_member` as an `ask`-tier, non-admin `ActionSpec` with a description noting open-task release and representative succession (`policy.py:62-68`, `policy.py:136-140`).
- `packages/cluster/anygarden/workspace/actions.py`: Added `removal_target()` helper that calls `require_visible_room`, checks self-removal, DM room, and non-member cases, returning the `Participant` row or raising `ActionFailed` (`actions.py:106-148`). Added `_remove_room_member()` runner that calls `removal_target` then `remove_participant` with a `BackgroundTasks` context (`actions.py:150-162`). Extended `_hire_agent()` to query all non-DM rooms the new agent joined and include them as `rooms` in the result (`actions.py:247-264`). Registered the new runner in `RUNNERS` (`actions.py:266`).
- `packages/cluster/anygarden/mcp/workspace_tools.py`: Threaded `request` and `identity` into `_check_proposal` so `remove_room_member`'s pre-card guard can call `removal_target` with the full request context (`workspace_tools.py:127`, `150-174`).
- `packages/cluster/tests/workspace/test_workspace_tools.py`: Added `on_room_added=AsyncMock()` to the lifecycle fixture stub. Added four new tests: `test_adding_an_agent_puts_it_in_the_room` (covers WSP-08 add path), `test_removing_an_agent_waits_for_approval_and_releases_its_work` (WSP-11), `test_a_removal_that_could_never_run_is_refused_without_a_card` (WSP-11, four bad-input cases), and `test_a_hired_agent_reports_the_rooms_it_joined` (WSP-12).
- `packages/cluster/tests/workspace/test_policy.py`: Added `remove_room_member` to the `ask`-tier assertion and verified its `admin_only` is false.
- `docs/requirements.md`: Added WSP-11 and WSP-12 requirement rows.

## Decisions

- **Approval required (ask tier), not `allowed`**: Removing an agent is a potentially destructive workspace mutation — it releases tasks and may change room representative — so it follows the same pattern as `archive_room` and `hire_agent`, requiring human sign-off via a card rather than executing immediately.
- **Pre-card guard via `removal_target()`**: Rather than letting obviously invalid proposals reach the approval queue (where they would fail at execution time and produce confusing pending cards), the same validation helper is invoked at proposal time. The identical function runs again at execution time, because membership can change between the proposal and the approval.
- **Delegate to `remove_participant`** (web UI's own handler) rather than writing a second removal path: this ensures business rules (task release, representative handoff, broadcasts) are not duplicated, and a single function is the source of truth.
- **`BackgroundTasks()` workaround**: `remove_participant` accepts a `BackgroundTasks` argument from FastAPI's DI; calling it after `await remove_participant(...)` processes the in-process side-effects synchronously within the action runner's session.
- **`rooms` added to `hire_agent` result, not as a separate tool**: The proposing agent (e.g. the PM in #980) is woken by the approval notice, whose `Result:` line is `row.result`; in #980 it wrongly reported that the subroom assignment was unconfirmed because the rooms were missing there. Enriching the result rather than a follow-up call avoids an extra round-trip and keeps the data atomically consistent with the hire operation.
- **DM excluded from `rooms`**: The auto-created DM is an implementation detail, not a meaningful workspace room; including it would mislead the woken agent into treating it as a regular collaboration channel.

## Result

- `remove_room_member` is available as an ask-action. Invalid proposals are rejected before posting a card (four error codes: `SELF_REMOVAL`, `DM_ROOM`, `NOT_A_MEMBER`, `ROOM_NOT_FOUND`). On approval, the participant is removed, their open tasks are released to `todo`, and representative succession runs.
- `hire_agent` results now include `rooms: [{id, name}]` for every non-DM room the new agent joined, enabling the woken agent to self-report its assignments.
- `add_room_member` now has test coverage.
- Requirements WSP-11 and WSP-12 are captured in `docs/requirements.md`.
