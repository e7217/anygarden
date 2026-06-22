# feat(cluster): orchestrator create_task soft dedup + fan-out 캡 (#484)

- Commit: `18fca28` (18fca286503958d4c71aa6b47b60348a96746432)
- Author: Changyong Um
- Date: 2026-06-22
- PR: —

## Situation

The orchestrator-facing MCP tool ``create_task`` (`mcp/tools.py`)
persists a new ``Task`` on every call without any duplicate or volume
guard. ``idempotency_key`` is left NULL, so two safeguards that exist
for the goal-firing path (#449) are simply absent here:

1. **No in-flight dedup.** When the orchestrator LLM calls the same
   tool twice in one turn — or re-fires after a transient error — it
   spawns duplicate tasks. The existing self-loop guard only blocks the
   orchestrator from assigning a task to *its own* participant; it does
   nothing about repeated identical assignments to a worker.
2. **No fan-out cap.** An orchestrator stuck in a decompose loop can
   create tasks without bound. Nothing stops a runaway turn from
   flooding a room with hundreds of open tasks.

Both failure modes are realistic for an LLM-driven conductor and both
degrade the room rather than crashing, so they had gone unnoticed.

## Task

- Add a **soft in-flight dedup** before persist: if an open
  (``todo``/``in_progress``) task already exists with the same
  ``(room_id, assignee_participant_id, title)``, return that task's id
  as an idempotent success instead of creating a second row — and do
  not re-inject the assignment mention (the assignee already woke on
  the first create).
- Add a **fan-out cap**: count the room's open tasks and refuse a new
  one once the count reaches ``ANYGARDEN_MAX_OPEN_TASKS_PER_ROOM``
  (default 50), as a fail-soft tool error rather than a crash.
- Keep everything else — input validation, orchestrator/strategy
  authorization, self-loop guard, assignee validation, the normal
  persist+inject path — byte-for-byte unchanged.
- No migration: reuse the existing ``ix_tasks_room_status`` index and
  the existing status vocabulary.

## Action

- `packages/cluster/anygarden/mcp/tools.py`
  - Added module-level ``_OPEN_TASK_STATUSES = ("todo", "in_progress")``
    and ``_DEFAULT_MAX_OPEN_TASKS_PER_ROOM = 50`` near the
    ``create_task`` section, with a comment tying the "open" set to the
    goal-path probe (#449) and the ``ix_tasks_room_status`` index.
  - Inserted, immediately before the persist block (after all existing
    validation/authorization), two fail-soft probes in order:
    1. **Dedup probe** — ``SELECT id ... WHERE room_id = :r AND
       (assignee = :a | assignee IS NULL) AND title = :t AND status IN
       ('todo','in_progress') LIMIT 1``. On a hit, returns
       ``_ok_result(..., deduplicated=True)`` with the existing
       ``task_id`` and skips both the new row and the mention injection.
    2. **Cap check** — ``SELECT count(*) ... WHERE room_id = :r AND
       status IN ('todo','in_progress')``; if ``>= cap`` returns
       ``_error_result(...)``. The cap is read at call time from
       ``ANYGARDEN_MAX_OPEN_TASKS_PER_ROOM`` with a try/except fallback
       to the default so a malformed env value can't disable the guard.
  - Hoisted ``clean_title = title.strip()`` so the probe and the
    persisted row share one normalized title.
  - Added ``import os`` and extended the SQLAlchemy import to include
    ``func`` (for ``func.count()``).
- `packages/cluster/tests/test_create_task_tool.py` — added eight unit
  tests (and a ``from sqlalchemy import func``) covering both
  safeguards and the unchanged paths; see "new test cases" below.

## Decisions

- **Soft probe over hard UNIQUE key (deferred).** A hard
  ``idempotency_key = f"{request_id}:{title_hash}"`` would give true
  exactly-once, but (a) it would false-dedup a legitimately-repeated
  title ("PR review" twice), (b) an ``IntegrityError`` risks crashing
  the turn, and (c) it needs the orchestrator's current-turn
  ``request_id`` plumbed into the MCP context. The open-status-scoped
  soft probe collapses only "the same not-yet-finished work" — a legit
  repeat occurs after the prior task closes, so it misses the probe and
  gets its own row. The hard-key work is split out as follow-up.
- **Skip mention re-injection on a dedup hit.** The first create
  already injected the assignment mention and woke the assignee.
  Re-injecting on the duplicate would double-wake the worker, defeating
  the point of dedup, so the hit path returns early before the inject
  block.
- **Cap counts open tasks only; reads env at call time.** Counting only
  ``todo``/``in_progress`` means closing a task frees a slot, matching
  the intent ("don't let *active* work run away") and the index. ``blocked``
  is deliberately excluded from "open" so a wedged blocker graph can't
  mask the cap. Reading the env per call (vs. at import) lets an
  operator or test override without a restart; default 50 sits above
  normal decomposition (10–20 tasks) and only trips on a flood.
- **Order: dedup → cap → persist.** A duplicate must succeed even when
  the room is at its cap (it adds no row), so the dedup probe runs
  first; the cap only gates genuinely-new rows.
- **Assumption / known limit.** Two near-simultaneous identical calls
  could both miss the probe and create two rows (a race) — acceptable
  for a *soft* guard; the per-room single-turn lock serialises the
  orchestrator in practice, and hard exactly-once is the follow-up.

## Result

- ``create_task`` now collapses same-turn duplicate calls to one row
  (idempotent ``deduplicated=True`` success, no double-wake) and
  refuses to exceed a per-room open-task ceiling with a readable
  fail-soft error the orchestrator can act on.
- New unit tests (8):
  - ``test_duplicate_open_task_is_deduplicated`` — second identical
    call returns the same id, ``deduplicated=True``, 1 row, 1 mention.
  - ``test_dedup_for_unassigned_tasks`` — NULL-assignee repeats collapse
    (consistent ``IS NULL`` matching).
  - ``test_closed_duplicate_title_creates_new_task`` — a ``done`` task
    with the same title does not block a fresh create (probe misses).
  - ``test_dedup_scoped_per_assignee`` — same title, different assignee
    → two distinct rows.
  - ``test_fanout_cap_blocks_excess_open_tasks`` — at the cap, the next
    distinct task is refused fail-soft and no row lands.
  - ``test_fanout_cap_ignores_closed_tasks`` — closing a task frees a
    cap slot.
  - ``test_dedup_hit_bypasses_cap`` — a dedup hit succeeds even at the
    cap (creates no row).
  - (plus the existing per-assignee/closed cases stay green unchanged).
- Verification: full ``uv run pytest packages/cluster`` → **1207
  passed, 1 deselected**; ``uv run ruff check`` on the two changed
  files → clean. (The repo's baseline ruff run reports pre-existing
  errors in unrelated untouched files; none are in this change.)
- Non-goals (follow-up): hard ``idempotency_key`` stamp + IntegrityError
  absorption (needs orchestrator request_id plumbing); no migration.
