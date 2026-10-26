# feat(campaigns): open campaign cycles from a scheduler and clamp their executions (#930)

- Commit: `1cfb31a` (1cfb31a — full SHA not available from hook stdout)
- Author: Claude Opus 5.5 / session agent
- Date: 2026-10-26
- PR: #930

## Situation

The campaign data model and state-service skeleton were added in a prior commit (#930 §3.1), but campaigns had no runtime that would actually advance them. Active campaigns with elapsed `next_cycle_at` or approaching deadlines sat indefinitely without opening a new execution cycle. The system needed an autonomous loop to drive campaign progress without human intervention.

## Task

- Implement `CampaignScheduler` (`campaigns/scheduler.py`): an async polling loop started/stopped alongside the Goal scheduler and exposed in `/healthz`.
- Each tick must select up to 25 due active campaigns with no open cycle and advance each in its own write transaction.
- Advancement must handle five terminal/blocking cases: room archived → `stopped`; deadline passed → `deadline_reached`; cycle-token budget exhausted → `budget_exhausted`; lead agent left room → `blocked`; otherwise open cycle N via CAS on `state_revision`.
- Opening a cycle must create a `Task` (idempotency key `campaign:{id}:{N}`) via the shared `materialize_assigned_task` funnel and a `CampaignAttempt` row, all in one commit.
- Implement `render_brief` (`campaigns/brief.py`): render the cycle Task's spec (purpose, criteria, budget/time remaining, best attempt, last 5 cycles, outcome tallies, recent lessons, situational directives, rules) capped at `BRIEF_MAX_CHARS` with rules always kept whole.
- `begin_execution` must accept a campaign cycle root: clamp `max_total_tokens` to the remaining cycle budget and `deadline_at` to the campaign deadline; refuse ended/closed/exhausted cycles; own the execution as the campaign owner; link the attempt to it.
- The cycle assignment message must direct the lead to `begin_project_execution` instead of `mark_task_status`.

## Action

**`packages/cluster/anygarden/campaigns/scheduler.py`** (new file):
- `CampaignScheduler` class with `start()` / `async stop()` managed via `asyncio.create_task` and a `_stop_event`.
- `_tick()`: runs a read-only query for due active campaign IDs (no open attempt, `next_cycle_at ≤ now` or `deadline_at ≤ now`), then calls `advance()` per campaign.
- `advance()`: acquires the write lock via `begin_write_transaction` (SQLite serialisation), delegates to `_advance()`, commits or rolls back.
- `_advance()`: checks room archival, deadline, token budget, next-cycle gate, and agent-in-room in order; then claims cycle N with `cas_update(cycles_run + 1)`, renders the brief with `render_brief`, creates the Task via `materialize_assigned_task(idempotency_key=cycle_idempotency_key(id, N))`, inserts a `CampaignAttempt`, flushes, and fires a `cycle_opened` event.
- `cycle_idempotency_key(campaign_id, cycle_no) → "campaign:{id}:{N}"` utility.
- `TickOutcome` frozen dataclass: `campaign_id`, `action`, optional `task_id`, `cycle_no`.

**`packages/cluster/anygarden/campaigns/brief.py`** (new file):
- `render_brief(campaign, attempts, *, cycle_no, now) → str`: pure function, no DB dependency, deterministic for a given state snapshot.
- Sections: Purpose (≤3000 chars), Criteria & status (mode, metric, budget %, time left), Best attempt, Recent 5 closed cycles with hypothesis/score/lesson, Failure-pattern tallies, Situational directives (plateau detected, missing `recorded_at`, deadline < 10 % remaining window), Rules (4 numbered rules always kept whole).
- Budget split: `BRIEF_MAX_CHARS − len(header) − len(rules)` distributed proportionally across the 6 variable sections.

**`packages/cluster/anygarden/task_materialize.py`** (refactor from previous commit, applied here):
- `materialize_assigned_task` now accepts `**fields: Any` for extra Task columns, allowing the campaign scheduler to pass `campaign_id=campaign_id` without a separate code path.

The `begin_execution` changes (clamping token budget and deadline, refusing closed cycles, linking `CampaignAttempt`) are part of this commit's broader diff alongside the scheduler and brief files.

## Decisions

- **CAS on `state_revision` via `cas_update`** rather than a DB-level advisory lock: the unique `(campaign_id, cycle_no)` `CampaignAttempt` row and `uq_tasks_idempotency_key` on Task together back the CAS so a duplicate `IntegrityError` on commit is the race-loss signal. This keeps the locking model explicit and recoverable without requiring DB-specific advisory-lock support.
- **Writer-lock-first for SQLite** (`begin_write_transaction` before reads in `advance()`): SQLite's serialised write model would fail a read→write upgrade mid-transaction; taking the write lock upfront serialises two concurrent ticks instead of raising. On PostgreSQL the same code falls back to ordinary transaction isolation.
- **`materialize_assigned_task` as the shared Task-creation funnel** rather than duplicating goal-executor logic in the scheduler: both paths now use one function, so the `[TASK]` mention wake-up (#266) and assignment timestamps (#314 pickup-timeout) are applied consistently without divergence.
- **Pure `render_brief`** (no DB calls, accepts pre-fetched `attempts`): makes the brief testable without a database session and deterministic for retries (a restart renders the same brief for the same cycle).
- **Proportional budget allocation across brief sections**: each section shrinks equally when the total exceeds the char cap, so no single section silently dominates; the Rules section is always kept whole to guarantee the agent receives its mandatory instructions.

## Result

- Active campaigns with elapsed `next_cycle_at` now automatically advance through the scheduler tick within one poll interval (default 30 s).
- Terminal conditions (archived room, deadline, budget exhaustion, missing lead) are detected and the campaign is transitioned to a terminal state with a matching `stop_reason`.
- Each cycle opens exactly one `Task` and one `CampaignAttempt`; a concurrent-tick race is resolved by `IntegrityError` rollback, leaving the faster writer's cycle standing.
- The cycle Task's spec is a structured Markdown brief capped at policy-defined character limit, rendered with situational guidance for the agent.
- `begin_execution` enforces the remaining cycle budget and campaign deadline, preventing agents from over-running a cycle's token allocation.
- The assignment message now correctly directs the agent to `begin_project_execution` (not `mark_task_status`), matching the campaign execution protocol.
