# fix(goals): keep in-progress tasks with a live agent turn out of the execution-timeout sweep (#930)

- Commit: `df28756` (df28756c2881c18848a5b02502fb34b45e19bc44)
- Author: Claude Opus 5.5
- Date: 2026-10-26
- PR: #930

## Situation

The stuck-task sweeper (`goals/sweeper.py`) marked every `in_progress` task
with `execution_id IS NULL` as `failed` once 600 s elapsed since `started_at`.
This was correct for truly wedged tasks, but it also caught tasks whose agent
turn was still actively open — specifically, promoted general tasks (whose
`execution_id` is never set) and future campaign-cycle tasks that had been
dispatched but had not yet called `begin_project_execution`. A long-running
agent turn on such a task would be killed mid-work by the sweeper, even though
the turn's own durable lease-recovery mechanism was fully capable of handling
it.

## Task

- Add a guard to the execution-timeout branch of `sweep_stuck_tasks` so that
  any `in_progress` task with at least one `AgentTurn` in an open state
  (`pending`, `leased`, `retrying`, or `completing`) is excluded from the sweep.
- The fix must not affect the pickup-timeout path (tasks still in `todo`), nor
  must it exempt tasks whose only turns are in terminal states (`completed`,
  `cancelled`, `failed`).
- Tests must cover all four open states (parametrized) and all three terminal
  states to confirm the existing failure path still fires.
- This is **Phase 0** of #930, a prerequisite for campaign-cycle tasks which
  will be `in_progress` without an execution while the scheduler opens them.

## Action

**`packages/cluster/anygarden/goals/sweeper.py`** (lines 86–103)

- Added a lazy import of `OPEN_TURN_STATES` from `anygarden.turns.service`
  inside the function body to avoid a circular import at module level.
- Defined a correlated `EXISTS` subquery `live_turn`:

  ```python
  live_turn = exists().where(
      AgentTurn.task_id == Task.id,
      AgentTurn.state.in_(OPEN_TURN_STATES),
  )
  ```

- Added `~live_turn` as an additional `WHERE` predicate on the
  execution-timeout query (`exec_stmt`), so only tasks with no open turn are
  selected.
- The pickup-timeout query (`pickup_stmt`) was not changed.
- Added `AgentTurn` to the existing import from `anygarden.db.models`.
- Updated the module docstring to document the new exclusion.

**`packages/cluster/tests/test_goals_sweeper.py`** (lines 346–409)

- Added a helper `_seed_overdue_in_progress` that creates a task past the
  execution threshold in a single `flush`.
- Added `_turn(task, room, p, agent, state)` factory.
- `test_in_progress_with_open_turn_is_not_failed` — parametrized over
  `["pending", "leased", "retrying", "completing"]`: asserts sweep returns 0
  and task stays `in_progress`.
- `test_in_progress_with_only_terminal_turns_is_failed` — parametrized over
  `["completed", "cancelled", "failed"]`: asserts sweep returns 1 and task
  flips to `failed` with `execution_timeout`.

## Decisions

**Option A — exclude tasks with any open `AgentTurn` (chosen)**:
Use a correlated `EXISTS` subquery on `AgentTurn.state IN OPEN_TURN_STATES`.
Keeps the sweeper simple (one SQL round-trip), leverages the already-defined
`OPEN_TURN_STATES` constant, and delegates liveness tracking entirely to the
turn lease system.

**Option B — add a new column (e.g. `has_live_turn`) on `Task`**:
Would avoid a join but requires a migration, a write path to maintain the
column, and introduces a consistency window. Rejected as over-engineering for
what is a defensive guard.

**Option C — widen the timeout from 600 s**:
Masks the problem without fixing it; long turns would still be at risk. Also
breaks the intended stuck-task detection for genuinely wedged cases. Rejected.

**Why `OPEN_TURN_STATES` from `turns.service`**: The constant is the
authoritative list used by the rest of the turn machinery (lease renewal,
session recovery, active-turn queries). Duplicating it would create drift.
The lazy import inside the function avoids a circular-import cycle between
`goals.sweeper` and `turns.service`.

**Assumption**: if `OPEN_TURN_STATES` gains new states in the future (e.g.
`pausing`), the sweeper exclusion will automatically pick them up. If a state
is ever removed or renamed without updating `OPEN_TURN_STATES`, the guard may
become too narrow and should be revisited.

## Result

- Tasks with an open agent turn are no longer failed by the sweeper regardless
  of how long the turn runs; the turn's own lease-recovery mechanism handles
  them.
- Tasks with no open turn (or only terminal turns) continue to be swept as
  before.
- Seven new parametrized test cases cover all open and terminal states; all
  pass.
- Unblocks Phase 1 of #930: campaign-cycle tasks can now be dispatched before
  `begin_project_execution` is called without the risk of the sweeper marking
  them failed.
