# feat(campaigns): add campaign models, migrations and the state service skeleton (#930)

- Commit: `86f5e7d` (86f5e7d2cb3d98907fcfc80381a5f847cb13a485)
- Author: Changyong Um
- Date: 2026-10-26
- PR: #930

## Situation

Issue #930 introduces metric-driven long-running Campaign loops — an entity that repeatedly spawns cycle Tasks until a measurable goal is achieved or a budget/deadline is exhausted. Before this commit the codebase had no Campaign concept: only Goal-driven executions existed, and there was no way to persist a multi-cycle optimization loop with structured attempt records, an audit trail, or a spec-validation layer. A Phase S storage spike (`.tmp/spike-930-storage.md`) compared two storage designs (A: separate `campaigns` tables vs C: re-using `tasks` as a parent) and confirmed variant A after finding that variant C introduced a sweeper bypass and lost attempt history when the parent Task was deleted.

## Task

Implement the persistence and business-logic foundation for Campaigns (Phase 1 of #930):

- Alembic migrations 097 and 098 for the new tables and a model column.
- SQLAlchemy models `Campaign`, `CampaignAttempt`, `CampaignEvent`, and `Task.campaign_id`.
- `campaigns/policy.py`: spec-validation constants and `validate_campaign_spec`.
- `campaigns/service.py`: pure state-machine `transition()`, CAS-guarded `apply_transition`, idempotent `record_event`, `create_campaign`, and time helpers with injectable clock.
- All alembic autogenerate checks must stay green (no metadata drift).
- Scope explicitly excludes: scheduler, brief rendering, `begin_execution` clamping, MCP tools, REST API, eval frames, retrospective — left for later phases.

## Action

- **`db/migrations/versions/097_campaigns.py`** (128 lines): creates `campaigns` (identity, judgment criteria, loop state), `campaign_attempts` (per-cycle record with agent-reported fields, eval fields, and `outcome`), `campaign_events` (audit trail with unique `event_key`), and adds `tasks.campaign_id` as a nullable FK to `campaigns` with `SET NULL` on delete and a B-tree index. Upgrade/downgrade are both implemented.
- **`db/migrations/versions/098_instruction_revision_context.py`** (30 lines): adds `agent_instruction_revisions.context` JSON column (nullable) and relaxes `source` from a constrained CHECK to an unconstrained `String(16)` so `"campaign"` is accepted.
- **`db/models.py`**: added `Campaign`, `CampaignAttempt`, `CampaignEvent` models and `Task.campaign_id` relationship. Models placed in `db/models.py` (not a separate file) so `create_all` sees them before the first import — a lesson from the spike.
- **`campaigns/policy.py`** (278 lines): defines `MIN_GAP_SECONDS=300`, `PLATEAU_WINDOW=5`, `RETRO_EVERY_CYCLES=10`, `EVAL_FAILURE_BLOCK_THRESHOLD=3`, `BRIEF_MAX_CHARS=12000`; enum vocabularies for mode/direction/trusted_eval; `validate_campaign_spec` enforcing `budget.max_total_tokens` required > 0, at least one of `metric.target` or `deadline_at`, evaluator shape (workspace-relative `cwd`, `timeout_sec ≤ 21600`); and `usable_tokens` budget helper.
- **`campaigns/service.py`** (432 lines): `_TRANSITIONS` dict encodes the §3.3 state table, built programmatically — all open statuses accept `deadline`, `budget_exhausted`, `stop`, `edit`, `extend`; targeted transitions (`pause`, `resume`, `block`, `plateau`, `target_reached`, etc.) are listed explicitly. `transition(status, event)` raises `IllegalTransition` on any missing key (including every event on terminal statuses). `cycle_closed_at` computes `max(finished_at, opened_at)` with an injectable `Clock` callable to decouple from the real clock. `next_cycle_at` = `max(closed + 300 s, wake_at)`. `record_event` is idempotent via a pre-check on `event_key` plus an `IntegrityError` catch for races. `create_campaign` validates the spec, looks up the room's lead `Participant`, and inserts the Campaign row. `apply_transition` and `cas_update` implement CAS on `state_revision` to prevent concurrent ticks from double-advancing state.

## Decisions

**Variant A (separate tables) over Variant C (Task hierarchy)**
- C required a sweeper bypass so the parent pseudo-Task survived the 600 s `in_progress` timeout, and deleting the parent erased all attempt history. A has neither problem.
- Both variants passed 14/14 spike scenarios with zero regressions against the existing 156-test suite, so the tiebreaker was structural cleanliness.
- A was confirmed by the user on 2026-10-26 after the spike report.

**Models in `db/models.py` (not a separate module)**
- The spike surfaced an alembic autogenerate miss when models lived outside `db/models.py`: `create_all` ran before the new module was imported, so the tables were invisible to the metadata comparison test. Keeping everything in one file guarantees import order at no meaningful cost (the file is already large).

**Pure `transition()` function over a method on a model class**
- The repo's existing `goals/` and `project_executions/` modules use module-level functions rather than active-record methods. Consistency was preferred.
- A pure dict lookup also makes the transition table easy to inspect and test exhaustively in isolation without a DB session.

**`source` column changed to `String(16)` instead of adding an enum value**
- The original `source` column used a `CheckConstraint`; adding `"campaign"` to an existing CHECK requires a DDL rewrite on SQLite (no `ALTER COLUMN`). Switching to an unconstrained `String(16)` is simpler and consistent with how other unconstrained small strings are handled elsewhere in the schema (unclear from available context whether a stricter constraint was originally intentional or incidental).

**Injectable clock on time helpers**
- `cycle_closed_at` and `next_cycle_at` accept an optional `clock: Clock` argument (default `utcnow`). This is needed because the sweeper and Task API write `finished_at` from the real wall clock while tests and the scheduler may run on a controlled clock; without injection, `max(finished_at, opened_at)` comparisons are non-deterministic in tests.

## Result

- Four new files: `097_campaigns.py`, `098_instruction_revision_context.py`, `campaigns/policy.py`, `campaigns/service.py`.
- `db/models.py` extended with three models and one FK column.
- Alembic `upgrade head` / `downgrade -1` roundtrip passes on SQLite.
- Existing goals and tasks test suites stay green (zero behavioral changes to existing code in this commit).
- **Still pending** (later phases): `CampaignScheduler`, `render_brief`, `begin_execution` clamping, `on_execution_terminal` / `on_eval_result` hooks, MCP tools (`record_campaign_attempt`, `schedule_campaign_wakeup`, etc.), REST API, retrospective cycle, eval frame messages.
