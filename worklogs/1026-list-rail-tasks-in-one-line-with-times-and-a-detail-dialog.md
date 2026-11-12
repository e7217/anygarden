# feat(tasks): list rail tasks in one line with times and a detail dialog (#1026)

- Commit: `2c32e3b` (2c32e3b8db9cb89e42bc83937a58b8dbd3d0d86f)
- Author: Changyong Um
- Date: 2026-11-12T15:06:28+09:00
- Issue: #1026

## Situation

The right rail's task list used an inefficient layout: task titles received roughly 117 px out of a 320 px rail, repeated status headings for each group, and expanded failure reasons, blockers, and execution details as inline `<details>` elements beneath each row. No temporal information was visible — there was no indication of how long a running task had been active or when a finished task ended.

## Task

Redesign the right rail task list to fit one task per line with meaningful trailing text, and move all detail content into an on-demand dialog:

- Flatten the list to a single ordering (in_progress → cancelling → blocked/failed → todo → 3 recent results; older history under "Task history").
- Replace per-status section headings with a status icon on each row.
- Show trailing time text: elapsed duration for running tasks, relative "ago" for finished tasks, blocked/failed label, or assignee for waiting tasks.
- Stop reserving row width for the hover-only delete button (it now overlays the trailing text on hover; the dialog also offers delete).
- Add a `TaskDetailDialog` opened by clicking the task title, surfacing status, assignee, duration, a progress timeline, results, sources, and edit controls (view-only for run-owned tasks).
- Expose `assigned_at`, `started_at`, and `finished_at` from `TaskOut` and the `task.updated` WebSocket payload.

## Action

- **`packages/cluster/anygarden/api/v1/tasks.py`**: Added `assigned_at`, `started_at`, `finished_at` fields to `TaskOut` (lines ~86-91); populated them in `_to_out()` via `.isoformat()` with null-guard.
- **`packages/cluster/anygarden/messages/service.py`**: Included the three new timestamp fields in the `task.updated` broadcast payload.
- **`packages/cluster/frontend/src/lib/taskTime.ts`** (new): `msSince`, `formatSpan`, `formatRunning`, `formatAgo`, `formatStamp` — time helpers used for row trailing text and dialog timeline, locale/i18n-aware via `Translate` / `Locale` / `formatDate` injection.
- **`packages/cluster/frontend/src/components/right-rail/taskStatusIcon.ts`** (new): `STATUS_ICON` record and `statusIconColor` function extracted from `TasksSection` so both the row list and the dialog share one source of truth.
- **`packages/cluster/frontend/src/components/right-rail/TaskDetailDialog.tsx`** (new, 454 lines): Full detail and edit dialog — status/assignee/duration header, progress timeline with per-event stamps, result markdown, source dependency list, inline title/assignee/status edits, delete button. Run-owned tasks render as view-only.
- **`packages/cluster/frontend/src/components/right-rail/TasksSection.tsx`**: Removed `TaskExecutionDetails` inline expander (~80 lines deleted), removed per-status section headings, added `useNow(60_000)` tick for live elapsed time, integrated `TaskDetailDialog`, replaced status label rendering with `STATUS_ICON` / `statusIconColor`, added trailing time logic using `formatAgo` / `formatRunning`.
- **`packages/cluster/frontend/src/hooks/useRoomTasks.ts`**: Added `assigned_at`, `started_at`, `finished_at` to the `Task` type.
- **`packages/cluster/frontend/src/i18n/catalogs/workflows.ts`**: Added i18n keys (en/ko) for time spans (`tasks.spanMinutes`, `tasks.spanHours`, `tasks.spanDays`, `tasks.justStarted`, `tasks.runningFor`, `tasks.justNow`) and dialog labels.
- **`packages/cluster/frontend/src/components/TaskRecoveryState.tsx`**: Exported `attemptOutcomeKey` and `FAILED_ATTEMPT_OUTCOMES` for the timeline, and added `hideHistory` so the dialog does not show run attempts twice.
- **`packages/cluster/tests/test_tasks_api.py`**: One test (`TestTaskTimestamps`) checks `assigned_at` on create, `started_at` after claim and `finished_at` after done.

## Decisions

- **Flat list vs. section headings**: The row icon (Circle/Clock/CheckCircle2/PauseCircle/XCircle) already conveys status at a glance; repeating it as a section label consumed vertical space and added cognitive noise. The flat list with icon-first ordering is visually lighter and matches the density the 320 px rail affords.

- **Dialog vs. inline `<details>` expansion**: Inline expansion pushes subsequent rows down and can make the list feel unstable as items open and close. A modal dialog gives detail content the full viewport width it needs (Markdown, timelines, source lists) without disrupting the list layout. The tradeoff is an extra click to reach details, which is acceptable for content that is rarely needed at a glance.

- **Extracting `taskStatusIcon.ts`**: Both `TasksSection` and `TaskDetailDialog` need the same status-to-icon and status-to-color mappings. Keeping them inline in `TasksSection` would have required either duplication or prop-drilling the icon component into the dialog. A thin shared module avoids both.

- **`taskTime.ts` helpers with injected `t`/`locale`/`formatDate`**: Pure functions that take their i18n dependencies as parameters are testable without mocking the React context, and they avoid importing the hook inside a utility module that might be used server-side later. The injection pattern is consistent with existing `taskPresentation.ts` helpers. Row times are minute-grained, so a one-minute re-render tick is enough; each render reads `Date.now()` so rows loaded mid-minute are not a minute behind the dialog.

- **Server timestamps exposed rather than computed on the client**: `assigned_at`, `started_at`, `finished_at` were already written by the server's task lifecycle logic; surfacing them via `TaskOut` avoids client-side reconstruction from status history and gives the dialog a reliable source for the progress timeline.

## Result

- Right-rail task rows are now single-line with a status icon and contextual trailing text (elapsed time, "X ago", assignee, or blocked/failed label).
- Clicking a task title opens `TaskDetailDialog` with full detail, edit, and delete capability; run-owned tasks are view-only.
- `TaskOut` and `task.updated` payloads carry `assigned_at`, `started_at`, `finished_at` for timeline rendering.
- One API test covers the timestamp fields; new unit tests cover row times, timeline order, attempts on the timeline, title edits and read-only tasks (frontend 1305 tests, task API 153 tests passing).
- The `TaskExecutionDetails` inline expander (~80 lines) and all per-status section headings have been removed from `TasksSection`.
- Remaining: a requeue clears `started_at`/`finished_at`, so the timeline shows only the latest pass for manual tasks; the server does not stamp when a task became blocked, so blocked rows show the state, not a duration. Not live-verified on a node (mocked API screenshots only).
