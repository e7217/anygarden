# fix(tasks): give a general task's thread-answer resume its own instructions (#808)

- Commit: `60b9bfe` (60b9bfeca4a785e7eb11d631d01248e994bdce82)
- Author: Changyong Um
- Date: 2026-10-12T10:28:19+09:00
- PR: #808 (issue)

## Situation

The #806 live check (2026-10-09) ran the general-room question flow (G3): ask, answer in the thread, resume. In 2 of 3 runs the resumed turn reported `done` even though writing the file had failed. The agent then tried to take the report back and could not.

The resume notice that `answer_from_thread` injected reused the assignment template:
- "assigned to you"
- "record in_progress right after starting"
- "call done when you respond"
- "the task auto-fails in 5 minutes"

First turns, which never saw this notice, reported the same failure honestly in 3 of 3 runs.

## Task

- Give the general-task thread-answer resume its own instructions:
  - Continue the same task using the answer.
  - The task is already in progress.
  - Report `done` only after the result is made and checked; otherwise report `blocked` or `failed` with the reason.
  - Drop the auto-failure threat and the start-recording step.
- Leave project execution resumes, which have their own wording, unchanged.
- Keep the chat card rendering for the new event value.

## Action

- `packages/cluster/anygarden/messages/service.py`
  - `inject_task_assignment_message` accepts `event="resumed"` and builds a `**RESUMED**` body for it.
  - The assignment body is unchanged for `assigned` and `reassigned`.
- `packages/cluster/anygarden/general_tasks.py`: `answer_from_thread` injects with `event="resumed"`.
- Frontend
  - `src/lib/taskAssignment.ts` accepts `resumed`.
  - `TaskAssignmentCard.tsx` maps events to labels through `EVENT_LABEL`.
  - `i18n/catalogs/chat.ts` adds `chat.taskResumed` ("Task resumed" / "작업 재개").
- Tests
  - `tests/test_general_tasks.py::test_thread_answer_resume_notice_has_its_own_instructions` checks the event, that the answer is present and that the warnings are absent.
  - `taskAssignment.test.ts` accepts `resumed`.
- `packages/cluster/CHANGELOG.md` gets a Fixed entry.

## Decisions

- **Options considered**
  - Reword the shared assignment template for every event.
  - Add a resume-specific branch keyed on a new event value.
  - Strip only the auto-failure sentence.
- **Chosen: the resume branch with a new event.**
  - The shared template is correct for real assignments, where the agent must record a start and the pickup sweeper applies.
  - Project execution resumes (`requests.py`, `approvals.py`, `qa_repairs.py`) carry their own execution block, and changing them was out of scope for a general-room defect.
  - A distinct `resumed` event also lets the card say "Task resumed" instead of "Task reassigned", which was wrong because the assignee did not change.
- **Rejected: stripping one sentence.** The numbered "call done when you respond" step is itself part of the push toward an early `done`.
- **Assumption:** the wording is the cause. The evidence:
  - Before (old notice, 10-09): 2 of 3 false `done`.
  - After (new notice, 10-12): 0 of 3. The same scenario, environment and model reported `blocked` honestly in all three runs.

  The samples are small (k=3). If false `done` reappears, consider letting the latest turn correct its own `done` (F2), which still needs a product decision.

## Result

- Resumed general tasks get resume-specific instructions and a "Task resumed" card.
- Live rerun (`docs/e2e/2026-10-12-808-resume-notice/`, local-only):
  - 0 of 3 false `done`, and all 3 runs reported `blocked` with the environment error and kept the drafted notice in the result.
  - Single resume turn: 3 of 3. No open turns.
- Test suites: cluster 2742 passed, frontend 819 passed, frontend build passed.
- Pending, out of scope: F2 (correcting a wrong `done`), F5 (message order after a very fast answer) and F7 (origin label of promoted tasks).
