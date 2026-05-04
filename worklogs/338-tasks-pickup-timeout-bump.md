# fix(tasks): bump pickup timeout and harden status directive (#338)

- Branch: `fix/338-tasks-pickup-timeout-bump`
- Date: 2026-05-05
- Issue: #338

## Situation

Some DM-created tasks reached `failed (pickup_timeout)` even when the
assigned agent eventually produced a useful answer. Two factors made that
failure mode likely: the pickup window was only 120 seconds, and the
synthetic task-assignment message carried the `mark_task_status` instruction
as a decorative trailing aside that some engines skipped.

## Task

- Give slower engines enough room to acknowledge a freshly assigned task.
- Make the task-status reporting instruction more difficult for the assignee
  LLM to miss.
- Preserve the first-line `<@user:{pid}> [TASK] {title}` contract used by
  mention routing, frontend title extraction, and message-log readers.

## Action

- Updated `TASK_PICKUP_TIMEOUT_SECONDS` from 120 to 300 seconds and refreshed
  the adjacent policy comment to describe the 5-minute pickup window.
- Reworked `inject_task_assignment_message` content from an italic aside into a
  `REQUIRED ACTIONS` block with explicit start and completion steps.
- Added a regression test that checks the new action-block markers while
  keeping the existing `mark_task_status`, task id, `in_progress`, and `done`
  assertions intact.
- Declared `python-multipart` as a cluster runtime dependency. The app imports
  a FastAPI multipart upload route during test setup, so a fresh worktree could
  not run `uv run pytest` without it.

## Result

- `uv run pytest tests/test_tasks_injection.py -x` passed.
- `uv run pytest tests/test_goals_sweeper.py tests/test_goals_scheduler.py -x`
  passed.
- `uv run pytest` passed: 915 passed, 1 deselected, 1 warning.
- `npm run build` in `packages/cluster/frontend` passed with the preexisting
  Vite large-chunk warning.
