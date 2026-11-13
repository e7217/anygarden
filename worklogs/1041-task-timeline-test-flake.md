# test(tasks): share one finish stamp in the failed-attempt timeline test

- Author: Claude (assisted, user-directed)
- Date: 2026-11-13
- PR: #1041

## Situation

The `anygarden-v0.29.0` release gate (Vitest) failed on
`TasksSection detail dialog > puts failed attempts on the timeline of finished work`:
the last timeline row was "Attempt 2: Succeeded" instead of "Done". PR CI does
not run Vitest, so it had not shown up before.

## Task

Find whether the timeline order is wrong or the test is flaky.

## Action

- The test computed the task's `finished_at` and the last attempt's
  `finished_at` with two separate `ago(5 * 60_000)` calls. When a millisecond
  passed between them, the attempt ended 1 ms later and sorted after "Done".
- `TaskTimeline` sorts stably and pushes "Done" after the attempts, so an exact
  tie already ends with "Done"; the product code is fine.
- The test now shares one `finishedAt` stamp.

## Result

`TasksSection.test.tsx` passes (16/16). The release gate was re-run for the
existing tag.
