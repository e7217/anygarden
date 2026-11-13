# fix(agent): recover a room session blocked by a cut-off turn (#1046)

- Commit: `d0e2739` (d0e27390e3f3b4cc6999f45af19eda5ba33f3b92)
- Author: Changyong Um
- Date: 2026-11-13T19:52:00+09:00
- PR: #1046

## Situation

When a cluster node restarted mid-turn the local `ReceiptStore` recorded the
interrupted execution with `outcome=unknown`.  Every subsequent turn in the
same room was then refused with `POLICY_DENIED` because `scope_blocked()`
returned `True` for any scope that owned an unknown-outcome row.  Users only
saw the generic "failed to generate a response" notice with no path forward
other than restarting the entire agent — a remediation that was neither
documented nor surfaced in the UI.

## Task

- Detect when every unknown execution blocking a scope belongs to a process that
  has provably exited (ENG-16).
- Retire those executions (lift the block) while keeping the original rows as
  replay-deduplication evidence.
- Drop the scope's native session handle so the next turn starts a fresh engine
  session instead of attempting a broken resume.
- Distinguish the still-blocked case from a pure execution-policy denial so the
  UI can show a targeted `/clear` hint rather than an agent-restart suggestion.
- Federated peers must keep receiving the closed `POLICY_DENIED` wire code they
  already handle; the new code stays local.

## Action

**`packages/cluster/anygarden/agent/runtime/execution/store.py`**
- Added `retired_unknowns (execution_id PK, scope)` SQLite table that records
  executions whose block has been lifted.
- `scope_blocked()` now delegates to `blocking_unknowns()`, which excludes
  retired rows from the `SELECT`.
- `retire_unknowns(scope, ids)` inserts into `retired_unknowns` and
  `DELETE FROM sessions WHERE scope=?` in one transaction, ensuring the broken
  session handle is gone before the next turn can attempt a resume.

**`packages/cluster/anygarden/agent/runtime/execution/manager.py`**
- Added `ScopeRecovery` frozen dataclass (`recovered: int`, `blocked: bool`).
- `LocalExecutionManager.recover_scope(scope)` iterates `blocking_unknowns`,
  checks each via an in-memory `asyncio.Task` reference or `ProcessTree.live()`
  / `.uncertain`, and calls `retire_unknowns` only when every unknown is
  confirmed gone.  A live or unverifiable process short-circuits with
  `ScopeRecovery(blocked=True)`.
- `scope_blocked()` branch in `_run_invocation` now emits the new
  `SESSION_PREVIOUS_TURN_UNRESOLVED` reason code rather than `POLICY_DENIED`.

**`packages/cluster/anygarden/agent/integrations/room_execution.py`**
- `RoomExecutionAdapter._recover_session()` calls `manager.recover_scope()`
  before the prompt is composed (so the dropped session is re-seeded per #805).
- `_SESSION_RECOVERED_NOTICE` i18n string prepended to the first reply of a
  recovered session.
- `_with_recovery_notice()` injects the prefix only when `_session_recovered`
  ContextVar is set.

**`packages/cluster/anygarden/agent/runtime/execution/contracts.py`**
- `SESSION_PREVIOUS_TURN_UNRESOLVED` added to `FAILURE_CODES`.

**`packages/cluster/anygarden/agent/runtime/execution/failure_feedback.py`**
- `terminal_failure_category()` maps `SESSION_PREVIOUS_TURN_UNRESOLVED` to
  `("PROCESS_OUTCOME_UNKNOWN", False)` so federated peers observe the same
  public projection as a plain unknown outcome.

**`packages/cluster/anygarden/agent/runtime/handler_wrapper.py`**
- `_SESSION_UNRESOLVED_NOTICE` user-facing string with `/clear` guidance.
- Mapped into `_FAILURE_NOTICES` dict.

**`packages/cluster/anygarden/federation/executor.py`**
- `_WIRE_ERROR_CODES` maps `SESSION_PREVIOUS_TURN_UNRESOLVED → POLICY_DENIED`
  for the federation wire, keeping the closed vocabulary intact.

**Frontend (`packages/cluster/frontend/src/`)**
- `turnHealth.ts`: `TurnFailureCategory` union extended with `'session'`;
  `classifyTurnFailure()` routes `SESSION_PREVIOUS_TURN_UNRESOLVED` to it.
- `RecentTurnSummary.tsx`: `reasonKey` and `CATEGORY_LABEL` maps extended for
  `'session'`.
- `i18n/catalogs/admin.ts`: new en/ko strings for `category.session` ("Earlier
  turn unresolved") and `reason.session` (wait-and-resend or `/clear`);
  existing `reason.policy` copy updated to suggest `/clear` instead of agent
  restart.

**Tests**
- `test_local_execution.py`: three new async tests covering (a) blocked-with-
  new-code, (b) recover → fresh session, (c) live process keeps block.
- `test_session_recovery.py`: 92-line integration suite for
  `RoomExecutionAdapter`.
- `turnHealth.test.ts`: `[ENG-16]` suite; `SESSION_PREVIOUS_TURN_UNRESOLVED`
  mapping to `'session'` category.
- `AgentSettingsDialog.turnHealth.test.tsx`: label check for new category.

**`docs/requirements.md`**: ENG-16 row added.

## Decisions

- **Retire vs. delete the unknown row** — deleting would break replay
  deduplication (the same execution ID arriving twice on reconnect would be
  accepted again). Retiring into a side table lifts the block while the
  original row remains as evidence. Alternative of marking the row
  `outcome=recovered` was considered but rejected: it would require every
  `scope_blocked` consumer to know an extra terminal state.

- **New error code vs. reusing POLICY_DENIED** — `POLICY_DENIED` was always
  misleading here (no policy was violated; a process died). A dedicated code
  lets the UI show a targeted hint (`/clear`) without guessing from context.
  Federation peers keep receiving `POLICY_DENIED` via `_WIRE_ERROR_CODES` so
  no wire-protocol change is needed; this was the decisive factor allowing a
  local-only change.

- **`recover_scope` before prompt composition** — calling it in
  `_recover_session` before `_forget_context_of_a_cleared_session` and before
  the seed-context step (per #805) ensures the fresh session receives a full
  context seed rather than inheriting a corrupt one. Alternative of recovering
  lazily inside `scope_blocked` was rejected because the manager's async task
  map is not safely inspectable from the synchronous store layer.

- **Assumption**: `ProcessTree.uncertain` returning `True` (e.g. permission
  errors checking `/proc`) keeps the scope blocked. If the deployment
  environment routinely restricts `/proc` access this assumption should be
  revisited — the block would then never auto-clear.

## Result

- Room sessions blocked by a node-restart-interrupted turn now auto-recover on
  the next mention: the interruption is retired, the native session is dropped,
  and the reply is prefixed with a one-line notice.
- Sessions blocked by a *live* process still refuse with the new
  `SESSION_PREVIOUS_TURN_UNRESOLVED` code and a UI hint pointing at `/clear`,
  instead of the opaque `POLICY_DENIED` / "agent restart" guidance.
- ENG-16 is covered by pytest and vitest suites; 322 lines added across 16
  files, all tests green.
- The federation wire protocol is unchanged; no peer upgrades required.
