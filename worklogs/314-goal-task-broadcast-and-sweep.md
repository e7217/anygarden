# fix(goals): broadcast scheduler-injected task assignments + add stuck task sweeper (#314)

- Commit: `624c1dc` (624c1dca6696a77d3691213c3b9766cd505cc66e)
- Author: Changyong Um
- Date: 2026-04-28T23:26:45+09:00
- PR: #314

## Situation

The autonomous-responsibility flow (#302) was producing tasks that never got picked up. In a live test a 60s-interval Goal accumulated 17 todo rows over 17 minutes while `agent01-claude` (online, mention-eligible) responded zero times. Investigation showed the scheduler's synthetic task-assignment message was being persisted to the DB but the WS broadcast step the agent's `decide_policy` mention path depends on was missing — the row was invisible to live ws sessions and the agent never woke. The `replay_since_seq` safety net only fires on reconnect, so an always-on session would never recover.

A second class of failure was uncovered while debugging: even with the broadcast fix, an agent could miss a frame, crash mid-task, or wedge — and there was no backstop to retire the stranded row, so the goal's `consecutive_failures` policy never got a chance to auto-pause a broken responsibility.

## Task

- Wire scheduler-injected task assignment messages into the same broadcast path the user-facing API already uses, without duplicating fan-out logic and without introducing a way for callers to silently forget the broadcast again.
- Add a backstop sweeper that flips genuinely stuck tasks (not human memos that happen to be `todo`) to `failed` so the existing `apply_completion` → `consecutive_failures` → goal `paused` chain runs.
- The pickup-timeout clock must start when an assignee gets attached, not at row creation, so a memo task assigned days later doesn't auto-fail the moment the new assignee gets it.
- The sweeper must clear the existing 17-row backlog automatically — no one-off cleanup script.
- Stay single-process; no external queue.

## Action

- `packages/cluster/doorae/messages/service.py`: `inject_task_assignment_message` gains a `manager: ConnectionManager | None` kwarg. When supplied, the function builds a `MessageOut` frame from the persisted row and calls `manager.broadcast(room.id, frame)` itself. Mirrors the existing `fanout_task_event` pattern at line 168 so the two helpers stay symmetric.
- `packages/cluster/doorae/goals/executor.py`: `trigger_goal` accepts `manager` and forwards it to `inject_task_assignment_message`. Also stamps `task.assigned_at = now` on creation (line ~115) so the sweeper has a clock.
- `packages/cluster/doorae/goals/scheduler.py`: `GoalScheduler.__init__` holds a `manager` reference and `_tick` passes it through to every `trigger_goal` call. New `_sweep` method runs in its own short session inside the polling loop, isolated from the trigger path so a sweep error can't roll back goal commits.
- `packages/cluster/doorae/goals/sweeper.py` (new): `sweep_stuck_tasks` runs two queries — `status='todo' AND assigned_at IS NOT NULL AND assigned_at < pickup_threshold` and `status='in_progress' AND started_at IS NOT NULL AND started_at < exec_threshold` — flips each row to `failed`, sets `error` to `pickup_timeout` / `execution_timeout`, calls `apply_completion(task, "failed")` (no-op for manual rows, full bookkeeping for goal-derived ones), and emits a `task.updated` frame via `fanout_task_event`.
- `packages/cluster/doorae/goals/policy.py`: adds `TASK_PICKUP_TIMEOUT_SECONDS = 120` and `TASK_EXECUTION_TIMEOUT_SECONDS = 600`.
- `packages/cluster/doorae/api/v1/tasks.py`: `create_task` stamps `assigned_at = now` when `assignee_participant_id` is set on creation; `update_task` refreshes it whenever the assignee transitions to a different value.
- `packages/cluster/doorae/db/models.py`: adds `Task.assigned_at: Mapped[Optional[datetime]]`.
- `packages/cluster/doorae/db/migrations/versions/038_task_assigned_at.py` (new): adds the column nullable + backfills `assigned_at = created_at` for any pre-existing row with an assignee. Rows without an assignee stay NULL so the sweeper's `IS NOT NULL` guard skips them.
- `packages/cluster/doorae/app.py`: passes `app.state.connection_manager` into the lifespan `GoalScheduler` instantiation.
- New tests (16): `tests/test_goals_sweeper.py` (9), `tests/test_goals_scheduler.py` (2), `tests/test_tasks_api.py::TestAssignedAt` (5), `tests/test_tasks_injection.py` (2 broadcast contract tests), `tests/test_migrations.py` (1 backfill verifier). 879 existing tests still pass.

## Decisions

Sourced from `.tmp/plan-314-goal-task-assignment-broadcast-and-sweep.md` §3.2.

- **Broadcast responsibility location** — three options weighed: (A) bake it into `inject_task_assignment_message`, (B) have each caller call a separate broadcast helper after `inject`, (C) push the broadcast into the DB-repository layer. Picked **A** because the very bug being fixed was exactly the failure mode of B — a caller (the scheduler path) silently forgot to broadcast. Repeating that shape would re-open the door. C was rejected because the repository layer is intentionally DB-only and has no `ConnectionManager` in scope; mixing layers would couple persistence to ws state. The clinching observation was that `fanout_task_event` already lives in the same module with the same shape (`manager` kwarg, `None`-tolerant), so option A keeps the two helpers symmetric and avoids inventing a new pattern.

- **`assigned_at` column vs. reusing `created_at`** — option of skipping the column and using `created_at + assignee != NULL` was considered. Rejected because a memo task can sit unassigned for days before someone attaches an assignee, and using `created_at` would falsely fail it the moment the new assignee touches it. The user explicitly called out this case ("assignee가 배정된 이후로 타임아웃을 걸어야 한다") — that pinned the decision to a dedicated column.

- **Sweep loop placement** — could be a separate FastAPI background task or external cron. Picked the existing `GoalScheduler` poll loop because (a) lifecycle/observability surface is already paid for, (b) the 30s default poll is the right cadence for 2m / 10m timeouts, and (c) trigger and sweep both need the same `ConnectionManager` reference. Sweep runs in its own session so a sweep failure can't roll back a sibling goal-trigger commit.

- **Timeout values** — pickup 120s / execution 600s. Healthy agents normally pick up < 30s and finish executions in well under 5 minutes, so these bounds are loose enough to avoid false positives but tight enough that a wedged agent gets noticed in one polling generation. Hardcoded constants for now; per-goal tunability is deliberately out of scope.

- **Backfill of stuck rows** — option of writing a one-off cleanup script vs. letting the new sweeper handle it on the first tick. Picked the latter (per user's "(a)" choice) because the migration backfills `assigned_at = created_at` for all assignee-bearing rows; rows that are already past the pickup window get failed on the first sweep, and the existing `consecutive_failures` policy auto-pauses the offending goals as a side effect — exactly the cleanup outcome we'd hand-write a script for.

- **Assumption to revisit if violated**: the scheduler is a single in-process loop. If we ever scale cluster horizontally without an advisory-lock layer, two replicas could double-trigger goals AND double-sweep stuck tasks; the broadcast-from-scheduler path would also need rerouting through a cross-process pub/sub. Both are explicitly out of scope here (#302 Phase 3).

## Result

- Scheduler-fired task assignment messages now reach live ws sessions, so `decide_policy` mention path actually wakes the assignee.
- Stuck tasks flip to `failed` with a reason code (`pickup_timeout` / `execution_timeout`) and the parent goal auto-pauses on the third consecutive failure, in line with the existing #302 policy.
- Backlog of 17 stranded rows from the live test will be cleaned up automatically on the first sweep tick after rollout (no separate cleanup needed).
- Manual smoke verification (interval=60s goal wakes the agent; agent stop → task transitions to failed within 2 min) is pending — automated tests cover the unit contracts and the scheduler→inject→broadcast wiring.
