# fix(ci): build before the release browser gate and fix a timeline test flake

- Author: Claude (assisted, user-directed)
- Date: 2026-11-13
- PR: #1041

## Situation

The `anygarden-v0.29.0` release gate (Vitest) failed on
`TasksSection detail dialog > puts failed attempts on the timeline of finished work`:
the last timeline row was "Attempt 2: Succeeded" instead of "Done". On re-run
Vitest passed but the browser E2E step timed out waiting for its web server.

## Task

Find out why the release gate fails and make the tag release cleanly.

## Action

- The test computed the task's `finished_at` and the last attempt's
  `finished_at` with two separate `ago(5 * 60_000)` calls. When a millisecond
  passed between them, the attempt ended 1 ms later and sorted after "Done".
- `TaskTimeline` sorts stably and pushes "Done" after the attempts, so an exact
  tie already ends with "Done"; the product code is fine.
- The test now shares one `finishedAt` stamp.
- Since #1002, Playwright on CI serves the built bundle with `vite preview`.
  `ci.yml` builds before E2E, but `release.yml` still ran E2E first and built
  afterwards, so there was no bundle to serve. `release.yml` now builds before
  the E2E step.

## Result

`TasksSection.test.tsx` passes (16/16). `anygarden-v0.29.0` had published
nothing (gate failed before build), so the tag is re-pointed at the merged fix.
