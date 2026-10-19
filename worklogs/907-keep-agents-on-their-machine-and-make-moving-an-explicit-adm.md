# fix(scheduler): keep agents on their machine and make moving an explicit admin action (#907)

- Commit: `538b123` (538b123bf6d32cc230a23e22f045a61c23e44646)
- Author: Changyong Um
- Date: 2026-10-19T17:26:54+09:00
- PR: #907

## Situation

A placed agent owns machine-local state: its workspace and memory files, the
machine's Codex login credentials, and host filesystem access when the agent is
trusted. Despite this, the server had several code paths that would silently
move a placed agent to any available machine — on crash-loop replacement
(`replacement_requested`), when the placed machine was connected but at
capacity, and whenever `POST /agents/{id}/start` or a roomless-agent recovery
reset `placed_on_machine_id = None`. This meant agents could unexpectedly lose
their workspace and machine-bound state after a transient failure, with no
admin involvement.

## Task

- Make agent placement sticky: once placed, a machine never changes unless an
  admin explicitly triggers a move.
- Cover all four paths that previously moved agents autonomously: crash loops,
  capacity pressure on the placed machine, manual start, and machine deletion.
- Add a first-class API endpoint (`PUT /agents/{id}/machine`) as the single
  authorized path for moves.
- Deprecate `restart_anywhere` as a creation-time policy (and migrate existing
  rows to `restart_on_same_machine`).
- Add `MACHINE_BUSY`, `MACHINE_REMOVED`, and `CRASH_LOOP` unavailability codes
  with Korean user-facing messages.
- Satisfy requirement AGT-10 with pytest, vitest, and Playwright coverage.

## Action

- **`packages/cluster/anygarden/agent_availability.py`**: Added three new
  `UNAVAILABLE_CODES` constants (`MACHINE_BUSY`, `MACHINE_REMOVED`,
  `CRASH_LOOP`) and their `render_unavailable_message` branches with Korean
  copy.
- **`packages/cluster/anygarden/db/models.py`**: Changed the `Agent.restart_policy`
  column default from `"restart_anywhere"` to `"restart_on_same_machine"`.
- **`packages/cluster/anygarden/api/v1/agents.py`**:
  - `AgentCreate.restart_policy` field changed to `Literal["stop", "restart_on_same_machine"]`,
    removing `restart_anywhere` as a valid input.
  - `POST /agents/{id}/start`: removed `agent.placed_on_machine_id = None` from
    the reset block; added `machine_id=agent.placed_on_machine_id` to both
    `reserve_machine_for` calls; guards `MACHINE_REMOVED` agents with 409.
  - New `PUT /agents/{id}/machine` endpoint (`AgentMachineChange` body): validates
    target machine is online and has capacity, stops agent on old machine, updates
    `placed_on_machine_id`, logs `agent_machine_changed`, then restarts if it
    was running.
- **`packages/cluster/anygarden/api/v1/machines.py`**: `DELETE /machines/{id}`
  now stamps every agent that was placed on the deleted machine — not only
  active ones — with `placed_on_machine_id = None`, `unavailable_code =
  MACHINE_REMOVED`, and `unavailable_detail = {machine_name}`. A stray
  dead `await db.execute(select(Agent)...)` with no `.scalars()` was also
  removed.
- **`packages/cluster/anygarden/scheduler/lifecycle.py`**:
  - Roomless-agent recovery path: removed `agent.placed_on_machine_id = None`
    reset; added early return when `MACHINE_REMOVED` is set (admin must pick a
    machine).
  - Capacity path: when the placed machine is connected but full, marks
    `MACHINE_BUSY` and returns instead of falling through to pick any machine.
  - `request_replacement` (legacy crash-loop frame from older daemons): renamed
    result variable `released → stopped`; instead of nulling the placement and
    calling `request_start`, now sets `desired_state="stopped"`, `actual_state=
    "crashed"`, stamps `CRASH_LOOP`, and logs `agent_crash_loop_stopped`.
- **`packages/cluster/anygarden/alembic/versions/094_sticky_agent_placement.py`**:
  New migration that bulk-updates rows where `restart_policy = 'restart_anywhere'`
  to `'restart_on_same_machine'`.
- **`packages/cluster/frontend/src/components/agent-settings/OverviewPanel.tsx`**:
  Shows the agent's current machine with a "Change" button; displays the
  unavailable reason with contextual "Start again" / "Change machine" actions.
- **`packages/cluster/frontend/src/components/agent-settings/MachineChangeDialog.tsx`**
  (new): Move dialog that warns the user that workspace files and memory stay
  on the old machine.
- **`packages/cluster/tests/test_agents_api.py`**, **`test_lifecycle.py`**,
  **`test_agent_availability.py`**, **`test_migrations.py`**: New test cases
  covering all affected paths.
- **`packages/cluster/frontend/e2e/agent-machine-change.spec.ts`** (new):
  Playwright E2E for the machine-change flow.
- **`docs/requirements.md`**: Added `AGT-10` row with `pytest, vitest` coverage
  declaration.

## Decisions

- **Keep placement on crash-loop rather than re-placing**: The legacy
  `replacement_requested` frame was designed for `restart_anywhere` agents that
  had no fixed home. Now that placement is sticky, re-placing on a new machine
  after a crash budget is spent would silently move workspace state. Stopping
  in place and marking `CRASH_LOOP` forces an admin decision, which is the
  correct gate. The alternative — retry on the same machine automatically — was
  rejected because the daemon already exhausted its local crash budget before
  sending the frame.
- **`MACHINE_BUSY` waits on the placed machine instead of falling through**:
  Releasing placement when the machine is temporarily full would let any
  machine pick up the agent, losing machine-local state. Waiting preserves the
  invariant at the cost of the agent being unavailable until capacity frees up.
  Accepting that downtime is preferable to silent data loss.
- **Single explicit move endpoint (`PUT /agents/{id}/machine`) rather than
  allowing moves through `POST /start`**: Combining them would make it ambiguous
  whether a start is "restart here" or "move then start". A dedicated endpoint
  with its own auth check, validation, and `agent_machine_changed` audit log
  keeps the invariant clear and auditable.
- **Remove `restart_anywhere` from `AgentCreate` rather than keeping it for
  legacy callers**: The policy is now meaningless for newly created agents since
  the server never acts on it. Allowing it at creation time would silently
  accept a value that has no effect, which is more confusing than refusing it.
  Existing rows are migrated by migration 094.
- Assumption: older daemons still send `replacement_requested` frames; the
  server now handles them with the sticky semantics. If a daemon is ever updated
  to send a different frame for this signal, the `request_replacement` handler
  should be revisited.

## Result

- Placed agents stay on their machine across crashes, capacity events, restarts,
  and machine deletions. The only code path that changes `placed_on_machine_id`
  is the new `PUT /agents/{id}/machine` endpoint.
- Three new unavailability states (`machine_busy`, `machine_removed`,
  `crash_loop`) surface actionable messages in the admin UI.
- Agent settings show the current machine with a move dialog that warns about
  workspace portability.
- Requirement AGT-10 is covered by pytest (server lifecycle and API tests),
  vitest (MachineChangeDialog and OverviewPanel unit tests), and Playwright
  (E2E machine-change flow).
- Migration 094 backfills all existing `restart_anywhere` rows; `AgentCreate`
  no longer accepts the value going forward.
